"""
Shopify Customer Sync
Pulls all customers from your Shopify store into the local database.
Enriches contacts with order history, location, and Shopify customer ID.
Also handles real-time webhooks for new customer creation.
"""

import re
import requests
import os
import hmac
import hashlib
import base64
from datetime import datetime
from database import Contact


# Fields pulled from Shopify Customer API
# Note: accepts_marketing is deprecated in API 2024-01 (returns null).
# Use email_marketing_consent.state and sms_marketing_consent.state instead.
_FIELDS = (
    "id,email,first_name,last_name,phone,tags,"
    "email_marketing_consent,sms_marketing_consent,"
    "orders_count,total_spent,created_at,default_address"
)


def _parse_email_consent(customer):
    """Return True only if customer has explicitly subscribed to email marketing."""
    consent = customer.get("email_marketing_consent") or {}
    return consent.get("state") == "subscribed"


def _parse_sms_consent(customer):
    """Return True only if customer has explicitly subscribed to SMS marketing."""
    consent = customer.get("sms_marketing_consent") or {}
    return consent.get("state") == "subscribed"


def _build_store_url():
    """Return the store base URL with https:// guaranteed."""
    url = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
    if url and not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


def _parse_shopify_timestamp(timestamp_str):
    """
    Parse Shopify ISO 8601 timestamp (e.g. '2025-11-15T10:30:45Z') to datetime.
    Returns datetime or None if parsing fails.
    """
    if not timestamp_str:
        return None
    try:
        # Shopify timestamps are in ISO 8601 format with Z suffix
        return datetime.strptime(timestamp_str.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def verify_shopify_webhook(request_body, request_headers):
    """
    Verify that a webhook came from Shopify using HMAC-SHA256 signature.
    Shopify signs webhooks using the app API secret (SHOPIFY_WEBHOOK_SECRET in .env).
    If SHOPIFY_WEBHOOK_SECRET is not set, verification is skipped (returns True with warning).

    Returns:
        (bool, str): (is_valid, error_message)
    """
    webhook_secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        # Secret not configured — skip verification (log warning)
        # Add SHOPIFY_WEBHOOK_SECRET to .env from Shopify Admin → Settings →
        # Custom apps → [App] → API credentials → API secret key
        return True, "SHOPIFY_WEBHOOK_SECRET not set — skipping HMAC verification"

    shopify_hmac = request_headers.get("X-Shopify-Hmac-SHA256", "")
    if not shopify_hmac:
        return False, "Missing X-Shopify-Hmac-SHA256 header"

    computed_hmac = base64.b64encode(
        hmac.new(
            webhook_secret.encode(),
            request_body,
            hashlib.sha256
        ).digest()
    ).decode()

    is_valid = hmac.compare_digest(computed_hmac, shopify_hmac)
    return is_valid, "" if is_valid else "Invalid HMAC signature"


def handle_shopify_customer_webhook(customer_data):
    """
    Process a Shopify customer created/updated webhook.
    Creates or updates a Contact record.

    Args:
        customer_data (dict): The customer JSON from Shopify webhook

    Returns:
        (Contact, bool): (contact, was_created)
    """
    email = (customer_data.get("email") or "").strip().lower()
    if not email:
        return None, False

    # Tags — preserve existing Shopify tags and add "shopify" marker
    tags = customer_data.get("tags") or ""
    if "shopify" not in tags:
        tags = ("shopify," + tags).strip(",")

    # Enriched fields
    address      = customer_data.get("default_address") or {}
    shopify_id   = str(customer_data.get("id") or "")
    city         = address.get("city") or ""
    country      = address.get("country_code") or ""
    total_orders = int(customer_data.get("orders_count") or 0)
    total_spent  = float(customer_data.get("total_spent") or 0)
    shopify_created_at = _parse_shopify_timestamp(customer_data.get("created_at"))

    contact, created = Contact.get_or_create(
        email=email,
        defaults={
            "first_name":   customer_data.get("first_name") or "",
            "last_name":    customer_data.get("last_name") or "",
            "phone":        customer_data.get("phone") or "",
            "tags":         tags,
            "source":       "shopify",
            "subscribed":   _parse_email_consent(customer_data),
            "sms_consent":  _parse_sms_consent(customer_data),
            "shopify_id":   shopify_id,
            "city":         city,
            "country":      country,
            "total_orders": total_orders,
            "total_spent":  total_spent,
            "created_at":   shopify_created_at or datetime.now(),
        }
    )

    if not created:
        # Update enriched fields on webhook (assuming it's a customer update)
        contact.first_name   = customer_data.get("first_name") or contact.first_name
        contact.last_name    = customer_data.get("last_name") or contact.last_name
        contact.phone        = customer_data.get("phone") or contact.phone
        # Only downgrade subscription if contact didn't explicitly opt in via popup
        shopify_consent = _parse_email_consent(customer_data)
        if shopify_consent or contact.source != "popup_widget":
            contact.subscribed = shopify_consent
        contact.sms_consent  = _parse_sms_consent(customer_data)
        contact.shopify_id   = shopify_id or contact.shopify_id
        contact.city         = city or contact.city
        contact.country      = country or contact.country
        contact.total_orders = total_orders if total_orders > 0 else contact.total_orders
        contact.total_spent  = total_spent if total_spent > 0 else contact.total_spent
        if shopify_created_at:
            contact.created_at = shopify_created_at
        if "shopify" not in contact.tags:
            contact.tags = (contact.tags + ",shopify").strip(",")
        contact.save()

    return contact, created


def sync_shopify_customers(progress_callback=None):
    """
    Sync all Shopify customers to local contacts database.
    Pulls enriched data: order count, total spent, city, country, Shopify ID, customer creation date.

    The actual customer creation date from Shopify is stored in created_at field for new contacts.
    On re-sync, existing created_at values are preserved (not overwritten).

    progress_callback(synced_so_far) is called after each page if provided.
    Returns: (synced_count, error_message, new_contacts_list)
    """
    store_url    = _build_store_url()
    access_token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

    if not store_url or not access_token:
        return 0, "Shopify credentials not configured. Add them to your .env file.", []

    headers      = {"X-Shopify-Access-Token": access_token}
    synced       = 0
    page_info    = None
    new_contacts = []
    seen_pages   = set()  # loop guard

    try:
        while True:
            url = f"{store_url}/admin/api/2024-01/customers.json"
            # Shopify cursor pagination: do NOT send fields/filters alongside page_info
            # or the cursor resets and loops forever.
            if page_info:
                params = {"limit": 250, "page_info": page_info}
            else:
                params = {"limit": 250, "fields": _FIELDS}

            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 401:
                return 0, "Invalid Shopify API token. Check your SHOPIFY_ACCESS_TOKEN.", []
            if response.status_code != 200:
                return synced, f"Shopify API error {response.status_code}: {response.text[:200]}", []

            customers = response.json().get("customers", [])
            if not customers:
                break

            for customer in customers:
                email = (customer.get("email") or "").strip().lower()
                if not email:
                    continue

                # Tags — preserve existing Shopify tags and add "shopify" marker
                tags = customer.get("tags") or ""
                if "shopify" not in tags:
                    tags = ("shopify," + tags).strip(",")

                # Enriched fields
                address      = customer.get("default_address") or {}
                shopify_id   = str(customer.get("id") or "")
                city         = address.get("city")         or ""
                country      = address.get("country_code") or ""
                total_orders = int(customer.get("orders_count") or 0)
                total_spent  = str(customer.get("total_spent")  or "0.00")

                # Parse Shopify customer creation date (ISO 8601 format)
                shopify_created_at = _parse_shopify_timestamp(customer.get("created_at"))

                contact, created = Contact.get_or_create(
                    email=email,
                    defaults={
                        "first_name":   customer.get("first_name") or "",
                        "last_name":    customer.get("last_name")  or "",
                        "phone":        customer.get("phone")      or "",
                        "tags":         tags,
                        "source":       "shopify",
                        "subscribed":   _parse_email_consent(customer),
                        "sms_consent":  _parse_sms_consent(customer),
                        "shopify_id":   shopify_id,
                        "city":         city,
                        "country":      country,
                        "total_orders": total_orders,
                        "total_spent":  total_spent,
                        "created_at":   shopify_created_at or datetime.now(),
                    }
                )

                if created:
                    new_contacts.append(contact)
                else:
                    # Update all enriched fields on re-sync
                    contact.first_name   = customer.get("first_name") or contact.first_name
                    contact.last_name    = customer.get("last_name")  or contact.last_name
                    contact.phone        = customer.get("phone")      or contact.phone
                    # Only downgrade subscription if contact didn't explicitly opt in via popup
                    shopify_consent = _parse_email_consent(customer)
                    if shopify_consent or contact.source != "popup_widget":
                        contact.subscribed = shopify_consent
                    contact.sms_consent  = _parse_sms_consent(customer)
                    contact.shopify_id   = shopify_id   or contact.shopify_id
                    contact.city         = city         or contact.city
                    contact.country      = country      or contact.country
                    contact.total_orders = total_orders if total_orders > 0 else contact.total_orders
                    contact.total_spent  = total_spent  if total_spent != "0.00" else contact.total_spent
                    if shopify_created_at:
                        contact.created_at = shopify_created_at
                    if "shopify" not in contact.tags:
                        contact.tags = (contact.tags + ",shopify").strip(",")
                    contact.save()

                synced += 1

            if progress_callback:
                progress_callback(synced)

            # Shopify cursor-based pagination via Link header
            link_header = response.headers.get("Link", "")
            page_info = None

            # Find the next link entry and extract page_info from it
            if 'rel="next"' in link_header:
                # Match the URL within angle brackets followed by rel="next"
                next_match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
                if next_match:
                    url = next_match.group(1)
                    # Extract page_info from the URL
                    page_match = re.search(r'page_info=([^&"]+)', url)
                    page_info = page_match.group(1) if page_match else None

            if not page_info:
                break
            # Loop guard — stop if we've seen this cursor before
            if page_info in seen_pages:
                break
            seen_pages.add(page_info)

        return synced, None, new_contacts

    except requests.exceptions.ConnectionError:
        return 0, f"Cannot connect to {store_url}. Check SHOPIFY_STORE_URL.", []
    except requests.exceptions.Timeout:
        return synced, "Shopify API timed out. Re-run sync to continue.", []
    except Exception as e:
        return synced, str(e), []
