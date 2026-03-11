'''
activity_sync.py — Customer Activity Engine
Pulls activity from Shopify (abandoned checkouts, orders) + Omnisend (email events)
Stores everything in CustomerActivity table and enriches CustomerProfile.
'''

import os, sys, json, logging, time, re
from datetime import datetime, timedelta
from dotenv import load_dotenv
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_THIS_DIR, '.env'))

sys.path.insert(0, _THIS_DIR)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STORE   = os.getenv('SHOPIFY_STORE_URL', '').rstrip('/')
TOKEN   = os.getenv('SHOPIFY_ACCESS_TOKEN', '')
API_VER = '2024-01'
BASE    = f"{STORE}/admin/api/{API_VER}"
OMNISEND_API_KEY = os.getenv('OMNISEND_API_KEY', '')
OMNISEND_BASE    = 'https://api.omnisend.com/v3'


# ── HTTP helpers ─────────────────────────────────────────────────────────────────────────────────
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

def _omnisend_get(path, params=None):
    url = OMNISEND_BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'X-API-KEY': OMNISEND_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.error(f"Omnisend HTTP {e.code} {path}: {e.read().decode()[:150]}")
        return {}
    except Exception as e:
        logger.error(f"Omnisend request failed: {e}")
        return {}

def _parse_next_url(link_header):
    if not link_header:
        return None
    for part in link_header.split(','):
        if 'rel="next"' in part:
            m = re.search(r'<([^>]+)>', part)
            if m:
                return m.group(1)
    return None

def _parse_dt(s):
    '''Parse Shopify/Omnisend datetime string to datetime.'''
    if not s:
        return None
    try:
        return datetime.strptime(s[:19].replace('T', ' '), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# ── 1. Sync Shopify Abandoned Checkouts ────────────────────────────────────────────
def sync_shopify_abandoned_checkouts():
    '''Pull all abandoned checkouts from Shopify and create CustomerActivity records.'''
    from database import Contact, CustomerActivity, init_db
    init_db()

    email_to_contact = {c.email.lower().strip(): c.id
                        for c in Contact.select(Contact.id, Contact.email)}

    all_checkouts = []
    url = f"{BASE}/checkouts.json"
    params = {'limit': 250}

    logger.info("Fetching Shopify abandoned checkouts...")
    while url:
        data, link = _shopify_get(url, params if params else None)
        params = None
        checkouts = data.get('checkouts', [])
        if not checkouts:
            break
        all_checkouts.extend(checkouts)
        logger.info(f"  Got {len(all_checkouts)} checkouts so far...")
        url = _parse_next_url(link)
        time.sleep(0.3)

    logger.info(f"Total abandoned checkouts: {len(all_checkouts)}")

    new_count = skipped = 0
    for ch in all_checkouts:
        email = (ch.get('email') or '').lower().strip()
        if not email:
            skipped += 1
            continue

        checkout_id = str(ch.get('id', ''))
        occurred_at = _parse_dt(ch.get('created_at'))
        completed_at = _parse_dt(ch.get('completed_at'))
        contact_id = email_to_contact.get(email)

        # Build product info from line_items
        line_items = ch.get('line_items', [])
        products = [item.get('title', '') for item in line_items[:5]]
        total = float(ch.get('total_price', 0) or 0)

        # If completed_at is set, this checkout was completed (= became an order)
        event_type = 'completed_checkout' if completed_at else 'abandoned_checkout'

        event_data = json.dumps({
            'checkout_id': checkout_id,
            'products': products,
            'total': total,
            'currency': ch.get('currency', 'CAD'),
            'item_count': len(line_items),
            'completed': bool(completed_at),
            'utm_source': _extract_utm(ch.get('landing_site', '') or ''),
        })

        # Check if already exists
        existing = CustomerActivity.get_or_none(
            (CustomerActivity.source_ref == checkout_id) &
            (CustomerActivity.source == 'shopify_checkout')
        )
        if existing:
            skipped += 1
            continue

        CustomerActivity.create(
            contact_id  = contact_id,
            email       = email,
            event_type  = event_type,
            event_data  = event_data,
            source      = 'shopify_checkout',
            source_ref  = checkout_id,
            occurred_at = occurred_at or datetime.now(),
        )
        new_count += 1

    logger.info(f"Shopify checkouts — new: {new_count}, skipped: {skipped}")
    return new_count


def _extract_utm(url_str):
    '''Extract utm_source from URL string.'''
    m = re.search(r'utm_source=([^&]+)', url_str)
    return m.group(1) if m else ''


# ── 2. Sync Shopify Orders as Activity ────────────────────────────────────────────────
def sync_shopify_order_activity():
    '''Create CustomerActivity records for placed orders (from existing ShopifyOrder data).'''
    from database import Contact, CustomerActivity, ShopifyOrder, init_db
    init_db()

    email_to_contact = {c.email.lower().strip(): c.id
                        for c in Contact.select(Contact.id, Contact.email)}

    orders = list(ShopifyOrder.select().order_by(ShopifyOrder.ordered_at.desc()))
    new_count = skipped = 0

    for o in orders:
        email = (o.email or '').lower().strip()
        if not email:
            skipped += 1
            continue

        source_ref = f"order_{o.shopify_order_id}"
        existing = CustomerActivity.get_or_none(
            (CustomerActivity.source_ref == source_ref) &
            (CustomerActivity.source == 'shopify_order')
        )
        if existing:
            skipped += 1
            continue

        contact_id = email_to_contact.get(email)
        event_data = json.dumps({
            'order_number': o.order_number,
            'order_total': o.order_total,
            'currency': o.currency,
            'financial_status': o.financial_status,
            'fulfillment_status': o.fulfillment_status,
            'discount': o.total_discounts,
            'discount_codes': o.discount_codes,
        })

        CustomerActivity.create(
            contact_id  = contact_id,
            email       = email,
            event_type  = 'placed_order',
            event_data  = event_data,
            source      = 'shopify_order',
            source_ref  = source_ref,
            occurred_at = o.ordered_at or datetime.now(),
        )
        new_count += 1

    logger.info(f"Shopify order activity — new: {new_count}, skipped: {skipped}")
    return new_count


# ── 3. Sync Omnisend Campaign Email Events ────────────────────────────────────────────
def sync_omnisend_email_events():
    '''Pull email open/click activity from Omnisend campaigns.'''
    from database import Contact, CustomerActivity, init_db
    init_db()

    email_to_contact = {c.email.lower().strip(): c.id
                        for c in Contact.select(Contact.id, Contact.email)}

    new_count = skipped = 0
    offset = 0

    logger.info("Fetching Omnisend contacts for email activity...")
    while True:
        data = _omnisend_get('/contacts', {'limit': 250, 'offset': offset,
                                            'fields': 'contactID,email,statuses,updatedAt'})
        contacts = data.get('contacts', [])
        if not contacts:
            break

        for c in contacts:
            email = (c.get('email') or '').lower().strip()
            if not email:
                continue

            contact_id = email_to_contact.get(email)
            statuses = c.get('statuses', {})
            updated_at = _parse_dt(c.get('updatedAt'))

            # Omnisend statuses contain email subscription state + opt-in info
            # We use updatedAt as a proxy for "last activity" in Omnisend
            if updated_at and email:
                source_ref = f"omni_contact_{c.get('contactID', '')}"
                existing = CustomerActivity.get_or_none(
                    CustomerActivity.source_ref == source_ref
                )
                if not existing:
                    CustomerActivity.create(
                        contact_id  = contact_id,
                        email       = email,
                        event_type  = 'email_activity',
                        event_data  = json.dumps({
                            'omnisend_id': c.get('contactID'),
                            'status': statuses.get('email', '') if isinstance(statuses, dict) else (statuses[0] if isinstance(statuses, list) and statuses else ''),
                            'tags': c.get('tags', []),
                        }),
                        source      = 'omnisend',
                        source_ref  = source_ref,
                        occurred_at = updated_at,
                    )
                    new_count += 1
                else:
                    skipped += 1

        paging = data.get('paging', {})
        if not paging.get('next'):
            break
        offset += 250
        time.sleep(0.2)

    logger.info(f"Omnisend email activity — new: {new_count}, skipped: {skipped}")
    return new_count


# ── 4. Enrich CustomerProfile with Activity Data (v2) ────────────────────────
def enrich_profiles_with_activity():
    '''Update CustomerProfile fields from CustomerActivity data — full journey intelligence.'''
    from database import Contact, CustomerProfile, CustomerActivity, ShopifyOrder, ShopifyOrderItem, init_db
    init_db()

    logger.info("Enriching customer profiles with activity data (v2)...")

    # Build bought-products set per email
    bought_map = {}
    for order in ShopifyOrder.select():
        email = (order.email or '').lower().strip()
        if email not in bought_map:
            bought_map[email] = set()
        items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order_id == order.id))
        for item in items:
            if item.product_title:
                bought_map[email].add(item.product_title.strip())

    # Get all emails that have activity
    all_emails = set()
    for a in CustomerActivity.select(CustomerActivity.email).distinct():
        if a.email:
            all_emails.add(a.email.lower().strip())

    updated = 0
    for email in all_emails:
        profile = CustomerProfile.get_or_none(CustomerProfile.email == email)
        if not profile:
            continue

        activities = list(
            CustomerActivity.select()
            .where(CustomerActivity.email == email)
            .order_by(CustomerActivity.occurred_at.desc())
        )
        if not activities:
            continue

        # Core counts
        abandon_count = sum(1 for a in activities if a.event_type == 'abandoned_checkout')
        order_count   = sum(1 for a in activities if a.event_type == 'placed_order')
        page_views    = sum(1 for a in activities if a.event_type == 'viewed_page')
        product_views = sum(1 for a in activities if a.event_type == 'viewed_product')
        blog_reads    = sum(1 for a in activities if a.event_type == 'viewed_blog')
        cart_views    = sum(1 for a in activities if a.event_type == 'viewed_cart')
        last_active   = activities[0].occurred_at if activities else None

        # Product view counts + search terms + blog titles
        product_view_counts = {}
        search_terms = []
        blog_titles = []
        last_product = ''

        for a in activities:
            try:
                data = json.loads(a.event_data or '{}')
            except Exception:
                data = {}

            if a.event_type == 'viewed_product':
                title = data.get('product_title', '').strip()
                if title:
                    product_view_counts[title] = product_view_counts.get(title, 0) + 1
                    if not last_product:
                        last_product = title
            elif a.event_type == 'searched':
                q = data.get('query', '').strip()
                if q and q not in search_terms:
                    search_terms.append(q)
            elif a.event_type == 'viewed_blog':
                t = data.get('article_title', '').strip()
                if t and t not in blog_titles:
                    blog_titles.append(t)

        # Intent: products viewed but never bought
        bought = bought_map.get(email, set())
        viewed_not_bought = sorted(
            [(p, c) for p, c in product_view_counts.items() if p not in bought],
            key=lambda x: -x[1]
        )

        # Engagement score 0-100
        engagement = min(100, int(
            page_views * 1 + product_views * 3 + blog_reads * 4 +
            cart_views * 8 + order_count * 15 + len(viewed_not_bought) * 5
        ))

        # Update profile fields
        profile.checkout_abandonment_count = abandon_count
        profile.last_active_at             = last_active
        profile.total_page_views           = page_views
        profile.total_product_views        = product_views
        profile.website_engagement_score   = engagement
        if last_product:
            profile.last_viewed_product = last_product

        # Enrich AI-readable profile_summary with activity data
        activity_str = ''
        if page_views > 0:
            activity_str += f' Visited store {page_views} time(s).'
        if viewed_not_bought:
            vnb = viewed_not_bought[0]
            activity_str += f' Viewed \'{vnb[0]}\' {vnb[1]}x but hasn\'t purchased.'
        if search_terms:
            activity_str += f' Searched for: {', '.join(search_terms[:3])}.'
        if blog_reads > 0:
            activity_str += f' Read {blog_reads} blog post(s).'
        if engagement >= 60:
            activity_str += f' High website engagement: {engagement}/100.'

        if activity_str and 'Visited store' not in (profile.profile_summary or ''):
            profile.profile_summary = ((profile.profile_summary or '').rstrip('.') + ' ' + activity_str.strip()).strip()

        # Compute churn prediction + LTV
        _compute_churn_prediction(profile)

        profile.save()
        updated += 1

    logger.info(f"Profile enrichment v2 complete — updated: {updated}")
    return updated



# ── 4b. Enrich a Single CustomerProfile (called after identification) ─────────

# ── 4c. Churn Risk + Predictive LTV Calculator ───────────────────────────────
def _compute_churn_prediction(profile):
    """
    Compute churn_risk, predicted_next_order_date, predicted_ltv for a profile.
    Uses existing fields: avg_days_between_orders, days_since_last_order,
    last_order_at, avg_order_value, total_orders, first_order_at.
    """
    from datetime import datetime, timedelta

    avg_cycle = profile.avg_days_between_orders or 0
    days_since = profile.days_since_last_order or 999
    total_orders = profile.total_orders or 0
    aov = profile.avg_order_value or 0
    last_order = profile.last_order_at
    first_order = profile.first_order_at

    # ── Churn Risk (0 = safe, 1 = overdue, 2+ = likely churned) ──
    if total_orders <= 1 or avg_cycle <= 0:
        # Single-purchase customer or no purchase cycle — use 90-day default
        if total_orders == 0:
            profile.churn_risk = 0.0  # Never purchased, can't churn
        elif days_since > 180:
            profile.churn_risk = 3.0  # Single buyer, 6+ months ago
        elif days_since > 90:
            profile.churn_risk = 2.0  # Single buyer, 3+ months ago
        else:
            profile.churn_risk = max(0.0, days_since / 90.0)
    else:
        profile.churn_risk = round(days_since / avg_cycle, 2)

    # ── Predicted Next Order Date ──
    if last_order and avg_cycle > 0 and total_orders > 1:
        profile.predicted_next_order_date = last_order + timedelta(days=avg_cycle)
    else:
        profile.predicted_next_order_date = None

    # ── Predicted Lifetime Value (3-year horizon) ──
    if total_orders > 0 and aov > 0 and first_order:
        account_age_days = max((datetime.now() - first_order).days, 1)
        orders_per_year = (total_orders / account_age_days) * 365
        # Project 3 years, with decay factor for churn risk
        decay = max(0.1, 1.0 - (profile.churn_risk * 0.2))  # higher churn = lower projection
        profile.predicted_ltv = round(aov * orders_per_year * 3.0 * decay, 2)
    else:
        profile.predicted_ltv = 0.0

    return profile


def enrich_single_profile(email):
    """Enrich one CustomerProfile immediately — called in background after /api/identify."""
    from database import Contact, CustomerProfile, CustomerActivity, ShopifyOrder, ShopifyOrderItem, init_db
    init_db()

    email = (email or '').lower().strip()
    if not email:
        return 0

    profile = CustomerProfile.get_or_none(CustomerProfile.email == email)
    if not profile:
        return 0

    # Build bought-products set for this email only
    bought = set()
    try:
        for order in ShopifyOrder.select().where(ShopifyOrder.email == email):
            items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order_id == order.id))
            for item in items:
                if item.product_title:
                    bought.add(item.product_title.strip())
    except Exception:
        pass

    activities = list(
        CustomerActivity.select()
        .where(CustomerActivity.email == email)
        .order_by(CustomerActivity.occurred_at.desc())
    )
    if not activities:
        return 0

    # Core counts
    abandon_count = sum(1 for a in activities if a.event_type == 'abandoned_checkout')
    order_count   = sum(1 for a in activities if a.event_type == 'placed_order')
    page_views    = sum(1 for a in activities if a.event_type == 'viewed_page')
    product_views = sum(1 for a in activities if a.event_type == 'viewed_product')
    blog_reads    = sum(1 for a in activities if a.event_type == 'viewed_blog')
    cart_views    = sum(1 for a in activities if a.event_type == 'viewed_cart')
    last_active   = activities[0].occurred_at if activities else None

    # Product view counts + search terms + blog titles
    product_view_counts = {}
    search_terms = []
    blog_titles  = []
    last_product = ''

    for a in activities:
        try:
            data = json.loads(a.event_data or '{}')
        except Exception:
            data = {}
        if a.event_type == 'viewed_product':
            title = data.get('product_title', '').strip()
            if title:
                product_view_counts[title] = product_view_counts.get(title, 0) + 1
                if not last_product:
                    last_product = title
        elif a.event_type == 'searched':
            q = data.get('query', '').strip()
            if q and q not in search_terms:
                search_terms.append(q)
        elif a.event_type == 'viewed_blog':
            t = data.get('article_title', '').strip()
            if t and t not in blog_titles:
                blog_titles.append(t)

    # Intent: products viewed but never bought
    viewed_not_bought = sorted(
        [(p, c) for p, c in product_view_counts.items() if p not in bought],
        key=lambda x: -x[1]
    )

    # Engagement score 0-100
    engagement = min(100, int(
        page_views * 1 + product_views * 3 + blog_reads * 4 +
        cart_views * 8 + order_count * 15 + len(viewed_not_bought) * 5
    ))

    # Update profile fields
    profile.checkout_abandonment_count = abandon_count
    profile.last_active_at             = last_active
    profile.total_page_views           = page_views
    profile.total_product_views        = product_views
    profile.website_engagement_score   = engagement
    if last_product:
        profile.last_viewed_product = last_product

    # Enrich AI-readable profile_summary
    activity_str = ''
    if page_views > 0:
        activity_str += f' Visited store {page_views} time(s).'
    if viewed_not_bought:
        vnb = viewed_not_bought[0]
        activity_str += f" Viewed '{vnb[0]}' {vnb[1]}x but hasn't purchased."
    if search_terms:
        activity_str += f' Searched for: {", ".join(search_terms[:3])}.'
    if blog_reads > 0:
        activity_str += f' Read {blog_reads} blog post(s).'
    if engagement >= 60:
        activity_str += f' High website engagement: {engagement}/100.'

    if activity_str and 'Visited store' not in (profile.profile_summary or ''):
        profile.profile_summary = ((profile.profile_summary or '').rstrip('.') + ' ' + activity_str.strip()).strip()

    # Compute churn prediction + LTV
    _compute_churn_prediction(profile)

    profile.save()
    logger.info(f"Single profile enriched: {email} (score={engagement}, churn={profile.churn_risk}, events={len(activities)})")
    return 1


# ── 5. Full Sync ──────────────────────────────────────────────────────────────────
def run_full_activity_sync():
    logger.info("=" * 60)
    logger.info("ACTIVITY SYNC STARTING")
    logger.info("=" * 60)

    # Sync all sources
    n1 = sync_shopify_abandoned_checkouts()
    n2 = sync_shopify_order_activity()
    n3 = sync_omnisend_email_events()

    # Enrich profiles
    n4 = enrich_profiles_with_activity()

    logger.info(f"SYNC COMPLETE — checkouts:{n1} orders:{n2} omnisend:{n3} profiles:{n4}")
    return {'checkouts': n1, 'orders': n2, 'omnisend': n3, 'profiles_enriched': n4}


if __name__ == '__main__':
    result = run_full_activity_sync()
    print(json.dumps(result, indent=2))
