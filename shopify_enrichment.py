"""
shopify_enrichment.py
Pull all orders + customers from Shopify, then rebuild CustomerProfile
for every single contact — even those with zero purchases.
"""

import os, sys, json, logging, time, re
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv('/var/www/mailengine/.env')

sys.path.insert(0, '/var/www/mailengine')

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STORE   = os.getenv('SHOPIFY_STORE_URL', '').rstrip('/')
TOKEN   = os.getenv('SHOPIFY_ACCESS_TOKEN', '')
API_VER = '2024-01'
BASE    = f"{STORE}/admin/api/{API_VER}"

# Category inference from product title
CATEGORY_KEYWORDS = {
    "Bluetooth Headsets": ["headset", "earpiece", "th11", "th-11", "g10", "g7", "g3", "geforce", "trucker headset", "bluetooth head"],
    "Dash Cams":          ["dash cam", "dashcam", "a20", "car camera", "parking mode", "dash-cam"],
    "Phone Accessories":  ["phone case", "screen protector", "charging", "cable", "usb-c", "usb c"],
    "Speakers":           ["speaker", "soundbar"],
    "Smart Home":         ["smart", "wifi plug", "bulb"],
}

def infer_category(title: str) -> str:
    t = title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return cat
    return "Other Electronics"


# ── Shopify API helpers ────────────────────────────────────────────────────────
import urllib.request, urllib.parse, urllib.error

def _shopify_get(url, params=None):
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'X-Shopify-Access-Token': TOKEN,
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            link = r.headers.get('Link', '') or ''
            return data, link
    except urllib.error.HTTPError as e:
        logger.error(f"Shopify HTTP {e.code}: {e.read().decode()[:200]}")
        return {}, ''
    except Exception as e:
        logger.error(f"Shopify request failed: {e}")
        return {}, ''

def _parse_next_url(link_header):
    """Extract rel=next URL from Shopify Link header."""
    if not link_header:
        return None
    for part in link_header.split(','):
        if 'rel="next"' in part:
            m = re.search(r'<([^>]+)>', part)
            if m:
                return m.group(1)
    return None


# ── Pull all Shopify orders ───────────────────────────────────────────────────
def fetch_all_shopify_orders(since_id=None):
    """Fetch all orders from Shopify. Returns list of raw order dicts."""
    all_orders = []
    url = f"{BASE}/orders.json"
    params = {'status': 'any', 'limit': 250}
    if since_id:
        params['since_id'] = since_id

    logger.info("Fetching all Shopify orders...")
    while url:
        data, link = _shopify_get(url, params if params else None)
        params = None  # only on first request; cursor takes over
        orders = data.get('orders', [])
        if not orders:
            break
        all_orders.extend(orders)
        logger.info(f"  Fetched {len(all_orders)} orders...")
        url = _parse_next_url(link)
        time.sleep(0.5)

    logger.info(f"Total Shopify orders fetched: {len(all_orders)}")
    return all_orders


# ── Pull all Shopify customers ────────────────────────────────────────────────
def fetch_all_shopify_customers():
    """Fetch all customer records from Shopify."""
    all_customers = []
    url = f"{BASE}/customers.json"
    params = {'limit': 250}

    logger.info("Fetching all Shopify customers...")
    while url:
        data, link = _shopify_get(url, params if params else None)
        params = None
        customers = data.get('customers', [])
        if not customers:
            break
        all_customers.extend(customers)
        logger.info(f"  Fetched {len(all_customers)} customers...")
        url = _parse_next_url(link)
        time.sleep(0.5)

    logger.info(f"Total Shopify customers fetched: {len(all_customers)}")
    return all_customers


# ── Store Shopify orders in DB ────────────────────────────────────────────────
def store_shopify_orders(orders):
    from database import Contact, ShopifyOrder, ShopifyOrderItem, init_db
    init_db()

    email_to_contact = {c.email.lower().strip(): c.id
                        for c in Contact.select(Contact.id, Contact.email)}

    new_count = updated_count = 0
    for o in orders:
        order_id = str(o.get('id', ''))
        if not order_id:
            continue

        email = (o.get('email') or o.get('contact_email') or '').lower().strip()
        customer = o.get('customer') or {}
        shipping = o.get('shipping_address') or o.get('billing_address') or {}
        discount_codes = ','.join(d.get('code', '') for d in (o.get('discount_codes') or []))

        try:
            ordered_at = datetime.strptime(o['created_at'][:19], "%Y-%m-%dT%H:%M:%S") if o.get('created_at') else None
        except Exception:
            ordered_at = None

        contact_id = email_to_contact.get(email)

        existing = ShopifyOrder.get_or_none(ShopifyOrder.shopify_order_id == order_id)
        if existing:
            existing.financial_status   = o.get('financial_status') or ''
            existing.fulfillment_status = o.get('fulfillment_status') or ''
            existing.save()
            updated_count += 1
            continue

        db_order = ShopifyOrder.create(
            contact_id         = contact_id,
            shopify_order_id   = order_id,
            order_number       = o.get('order_number') or 0,
            email              = email,
            first_name         = customer.get('first_name') or '',
            last_name          = customer.get('last_name') or '',
            order_total        = float(o.get('total_price') or 0),
            subtotal           = float(o.get('subtotal_price') or 0),
            total_tax          = float(o.get('total_tax') or 0),
            total_discounts    = float(o.get('total_discounts') or 0),
            currency           = o.get('currency') or 'CAD',
            financial_status   = o.get('financial_status') or '',
            fulfillment_status = o.get('fulfillment_status') or '',
            discount_codes     = discount_codes,
            shipping_city      = shipping.get('city') or '',
            shipping_province  = shipping.get('province') or '',
            source_name        = o.get('source_name') or 'web',
            tags               = o.get('tags') or '',
            ordered_at         = ordered_at,
        )

        for item in (o.get('line_items') or []):
            ShopifyOrderItem.create(
                order           = db_order,
                shopify_line_id = str(item.get('id') or ''),
                product_id      = str(item.get('product_id') or ''),
                variant_id      = str(item.get('variant_id') or ''),
                product_title   = item.get('name') or item.get('title') or '',
                variant_title   = item.get('variant_title') or '',
                sku             = item.get('sku') or '',
                quantity        = item.get('quantity') or 1,
                unit_price      = float(item.get('price') or 0),
                total_discount  = float(item.get('total_discount') or 0),
                vendor          = item.get('vendor') or '',
                product_type    = item.get('product_type') or '',
            )
        new_count += 1

    logger.info(f"Shopify orders — new: {new_count}, updated: {updated_count}")
    return new_count, updated_count


# ── Store Shopify customers in DB ─────────────────────────────────────────────
def store_shopify_customers(customers):
    from database import Contact, ShopifyCustomer, init_db
    init_db()

    email_to_contact = {c.email.lower().strip(): c.id
                        for c in Contact.select(Contact.id, Contact.email)}

    new_count = updated_count = 0
    for c in customers:
        shopify_id = str(c.get('id', ''))
        if not shopify_id:
            continue

        email = (c.get('email') or '').lower().strip()
        if not email:
            continue

        address = (c.get('addresses') or [{}])[0] if c.get('addresses') else {}
        contact_id = email_to_contact.get(email)

        try:
            created_at = datetime.strptime(c['created_at'][:19], "%Y-%m-%dT%H:%M:%S") if c.get('created_at') else None
        except Exception:
            created_at = None

        mc = c.get('email_marketing_consent') or {}
        accepts_marketing = mc.get('state') == 'subscribed'

        existing = ShopifyCustomer.get_or_none(ShopifyCustomer.shopify_id == shopify_id)
        if existing:
            existing.orders_count     = c.get('orders_count') or 0
            existing.total_spent      = float(c.get('total_spent') or 0)
            existing.tags             = c.get('tags') or ''
            existing.accepts_marketing = accepts_marketing
            existing.contact_id       = contact_id
            existing.last_synced_at   = datetime.now()
            existing.save()
            updated_count += 1
        else:
            ShopifyCustomer.create(
                contact_id        = contact_id,
                shopify_id        = shopify_id,
                email             = email,
                first_name        = c.get('first_name') or '',
                last_name         = c.get('last_name') or '',
                phone             = c.get('phone') or '',
                orders_count      = c.get('orders_count') or 0,
                total_spent       = float(c.get('total_spent') or 0),
                tags              = c.get('tags') or '',
                city              = address.get('city') or '',
                province          = address.get('province') or '',
                country           = address.get('country') or 'Canada',
                accepts_marketing = accepts_marketing,
                shopify_created_at = created_at,
            )
            new_count += 1

    logger.info(f"Shopify customers — new: {new_count}, updated: {updated_count}")
    return new_count, updated_count


# ── Rebuild CustomerProfile for ALL contacts ──────────────────────────────────
def rebuild_all_profiles():
    """Build/refresh CustomerProfile for every contact — with or without orders."""
    from database import Contact, ShopifyOrder, ShopifyOrderItem, ShopifyCustomer, CustomerProfile, init_db
    init_db()

    all_contacts = list(Contact.select())
    logger.info(f"Rebuilding profiles for {len(all_contacts)} contacts...")

    done = 0
    errors = 0
    for contact in all_contacts:
        try:
            _build_profile(contact)
            done += 1
        except Exception as e:
            logger.error(f"Profile failed for {contact.email}: {e}")
            errors += 1

    logger.info(f"Profiles done: {done}, errors: {errors}")
    return done


def _build_profile(contact):
    from database import ShopifyOrder, ShopifyOrderItem, ShopifyCustomer, CustomerProfile

    email = (contact.email or '').lower().strip()

    # ── Shopify customer record ───────────────────────────────────────────────
    sc = ShopifyCustomer.get_or_none(ShopifyCustomer.email == email)

    # ── Pull paid orders from Shopify ─────────────────────────────────────────
    paid_statuses = {'paid', 'partially_refunded'}
    orders = list(
        ShopifyOrder.select()
        .where(
            ShopifyOrder.email == email,
            ShopifyOrder.financial_status.in_(paid_statuses)
        )
        .order_by(ShopifyOrder.ordered_at)
    )

    # ── Compute purchase stats ────────────────────────────────────────────────
    total_orders = len(orders)
    total_spent  = round(sum(o.order_total for o in orders), 2)
    avg_order_val = round(total_spent / total_orders, 2) if total_orders else 0.0

    first_order_at = orders[0].ordered_at if orders else None
    last_order_at  = orders[-1].ordered_at if orders else None
    now            = datetime.utcnow()
    days_since     = (now - last_order_at).days if last_order_at else 999

    if total_orders >= 2 and first_order_at and last_order_at:
        span = (last_order_at - first_order_at).days
        avg_days_between = round(span / (total_orders - 1), 1)
    else:
        avg_days_between = 0.0

    # ── Product preferences from line items ───────────────────────────────────
    order_ids = [o.id for o in orders]
    items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order_id.in_(order_ids))) if order_ids else []
    total_items = sum(i.quantity for i in items)

    product_counts  = {}
    category_counts = {}
    all_products    = []

    for item in items:
        title = (item.product_title or '').strip()
        if not title:
            continue
        qty = item.quantity or 1
        product_counts[title] = product_counts.get(title, 0) + qty
        cat = infer_category(title)
        category_counts[cat] = category_counts.get(cat, 0) + qty
        all_products.append({
            'title': title,
            'sku': item.sku,
            'qty': qty,
            'price': item.unit_price,
            'category': cat,
        })

    top_products   = sorted(product_counts, key=product_counts.get, reverse=True)[:5]
    top_categories = sorted(category_counts, key=category_counts.get, reverse=True)[:3]

    # ── Price tier ────────────────────────────────────────────────────────────
    if items and total_items > 0:
        avg_item_price = total_spent / total_items
        price_tier = 'budget' if avg_item_price < 35 else ('mid' if avg_item_price < 85 else 'premium')
    else:
        price_tier = 'unknown'

    # ── Discount behaviour ────────────────────────────────────────────────────
    orders_with_discount = sum(1 for o in orders if o.total_discounts > 0)
    discount_sensitivity = round(orders_with_discount / total_orders, 2) if total_orders else 0.0
    has_used_discount    = orders_with_discount > 0

    # ── Geography (prefer Shopify customer record) ────────────────────────────
    if sc and sc.city:
        city, province = sc.city, sc.province
    elif orders:
        city, province = orders[-1].shipping_city, orders[-1].shipping_province
    else:
        city, province = contact.city or '', ""

    # ── Plain-English profile summary for Claude ──────────────────────────────
    name_str       = contact.first_name or (sc.first_name if sc else '') or 'Customer'
    products_str   = ', '.join(top_products[:3]) if top_products else 'none yet'
    categories_str = ', '.join(top_categories) if top_categories else 'none yet'
    location_str   = f"{city}, {province}".strip(', ') or 'Canada'
    discount_str   = (f"uses discounts {int(discount_sensitivity*100)}% of the time"
                      if has_used_discount else "never used a discount code")
    freq_str       = f"every {int(avg_days_between)} days" if avg_days_between > 0 else "once"

    if total_orders == 0:
        profile_summary = (
            f"{name_str} is a subscriber with no purchases yet. "
            f"Subscribed contact, location: {location_str}. "
            f"Potential first-time buyer — needs awareness/welcome messaging."
        )
    else:
        profile_summary = (
            f"{name_str}: {total_orders} order(s), ${total_spent:.2f} CAD total, "
            f"avg ${avg_order_val:.2f}/order. Buys {freq_str}. "
            f"Last purchase {days_since} days ago. "
            f"Products: {products_str}. Categories: {categories_str}. "
            f"Price tier: {price_tier}. {discount_str.capitalize()}. "
            f"Location: {location_str}."
        )

    # ── Upsert ────────────────────────────────────────────────────────────────
    profile = CustomerProfile.get_or_none(CustomerProfile.email == email)
    data = dict(
        contact_id              = contact.id,
        email                   = email,
        total_orders            = total_orders,
        total_spent             = total_spent,
        avg_order_value         = avg_order_val,
        first_order_at          = first_order_at,
        last_order_at           = last_order_at,
        days_since_last_order   = days_since,
        avg_days_between_orders = avg_days_between,
        top_products            = json.dumps(top_products),
        top_categories          = json.dumps(top_categories),
        all_products_bought     = json.dumps(all_products[:30]),
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
    else:
        CustomerProfile.create(**data)


# ── Full Shopify backfill ─────────────────────────────────────────────────────
def run_shopify_backfill():
    logger.info("=" * 60)
    logger.info("SHOPIFY FULL BACKFILL STARTING")
    logger.info("=" * 60)

    # 1. Pull and store all orders
    orders = fetch_all_shopify_orders()
    store_shopify_orders(orders)

    # 2. Pull and store all customers
    customers = fetch_all_shopify_customers()
    store_shopify_customers(customers)

    # 3. Rebuild all profiles
    count = rebuild_all_profiles()
    logger.info(f"BACKFILL COMPLETE — {count} profiles built")
    return count


if __name__ == '__main__':
    run_shopify_backfill()
