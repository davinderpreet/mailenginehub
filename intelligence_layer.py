"""
intelligence_layer.py — Unified query API for all business intelligence.

This is the single import point for all pillars (Templates, Flows, AM, Campaigns).
It is a read-only query layer over pre-computed data. No AI calls. No nightly computation.

Contract version: 1
All functions return dicts with a stable top-level structure. Consumers should
check SCHEMA_VERSION and handle missing keys gracefully.

Usage:
    from intelligence_layer import get_contact_intelligence, get_next_products
    intel = get_contact_intelligence(contact_id)
    products = get_next_products(contact_id)
"""

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


# ═══════════════════════════════════════════════════════════════
# Product Intelligence
# ═══════════════════════════════════════════════════════════════

def get_next_products(contact_id):
    """What should we recommend to this contact next?

    Returns: {
        "schema_version": 1,
        "replacements": [...],
        "upgrades": [...],
        "cross_sells": [...],
        "accessories": [...],
        "reorders": [...],
        "top_pick": {...} or None,
    }
    """
    try:
        from product_intelligence import get_customer_product_context
        result = get_customer_product_context(contact_id)
        result["schema_version"] = SCHEMA_VERSION
        return result
    except Exception as e:
        logger.warning("[IntelligenceLayer] get_next_products failed: %s", e)
        return {
            "schema_version": SCHEMA_VERSION,
            "replacements": [], "upgrades": [], "cross_sells": [],
            "accessories": [], "reorders": [], "top_pick": None,
        }


def get_product_commercial(product_id_or_type):
    """Look up commercial data for a product.

    Args:
        product_id_or_type: Shopify product_id OR product_type string

    Returns: {
        "schema_version": 1,
        "found": bool,
        "product_title": str,
        "product_type": str,
        "current_price": float,
        "margin_pct": float or None,
        "margin_source": str,
        "stock_pressure": str,
        "promotion_eligible": bool,
        "profitability_score": int,
        "max_discount_pct": float,
    }
    """
    from database import ProductCommercial

    result = {
        "schema_version": SCHEMA_VERSION,
        "found": False,
        "product_title": "",
        "product_type": "",
        "current_price": 0.0,
        "margin_pct": None,
        "margin_source": "unknown",
        "stock_pressure": "unknown",
        "promotion_eligible": False,
        "profitability_score": 0,
        "max_discount_pct": 0.0,
    }

    try:
        # Try by product_id first, then by title match
        pc = (ProductCommercial.get_or_none(ProductCommercial.product_id == str(product_id_or_type)) or
              ProductCommercial.get_or_none(ProductCommercial.product_title == str(product_id_or_type)))

        if pc:
            margin = pc.margin_pct or 0.0
            result.update({
                "found": True,
                "product_title": pc.product_title,
                "product_type": pc.product_type,
                "current_price": pc.current_price,
                "margin_pct": pc.margin_pct,
                "margin_source": pc.margin_source,
                "stock_pressure": pc.stock_pressure,
                "promotion_eligible": pc.promotion_eligible,
                "profitability_score": pc.profitability_score,
                # Max discount: leave 15% margin floor
                "max_discount_pct": max(0, (margin - 0.15) * 100) if margin else 0.0,
            })
    except Exception as e:
        logger.debug("[IntelligenceLayer] get_product_commercial error: %s", e)

    return result


def get_promotable_products(category=None, min_margin=0.20):
    """Products eligible for promotion.

    Returns: list of {product_id, product_title, product_type, margin_pct, stock_pressure, max_discount_pct}
    """
    from database import ProductCommercial

    try:
        query = ProductCommercial.select().where(
            ProductCommercial.promotion_eligible == True,
            ProductCommercial.stock_pressure != "out_of_stock",
        )
        if category:
            query = query.where(ProductCommercial.product_type == category)
        if min_margin > 0:
            query = query.where(ProductCommercial.margin_pct >= min_margin)

        return [{
            "product_id": p.product_id,
            "product_title": p.product_title,
            "product_type": p.product_type,
            "margin_pct": p.margin_pct,
            "stock_pressure": p.stock_pressure,
            "max_discount_pct": max(0, ((p.margin_pct or 0) - 0.15) * 100),
        } for p in query.order_by(ProductCommercial.profitability_score.desc()).limit(50)]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# Customer Intelligence
# ═══════════════════════════════════════════════════════════════

def get_contact_intelligence(contact_id):
    """THE unified contact profile. Single call to get everything.

    Returns: {
        "schema_version": 1,
        "contact": {name, email, tags, source, subscribed, ...},
        "classification": {lifecycle_stage, customer_type, rfm_segment, ...},
        "scores": {intent, reorder_likelihood, churn_risk, engagement, sunset_risk, ...},
        "purchase": {total_orders, total_spent, aov, days_since_last, avg_cycle_days, ...},
        "products": {top_categories, category_affinities, ...},
        "timing": {preferred_hour, preferred_dow, optimal_gap_hours, channel_preference, ...},
        "engagement": {last_open_days_ago, emails_7d, emails_30d, website_score, ...},
        "learning": {top_templates, top_actions, rejection_summary, ...},
        "next_products": <output of get_next_products>,
    }
    """
    from database import Contact, CustomerProfile, ContactScore

    now = datetime.now()

    result = {
        "schema_version": SCHEMA_VERSION,
        "contact": {},
        "classification": {},
        "scores": {},
        "purchase": {},
        "products": {},
        "timing": {},
        "engagement": {},
        "learning": {},
        "next_products": {},
    }

    # ── Contact basics ──
    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return result

    result["contact"] = {
        "id": contact.id,
        "email": contact.email,
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "tags": contact.tags or "",
        "source": contact.source or "unknown",
        "subscribed": contact.subscribed,
        "total_orders": contact.total_orders,
        "total_spent": float(contact.total_spent or 0),
        "created_at": contact.created_at,
    }

    # ── CustomerProfile ──
    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact_id)
    if profile:
        result["classification"] = {
            "lifecycle_stage": profile.lifecycle_stage or "unknown",
            "customer_type": profile.customer_type or "unknown",
        }

        result["scores"] = {
            "intent": profile.intent_score or 0,
            "reorder_likelihood": profile.reorder_likelihood or 0,
            "churn_risk": profile.churn_risk_score or 0,
            "discount_sensitivity": profile.discount_sensitivity or 0.0,
        }

        result["purchase"] = {
            "total_orders": profile.total_orders or 0,
            "total_spent": float(profile.total_spent or 0),
            "avg_order_value": float(profile.avg_order_value or 0),
            "days_since_last_order": profile.days_since_last_order or 999,
            "avg_days_between_orders": float(profile.avg_days_between_orders or 0),
            "first_order_at": profile.first_order_at,
            "last_order_at": profile.last_order_at,
            "price_tier": profile.price_tier or "unknown",
            "has_used_discount": profile.has_used_discount,
        }

        # Products — parsed from profile (cached summary, not truth source)
        top_products = _safe_json(profile.top_products, [])
        top_categories = _safe_json(profile.top_categories, [])
        category_affinities = _safe_json(profile.category_affinity_json, {})

        result["products"] = {
            "top_products": top_products[:10],
            "top_categories": top_categories[:5],
            "category_affinities": category_affinities,
            "next_purchase_category": profile.next_purchase_category or "",
            "last_viewed_product": profile.last_viewed_product or "",
        }

        result["timing"] = {
            "preferred_hour": profile.preferred_send_hour if profile.preferred_send_hour >= 0 else None,
            "preferred_dow": profile.preferred_send_dow if profile.preferred_send_dow >= 0 else None,
            "channel_preference": profile.channel_preference or "email",
        }

        result["engagement"]["website_score"] = profile.website_engagement_score or 0
        result["engagement"]["total_product_views"] = profile.total_product_views or 0
        result["engagement"]["last_active_at"] = profile.last_active_at

        # Confidence scores
        result["classification"]["confidence"] = {
            "lifecycle": profile.confidence_lifecycle or 0,
            "intent": profile.confidence_intent or 0,
            "reorder": profile.confidence_reorder or 0,
            "category": profile.confidence_category or 0,
            "churn": profile.confidence_churn or 0,
            "send_window": profile.confidence_send_window or 0,
            "discount": profile.confidence_discount or 0,
        }

    # ── ContactScore (RFM) ──
    score = ContactScore.get_or_none(ContactScore.contact == contact_id)
    if score:
        result["classification"]["rfm_segment"] = score.rfm_segment or "new"
        result["scores"]["engagement"] = score.engagement_score or 0
        result["scores"]["sunset_risk"] = score.sunset_score or 0
        result["timing"]["optimal_gap_hours"] = score.optimal_gap_hours or 48.0
        result["engagement"].update({
            "recency_days": score.recency_days or 999,
            "frequency_rate": score.frequency_rate or 0.0,
        })

    # ── Contact-level engagement ──
    result["engagement"].update({
        "emails_7d": getattr(contact, "emails_received_7d", 0) or 0,
        "emails_30d": getattr(contact, "emails_received_30d", 0) or 0,
        "last_open_at": getattr(contact, "last_open_at", None),
        "last_click_at": getattr(contact, "last_click_at", None),
    })
    if result["engagement"].get("last_open_at"):
        result["engagement"]["last_open_days_ago"] = (now - result["engagement"]["last_open_at"]).days
    else:
        result["engagement"]["last_open_days_ago"] = None

    # ── Learning data ──
    result["learning"] = _get_learning_data(contact_id, result["classification"].get("rfm_segment", "new"))

    # ── Next products ──
    result["next_products"] = get_next_products(contact_id)

    return result


def _get_learning_data(contact_id, segment):
    """Internal: gather learning data (templates, actions, rejection patterns)."""
    data = {
        "top_templates": [],
        "top_actions": [],
        "rejection_summary": "",
    }

    # Top templates for segment
    try:
        from strategy_optimizer import get_template_recommendations
        data["top_templates"] = get_template_recommendations(segment)[:3]
    except Exception:
        pass

    # Top actions for segment
    try:
        from database import ActionPerformance
        rows = (ActionPerformance.select()
                .where(ActionPerformance.segment == segment,
                       ActionPerformance.sample_size >= 20)
                .order_by(ActionPerformance.conversion_rate.desc())
                .limit(3))
        data["top_actions"] = [{
            "action_type": r.action_type,
            "conversion_rate": round(r.conversion_rate * 100, 1),
            "open_rate": round(r.open_rate * 100, 1),
            "sample_size": r.sample_size,
        } for r in rows]
    except Exception:
        pass

    # Rejection patterns from AM
    try:
        from database import ContactStrategy
        cs = ContactStrategy.get_or_none(ContactStrategy.contact == contact_id)
        if cs and cs.rejection_reasons:
            from learning_context import analyze_rejection_patterns
            data["rejection_summary"] = analyze_rejection_patterns(cs.rejection_reasons)
    except Exception:
        pass

    return data


# ═══════════════════════════════════════════════════════════════
# Timing Gate
# ═══════════════════════════════════════════════════════════════

def should_contact_now(contact_id):
    """Shared timing gate — should we email this contact right now?

    Checks:
    1. Subscription status
    2. Suppression status
    3. Sunset score (disengaged contacts)
    4. Optimal gap since last email
    5. Weekly email cap

    Returns: {
        "schema_version": 1,
        "can_send": bool,
        "reason": str,
        "next_available_at": datetime or None,
        "gap_hours": float,
        "gap_hours_required": float,
    }
    """
    from database import Contact, ContactScore, SuppressionEntry, AutoEmail, FlowEmail, CampaignEmail

    now = datetime.now()
    result = {
        "schema_version": SCHEMA_VERSION,
        "can_send": False,
        "reason": "",
        "next_available_at": None,
        "gap_hours": 0,
        "gap_hours_required": 48,
    }

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        result["reason"] = "contact_not_found"
        return result

    # Check subscription
    if not contact.subscribed:
        result["reason"] = "unsubscribed"
        return result

    # Check suppression
    try:
        suppressed = SuppressionEntry.get_or_none(SuppressionEntry.email == contact.email.lower())
        if suppressed:
            result["reason"] = f"suppressed:{suppressed.reason}"
            return result
    except Exception:
        pass

    # Check sunset score
    try:
        score = ContactScore.get_or_none(ContactScore.contact == contact_id)
        if score and score.sunset_score >= 85:
            result["reason"] = "sunset_suppressed"
            return result
        if score:
            result["gap_hours_required"] = score.optimal_gap_hours or 48.0
    except Exception:
        pass

    # Find last email sent across all channels
    last_sent_at = None
    try:
        # AutoEmail (AM)
        last_auto = (AutoEmail.select(AutoEmail.sent_at)
                     .where(AutoEmail.contact == contact_id, AutoEmail.status == "sent")
                     .order_by(AutoEmail.sent_at.desc())
                     .first())
        if last_auto and last_auto.sent_at:
            last_sent_at = last_auto.sent_at

        # FlowEmail
        last_flow = (FlowEmail.select(FlowEmail.sent_at)
                     .where(FlowEmail.contact == contact_id, FlowEmail.status == "sent")
                     .order_by(FlowEmail.sent_at.desc())
                     .first())
        if last_flow and last_flow.sent_at:
            if last_sent_at is None or last_flow.sent_at > last_sent_at:
                last_sent_at = last_flow.sent_at

        # CampaignEmail
        last_campaign = (CampaignEmail.select(CampaignEmail.created_at)
                         .where(CampaignEmail.contact_id == contact_id, CampaignEmail.status == "sent")
                         .order_by(CampaignEmail.created_at.desc())
                         .first())
        if last_campaign and last_campaign.created_at:
            if last_sent_at is None or last_campaign.created_at > last_sent_at:
                last_sent_at = last_campaign.created_at
    except Exception:
        pass

    # Compute gap
    if last_sent_at:
        # Handle timezone-aware vs naive
        if hasattr(last_sent_at, 'tzinfo') and last_sent_at.tzinfo:
            last_sent_at = last_sent_at.replace(tzinfo=None)
        gap_hours = (now - last_sent_at).total_seconds() / 3600
        result["gap_hours"] = round(gap_hours, 1)

        if gap_hours < result["gap_hours_required"]:
            hours_remaining = result["gap_hours_required"] - gap_hours
            result["reason"] = f"too_soon (sent {gap_hours:.0f}h ago, need {result['gap_hours_required']:.0f}h gap)"
            result["next_available_at"] = last_sent_at + timedelta(hours=result["gap_hours_required"])
            return result

    # Check weekly cap (soft limit: 4 emails per 7 days)
    emails_7d = getattr(contact, "emails_received_7d", 0) or 0
    if emails_7d >= 4:
        result["reason"] = f"weekly_cap ({emails_7d} emails in last 7 days)"
        return result

    result["can_send"] = True
    result["reason"] = "ok"
    return result


# ═══════════════════════════════════════════════════════════════
# Business Rules
# ═══════════════════════════════════════════════════════════════

def get_discount_policy(contact_id, purpose, candidate_products=None):
    """Should we offer a discount? If so, what kind?

    Considers:
    - Contact's discount sensitivity and history
    - RFM segment (VIPs shouldn't get cheapened)
    - Purpose (winback needs stronger incentive than education)
    - Product margins for candidate products (if provided)

    Args:
        contact_id: Contact ID
        purpose: e.g. "cart_abandonment", "winback", "cross_sell", "welcome"
        candidate_products: optional list of product_ids or titles to check margin

    Returns: {
        "schema_version": 1,
        "offer_discount": bool,
        "discount_type": "percentage" | "free_shipping" | None,
        "discount_value": str or None,  # "5", "10", etc.
        "max_discount_pct": float,
        "reason": str,
        "product_margins": [{"product", "margin_pct", "max_discount"}] (if candidates provided),
    }
    """
    from database import Contact, CustomerProfile, ContactScore

    result = {
        "schema_version": SCHEMA_VERSION,
        "offer_discount": False,
        "discount_type": None,
        "discount_value": None,
        "max_discount_pct": 0.0,
        "reason": "",
        "product_margins": [],
    }

    # ── Gather contact data ──
    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact_id)
    score = ContactScore.get_or_none(ContactScore.contact == contact_id)

    segment = score.rfm_segment if score else "new"
    discount_sensitivity = profile.discount_sensitivity if profile else 0.0
    has_used_discount = profile.has_used_discount if profile else False
    lifecycle = profile.lifecycle_stage if profile else "unknown"

    # ── Check product margins if candidates provided ──
    min_margin = 1.0  # start high
    if candidate_products:
        for prod_key in candidate_products:
            commercial = get_product_commercial(prod_key)
            margin = commercial.get("margin_pct") or 0.40  # fallback
            result["product_margins"].append({
                "product": prod_key,
                "margin_pct": margin,
                "max_discount": max(0, (margin - 0.15) * 100),
            })
            if margin < min_margin:
                min_margin = margin
    else:
        min_margin = 0.40  # default assumption

    result["max_discount_pct"] = max(0, (min_margin - 0.15) * 100)

    # ── Decision rules ──

    # VIP/loyal customers who buy full price — don't cheapen the relationship
    if segment in ("champion", "loyal") and not has_used_discount and lifecycle in ("loyal", "vip"):
        result["reason"] = "Full-price buyer in loyal/VIP segment — avoid discounting"
        return result

    # Margin too thin for any discount
    if min_margin < 0.20:
        result["reason"] = f"Margin too thin ({min_margin:.0%}) for discount on these products"
        return result

    # Purpose-based defaults
    purpose_defaults = {
        "cart_abandonment":   ("percentage", "5"),
        "checkout_recovery":  ("percentage", "5"),
        "browse_abandonment": ("free_shipping", "100"),
        "winback":            ("percentage", "10"),
        "welcome":            ("percentage", "5"),
        "loyalty_reward":     ("percentage", "10"),
        "cross_sell":         (None, None),   # no default discount for cross-sell
        "upsell":             (None, None),
        "education":          (None, None),
        "reorder_reminder":   (None, None),
        "post_purchase":      (None, None),
    }

    default_type, default_value = purpose_defaults.get(purpose, (None, None))

    # For purposes with defaults, decide based on sensitivity
    if default_type:
        # Always offer for high-intent recovery
        if purpose in ("cart_abandonment", "checkout_recovery"):
            result["offer_discount"] = True
            result["discount_type"] = default_type
            result["discount_value"] = default_value
            result["reason"] = f"Recovery email — {purpose} standard offer"
            return result

        # Winback: offer if churned or at-risk
        if purpose == "winback" and lifecycle in ("churned", "at_risk"):
            result["offer_discount"] = True
            result["discount_type"] = default_type
            result["discount_value"] = default_value
            result["reason"] = f"Winback for {lifecycle} contact"
            return result

        # Welcome: always offer
        if purpose == "welcome":
            result["offer_discount"] = True
            result["discount_type"] = default_type
            result["discount_value"] = default_value
            result["reason"] = "Welcome offer for new subscriber"
            return result

        # Loyalty: offer if truly loyal
        if purpose == "loyalty_reward" and lifecycle in ("loyal", "vip"):
            result["offer_discount"] = True
            result["discount_type"] = default_type
            result["discount_value"] = default_value
            result["reason"] = "Loyalty reward for VIP/loyal customer"
            return result

    # For purposes without defaults, offer only if discount-sensitive
    if discount_sensitivity >= 0.4 and has_used_discount:
        # Cap at margin-safe value
        safe_value = str(min(10, int(result["max_discount_pct"])))
        if int(safe_value) >= 5:
            result["offer_discount"] = True
            result["discount_type"] = "percentage"
            result["discount_value"] = safe_value
            result["reason"] = f"Discount-sensitive customer ({discount_sensitivity:.0%} of orders used codes)"
            return result

    result["reason"] = f"No discount warranted for {purpose} to {lifecycle}/{segment} contact"
    return result


def get_seasonal_context():
    """Current seasonal context for messaging.

    Returns: {
        "schema_version": 1,
        "key": str,
        "label": str,
        "themes": [str],
        "promo_appropriate": bool,
        "month": int,
    }
    """
    from shared_constants import get_current_season
    result = get_current_season()
    result["schema_version"] = SCHEMA_VERSION
    return result


def get_competitive_positioning(product_type=None):
    """Structured competitive data.

    Returns: {
        "schema_version": 1,
        "competitors": {name: {positioning, key_diff, approach}},
    }
    """
    from shared_constants import COMPETITIVE_POSITIONING

    result = {"schema_version": SCHEMA_VERSION, "competitors": COMPETITIVE_POSITIONING}
    return result


def get_business_brief():
    """Structured version of the AM business brief.

    Returns: {
        "schema_version": 1,
        "categories": {name: {margin_estimate, reorder_cycle_days}},
        "upgrade_paths": {category: [(tier, low, high)]},
        "value_props": [str],
        "seasonal": {...},
        "competitive": {...},
    }
    """
    from shared_constants import (CATEGORY_KEYWORDS, MARGIN_ESTIMATES,
                                  REORDER_CYCLES, UPGRADE_PATHS,
                                  VALUE_PROPS, COMPETITIVE_POSITIONING)

    categories = {}
    for cat in CATEGORY_KEYWORDS:
        if cat == "Other Electronics":
            continue
        cycle_info = REORDER_CYCLES.get(cat)
        categories[cat] = {
            "margin_estimate": MARGIN_ESTIMATES.get(cat, 0.40),
            "reorder_cycle_days": cycle_info[0] if cycle_info else None,
            "consumable": cycle_info[3] if cycle_info else False,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "categories": categories,
        "upgrade_paths": {
            cat: [(t[0], t[1], t[2]) for t in tiers]
            for cat, tiers in UPGRADE_PATHS.items()
        },
        "value_props": VALUE_PROPS,
        "seasonal": get_seasonal_context(),
        "competitive": COMPETITIVE_POSITIONING,
    }


# ═══════════════════════════════════════════════════════════════
# Prompt Formatting
# ═══════════════════════════════════════════════════════════════

def format_intelligence_for_prompt(contact_id, segment=None):
    """Format the structured intelligence as text for Claude prompts.
    Replaces learning_context.build_learning_prompt_section().

    Returns: str (ready to inject into prompt), or "" if no meaningful data
    """
    intel = get_contact_intelligence(contact_id)
    if segment:
        intel["classification"]["rfm_segment"] = segment

    seg = intel["classification"].get("rfm_segment", "new")
    lines = ["[INTELLIGENCE BRIEF]"]

    # Product recommendations
    np = intel.get("next_products", {})
    if np.get("top_pick"):
        tp = np["top_pick"]
        lines.append(f"Top recommendation: {tp['action']} — {tp['product_key']} ({tp['reason']})")

    if np.get("replacements"):
        lines.append("Replacements due:")
        for r in np["replacements"][:3]:
            lines.append(f"  - {r['product_key']}: due in {r['due_in_days']} days ({r['reason']})")

    if np.get("upgrades"):
        for u in np["upgrades"][:2]:
            status = "READY" if u["ready"] else f"in {u['days_owned']} days"
            lines.append(f"  Upgrade: {u['from_product']} → {u.get('to_product') or u['to_tier']} ({status})")

    # Learning data
    learning = intel.get("learning", {})
    if learning.get("top_actions"):
        lines.append(f"Best actions for {seg} customers:")
        for a in learning["top_actions"]:
            lines.append(f"  - {a['action_type']}: {a['conversion_rate']}% conversion, {a['open_rate']}% opens (n={a['sample_size']})")

    if learning.get("top_templates"):
        lines.append(f"Top templates for {seg} customers:")
        for t in learning["top_templates"]:
            lines.append(f"  - {t.get('name', 'template')}: {t.get('open_rate', 0) * 100:.1f}% opens, ${t.get('revenue_per_send', 0):.2f} rev/send")

    if learning.get("rejection_summary"):
        lines.append(f"[PAST FEEDBACK] {learning['rejection_summary']}")

    # Scores
    scores = intel.get("scores", {})
    lines.append(f"Engagement: {scores.get('engagement', 0)}/100 | Segment: {seg} | Sunset risk: {scores.get('sunset_risk', 0)}/100")

    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Diagnostics
# ═══════════════════════════════════════════════════════════════

def diagnose_contact(contact_id):
    """Pretty-print full intelligence output for a contact.
    For debugging and trust-building before wiring into consumers.
    """
    import json as _json

    intel = get_contact_intelligence(contact_id)

    lines = []
    lines.append("=" * 60)
    lines.append(f"INTELLIGENCE REPORT: Contact #{contact_id}")
    lines.append("=" * 60)

    c = intel.get("contact", {})
    lines.append(f"\nCONTACT: {c.get('first_name', '')} {c.get('last_name', '')} <{c.get('email', '')}>")
    lines.append(f"  Source: {c.get('source', '?')} | Subscribed: {c.get('subscribed', '?')}")

    cl = intel.get("classification", {})
    lines.append(f"\nCLASSIFICATION:")
    lines.append(f"  Lifecycle: {cl.get('lifecycle_stage', '?')} | Type: {cl.get('customer_type', '?')} | RFM: {cl.get('rfm_segment', '?')}")

    s = intel.get("scores", {})
    lines.append(f"\nSCORES:")
    lines.append(f"  Intent: {s.get('intent', 0)} | Reorder: {s.get('reorder_likelihood', 0)} | Churn: {s.get('churn_risk', 0)}")
    lines.append(f"  Engagement: {s.get('engagement', 0)} | Sunset: {s.get('sunset_risk', 0)} | Discount Sens: {s.get('discount_sensitivity', 0):.1%}")

    p = intel.get("purchase", {})
    lines.append(f"\nPURCHASE:")
    lines.append(f"  Orders: {p.get('total_orders', 0)} | Spent: ${p.get('total_spent', 0):,.2f} | AOV: ${p.get('avg_order_value', 0):,.2f}")
    lines.append(f"  Days since last: {p.get('days_since_last_order', '?')} | Avg cycle: {p.get('avg_days_between_orders', '?')} days")

    pr = intel.get("products", {})
    lines.append(f"\nPRODUCTS:")
    lines.append(f"  Top categories: {', '.join(pr.get('top_categories', []))}")
    lines.append(f"  Next category: {pr.get('next_purchase_category', '?')}")

    np = intel.get("next_products", {})
    if np.get("top_pick"):
        tp = np["top_pick"]
        lines.append(f"\nTOP RECOMMENDATION: {tp['action'].upper()} — {tp['product_key']}")
        lines.append(f"  Reason: {tp['reason']}")

    if np.get("replacements"):
        lines.append(f"\n  Replacements:")
        for r in np["replacements"]:
            lines.append(f"    - {r['product_key']}: due in {r['due_in_days']} days")

    if np.get("upgrades"):
        lines.append(f"\n  Upgrades:")
        for u in np["upgrades"]:
            lines.append(f"    - {u['from_product']} → {u.get('to_product') or u['to_tier']} ({'READY' if u['ready'] else 'not yet'})")

    if np.get("cross_sells"):
        lines.append(f"\n  Cross-sells:")
        for cs in np["cross_sells"]:
            lines.append(f"    - {cs['target_key']} (strength: {cs['strength']})")

    t = intel.get("timing", {})
    lines.append(f"\nTIMING:")
    lines.append(f"  Preferred hour: {t.get('preferred_hour', '?')} | DOW: {t.get('preferred_dow', '?')}")
    lines.append(f"  Gap: {t.get('optimal_gap_hours', 48)}h | Channel: {t.get('channel_preference', '?')}")

    timing_gate = should_contact_now(contact_id)
    lines.append(f"\n  Can send now: {timing_gate['can_send']} — {timing_gate['reason']}")

    lines.append(f"\nSCHEMA VERSION: {SCHEMA_VERSION}")
    lines.append("=" * 60)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _safe_json(raw, default):
    """Safely parse a JSON string, return default on failure."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default
