"""
One-time backfill: extract checkout_token, cart_token, and shopify_customer_id
from existing CustomerActivity.event_data JSON into the new first-class columns.

Run manually after deploying the schema migration:
    python backfill_activity_tokens.py

Safe to re-run — only updates rows where the target column is still empty.
"""

import json
import sys
import time
from datetime import datetime

# Bootstrap database
from database import CustomerActivity, init_db

init_db()

BATCH_SIZE = 100

# Event types that may contain checkout/cart tokens
CHECKOUT_TYPES = {"abandoned_checkout", "started_checkout", "completed_checkout"}
CART_TYPES = {"viewed_cart", "started_checkout", "abandoned_checkout"}


def _extract_checkout_token(data):
    """Extract checkout token from parsed event_data dict."""
    for key in ("checkout_token", "checkout_id", "token"):
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _extract_cart_token(data):
    """Extract cart token from parsed event_data dict."""
    for key in ("cart_token", "cartToken"):
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _extract_shopify_customer_id(data):
    """Extract Shopify customer ID from parsed event_data dict."""
    # Direct field
    for key in ("shopify_customer_id", "customer_id"):
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()
    # Nested customer object
    cust = data.get("customer")
    if isinstance(cust, dict) and cust.get("id"):
        return str(cust["id"]).strip()
    return ""


def backfill():
    print("=" * 60)
    print("Backfill: CustomerActivity token fields")
    print("=" * 60)

    # Process checkout_token
    filled_ct = 0
    rows = list(CustomerActivity.select().where(
        CustomerActivity.checkout_token == "",
        CustomerActivity.event_type.in_(list(CHECKOUT_TYPES)),
    ))
    print(f"\nCheckout token candidates: {len(rows)}")
    batch = []
    for row in rows:
        try:
            data = json.loads(row.event_data or "{}")
        except Exception:
            continue
        token = _extract_checkout_token(data)
        if token:
            batch.append((row.id, token))
        if len(batch) >= BATCH_SIZE:
            for rid, tok in batch:
                CustomerActivity.update(checkout_token=tok).where(CustomerActivity.id == rid).execute()
            filled_ct += len(batch)
            batch = []
    if batch:
        for rid, tok in batch:
            CustomerActivity.update(checkout_token=tok).where(CustomerActivity.id == rid).execute()
        filled_ct += len(batch)
    print(f"  Filled checkout_token: {filled_ct}")

    # Process cart_token
    filled_cart = 0
    rows = list(CustomerActivity.select().where(
        CustomerActivity.cart_token == "",
        CustomerActivity.event_type.in_(list(CART_TYPES)),
    ))
    print(f"\nCart token candidates: {len(rows)}")
    batch = []
    for row in rows:
        try:
            data = json.loads(row.event_data or "{}")
        except Exception:
            continue
        token = _extract_cart_token(data)
        if token:
            batch.append((row.id, token))
        if len(batch) >= BATCH_SIZE:
            for rid, tok in batch:
                CustomerActivity.update(cart_token=tok).where(CustomerActivity.id == rid).execute()
            filled_cart += len(batch)
            batch = []
    if batch:
        for rid, tok in batch:
            CustomerActivity.update(cart_token=tok).where(CustomerActivity.id == rid).execute()
        filled_cart += len(batch)
    print(f"  Filled cart_token: {filled_cart}")

    # Process shopify_customer_id
    filled_sid = 0
    rows = list(CustomerActivity.select().where(
        CustomerActivity.shopify_customer_id == "",
        CustomerActivity.event_data.contains('"customer_id"'),
    ))
    # Also check for "shopify_customer_id" in event_data
    rows2 = list(CustomerActivity.select().where(
        CustomerActivity.shopify_customer_id == "",
        CustomerActivity.event_data.contains('"shopify_customer_id"'),
    ))
    seen_ids = {r.id for r in rows}
    for r in rows2:
        if r.id not in seen_ids:
            rows.append(r)
    print(f"\nShopify customer ID candidates: {len(rows)}")
    batch = []
    for row in rows:
        try:
            data = json.loads(row.event_data or "{}")
        except Exception:
            continue
        sid = _extract_shopify_customer_id(data)
        if sid:
            batch.append((row.id, sid))
        if len(batch) >= BATCH_SIZE:
            for rid, val in batch:
                CustomerActivity.update(shopify_customer_id=val).where(CustomerActivity.id == rid).execute()
            filled_sid += len(batch)
            batch = []
    if batch:
        for rid, val in batch:
            CustomerActivity.update(shopify_customer_id=val).where(CustomerActivity.id == rid).execute()
        filled_sid += len(batch)
    print(f"  Filled shopify_customer_id: {filled_sid}")

    print(f"\n{'=' * 60}")
    print(f"Done. checkout_token={filled_ct}, cart_token={filled_cart}, shopify_customer_id={filled_sid}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    backfill()
