"""
Campaign Planner & Opportunity Engine -- Phase 2C
Aggregates individual contact decisions (MessageDecision) into campaign-level
opportunities. Groups by action_type, scores quality, runs preflight simulation,
and produces ranked campaign suggestions.

Nightly at 4:15 AM after next-best-message (4:00 AM), or on-demand.
Run: python3 campaign_planner.py
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict, Counter

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

CAMPAIGN_TYPE_NAMES = {
    "reorder_reminder": "Reorder Campaign",
    "cross_sell":       "Cross-Sell Campaign",
    "upsell":           "Upsell Campaign",
    "new_product":      "New Product Launch",
    "winback":          "Winback Campaign",
    "education":        "Content / Nurture",
    "loyalty_reward":   "VIP Rewards",
    "discount_offer":   "Discount Push",
}

CAMPAIGN_TYPE_ICONS = {
    "reorder_reminder": "fa-redo",
    "cross_sell":       "fa-random",
    "upsell":           "fa-arrow-up",
    "new_product":      "fa-star",
    "winback":          "fa-heart-broken",
    "education":        "fa-book-open",
    "loyalty_reward":   "fa-crown",
    "discount_offer":   "fa-tag",
}

CAMPAIGN_TYPE_COLORS = {
    "reorder_reminder": "var(--cyan)",
    "cross_sell":       "var(--purple2)",
    "upsell":           "var(--purple)",
    "new_product":      "var(--green)",
    "winback":          "var(--red)",
    "education":        "var(--amber)",
    "loyalty_reward":   "var(--pink)",
    "discount_offer":   "var(--green)",
}

ACTION_TO_PURPOSE = {
    "reorder_reminder": "upsell",
    "cross_sell":       "upsell",
    "upsell":           "upsell",
    "new_product":      "high_intent",
    "winback":          "winback",
    "education":        "re_engagement",
    "loyalty_reward":   "loyalty_reward",
    "discount_offer":   "cart_abandonment",
}

CONVERSION_RATES = {
    "reorder_reminder": 0.05,
    "cross_sell":       0.03,
    "upsell":           0.025,
    "new_product":      0.02,
    "winback":          0.015,
    "education":        0.005,
    "loyalty_reward":   0.04,
    "discount_offer":   0.04,
}

# Warmup phase limits (same as campaign_preflight.py)
WARMUP_LIMITS = {
    1: 50, 2: 150, 3: 350, 4: 750, 5: 1500, 6: 3000, 7: 7000, 8: 99999,
}

SUBJECT_LINE_ANGLES = {
    "reorder_reminder": [
        "Time to restock? Your {product} may be running low",
        "Still loving your {product}? Get more before they sell out",
        "{first_name}, ready for a refill?",
    ],
    "cross_sell": [
        "Pair these with your {product} for the perfect setup",
        "People who bought {product} also love these",
        "{first_name}, complete your collection",
    ],
    "upsell": [
        "Ready to upgrade? Premium {category} starting at...",
        "{first_name}, step up your {category} game",
        "Your next {category} upgrade is waiting",
    ],
    "new_product": [
        "Just arrived: New {category} you'll love",
        "Be the first to try our latest {category}",
        "{first_name}, check out what's new",
    ],
    "winback": [
        "{first_name}, we miss you! Here's what you've been missing",
        "It's been a while -- come see what's new",
        "Your favourites are waiting, {first_name}",
    ],
    "education": [
        "The ultimate guide to getting more from your {product}",
        "{first_name}, tips & tricks for your {category}",
        "Did you know? Hidden features of your {product}",
    ],
    "loyalty_reward": [
        "{first_name}, you've earned something special",
        "VIP exclusive: A thank-you just for you",
        "Your loyalty has unlocked a reward",
    ],
    "discount_offer": [
        "{first_name}, a special offer just for you",
        "Limited time: Save on {category} this week",
        "Your exclusive discount is waiting",
    ],
}


# ═══════════════════════════════════════════════════════════════
# Preflight Simulation
# ═══════════════════════════════════════════════════════════════

def _simulate_preflight(segment_size, avg_fatigue, complaint_risk_pct):
    """Lightweight preflight check for a campaign opportunity.
    Returns (status: str, warnings: list[str])
    """
    from database import WarmupConfig
    warnings = []
    status = "PASS"

    # Check warmup headroom
    try:
        wc = WarmupConfig.get_by_id(1)
        if wc.is_active:
            daily_limit = WARMUP_LIMITS.get(wc.current_phase, 50)
            headroom = daily_limit - (wc.emails_sent_today or 0)
            if headroom <= 0:
                status = "BLOCK"
                warnings.append("Daily warmup limit reached -- no sends remaining today")
            elif headroom < segment_size:
                if status != "BLOCK":
                    status = "WARN"
                warnings.append(f"Can only send {headroom} of {segment_size} due to warmup limit")
    except Exception:
        pass

    # Fatigue
    if avg_fatigue >= 50:
        if status != "BLOCK":
            status = "WARN"
        warnings.append(f"High average fatigue ({avg_fatigue:.0f}/100) -- risk of complaints")

    # Complaint risk
    if complaint_risk_pct >= 10:
        status = "BLOCK"
        warnings.append(f"Complaint risk {complaint_risk_pct:.1f}% -- above 10% threshold")
    elif complaint_risk_pct >= 5:
        if status != "BLOCK":
            status = "WARN"
        warnings.append(f"Complaint risk {complaint_risk_pct:.1f}% -- elevated")

    # Small segment
    if segment_size < 10:
        warnings.append(f"Small segment ({segment_size}) -- limited statistical value")

    return status, warnings


# ═══════════════════════════════════════════════════════════════
# Quality Score
# ═══════════════════════════════════════════════════════════════

def _score_opportunity(segment_size, avg_engagement, predicted_revenue,
                       complaint_risk_pct, avg_fatigue, warmup_headroom):
    """Score a campaign opportunity 0-100."""
    score = 30
    if segment_size >= 50:
        score += 20
    elif segment_size >= 20:
        score += 10
    if avg_engagement >= 40:
        score += 15
    elif avg_engagement >= 20:
        score += 7
    if predicted_revenue >= 500:
        score += 15
    elif predicted_revenue >= 100:
        score += 7
    if complaint_risk_pct < 5:
        score += 10
    if avg_fatigue >= 50:
        score -= 20
    if complaint_risk_pct >= 10:
        score -= 15
    if warmup_headroom < segment_size:
        score -= 10
    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════
# Urgency
# ═══════════════════════════════════════════════════════════════

def _compute_urgency(action_type, avg_churn, avg_days_since, avg_reorder_days,
                     at_risk_pct):
    """Determine urgency level for a campaign opportunity."""
    if action_type == "winback" and avg_churn >= 70:
        return "critical"
    if action_type == "reorder_reminder" and avg_reorder_days > 0 and avg_days_since >= avg_reorder_days:
        return "high"
    if action_type == "discount_offer" and at_risk_pct > 50:
        return "high"
    if action_type in ("education", "new_product") and avg_churn < 30:
        return "low"
    return "medium"


# ═══════════════════════════════════════════════════════════════
# Campaign Brief
# ═══════════════════════════════════════════════════════════════

def _generate_campaign_brief(action_type, segment_size, avg_aov, top_products,
                              top_categories, send_window, predicted_revenue,
                              predicted_profit, complaint_risk_pct, urgency,
                              safe_send_volume, preflight_warnings):
    """Generate a text campaign brief for human review."""
    type_name = CAMPAIGN_TYPE_NAMES.get(action_type, action_type)
    purpose = ACTION_TO_PURPOSE.get(action_type, "")
    top_cat = top_categories[0] if top_categories else "Electronics"
    top_prod = top_products[0] if top_products else "your purchase"

    # Subject line angles
    angles = SUBJECT_LINE_ANGLES.get(action_type, [
        "We have something for you",
        "Check this out, {first_name}",
        "Don't miss this",
    ])
    angles_formatted = []
    for a in angles:
        angles_formatted.append(a.replace("{product}", top_prod)
                                 .replace("{category}", top_cat)
                                 .replace("{first_name}", "{{first_name}}"))

    # Talking points
    talk = _get_talking_points(action_type, top_products, top_categories, avg_aov)

    # Preflight notes
    pf_section = "None" if not preflight_warnings else "\n".join(f"  - {w}" for w in preflight_warnings)

    window_str = f"{send_window}:00" if send_window >= 0 else "Unknown"

    brief = f"""CAMPAIGN BRIEF: {type_name}
{'=' * 60}
Urgency:              {urgency.upper()}
Email Purpose:        {purpose}
Segment Size:         {segment_size:,} contacts
Safe Send Volume:     {safe_send_volume:,}
Predicted Revenue:    ${predicted_revenue:,.0f}
Predicted Profit:     ${predicted_profit:,.0f}
Complaint Risk:       {complaint_risk_pct:.1f}%
Recommended Send:     {window_str}

TARGET SEGMENT
{'-' * 40}
{_describe_segment(action_type, top_categories, top_products, segment_size)}

SUBJECT LINE ANGLES
{'-' * 40}
  1. {angles_formatted[0]}
  2. {angles_formatted[1]}
  3. {angles_formatted[2]}

KEY TALKING POINTS
{'-' * 40}
{talk}

PREFLIGHT WARNINGS
{'-' * 40}
{pf_section}

DO's
{'-' * 40}
  - Personalize with customer name and past purchases
  - Include clear CTA button
  - Test subject line with A/B if segment > 100
  - Schedule during recommended send window
  - Use product images from their purchase history

DON'Ts
{'-' * 40}
  - Don't include out-of-stock products
  - Don't offer discounts to VIPs who buy full price
  - Don't exceed warmup daily limit ({safe_send_volume:,} max today)
  - Don't send to contacts with fatigue > 50
  - Don't use aggressive language with at-risk customers"""

    return brief.strip()


def _describe_segment(action_type, top_categories, top_products, segment_size):
    """Generate a human-readable segment description."""
    cat = top_categories[0] if top_categories else "electronics"
    descs = {
        "reorder_reminder": f"{segment_size:,} customers who are due to repurchase, primarily in {cat}. "
                           f"High reorder likelihood, previously bought products like {', '.join(top_products[:3]) if top_products else 'various items'}.",
        "cross_sell":       f"{segment_size:,} customers with purchases in one category, ready for complementary {cat} products.",
        "upsell":           f"{segment_size:,} mid-tier customers who may be ready to upgrade to premium {cat}.",
        "new_product":      f"{segment_size:,} engaged contacts who would be interested in new {cat} arrivals.",
        "winback":          f"{segment_size:,} churned or at-risk customers who haven't purchased in 90+ days. "
                           f"Previously bought {cat} products.",
        "education":        f"{segment_size:,} contacts who would benefit from educational content about {cat}. "
                           f"Low commercial pressure, nurture-focused.",
        "loyalty_reward":   f"{segment_size:,} VIP and loyal customers with 5+ orders. Reward their loyalty and "
                           f"make them feel valued.",
        "discount_offer":   f"{segment_size:,} price-sensitive customers who respond well to discount offers. "
                           f"Previously used discount codes.",
    }
    return descs.get(action_type, f"{segment_size:,} contacts matching the {action_type} criteria.")


def _get_talking_points(action_type, top_products, top_categories, avg_aov):
    """Generate key talking points for the brief."""
    cat = top_categories[0] if top_categories else "electronics"
    prod_list = ", ".join(top_products[:3]) if top_products else "their favourite products"

    points = {
        "reorder_reminder": f"""  - Remind them of past purchases: {prod_list}
  - Mention estimated time since last order
  - Suggest the same products or updated versions
  - Avg order value: ${avg_aov:,.0f} -- consider free shipping threshold""",
        "cross_sell": f"""  - Suggest complementary {cat} products
  - Reference their existing purchases: {prod_list}
  - Show 'customers also bought' social proof
  - Bundle offers can increase basket size""",
        "upsell": f"""  - Highlight premium features and benefits
  - Compare current tier to upgrade options
  - Emphasize quality and longevity
  - Use customer reviews and ratings""",
        "new_product": f"""  - Showcase new arrivals in {cat}
  - Create excitement and urgency
  - Offer early access or first-look content
  - Use high-quality product images""",
        "winback": f"""  - Acknowledge the gap: 'It's been a while'
  - Show what's new since their last visit
  - Remind them of past purchases: {prod_list}
  - Consider a small incentive to re-engage""",
        "education": f"""  - Share tips and tricks for {cat}
  - How-to guides and product care
  - No hard sell -- build trust and authority
  - Link to blog content or video tutorials""",
        "loyalty_reward": f"""  - Thank them for their continued support
  - Exclusive VIP offer or early access
  - Personalized recommendation based on purchase history
  - Make them feel valued, not marketed to""",
        "discount_offer": f"""  - Clear discount value proposition
  - Time-limited offer to create urgency
  - Apply to products they've browsed or bought: {prod_list}
  - Avg order value: ${avg_aov:,.0f} -- set discount to maintain margin""",
    }
    return points.get(action_type, f"  - Personalize based on purchase history\n  - Include clear CTA")


# ═══════════════════════════════════════════════════════════════
# Core: Scan Opportunities
# ═══════════════════════════════════════════════════════════════

def scan_opportunities():
    """Scan MessageDecision table, generate ranked campaign suggestions.
    Returns list of dicts sorted by quality_score DESC.
    """
    from database import (MessageDecision, SuggestedCampaign, OpportunityScanLog,
                          CustomerProfile, ContactScore, WarmupConfig, Contact,
                          init_db)
    from peewee import fn
    init_db()

    start_time = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    today_pretty = datetime.now().strftime("%b %-d")

    # Clear today's existing suggested (idempotent re-scan)
    SuggestedCampaign.delete().where(
        SuggestedCampaign.scan_date == today,
        SuggestedCampaign.status == "suggested"
    ).execute()

    # Get warmup headroom
    warmup_headroom = 50
    try:
        wc = WarmupConfig.get_by_id(1)
        if wc.is_active:
            daily_limit = WARMUP_LIMITS.get(wc.current_phase, 50)
            warmup_headroom = max(0, daily_limit - (wc.emails_sent_today or 0))
        else:
            warmup_headroom = 99999
    except Exception:
        pass

    # Query all non-wait, non-suppressed decisions
    decisions = list(MessageDecision.select().where(
        MessageDecision.action_type.not_in(["wait", "switch_channel"]),
        MessageDecision.suppression_active == False
    ))

    # Group by action_type
    groups = defaultdict(list)
    for md in decisions:
        groups[md.action_type].append(md)

    opportunities = []

    for action_type, md_list in groups.items():
        segment_size = len(md_list)
        if segment_size < 5:
            continue

        contact_ids = [md.contact_id for md in md_list]

        # ── Aggregate MessageDecision snapshot metrics ──
        avg_fatigue = sum(md.fatigue_score or 0 for md in md_list) / segment_size
        avg_intent = sum(md.intent_score or 0 for md in md_list) / segment_size
        avg_churn = sum(md.churn_risk_score or 0 for md in md_list) / segment_size
        avg_reorder = sum(md.reorder_likelihood or 0 for md in md_list) / segment_size
        avg_days_since = sum(md.days_since_last_order or 0 for md in md_list) / segment_size

        # ── Engagement from ContactScore ──
        avg_engagement = 0
        try:
            eng_scores = list(ContactScore.select(ContactScore.engagement_score)
                             .where(ContactScore.contact.in_(contact_ids[:200])))
            if eng_scores:
                avg_engagement = sum(e.engagement_score or 0 for e in eng_scores) / len(eng_scores)
        except Exception:
            pass

        # ── CustomerProfile data (sample 200) ──
        aovs = []
        top_products_counter = Counter()
        top_categories_counter = Counter()
        at_risk_count = 0
        send_hours = []

        sample_ids = contact_ids[:200]
        for cid in sample_ids:
            try:
                cp = CustomerProfile.get(CustomerProfile.contact == cid)
                if cp.avg_order_value and cp.avg_order_value > 0:
                    aovs.append(cp.avg_order_value)
                # Top products
                prods = json.loads(cp.top_products or "[]")
                for p in prods[:3]:
                    if isinstance(p, str):
                        top_products_counter[p] += 1
                # Top categories
                cats = json.loads(cp.top_categories or "[]")
                for c in cats[:2]:
                    if isinstance(c, str):
                        top_categories_counter[c] += 1
                # Lifecycle
                if cp.lifecycle_stage in ("at_risk", "churned"):
                    at_risk_count += 1
                # Send window
                if cp.preferred_send_hour is not None and cp.preferred_send_hour >= 0:
                    send_hours.append(cp.preferred_send_hour)
            except CustomerProfile.DoesNotExist:
                pass

        avg_aov = sum(aovs) / len(aovs) if aovs else 80
        top_products = [p for p, _ in top_products_counter.most_common(5)]
        top_categories = [c for c, _ in top_categories_counter.most_common(3)]
        at_risk_pct = at_risk_count / len(sample_ids) * 100 if sample_ids else 0

        # Recommended send window
        send_window = -1
        if send_hours:
            send_window = Counter(send_hours).most_common(1)[0][0]

        # ── Predicted revenue ──
        conv_rate = CONVERSION_RATES.get(action_type, 0.02)
        predicted_conversions = max(1, int(segment_size * conv_rate))
        predicted_revenue = round(predicted_conversions * avg_aov, 2)

        # ── Complaint risk ──
        high_risk_count = sum(1 for md in md_list
                              if (md.fatigue_score or 0) >= 50)
        complaint_risk_pct = round(high_risk_count / segment_size * 100, 1)

        # ── Deliverability risk score (0-100) — Rule 4 compliance ──
        spam_risk_count = 0
        try:
            spam_risk_ids = contact_ids[:200]
            spam_risk_contacts = list(Contact.select(Contact.spam_risk_score)
                                      .where(Contact.id.in_(spam_risk_ids)))
            spam_risk_count = sum(1 for c in spam_risk_contacts
                                  if (c.spam_risk_score or 0) >= 40)
        except Exception:
            pass
        fatigue_pct = complaint_risk_pct  # already % of high-fatigue contacts
        spam_pct = round(spam_risk_count / max(1, min(200, segment_size)) * 100, 1)

        # ── Preflight simulation ──
        preflight_status, preflight_warnings = _simulate_preflight(
            segment_size, avg_fatigue, complaint_risk_pct
        )

        # ── Safe send volume ──
        fatigue_safe = sum(1 for md in md_list if (md.fatigue_score or 0) < 50)
        safe_send = min(segment_size, warmup_headroom, fatigue_safe)

        # ── Deliverability risk score (0-100) — Rule 4 compliance ──
        headroom_pct = max(0, 100 - round(safe_send / max(1, segment_size) * 100))
        deliverability_risk = int(fatigue_pct * 0.4 + spam_pct * 0.3 + headroom_pct * 0.3)
        deliverability_risk = min(100, max(0, deliverability_risk))

        # ── Quality score ──
        quality_score = _score_opportunity(
            segment_size, avg_engagement, predicted_revenue,
            complaint_risk_pct, avg_fatigue, warmup_headroom
        )

        # ── Urgency ──
        avg_reorder_days = 90  # default
        urgency = _compute_urgency(
            action_type, avg_churn, avg_days_since, avg_reorder_days, at_risk_pct
        )

        # ── Recommended offer type ──
        recommended_offer = "none"
        if action_type == "discount_offer":
            recommended_offer = "percentage_off"
        elif action_type == "loyalty_reward":
            recommended_offer = "early_access"
        elif action_type == "winback":
            # Check if segment is discount-responsive
            try:
                from profit_engine import get_customer_discount_eligibility
                disc_sample = contact_ids[:30]
                disc_count = sum(1 for cid in disc_sample
                                if get_customer_discount_eligibility(cid).get("should_discount", True))
                if disc_count / max(1, len(disc_sample)) > 0.6:
                    recommended_offer = "percentage_off"
            except ImportError:
                pass

        # ── Campaign name ──
        top_cat = top_categories[0] if top_categories else "Electronics"
        campaign_name = f"{CAMPAIGN_TYPE_NAMES.get(action_type, action_type)} -- {top_cat} -- {today_pretty}"

        # ── Target description ──
        target_desc = _describe_segment(action_type, top_categories, top_products, segment_size)

        # ── Brief ──
        brief = _generate_campaign_brief(
            action_type, segment_size, avg_aov, top_products, top_categories,
            send_window, predicted_revenue, 0, complaint_risk_pct, urgency,
            safe_send, preflight_warnings
        )

        # ── Recommended channel ──
        channel = "email"

        # ── Create SuggestedCampaign ──
        sc = SuggestedCampaign.create(
            scan_date=today,
            campaign_type=action_type,
            campaign_name=campaign_name,
            target_description=target_desc,
            segment_size=segment_size,
            eligible_contacts_json=json.dumps(contact_ids[:500]),
            quality_score=quality_score,
            urgency=urgency,
            recommended_send_window=send_window,
            recommended_channel=channel,
            recommended_offer_type=recommended_offer,
            predicted_revenue=predicted_revenue,
            predicted_conversions=predicted_conversions,
            predicted_complaint_risk=complaint_risk_pct,
            safe_send_volume=safe_send,
            preflight_status=preflight_status,
            preflight_warnings_json=json.dumps(preflight_warnings),
            brief_text=brief,
            status="suggested",
            deliverability_risk_score=deliverability_risk,
        )

        opportunities.append({
            "id": sc.id,
            "campaign_type": action_type,
            "campaign_name": campaign_name,
            "quality_score": quality_score,
            "urgency": urgency,
            "segment_size": segment_size,
            "predicted_revenue": predicted_revenue,
            "preflight_status": preflight_status,
        })

        print(f"  [{quality_score:3d}] {campaign_name} "
              f"({segment_size:,} contacts, ${predicted_revenue:,.0f}, "
              f"preflight: {preflight_status})")

    # ── Profit enrichment (Phase 2D) ──
    try:
        from profit_engine import compute_campaign_profit_forecast
        for sc in SuggestedCampaign.select().where(
            SuggestedCampaign.scan_date == today,
            SuggestedCampaign.status == "suggested"
        ):
            compute_campaign_profit_forecast(sc.id)
            # Update brief with profit data
            sc_refreshed = SuggestedCampaign.get_by_id(sc.id)
            if sc_refreshed.net_profit > 0:
                brief_update = sc_refreshed.brief_text.replace(
                    "Predicted Profit:     $0",
                    f"Predicted Profit:     ${sc_refreshed.net_profit:,.0f}"
                )
                sc_refreshed.brief_text = brief_update
                sc_refreshed.save()
    except ImportError:
        logger.warning("profit_engine not available -- skipping profit forecasts")
    except Exception as e:
        logger.warning(f"Profit enrichment failed: {e}")

    # ── Log scan ──
    duration = round(time.time() - start_time, 2)
    total_eligible = sum(o["segment_size"] for o in opportunities)
    OpportunityScanLog.create(
        scan_date=today,
        opportunities_found=len(opportunities),
        total_eligible_contacts=total_eligible,
        scan_duration_seconds=duration,
    )

    # Sort by quality score
    opportunities.sort(key=lambda x: x["quality_score"], reverse=True)

    print(f"\n[OK] Scan complete: {len(opportunities)} opportunities, "
          f"{total_eligible:,} eligible contacts, {duration:.1f}s")

    return opportunities


# ═══════════════════════════════════════════════════════════════
# Accept / Dismiss
# ═══════════════════════════════════════════════════════════════


def _generate_campaign_email(sc):
    """Generate email content for a campaign using Claude AI.
    Returns EmailTemplate ID, or 0 if generation fails."""
    import json
    try:
        from ai_engine import _get_anthropic_client, BRAND_CONTEXT, EMAIL_PURPOSES
        from email_templates import render_email
        from database import EmailTemplate

        purpose = ACTION_TO_PURPOSE.get(sc.campaign_type, "re_engagement")
        purpose_desc = EMAIL_PURPOSES.get(purpose, "Send a relevant, helpful email.")

        # Parse top products
        top_products = "Not specified"
        if sc.top_products_json:
            try:
                prods = json.loads(sc.top_products_json)
                if prods:
                    top_products = ", ".join(prods[:8])
            except Exception:
                pass

        prompt = BRAND_CONTEXT + """

CAMPAIGN BRIEF:
""" + (sc.brief_text or sc.campaign_name) + """

TARGET AUDIENCE: """ + (sc.target_description or "General customer base") + """
CAMPAIGN TYPE: """ + sc.campaign_type + """
EMAIL PURPOSE: """ + purpose + " — " + purpose_desc + """
TOP PRODUCTS: """ + top_products + """

INSTRUCTIONS:
- Write a compelling campaign email for this audience segment
- Use {{first_name}} as the recipient's name variable — it will be replaced at send time
- For the greeting, use something like "Hey {{first_name}}," or "Hi {{first_name}},"
- Reference the top products naturally if relevant to the campaign type
- Keep it warm, conversational, and helpful — like a fellow trucker who knows their tech
- Subject line should be short and compelling — you may include {{first_name}} if it fits
- The CTA button should link to https://ldas.ca or a relevant collection page
- Keep body paragraphs concise (2-3 sentences each, max 3 paragraphs)

Return ONLY valid JSON (no markdown, no code blocks) with this structure:
{
  "subject": "email subject line",
  "preheader": "inbox preview text (max 80 chars)",
  "hero_headline": "big headline at top of email",
  "hero_subheadline": "optional smaller text below headline",
  "body_paragraphs": ["paragraph 1", "paragraph 2"],
  "cta_text": "button text like Shop Now",
  "cta_url": "https://ldas.ca",
  "urgency_message": "optional urgency text (leave empty string if not needed)"
}"""

        client = _get_anthropic_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split(chr(10), 1)[1] if chr(10) in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        email_content = json.loads(raw)

        # Render full HTML email using existing templates
        html = render_email(purpose, email_content, products=[], discount=None)

        # Create EmailTemplate
        template = EmailTemplate.create(
            name="[AI] " + sc.campaign_name,
            subject=email_content.get("subject", sc.campaign_name),
            preview_text=email_content.get("preheader", ""),
            html_body=html,
            shell_version=0,  # render_email already includes the shell
        )

        print("[CampaignPlanner] AI-generated template #%d for '%s'" % (template.id, sc.campaign_name))
        return template.id

    except Exception as e:
        print("[CampaignPlanner] AI generation failed for '%s': %s" % (sc.campaign_name, e))
        return 0


def accept_opportunity(suggested_campaign_id):
    """Mark a suggested campaign as accepted and create a draft Campaign.
    Returns the new Campaign ID.
    """
    from database import SuggestedCampaign, Campaign

    sc = SuggestedCampaign.get_by_id(suggested_campaign_id)
    sc.status = "accepted"
    sc.accepted_at = datetime.now()
    sc.save()

    # Generate email content via AI
    template_id = _generate_campaign_email(sc)

    # Create a draft Campaign with AI-generated template
    campaign = Campaign.create(
        name=sc.campaign_name,
        from_name=os.environ.get("DEFAULT_FROM_NAME", "LDAS Electronics"),
        from_email=os.environ.get("DEFAULT_FROM_EMAIL", ""),
        template_id=template_id,
        segment_filter=f"planner:{suggested_campaign_id}",
        status="draft",
    )

    return campaign.id


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/mailengine")
    from database import init_db
    init_db()

    print("Scanning campaign opportunities...\n")
    opportunities = scan_opportunities()
    print(f"\n{'=' * 60}")
    print(f"Found {len(opportunities)} campaign opportunities")
    for opp in opportunities:
        print(f"  [{opp['quality_score']:3d}] {opp['campaign_name']} "
              f"({opp['segment_size']:,} contacts, ${opp['predicted_revenue']:,.0f})")
