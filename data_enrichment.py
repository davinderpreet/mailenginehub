"""
data_enrichment.py — Customer Profile Enrichment Engine
Pulls order history from Omnisend, computes rich CustomerProfile per contact.
"""

import os, json, logging, time
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OMNISEND_API_KEY = os.getenv("OMNISEND_API_KEY", "")
OMNISEND_BASE    = "https://api.omnisend.com/v3"

# ── Category inference from product title keywords ────────────────────────────
CATEGORY_KEYWORDS = {
    "Bluetooth Headsets": ["headset", "bluetooth headset", "earpiece", "g10", "g7", "th11", "th-11", "trucker headset"],
    "Dash Cams":          ["dash cam", "dashcam", "a20", "car camera", "parking mode"],
    "Phone Accessories":  ["phone case", "screen protector", "charger", "cable", "usb"],
    "Speakers":           ["speaker", "soundbar", "audio"],
    "Smart Home":         ["smart", "wifi plug", "bulb", "automation"],
    "Other Electronics":  [],  # fallback
}

def infer_category(product_title: str) -> str:
    title_lower = product_title.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category == "Other Electronics":
            continue
        for kw in keywords:
            if kw in title_lower:
                return category
    return "Other Electronics"


# ── Omnisend API helper ────────────────────────────────────────────────────────
def _omnisend_get(path: str, params: dict = None) -> dict:
    import urllib.request, urllib.parse, urllib.error
    url = OMNISEND_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-KEY": OMNISEND_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.error(f"Omnisend API error {e.code} for {path}: {e.read().decode()[:200]}")
        return {}
    except Exception as e:
        logger.error(f"Omnisend request failed: {e}")
        return {}


# ── Pull ALL orders from Omnisend (paginated) ─────────────────────────────────
def fetch_all_omnisend_orders(since_date: str = None) -> list:
    """Fetch all orders. Optional since_date = 'YYYY-MM-DD' for incremental sync."""
    all_orders = []
    offset = 0
    limit  = 250
    params = {"limit": limit}
    if since_date:
        params["dateFrom"] = since_date + "T00:00:00Z"

    logger.info("Fetching orders from Omnisend...")
    while True:
        params["offset"] = offset
        data = _omnisend_get("/orders", params)
        orders = data.get("orders") or []
        if not orders:
            break
        all_orders.extend(orders)
        logger.info(f"  Fetched {len(all_orders)} orders so far...")
        if data.get("paging", {}).get("next") is None:
            break
        offset += limit
        time.sleep(0.2)  # rate limit courtesy

    logger.info(f"Total orders fetched: {len(all_orders)}")
    return all_orders


# ── Store orders in DB ─────────────────────────────────────────────────────────
def store_orders(orders: list) -> tuple:
    """Upsert orders + items into DB. Returns (new, updated, skipped) counts."""
    from database import Contact, OmnisendOrder, OmnisendOrderItem, init_db
    init_db()

    # Build email → contact_id lookup
    email_to_contact = {}
    for c in Contact.select(Contact.id, Contact.email):
        email_to_contact[c.email.lower().strip()] = c.id

    new_count = updated_count = skipped_count = 0

    for o in orders:
        order_id = str(o.get("orderID", ""))
        if not order_id:
            skipped_count += 1
            continue

        email   = (o.get("email") or "").lower().strip()
        contact_id = email_to_contact.get(email)

        # Parse order total (Omnisend stores in cents)
        order_total   = round((o.get("orderSum") or 0) / 100, 2)
        discount_amt  = round((o.get("discountSum") or 0) / 100, 2)
        shipping_addr = o.get("shippingAddress") or o.get("billingAddress") or {}

        try:
            ordered_at = datetime.strptime(o["createdAt"], "%Y-%m-%dT%H:%M:%SZ") if o.get("createdAt") else None
        except ValueError:
            ordered_at = None

        existing = OmnisendOrder.get_or_none(OmnisendOrder.order_id == order_id)
        if existing:
            # Update if changed
            existing.payment_status    = o.get("paymentStatus") or ""
            existing.fulfillment_status = o.get("fulfillmentStatus") or ""
            existing.save()
            updated_count += 1
            db_order = existing
        else:
            db_order = OmnisendOrder.create(
                contact_id       = contact_id,
                email            = email,
                order_id         = order_id,
                order_number     = o.get("orderNumber") or 0,
                order_total      = order_total,
                currency         = o.get("currency") or "CAD",
                payment_status   = o.get("paymentStatus") or "",
                fulfillment_status = o.get("fulfillmentStatus") or "",
                discount_code    = o.get("discountCode") or "",
                discount_amount  = discount_amt,
                shipping_city    = shipping_addr.get("city") or "",
                shipping_province = shipping_addr.get("state") or "",
                ordered_at       = ordered_at,
            )
            # Store line items
            for item in (o.get("products") or []):
                OmnisendOrderItem.create(
                    order         = db_order,
                    product_id    = str(item.get("productID") or ""),
                    product_title = item.get("title") or "",
                    variant_title = item.get("variantTitle") or "",
                    sku           = item.get("sku") or "",
                    quantity      = item.get("quantity") or 1,
                    unit_price    = round((item.get("price") or 0) / 100, 2),
                    discount      = round((item.get("discount") or 0) / 100, 2),
                    vendor        = item.get("vendor") or "",
                )
            new_count += 1

    logger.info(f"Orders stored — new: {new_count}, updated: {updated_count}, skipped: {skipped_count}")
    return new_count, updated_count, skipped_count


# ── Compute CustomerProfile for all contacts ─────────────────────────────────
def compute_all_profiles() -> int:
    """Compute/refresh CustomerProfile for every contact that has orders. Returns count."""
    from database import (Contact, OmnisendOrder, OmnisendOrderItem,
                          CustomerProfile, init_db)
    from peewee import fn
    init_db()

    # Get all distinct emails that have orders
    emails_with_orders = set(
        r.email for r in OmnisendOrder.select(OmnisendOrder.email).distinct()
    )
    logger.info(f"Computing profiles for {len(emails_with_orders)} customers with order history...")

    count = 0
    for email in emails_with_orders:
        try:
            _compute_profile_for_email(email)
            count += 1
        except Exception as e:
            logger.error(f"Profile compute failed for {email}: {e}")

    logger.info(f"Profiles computed: {count}")
    return count


def _compute_profile_for_email(email: str):
    """Compute and upsert CustomerProfile for one email."""
    from database import Contact, OmnisendOrder, OmnisendOrderItem, CustomerProfile
    from peewee import fn

    contact = Contact.get_or_none(Contact.email == email)

    # Pull all paid orders for this email
    orders = list(
        OmnisendOrder.select()
        .where(
            OmnisendOrder.email == email,
            OmnisendOrder.payment_status == "paid"
        )
        .order_by(OmnisendOrder.ordered_at)
    )

    if not orders:
        return

    # ── Core purchase stats ──────────────────────────────────────────────────
    total_orders  = len(orders)
    total_spent   = sum(o.order_total for o in orders)
    avg_order_val = round(total_spent / total_orders, 2)

    first_order   = orders[0].ordered_at
    last_order    = orders[-1].ordered_at
    now           = datetime.utcnow()
    days_since    = (now - last_order).days if last_order else 999

    # Average days between orders
    if total_orders >= 2 and first_order and last_order:
        span_days = (last_order - first_order).days
        avg_days_between = round(span_days / (total_orders - 1), 1)
    else:
        avg_days_between = 0.0

    # ── Product preferences ──────────────────────────────────────────────────
    order_ids     = [o.id for o in orders]
    items         = list(OmnisendOrderItem.select().where(OmnisendOrderItem.order_id.in_(order_ids)))
    total_items   = sum(i.quantity for i in items)

    # Count product title frequency
    product_counts = {}
    category_counts = {}
    all_products = []

    for item in items:
        title = item.product_title.strip()
        if not title:
            continue
        product_counts[title] = product_counts.get(title, 0) + item.quantity
        category = infer_category(title)
        category_counts[category] = category_counts.get(category, 0) + item.quantity
        all_products.append({
            "title": title,
            "sku": item.sku,
            "qty": item.quantity,
            "price": item.unit_price,
        })

    top_products   = sorted(product_counts, key=product_counts.get, reverse=True)[:5]
    top_categories = sorted(category_counts, key=category_counts.get, reverse=True)[:3]

    # ── Price tier ───────────────────────────────────────────────────────────
    if items:
        avg_item_price = total_spent / total_items if total_items else 0
        if avg_item_price < 30:
            price_tier = "budget"
        elif avg_item_price < 80:
            price_tier = "mid"
        else:
            price_tier = "premium"
    else:
        price_tier = "unknown"

    # ── Discount behaviour ───────────────────────────────────────────────────
    orders_with_discount   = sum(1 for o in orders if o.discount_amount > 0)
    discount_sensitivity   = round(orders_with_discount / total_orders, 2)
    has_used_discount      = orders_with_discount > 0

    # ── Geography (from most recent order) ───────────────────────────────────
    city     = orders[-1].shipping_city or ""
    province = orders[-1].shipping_province or ""

    # ── Plain-English profile summary for Claude ─────────────────────────────
    products_str   = ", ".join(top_products[:3]) if top_products else "various products"
    categories_str = ", ".join(top_categories) if top_categories else "electronics"
    location_str   = f"{city}, {province}" if city else province or "Canada"
    freq_str       = (f"every {int(avg_days_between)} days" if avg_days_between > 0 else "once")
    discount_str   = (f"uses discounts {int(discount_sensitivity*100)}% of the time" if has_used_discount
                      else "never used a discount code")

    profile_summary = (
        f"{total_orders} order(s), ${total_spent:.2f} CAD total spent, "
        f"avg ${avg_order_val:.2f} per order. "
        f"Buys {freq_str}. Last order {days_since} days ago. "
        f"Top products: {products_str}. "
        f"Categories: {categories_str}. "
        f"Price tier: {price_tier}. {discount_str.capitalize()}. "
        f"Location: {location_str}."
    )

    # ── Upsert CustomerProfile ───────────────────────────────────────────────
    profile = CustomerProfile.get_or_none(CustomerProfile.email == email)
    data = dict(
        contact_id              = contact.id if contact else None,
        email                   = email,
        total_orders            = total_orders,
        total_spent             = total_spent,
        avg_order_value         = avg_order_val,
        first_order_at          = first_order,
        last_order_at           = last_order,
        days_since_last_order   = days_since,
        avg_days_between_orders = avg_days_between,
        top_products            = json.dumps(top_products),
        top_categories          = json.dumps(top_categories),
        all_products_bought     = json.dumps(all_products[:20]),
        price_tier              = price_tier,
        has_used_discount       = has_used_discount,
        discount_sensitivity    = discount_sensitivity,
        total_items_bought      = total_items,
        city                    = city,
        province                = province,
        profile_summary         = profile_summary,
        last_computed_at        = datetime.now(),
    )
    if profile:
        for k, v in data.items():
            setattr(profile, k, v)
        profile.save()

    # ── Sync Contact table with computed order data ──
    try:
        if contact:
            _sync_changed = False
            if contact.total_orders != total_orders:
                contact.total_orders = total_orders
                _sync_changed = True
            _spent_str = f"{total_spent:.2f}"
            if str(contact.total_spent) != _spent_str:
                contact.total_spent = _spent_str
                _sync_changed = True
            if contact.source == 'pixel_capture' and not contact.subscribed and total_orders > 0:
                contact.subscribed = True
                _sync_changed = True
            if _sync_changed:
                contact.save()
    except Exception:
        pass
    else:
        CustomerProfile.create(**data)


# ── Full backfill (run once) ──────────────────────────────────────────────────
def run_full_backfill():
    """Pull ALL historical orders from Omnisend + compute profiles. Run once."""
    logger.info("=" * 60)
    logger.info("STARTING FULL HISTORICAL BACKFILL")
    logger.info("=" * 60)

    # 1. Fetch all orders
    orders = fetch_all_omnisend_orders()
    if not orders:
        logger.error("No orders fetched. Check OMNISEND_API_KEY.")
        return

    # 2. Store in DB
    new, updated, skipped = store_orders(orders)
    logger.info(f"Orders in DB — new: {new}, updated: {updated}, skipped: {skipped}")

    # 3. Compute profiles
    profile_count = compute_all_profiles()
    logger.info(f"Customer profiles built: {profile_count}")
    logger.info("BACKFILL COMPLETE")


# ── Daily incremental sync ────────────────────────────────────────────────────
def run_daily_sync():
    """Pull orders from the last 3 days and recompute affected profiles."""
    since = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
    logger.info(f"Daily sync — fetching orders since {since}")

    orders = fetch_all_omnisend_orders(since_date=since)
    if not orders:
        logger.info("No new orders found.")
        return

    new, updated, _ = store_orders(orders)
    logger.info(f"Sync complete — new: {new}, updated: {updated}")

    # Recompute profiles for affected emails only
    affected_emails = set(o.get("email", "").lower().strip() for o in orders if o.get("email"))
    logger.info(f"Recomputing profiles for {len(affected_emails)} customers...")
    from database import init_db
    init_db()
    for email in affected_emails:
        try:
            _compute_profile_for_email(email)
        except Exception as e:
            logger.error(f"Profile sync failed for {email}: {e}")


if __name__ == "__main__":
    run_full_backfill()
