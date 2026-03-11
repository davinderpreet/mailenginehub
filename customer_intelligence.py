"""
Customer Intelligence Service — Phase 2A
Computes and persists a complete intelligence profile for every contact:
  - Lifecycle stage (8 states)
  - Customer type classification
  - Intent score (0-100)
  - Reorder likelihood (0-100)
  - Churn risk normalized (0-100)
  - Category affinity (scored dict)
  - Next likely purchase category
  - Preferred send window (hour + day of week)
  - Channel preference
  - Confidence scores for each field
  - Plain-English intelligence summary

Run nightly at 3:30 AM after activity sync, or on-demand per contact.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Reuse the same category keywords as data_enrichment.py / shopify_enrichment.py
CATEGORY_KEYWORDS = {
    "Bluetooth Headsets": ["headset", "earpiece", "th11", "th-11", "g10", "g7", "g3",
                           "geforce", "trucker headset", "bluetooth head"],
    "Dash Cams":          ["dash cam", "dashcam", "a20", "car camera", "parking mode", "dash-cam"],
    "Phone Accessories":  ["phone case", "screen protector", "charging", "cable", "usb-c", "usb c"],
    "Speakers":           ["speaker", "soundbar"],
    "Smart Home":         ["smart", "wifi plug", "bulb"],
    "Other Electronics":  [],
}

DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

LIFECYCLE_LABELS = {
    "prospect":      "Prospect",
    "new_customer":  "New Customer",
    "active_buyer":  "Active Buyer",
    "loyal":         "Loyal Customer",
    "vip":           "VIP",
    "at_risk":       "At Risk",
    "churned":       "Churned",
    "reactivated":   "Reactivated",
    "unknown":       "Unknown",
}

CUSTOMER_TYPE_LABELS = {
    "browser":         "Browser",
    "one_time":        "One-Time Buyer",
    "repeat":          "Repeat Buyer",
    "loyal":           "Loyal Buyer",
    "vip":             "VIP",
    "discount_seeker": "Discount Seeker",
    "dormant":         "Dormant",
    "unknown":         "Unknown",
}


def _infer_category(product_title):
    """Match product title to a category using keyword matching."""
    t = product_title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if cat == "Other Electronics":
            continue
        if any(kw in t for kw in kws):
            return cat
    return "Other Electronics"


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 1: Lifecycle Stage
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_lifecycle_stage(contact, profile, score, orders_list):
    """
    8 states: prospect, new_customer, active_buyer, loyal, vip, at_risk, churned, reactivated
    Returns (stage, confidence)
    """
    now = datetime.now()
    total_orders = profile.total_orders if profile else 0
    total_spent = profile.total_spent if profile else 0.0
    days_since = profile.days_since_last_order if profile else 999
    first_order_at = profile.first_order_at if profile else None
    last_order_at = profile.last_order_at if profile else None
    account_age = (now - contact.created_at).days if contact.created_at else 0

    # Confidence based on data availability
    conf = 0
    if total_orders > 0:
        conf += 40
    if score and score.engagement_score > 0:
        conf += 20
    if profile and profile.website_engagement_score > 0:
        conf += 20
    if account_age > 30:
        conf += 20
    conf = min(100, conf)

    # ── Reactivated check (must be first — overrides at_risk/churned) ──
    if total_orders >= 2 and orders_list and len(orders_list) >= 2:
        # Check for a gap > 120 days between consecutive orders + recent purchase
        sorted_orders = sorted(orders_list, key=lambda o: o.ordered_at if o.ordered_at else datetime.min)
        had_gap = False
        for i in range(1, len(sorted_orders)):
            prev_date = sorted_orders[i - 1].ordered_at
            curr_date = sorted_orders[i].ordered_at
            if prev_date and curr_date:
                gap = (curr_date - prev_date).days
                if gap > 120:
                    had_gap = True
                    break
        if had_gap and last_order_at and (now - last_order_at).days <= 30:
            return "reactivated", conf

    # ── VIP ──
    if total_orders >= 5 and total_spent >= 300:
        return "vip", conf

    # ── Churned ──
    if total_orders >= 1 and days_since > 180:
        return "churned", conf
    if profile and profile.churn_risk >= 2.0 and total_orders >= 1:
        return "churned", conf

    # ── At Risk ──
    if total_orders >= 2 and 90 < days_since <= 180:
        return "at_risk", conf

    # ── Loyal ──
    if total_orders >= 3 and days_since <= 120 and total_spent >= 100:
        return "loyal", conf

    # ── Active Buyer ──
    if total_orders >= 2 and days_since <= 90:
        return "active_buyer", conf

    # ── New Customer ──
    if total_orders >= 1 and first_order_at and (now - first_order_at).days <= 30:
        return "new_customer", conf
    if account_age <= 7:
        return "new_customer", conf

    # ── Prospect (no orders, has been around) ──
    if total_orders == 0:
        return "prospect", conf

    # Fallback — has orders but doesn't fit other categories
    if total_orders == 1 and days_since <= 90:
        return "active_buyer", conf

    return "at_risk", conf


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 2: Customer Type
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_customer_type(contact, profile, score):
    """
    Behavioral classification: vip, loyal, discount_seeker, repeat, one_time, browser, dormant
    Returns customer_type string
    """
    total_orders = profile.total_orders if profile else 0
    total_spent = profile.total_spent if profile else 0.0
    discount_sens = profile.discount_sensitivity if profile else 0.0
    web_score = profile.website_engagement_score if profile else 0
    engagement = score.engagement_score if score else 0
    last_open = contact.last_open_at
    now = datetime.now()

    # Priority order: VIP > LOYAL > DISCOUNT_SEEKER > REPEAT > ONE_TIME > BROWSER > DORMANT

    # VIP
    if total_orders >= 5 and total_spent >= 300:
        return "vip"

    # Loyal
    if total_orders >= 5:
        return "loyal"

    # Discount Seeker (must have orders to measure sensitivity)
    if total_orders >= 2 and discount_sens >= 0.6:
        return "discount_seeker"

    # Repeat
    if 2 <= total_orders <= 4:
        return "repeat"

    # One-time
    if total_orders == 1:
        return "one_time"

    # Browser (no orders but has web activity)
    if total_orders == 0 and web_score > 0:
        return "browser"

    # Dormant (no orders, no recent activity)
    if total_orders == 0:
        days_since_open = (now - last_open).days if last_open else 999
        if days_since_open > 90 and web_score == 0:
            return "dormant"
        return "browser"  # has some activity

    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 3: Intent Score (0-100)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_intent_score(contact, profile, activity_14d, opens_14d_count):
    """
    Purchase intent based on recent signals (last 14 days).
    Returns (intent_score, confidence)
    """
    web_score = profile.website_engagement_score if profile else 0
    days_since = profile.days_since_last_order if profile else 999
    total_orders = profile.total_orders if profile else 0

    # Count product views in last 14 days
    product_views_14d = sum(1 for a in activity_14d if a.event_type == "viewed_product")

    # Check for cart activity in last 14 days
    has_cart = any(a.event_type in ("started_checkout", "abandoned_checkout") for a in activity_14d)

    # Normalize inputs
    web_norm = min(100, web_score)                                       # already 0-100
    view_norm = min(100, product_views_14d * 10) if product_views_14d <= 5 else min(100, 50 + product_views_14d * 5)
    cart_norm = 100 if has_cart else 0
    open_norm = min(100, opens_14d_count * 20) if opens_14d_count <= 3 else min(100, 70 + opens_14d_count * 6)

    # Recency boost
    if total_orders == 0:
        recency_norm = 30  # unknown
    elif days_since <= 30:
        recency_norm = 100
    elif days_since <= 90:
        recency_norm = 50
    else:
        recency_norm = 10

    # Weighted sum
    intent = int(
        web_norm * 0.30 +
        view_norm * 0.25 +
        cart_norm * 0.20 +
        open_norm * 0.15 +
        recency_norm * 0.10
    )
    intent = max(0, min(100, intent))

    # Confidence based on data points used
    data_points = sum([
        1 if web_score > 0 else 0,
        1 if product_views_14d > 0 else 0,
        1 if has_cart else 0,
        1 if opens_14d_count > 0 else 0,
        1 if total_orders > 0 else 0,
    ])
    confidence = min(100, data_points * 20)

    return intent, confidence


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 4: Reorder Likelihood (0-100)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_reorder_likelihood(profile, score):
    """
    How likely the contact will order again within their typical cycle.
    Returns (likelihood, confidence)
    """
    total_orders = profile.total_orders if profile else 0
    days_since = profile.days_since_last_order if profile else 999
    avg_cycle = profile.avg_days_between_orders if profile else 0
    engagement = score.engagement_score if score else 0
    discount_sens = profile.discount_sensitivity if profile else 0.0

    if total_orders == 0:
        return 0, 0

    if total_orders == 1:
        base = 30
        if days_since < 60:
            base += 20
        if engagement > 50:
            base += 15
        return min(100, base), 30

    # 2+ orders — use cycle ratio
    if avg_cycle > 0:
        cycle_ratio = days_since / avg_cycle
    else:
        cycle_ratio = 1.0  # can't compute, assume approaching

    if cycle_ratio < 0.5:
        base = 90   # well within cycle
    elif cycle_ratio < 1.0:
        base = 70   # approaching
    elif cycle_ratio < 1.5:
        base = 40   # overdue
    else:
        base = 15   # unlikely

    # Adjustments
    if engagement > 60:
        base += 10
    if discount_sens > 0.5:
        base += 5  # discount could trigger reorder

    base = max(0, min(100, base))

    # Confidence
    if total_orders >= 10:
        conf = 95
    elif total_orders >= 4:
        conf = 80
    elif total_orders >= 2:
        conf = 60
    else:
        conf = 30

    return base, conf


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 5: Category Affinity (scored dict)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_category_affinity(profile, orders_list):
    """
    Compute weighted affinity scores per product category.
    Returns (affinity_dict, next_purchase_category, confidence)
    """
    all_bought = json.loads(profile.all_products_bought or "[]") if profile else []
    total_items = profile.total_items_bought if profile else 0

    if not all_bought:
        return {}, "", 0

    now = datetime.now()

    # Build per-category data: count of items + most recent purchase date
    cat_data = {}
    for item in all_bought:
        cat = item.get("category", "Other Electronics")
        qty = item.get("qty", 1)
        if cat not in cat_data:
            cat_data[cat] = {"count": 0, "last_date": None}
        cat_data[cat]["count"] += qty

    # Find last purchase date per category from order items
    for order in (orders_list or []):
        if not order.ordered_at:
            continue
        try:
            items = list(order.items) if hasattr(order, 'items') else []
        except Exception:
            items = []
        for item in items:
            cat = _infer_category(item.product_title or "")
            if cat in cat_data:
                if cat_data[cat]["last_date"] is None or order.ordered_at > cat_data[cat]["last_date"]:
                    cat_data[cat]["last_date"] = order.ordered_at

    # Compute affinity scores
    affinity = {}
    best_due_cat = ""
    best_due_score = 0
    best_overall_cat = ""
    best_overall_score = 0

    for cat, data in cat_data.items():
        if cat == "Other Electronics" and data["count"] <= 1:
            continue  # skip low-signal fallback category

        items_in_cat = data["count"]
        last_date = data["last_date"]

        # Recency factor
        if last_date:
            days_ago = (now - last_date).days
            if days_ago < 90:
                recency_factor = 1.0
            elif days_ago < 180:
                recency_factor = 0.7
            else:
                recency_factor = 0.4
        else:
            recency_factor = 0.5  # unknown recency

        # Frequency factor (5+ items in category = max)
        frequency_factor = min(1.0, items_in_cat / 5.0)

        score = int(frequency_factor * 60 + recency_factor * 40)
        affinity[cat] = score

        # Track best overall
        if score > best_overall_score:
            best_overall_score = score
            best_overall_cat = cat

        # Track best "due" category (not recently bought)
        if recency_factor < 1.0 and score > best_due_score:
            best_due_score = score
            best_due_cat = cat

    # Next purchase category: prefer a category they're "due" for
    next_cat = best_due_cat if best_due_cat else best_overall_cat

    # Confidence based on total items bought
    confidence = min(100, total_items * 15)

    return affinity, next_cat, confidence


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 6: Preferred Send Window
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_send_window(campaign_opens, flow_opens):
    """
    Analyze open timestamps to find preferred send hour and day of week.
    Returns (hour, dow, confidence)  — hour -1 means unknown
    """
    open_times = []
    for ce in campaign_opens:
        if ce.opened_at:
            open_times.append(ce.opened_at)
    for fe in flow_opens:
        if fe.opened_at:
            open_times.append(fe.opened_at)

    n = len(open_times)
    if n < 3:
        return -1, -1, 0

    # Extract hours and days
    hours = [t.hour for t in open_times]
    dows = [t.weekday() for t in open_times]

    # Mode with ±1 hour smoothing for hour
    hour_counts = Counter()
    for h in hours:
        hour_counts[h] += 2        # center weight
        hour_counts[(h - 1) % 24] += 1  # adjacent hours
        hour_counts[(h + 1) % 24] += 1
    preferred_hour = hour_counts.most_common(1)[0][0]

    # Simple mode for day of week
    dow_counts = Counter(dows)
    preferred_dow = dow_counts.most_common(1)[0][0]

    # Confidence
    if n >= 20:
        conf = 95
    elif n >= 11:
        conf = 80
    elif n >= 6:
        conf = 60
    elif n >= 3:
        conf = 30
    else:
        conf = 0

    return preferred_hour, preferred_dow, conf


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 7: Channel Preference
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_channel_preference(contact, score):
    """
    Simple heuristic: email vs sms vs both.
    Returns (channel, confidence)
    """
    has_sms = bool(contact.sms_consent and contact.phone)
    engagement = score.engagement_score if score else 0

    if has_sms:
        if engagement < 30:
            return "sms", 70 if engagement > 0 else 30
        else:
            return "both", 70
    else:
        return "email", 70 if engagement > 0 else 30


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 8: Churn Risk Normalized (0-100)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_churn_normalized(profile):
    """
    Normalize existing churn_risk float (0-3+) to 0-100 scale.
    Returns (churn_score, confidence)
    """
    total_orders = profile.total_orders if profile else 0
    churn_risk = profile.churn_risk if profile else 0.0

    churn_score = min(100, int(churn_risk * 33.3))

    # Confidence
    if total_orders == 0:
        conf = 0
    elif total_orders == 1:
        conf = 30
    elif total_orders <= 3:
        conf = 60
    elif total_orders <= 9:
        conf = 80
    else:
        conf = 95

    return churn_score, conf


# ═══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM 9: Intelligence Summary
# ═══════════════════════════════════════════════════════════════════════════════

def _build_intelligence_summary(lifecycle, ctype, intent, reorder, churn_score,
                                 next_cat, send_hour, send_dow, discount_sens,
                                 contact_name, profile=None, affinity=None,
                                 orders_count=0, total_spent=0.0, avg_order=0.0,
                                 days_since=999, avg_cycle=0, top_products=None,
                                 channel=None, price_tier=None,
                                 web_activity=None):
    """Build a detailed intelligence summary for the AI engine and the UI.

    Includes purchase history, product preferences, spending behavior,
    timing patterns, website browsing activity, and actionable context —
    so the AI knows exactly what product to offer and when.
    """
    import json as _json

    name = contact_name or "Contact"
    parts = []

    # ── 1. Identity + Stage ──
    stage_label = LIFECYCLE_LABELS.get(lifecycle, lifecycle)
    type_label = CUSTOMER_TYPE_LABELS.get(ctype, ctype)
    parts.append(f"{name} is a {stage_label} ({type_label}).")

    # ── 2. Purchase History ──
    if orders_count > 0:
        parts.append(
            f"Purchase history: {orders_count} order{'s' if orders_count != 1 else ''}, "
            f"${total_spent:.2f} total spent, ${avg_order:.2f} avg order value."
        )
    else:
        parts.append("No purchases yet — subscriber only.")

    # ── 3. Products Bought ──
    prods = []
    if top_products:
        try:
            prods = _json.loads(top_products) if isinstance(top_products, str) else top_products
        except Exception:
            prods = []

    if prods:
        # Deduplicate and count
        prod_counts = {}
        for p in prods:
            title = p if isinstance(p, str) else (p.get("title", "") if isinstance(p, dict) else str(p))
            if title:
                prod_counts[title] = prod_counts.get(title, 0) + 1

        # Show top 5 products
        sorted_prods = sorted(prod_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        prod_strs = []
        for title, cnt in sorted_prods:
            # Shorten very long product names
            short = title[:80] + "..." if len(title) > 80 else title
            if cnt > 1:
                prod_strs.append(f"{short} (x{cnt})")
            else:
                prod_strs.append(short)
        parts.append("Products bought: " + "; ".join(prod_strs) + ".")

    # ── 4. Category Affinity ──
    if affinity and isinstance(affinity, dict) and len(affinity) > 0:
        sorted_cats = sorted(affinity.items(), key=lambda x: x[1], reverse=True)
        cat_strs = [f"{cat} ({score}/100)" for cat, score in sorted_cats]
        parts.append("Category affinity: " + ", ".join(cat_strs) + ".")

    # ── 5. Price Tier ──
    if price_tier and price_tier != "unknown":
        tier_labels = {"budget": "Budget buyer (<$50 avg)", "mid": "Mid-range buyer ($50-100 avg)", "premium": "Premium buyer (>$100 avg)"}
        parts.append(f"Price tier: {tier_labels.get(price_tier, price_tier)}.")

    # ── 6. Timing ──
    if orders_count > 0:
        if days_since <= 30:
            timing = f"Last ordered {days_since} days ago (recent)."
        elif days_since <= 90:
            timing = f"Last ordered {days_since} days ago."
        elif days_since <= 180:
            timing = f"Last ordered {days_since} days ago (going cold)."
        else:
            timing = f"Last ordered {days_since} days ago (inactive)."
        parts.append(timing)

        if avg_cycle > 0 and orders_count >= 2:
            if days_since > avg_cycle * 1.5:
                parts.append(f"Typical reorder cycle: {int(avg_cycle)} days — significantly overdue.")
            elif days_since > avg_cycle:
                parts.append(f"Typical reorder cycle: {int(avg_cycle)} days — overdue.")
            else:
                remaining = int(avg_cycle - days_since)
                parts.append(f"Typical reorder cycle: {int(avg_cycle)} days — {remaining} days until due.")

    # ── 7. Website Browsing Activity ──
    if web_activity:
        wa = web_activity
        web_parts = []

        # Last visit
        if wa.get("last_visit"):
            from datetime import datetime as _dt
            try:
                last_dt = wa["last_visit"] if isinstance(wa["last_visit"], _dt) else _dt.fromisoformat(str(wa["last_visit"]))
                days_ago_web = (datetime.now() - last_dt).days
                if days_ago_web == 0:
                    web_parts.append("Last website visit: today")
                elif days_ago_web == 1:
                    web_parts.append("Last website visit: yesterday")
                else:
                    web_parts.append(f"Last website visit: {days_ago_web} days ago")
            except Exception:
                pass

        # Products browsed
        if wa.get("products_browsed"):
            prods_browsed = wa["products_browsed"][:5]  # top 5
            prod_names = []
            for pb in prods_browsed:
                pname = pb.get("name", "")
                if pname:
                    # Clean up slug-style names
                    pname = pname.replace("-", " ").title()
                    if len(pname) > 60:
                        pname = pname[:60] + "..."
                    views = pb.get("views", 1)
                    if views > 1:
                        prod_names.append(f"{pname} ({views}x)")
                    else:
                        prod_names.append(pname)
            if prod_names:
                web_parts.append("Products browsed: " + "; ".join(prod_names))

        # Abandoned checkouts
        if wa.get("abandoned_carts"):
            carts = wa["abandoned_carts"][:3]
            cart_strs = []
            for cart in carts:
                items = cart.get("products", [])
                total = cart.get("total", 0)
                date = cart.get("date", "")
                if items:
                    cart_strs.append(f"{', '.join(items[:3])} (${total}, {date})")
            if cart_strs:
                web_parts.append("ABANDONED CART: " + "; ".join(cart_strs))

        # Web engagement score
        if wa.get("engagement_score", 0) > 0:
            web_parts.append(f"Website engagement: {wa['engagement_score']}/100")

        if web_parts:
            parts.append(" ".join(web_parts) + ".")

    # ── 8. Scores ──
    parts.append(f"Intent: {intent}/100. Reorder likelihood: {reorder}/100.")

    if churn_score > 70:
        parts.append(f"Churn risk: HIGH ({churn_score}/100).")
    elif churn_score > 50:
        parts.append(f"Churn risk: ELEVATED ({churn_score}/100).")
    elif churn_score > 30:
        parts.append(f"Churn risk: moderate ({churn_score}/100).")

    if next_cat:
        parts.append(f"Next likely purchase category: {next_cat}.")

    # ── 8. Engagement Preferences ──
    if discount_sens > 0.5:
        parts.append(f"Discount-responsive ({int(discount_sens * 100)}% of orders used codes) — promotional offers likely effective.")
    elif discount_sens > 0 and orders_count > 0:
        parts.append(f"Low discount usage ({int(discount_sens * 100)}%) — responds to value, not just price.")

    if send_hour >= 0:
        dow_name = DOW_NAMES[send_dow] if 0 <= send_dow <= 6 else "Unknown"
        parts.append(f"Best send time: {send_hour}:00 on {dow_name}.")

    if channel and channel != "email":
        channel_labels = {"sms": "Prefers SMS", "both": "Responds to both Email + SMS"}
        parts.append(channel_labels.get(channel, f"Channel: {channel}") + ".")

    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN: compute_intelligence (single contact)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_intelligence(contact_id):
    """
    Compute full intelligence profile for a single contact.
    Reads from Contact, ContactScore, CustomerProfile, CustomerActivity,
    CampaignEmail, FlowEmail, ShopifyOrder, ShopifyOrderItem.
    Writes results to CustomerProfile.
    Returns dict of computed values.
    """
    from database import (Contact, ContactScore, CustomerProfile,
                          CustomerActivity, CampaignEmail, FlowEmail,
                          ShopifyOrder, ShopifyOrderItem, init_db)
    init_db()
    now = datetime.now()
    cutoff_14d = now - timedelta(days=14)

    # ── Load data ──
    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return {"error": f"Contact {contact_id} not found"}

    profile = CustomerProfile.get_or_none(CustomerProfile.contact_id == contact_id)
    if not profile:
        return {"error": f"No profile for contact {contact_id}"}

    score = ContactScore.get_or_none(ContactScore.contact_id == contact_id)

    # Orders with items
    orders_list = list(
        ShopifyOrder.select()
        .where(ShopifyOrder.email == contact.email.lower())
        .order_by(ShopifyOrder.ordered_at.desc())
    )
    for o in orders_list:
        try:
            o.items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order_id == o.id))
        except Exception:
            o.items = []

    # Recent activity (14 days)
    activity_14d = list(
        CustomerActivity.select()
        .where(CustomerActivity.email == contact.email.lower(),
               CustomerActivity.occurred_at >= cutoff_14d)
    )

    # Opens in last 14 days
    opens_14d = CampaignEmail.select().where(
        CampaignEmail.contact_id == contact_id,
        CampaignEmail.opened == True,
        CampaignEmail.opened_at >= cutoff_14d
    ).count()
    opens_14d += FlowEmail.select().where(
        FlowEmail.contact_id == contact_id,
        FlowEmail.opened == True,
        FlowEmail.opened_at >= cutoff_14d
    ).count()

    # All campaign opens (for send window analysis)
    all_campaign_opens = list(
        CampaignEmail.select(CampaignEmail.opened_at)
        .where(CampaignEmail.contact_id == contact_id, CampaignEmail.opened == True)
    )
    all_flow_opens = list(
        FlowEmail.select(FlowEmail.opened_at)
        .where(FlowEmail.contact_id == contact_id, FlowEmail.opened == True)
    )

    # ── Run algorithms ──
    lifecycle, conf_lifecycle = _compute_lifecycle_stage(contact, profile, score, orders_list)
    ctype = _compute_customer_type(contact, profile, score)
    intent, conf_intent = _compute_intent_score(contact, profile, activity_14d, opens_14d)
    reorder, conf_reorder = _compute_reorder_likelihood(profile, score)
    affinity, next_cat, conf_category = _compute_category_affinity(profile, orders_list)
    send_hour, send_dow, conf_window = _compute_send_window(all_campaign_opens, all_flow_opens)
    channel, conf_channel = _compute_channel_preference(contact, score)
    churn_score, conf_churn = _compute_churn_normalized(profile)

    # Discount confidence
    discount_sens = profile.discount_sensitivity if profile else 0.0
    conf_discount = min(100, (profile.total_orders if profile else 0) * 20)

    # ── Gather website browsing activity ──
    web_activity = {}
    try:
        # Last visit date
        last_visit_rec = (
            CustomerActivity.select(CustomerActivity.occurred_at)
            .where(
                CustomerActivity.email == contact.email.lower(),
                CustomerActivity.event_type.in_(["viewed_product", "viewed_page", "viewed_cart"])
            )
            .order_by(CustomerActivity.occurred_at.desc())
            .first()
        )
        if last_visit_rec:
            web_activity["last_visit"] = last_visit_rec.occurred_at

        # Products browsed (deduplicated, with view counts)
        product_views = list(
            CustomerActivity.select(CustomerActivity.event_data, CustomerActivity.occurred_at)
            .where(
                CustomerActivity.email == contact.email.lower(),
                CustomerActivity.event_type == "viewed_product"
            )
            .order_by(CustomerActivity.occurred_at.desc())
            .limit(50)
        )
        if product_views:
            prod_counts = {}
            for pv in product_views:
                try:
                    d = json.loads(pv.event_data)
                    url = d.get("url", "")
                    name = url.split("/products/")[-1] if "/products/" in url else ""
                    if name:
                        if name not in prod_counts:
                            prod_counts[name] = {"name": name, "views": 0}
                        prod_counts[name]["views"] += 1
                except Exception:
                    pass
            web_activity["products_browsed"] = sorted(
                prod_counts.values(), key=lambda x: x["views"], reverse=True
            )

        # Abandoned checkouts
        abandoned = list(
            CustomerActivity.select(CustomerActivity.event_data, CustomerActivity.occurred_at)
            .where(
                CustomerActivity.email == contact.email.lower(),
                CustomerActivity.event_type == "abandoned_checkout"
            )
            .order_by(CustomerActivity.occurred_at.desc())
            .limit(3)
        )
        if abandoned:
            carts = []
            for ab in abandoned:
                try:
                    d = json.loads(ab.event_data)
                    carts.append({
                        "products": d.get("products", []),
                        "total": d.get("total", 0),
                        "date": ab.occurred_at.strftime("%b %d") if ab.occurred_at else "",
                    })
                except Exception:
                    pass
            if carts:
                web_activity["abandoned_carts"] = carts

        # Web engagement score
        if profile and profile.website_engagement_score:
            web_activity["engagement_score"] = profile.website_engagement_score

    except Exception as e:
        logger.error(f"[Intelligence] Web activity gather error for {contact_id}: {e}")

    # Intelligence summary — rich version with full purchase + browsing context
    _top_prods = profile.all_products_bought if profile else "[]"
    summary = _build_intelligence_summary(
        lifecycle, ctype, intent, reorder, churn_score,
        next_cat, send_hour, send_dow, discount_sens,
        contact.first_name,
        profile=profile,
        affinity=affinity,
        orders_count=profile.total_orders if profile else 0,
        total_spent=profile.total_spent if profile else 0.0,
        avg_order=profile.avg_order_value if profile else 0.0,
        days_since=profile.days_since_last_order if profile else 999,
        avg_cycle=profile.avg_days_between_orders if profile else 0,
        top_products=_top_prods,
        channel=channel,
        price_tier=profile.price_tier if profile else None,
        web_activity=web_activity,
    )

    # ── Persist to CustomerProfile ──
    profile.lifecycle_stage = lifecycle
    profile.customer_type = ctype
    profile.intent_score = intent
    profile.reorder_likelihood = reorder
    profile.category_affinity_json = json.dumps(affinity)
    profile.next_purchase_category = next_cat
    profile.preferred_send_hour = send_hour
    profile.preferred_send_dow = send_dow
    profile.channel_preference = channel
    profile.churn_risk_score = churn_score
    profile.confidence_lifecycle = conf_lifecycle
    profile.confidence_intent = conf_intent
    profile.confidence_reorder = conf_reorder
    profile.confidence_category = conf_category
    profile.confidence_send_window = conf_window
    profile.confidence_channel = conf_channel
    profile.confidence_discount = conf_discount
    profile.confidence_churn = conf_churn
    profile.intelligence_summary = summary
    profile.last_intelligence_at = now
    profile.save()

    result = {
        "contact_id": contact_id,
        "lifecycle_stage": lifecycle,
        "customer_type": ctype,
        "intent_score": intent,
        "reorder_likelihood": reorder,
        "churn_risk_score": churn_score,
        "category_affinity": affinity,
        "next_purchase_category": next_cat,
        "preferred_send_hour": send_hour,
        "preferred_send_dow": send_dow,
        "channel_preference": channel,
        "intelligence_summary": summary,
        "confidence": {
            "lifecycle": conf_lifecycle,
            "intent": conf_intent,
            "reorder": conf_reorder,
            "category": conf_category,
            "send_window": conf_window,
            "channel": conf_channel,
            "discount": conf_discount,
            "churn": conf_churn,
        },
    }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  BATCH: compute_all_intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def compute_all_intelligence():
    """
    Compute intelligence for every contact that has a CustomerProfile.
    Called nightly at 3:30 AM.
    Returns count of contacts scored.
    """
    from database import Contact, CustomerProfile, init_db
    init_db()

    # Get all contact IDs that have profiles
    profile_contacts = list(
        CustomerProfile.select(CustomerProfile.contact_id)
        .tuples()
    )
    contact_ids = [row[0] for row in profile_contacts]

    scored = 0
    errors = 0
    for cid in contact_ids:
        try:
            result = compute_intelligence(cid)
            if "error" not in result:
                scored += 1
            else:
                errors += 1
        except Exception as e:
            logger.error(f"Intelligence error for contact {cid}: {e}")
            errors += 1

    logger.info(f"[Intelligence] Scored {scored} contacts ({errors} errors)")
    return scored


# ── CLI entry point for manual runs ──
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, '/var/www/mailengine')

    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        # Score a single contact
        cid = int(sys.argv[1])
        result = compute_intelligence(cid)
        print(json.dumps(result, indent=2, default=str))
    else:
        # Batch all
        count = compute_all_intelligence()
        print(f"Done: {count} contacts scored")
