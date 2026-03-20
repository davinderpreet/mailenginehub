"""
discount_engine.py — Generate unique Shopify discount codes for email campaigns.

Creates real discount codes via Shopify Price Rules API.
Each code is single-use, once-per-customer, with an expiry date.
"""

import sys
sys.path.insert(0, '/var/www/mailengine')

import os
import string
import random
import requests
import logging
from datetime import datetime, timedelta
from database import GeneratedDiscount, Contact, init_db

logger = logging.getLogger("discount_engine")

STORE_URL = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2024-01"

# ── Discount Strategies ──────────────────────────────────────
DISCOUNT_STRATEGIES = {
    "cart_abandonment":   {"type": "percentage",    "value": "5",   "expires_hours": 48,   "prefix": "CART"},
    "browse_abandonment": {"type": "free_shipping", "value": "100", "expires_hours": 72,   "prefix": "SHIP"},
    "winback":            {"type": "percentage",    "value": "10",  "expires_hours": 168,  "prefix": "WB"},
    "welcome":            {"type": "percentage",    "value": "5",   "expires_hours": 336,  "prefix": "WELCOME"},
    "loyalty_reward":     {"type": "percentage",    "value": "10",  "expires_hours": 168,  "prefix": "VIP"},
    "upsell":             {"type": "percentage",    "value": "5",   "expires_hours": 120,  "prefix": "UP"},
    "re_engagement":      {"type": "percentage",    "value": "5",   "expires_hours": 168,  "prefix": "RE"},
    "high_intent":        {"type": "free_shipping", "value": "100", "expires_hours": 72,   "prefix": "HI"},
}


def _random_code(length=6):
    """Generate random alphanumeric code (uppercase, no ambiguous chars)."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No I/O/0/1 to avoid confusion
    return "".join(random.choice(chars) for _ in range(length))


def _shopify_post(endpoint, data):
    """Make a POST request to Shopify Admin API."""
    url = "%s/admin/api/%s/%s" % (STORE_URL, API_VERSION, endpoint)
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=data, headers=headers, timeout=30)
    return resp


def _shopify_delete(endpoint):
    """Make a DELETE request to Shopify Admin API."""
    url = "%s/admin/api/%s/%s" % (STORE_URL, API_VERSION, endpoint)
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    resp = requests.delete(url, headers=headers, timeout=30)
    return resp


def generate_discount_code(email, purpose, override_value=None):
    """
    Create a unique Shopify discount code for a customer.

    Args:
        email: Customer email
        purpose: One of DISCOUNT_STRATEGIES keys
        override_value: Override the default discount value

    Returns:
        dict: {code, value, discount_type, expires_at, shopify_price_rule_id} or None
    """
    strategy = DISCOUNT_STRATEGIES.get(purpose)
    if not strategy:
        logger.warning("Unknown discount purpose: %s", purpose)
        return None

    if not STORE_URL or not ACCESS_TOKEN:
        logger.warning("Shopify credentials not configured")
        return None

    # Generate unique code
    prefix = strategy["prefix"]
    code = "LDAS-%s-%s" % (prefix, _random_code(6))

    # Ensure uniqueness
    attempts = 0
    while GeneratedDiscount.get_or_none(GeneratedDiscount.code == code):
        code = "LDAS-%s-%s" % (prefix, _random_code(6))
        attempts += 1
        if attempts > 10:
            logger.error("Could not generate unique code after 10 attempts")
            return None

    discount_type = strategy["type"]
    value = override_value or strategy["value"]
    now = datetime.now()
    expires_at = now + timedelta(hours=strategy["expires_hours"])

    # Create Shopify Price Rule
    if discount_type == "free_shipping":
        price_rule_data = {
            "price_rule": {
                "title": code,
                "target_type": "shipping_line",
                "target_selection": "all",
                "allocation_method": "each",
                "value_type": "percentage",
                "value": "-100.0",
                "customer_selection": "all",
                "usage_limit": 1,
                "once_per_customer": True,
                "starts_at": now.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
                "ends_at": expires_at.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
            }
        }
    else:
        price_rule_data = {
            "price_rule": {
                "title": code,
                "target_type": "line_item",
                "target_selection": "all",
                "allocation_method": "across",
                "value_type": "percentage",
                "value": "-%s.0" % value,
                "customer_selection": "all",
                "usage_limit": 1,
                "once_per_customer": True,
                "starts_at": now.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
                "ends_at": expires_at.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
            }
        }

    # Try to create in Shopify (requires write_price_rules scope)
    price_rule_id = ""
    shopify_discount_id = ""
    shopify_synced = False

    try:
        resp = _shopify_post("price_rules.json", price_rule_data)
        if resp.status_code in (200, 201):
            price_rule = resp.json().get("price_rule", {})
            price_rule_id = str(price_rule.get("id", ""))

            # Create Discount Code for this Price Rule
            discount_data = {"discount_code": {"code": code}}
            resp2 = _shopify_post(
                "price_rules/%s/discount_codes.json" % price_rule_id,
                discount_data
            )
            if resp2.status_code in (200, 201):
                discount_code_obj = resp2.json().get("discount_code", {})
                shopify_discount_id = str(discount_code_obj.get("id", ""))
                shopify_synced = True
            else:
                logger.warning("Price rule created but discount code failed: %s", resp2.text[:200])
        elif resp.status_code == 403:
            # API token lacks write_price_rules scope — create locally only
            logger.info("Shopify write_price_rules not available — code %s created locally only", code)
        else:
            logger.warning("Shopify price rule creation failed (%d) — code %s created locally only",
                          resp.status_code, code)
    except Exception as e:
        logger.warning("Shopify API error (code %s created locally): %s", code, e)

    try:
        # Store in our database (always, even without Shopify sync)
        contact = Contact.get_or_none(Contact.email == email)
        GeneratedDiscount.create(
            contact=contact,
            email=email,
            code=code,
            purpose=purpose,
            discount_type=discount_type,
            value=value,
            shopify_price_rule_id=price_rule_id,
            shopify_discount_id=shopify_discount_id,
            expires_at=expires_at,
            created_at=now,
        )

        status = "Shopify + local" if shopify_synced else "local only"
        logger.info("Discount created (%s): %s for %s (%s, %s%% off, expires %s)",
                     status, code, email, purpose, value, expires_at.strftime("%b %d"))

        return {
            "code": code,
            "value": value,
            "discount_type": discount_type,
            "expires_at": expires_at,
            "shopify_price_rule_id": price_rule_id,
            "shopify_synced": shopify_synced,
        }

    except Exception as e:
        logger.error("Discount creation failed for %s: %s", email, e)
        return None


def get_or_create_discount(email, purpose):
    """
    Get an existing active discount code, or create a new one.
    Prevents duplicate codes for the same customer + purpose.

    Returns:
        dict: {code, value, discount_type, expires_at} or None
    """
    now = datetime.now()

    # Check for existing unexpired, unused discount
    existing = (GeneratedDiscount.select()
        .where(
            GeneratedDiscount.email == email,
            GeneratedDiscount.purpose == purpose,
            GeneratedDiscount.used == False,
            GeneratedDiscount.expires_at > now,
        )
        .order_by(GeneratedDiscount.created_at.desc())
        .first())

    if existing:
        return {
            "code": existing.code,
            "value": existing.value,
            "discount_type": existing.discount_type,
            "expires_at": existing.expires_at,
        }

    # Create new discount
    return generate_discount_code(email, purpose)


def get_active_discount(email, purpose=None):
    """
    Look up the customer's most recent active (unexpired, unused) discount code.
    If purpose is given, filter by purpose. Otherwise return any active code.

    Returns:
        dict: {code, value, discount_type, expires_at} or None
    """
    now = datetime.now()
    query = (GeneratedDiscount.select()
        .where(
            GeneratedDiscount.email == email,
            GeneratedDiscount.used == False,
            GeneratedDiscount.expires_at > now,
        )
        .order_by(GeneratedDiscount.created_at.desc()))

    if purpose:
        query = query.where(GeneratedDiscount.purpose == purpose)

    existing = query.first()
    if existing:
        return {
            "code": existing.code,
            "value": existing.value,
            "discount_type": existing.discount_type,
            "expires_at": existing.expires_at,
        }
    return None


def get_discount_display(discount_info):
    """
    Convert discount info to human-readable display text.

    Args:
        discount_info: dict from get_or_create_discount()

    Returns:
        dict: {code, display_text, expires_text, value_display}
    """
    if not discount_info:
        return None

    code = discount_info["code"]
    dtype = discount_info["discount_type"]
    value = discount_info["value"]
    expires = discount_info["expires_at"]

    if dtype == "free_shipping":
        value_display = "FREE SHIPPING"
        display_text = "Free shipping on your order"
    else:
        value_display = "%s%% OFF" % value
        display_text = "%s%% off your entire order" % value

    # Format expiry
    if expires:
        days_left = (expires - datetime.now()).days
        if days_left <= 0:
            expires_text = "Expires today"
        elif days_left == 1:
            expires_text = "Expires tomorrow"
        elif days_left <= 7:
            expires_text = "Expires in %d days" % days_left
        else:
            expires_text = "Expires %s" % expires.strftime("%b %d")
    else:
        expires_text = ""

    return {
        "code": code,
        "display_text": display_text,
        "value_display": value_display,
        "expires_text": expires_text,
    }


def cleanup_expired_discounts(days_old=30):
    """
    Delete expired, unused discount codes from Shopify and our database.
    Run nightly to prevent Shopify price rule accumulation.
    """
    cutoff = datetime.now() - timedelta(days=days_old)

    expired = list(GeneratedDiscount.select().where(
        GeneratedDiscount.used == False,
        GeneratedDiscount.expires_at < cutoff,
    ))

    deleted = 0
    for d in expired:
        # Delete from Shopify
        if d.shopify_price_rule_id:
            try:
                _shopify_delete("price_rules/%s.json" % d.shopify_price_rule_id)
            except:
                pass

        # Delete from our database
        d.delete_instance()
        deleted += 1

    if deleted > 0:
        logger.info("Cleaned up %d expired discount codes", deleted)
    return deleted


# ── CLI ──────────────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/var/www/mailengine/.env")

    # Re-read env
    STORE_URL = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
    ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

    init_db()

    if "--test" in sys.argv:
        # Test creating a discount code
        email = sys.argv[2] if len(sys.argv) > 2 else "test@example.com"
        purpose = sys.argv[3] if len(sys.argv) > 3 else "winback"
        print("=== Test Discount Code Generation ===")
        print("Email: %s, Purpose: %s" % (email, purpose))
        result = generate_discount_code(email, purpose)
        if result:
            print("SUCCESS:")
            print("  Code: %s" % result["code"])
            print("  Value: %s%% off" % result["value"])
            print("  Type: %s" % result["discount_type"])
            print("  Expires: %s" % result["expires_at"])
            print("  Price Rule ID: %s" % result["shopify_price_rule_id"])

            display = get_discount_display(result)
            print("\nDisplay:")
            print("  %s" % display["value_display"])
            print("  %s" % display["display_text"])
            print("  %s" % display["expires_text"])
        else:
            print("FAILED")

    elif "--cleanup" in sys.argv:
        cleaned = cleanup_expired_discounts()
        print("Cleaned up %d expired discounts" % cleaned)

    elif "--list" in sys.argv:
        count = GeneratedDiscount.select().count()
        print("Total discount codes: %d" % count)
        for d in GeneratedDiscount.select().order_by(GeneratedDiscount.created_at.desc()).limit(10):
            status = "USED" if d.used else ("EXPIRED" if d.expires_at and d.expires_at < datetime.now() else "ACTIVE")
            print("  %s | %s | %s | %s | %s" % (
                d.code, d.email[:30], d.purpose, d.value + "%", status
            ))

    else:
        print("Usage:")
        print("  python discount_engine.py --test [email] [purpose]")
        print("  python discount_engine.py --list")
        print("  python discount_engine.py --cleanup")
