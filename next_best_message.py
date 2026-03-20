"""
Next-Best-Message Decision Engine — Phase 2B
Deterministic, rule-based engine that selects the single best action for each
contact, explains why, and logs every rejected alternative.

10 action types:
  reorder_reminder, cross_sell, upsell, new_product, winback,
  education, loyalty_reward, discount_offer, wait, switch_channel

Run nightly at 4:00 AM after customer intelligence, or on-demand per contact.
"""

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ─── Action type constants ───────────────────────────────────────────────────

ACTION_TYPES = [
    "reorder_reminder", "cross_sell", "upsell", "new_product", "winback",
    "education", "loyalty_reward", "discount_offer", "wait", "switch_channel",
]

# Map action types to ai_engine EMAIL_PURPOSES for downstream use
ACTION_TO_PURPOSE = {
    "reorder_reminder": "reorder_reminder",
    "cross_sell":       "cross_sell",
    "upsell":           "upsell",
    "new_product":      "new_product",
    "winback":          "winback",
    "education":        "education",
    "loyalty_reward":   "loyalty_reward",
    "discount_offer":   "discount_offer",
    "wait":             "",
    "switch_channel":   "",
}

ACTION_LABELS = {
    "reorder_reminder": "Reorder Reminder",
    "cross_sell":       "Cross-Sell",
    "upsell":           "Upsell",
    "new_product":      "New Product",
    "winback":          "Winback",
    "education":        "Education",
    "loyalty_reward":   "Loyalty Reward",
    "discount_offer":   "Discount Offer",
    "wait":             "Wait",
    "switch_channel":   "Switch Channel",
}


# ─── Helper: last time an action was decided + executed ──────────────────────

def _last_executed_date(contact_id, action_type):
    """Return datetime of last time this action_type was executed for this contact, or None."""
    from database import MessageDecisionHistory
    try:
        row = (MessageDecisionHistory.select(MessageDecisionHistory.executed_at)
               .where(
                   MessageDecisionHistory.contact == contact_id,
                   MessageDecisionHistory.action_type == action_type,
                   MessageDecisionHistory.was_executed == True,
               )
               .order_by(MessageDecisionHistory.executed_at.desc())
               .first())
        return row.executed_at if row and row.executed_at else None
    except Exception:
        return None


def _last_any_email_date(contact_id):
    """Return datetime of last email sent to this contact (any source)."""
    from database import CampaignEmail, FlowEmail
    latest = None
    try:
        ce = (CampaignEmail.select(CampaignEmail.created_at)
              .where(CampaignEmail.contact == contact_id, CampaignEmail.status == "sent")
              .order_by(CampaignEmail.created_at.desc())
              .first())
        if ce:
            latest = ce.created_at
    except Exception:
        pass
    try:
        fe = (FlowEmail.select(FlowEmail.sent_at)
              .where(FlowEmail.contact == contact_id, FlowEmail.status == "sent")
              .order_by(FlowEmail.sent_at.desc())
              .first())
        if fe and fe.sent_at:
            if latest is None or fe.sent_at > latest:
                latest = fe.sent_at
    except Exception:
        pass
    return latest


def _days_since(dt):
    """Return days since a datetime, or 9999 if None."""
    if dt is None:
        return 9999
    return max(0, (datetime.now() - dt).days)


def _recent_bounces_30d(contact_id):
    """Count bounces for this contact in the last 30 days."""
    from database import BounceLog, Contact
    try:
        contact = Contact.get_by_id(contact_id)
        cutoff = datetime.now() - timedelta(days=30)
        return (BounceLog.select()
                .where(BounceLog.email == contact.email, BounceLog.timestamp >= cutoff)
                .count())
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  SCORING POLICIES (each returns dict with score, reason, eligible, rejection_reason)
# ═══════════════════════════════════════════════════════════════════════════════

def _score_reorder_reminder(contact, profile, score, last_sent):
    """Policy 1: Reorder Reminder — customer due to repurchase."""
    total_orders = profile.total_orders if profile else 0
    reorder = getattr(profile, "reorder_likelihood", 0) or 0
    days_since = getattr(profile, "days_since_last_order", 999) or 999
    avg_days = getattr(profile, "avg_days_between_orders", 0) or 0
    next_cat = getattr(profile, "next_purchase_category", "") or ""

    if total_orders < 1:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "Never purchased — reorder not applicable"}
    if reorder < 40:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": f"Reorder likelihood too low ({reorder}/100)"}
    if days_since < 14:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": f"Purchased too recently ({days_since} days ago)"}

    s = reorder
    reason_parts = [f"Reorder likelihood {reorder}/100"]

    if avg_days > 0 and days_since >= avg_days * 0.8:
        s += 15
        reason_parts.append(f"approaching {int(avg_days)}-day reorder cycle (day {days_since})")
    if next_cat:
        s += 10
        reason_parts.append(f"next category: {next_cat}")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 14:
        s -= 20
        reason_parts.append(f"reorder sent {days_since_sent}d ago (−20)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_cross_sell(contact, profile, score, last_sent):
    """Policy 2: Cross-Sell — suggest complementary category."""
    total_orders = profile.total_orders if profile else 0
    days_since = getattr(profile, "days_since_last_order", 999) or 999
    intent = getattr(profile, "intent_score", 0) or 0
    next_cat = getattr(profile, "next_purchase_category", "") or ""
    affinity = {}
    try:
        affinity = json.loads(getattr(profile, "category_affinity_json", "{}") or "{}")
    except Exception:
        pass

    if total_orders < 1:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "No purchases — cross-sell not applicable"}
    if len(affinity) < 2:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "Not enough category diversity for cross-sell"}
    if days_since > 120:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": f"Last purchase too long ago ({days_since}d) for cross-sell"}

    s = 50
    reason_parts = [f"{len(affinity)} category affinities"]

    if total_orders >= 3:
        s += 20
        reason_parts.append(f"{total_orders} orders (strong purchase history)")
    if next_cat:
        top_cats = sorted(affinity.keys(), key=lambda k: affinity[k], reverse=True)
        if top_cats and next_cat != top_cats[0]:
            s += 15
            reason_parts.append(f"predicted next: {next_cat}")
    if intent >= 40:
        s += 10
        reason_parts.append(f"intent {intent}/100")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 21:
        s -= 30
        reason_parts.append(f"cross-sell sent {days_since_sent}d ago (−30)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_upsell(contact, profile, score, last_sent):
    """Policy 3: Upsell — suggest higher-value products."""
    total_orders = profile.total_orders if profile else 0
    price_tier = getattr(profile, "price_tier", "unknown") or "unknown"
    total_spent = profile.total_spent if profile else 0.0
    intent = getattr(profile, "intent_score", 0) or 0
    engagement = score.engagement_score if score else 0

    if total_orders < 1:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "No purchases — upsell not applicable"}
    if price_tier == "premium":
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "Already buying premium products"}

    s = 35
    reason_parts = [f"price tier: {price_tier}"]

    if price_tier == "mid" and total_spent >= 100:
        s += 25
        reason_parts.append(f"${total_spent:.0f} spent — ready for premium")
    if intent >= 50:
        s += 15
        reason_parts.append(f"intent {intent}/100")
    if engagement >= 50:
        s += 10
        reason_parts.append(f"engagement {engagement}/100")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 30:
        s -= 20
        reason_parts.append(f"upsell sent {days_since_sent}d ago (−20)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_new_product(contact, profile, score, last_sent):
    """Policy 4: New Product Introduction — new arrivals matching affinity."""
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    engagement = score.engagement_score if score else 0
    intent = getattr(profile, "intent_score", 0) or 0
    last_open = getattr(contact, "last_open_at", None)

    if lifecycle == "churned" and engagement == 0:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "Churned with no engagement — new product email would be wasted"}

    s = 30
    reason_parts = ["New product introduction"]

    if engagement >= 40:
        s += 20
        reason_parts.append(f"engagement {engagement}/100 (opens emails)")
    if last_open and _days_since(last_open) <= 14:
        s += 15
        reason_parts.append(f"opened email {_days_since(last_open)}d ago")
    if intent >= 30:
        s += 10
        reason_parts.append(f"intent {intent}/100")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 14:
        s -= 15
        reason_parts.append(f"new product email sent {days_since_sent}d ago (−15)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_winback(contact, profile, score, last_sent):
    """Policy 5: Winback — re-engage churned or at-risk customers."""
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    total_orders = profile.total_orders if profile else 0
    days_since = getattr(profile, "days_since_last_order", 999) or 999
    churn_risk = getattr(profile, "churn_risk_score", 0) or 0
    predicted_ltv = getattr(profile, "predicted_ltv", 0) or 0
    last_open = getattr(contact, "last_open_at", None)

    is_eligible = (lifecycle in ("churned", "at_risk") or
                   (total_orders >= 1 and days_since >= 90))

    if not is_eligible:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "Customer is currently active — winback not needed"}

    s = int(churn_risk * 0.7)
    reason_parts = [f"{lifecycle} customer, {days_since}d since last order"]

    if lifecycle == "churned":
        days_since_sent = _days_since(last_sent)
        if days_since_sent >= 60:
            s += 20
            reason_parts.append(f"no winback in {days_since_sent}d")
    if predicted_ltv >= 100:
        s += 15
        reason_parts.append(f"predicted LTV ${predicted_ltv:.0f}")
    if last_open and _days_since(last_open) <= 30:
        s += 10
        reason_parts.append(f"last opened {_days_since(last_open)}d ago (reachable)")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 30:
        s -= 40
        reason_parts.append(f"winback sent {days_since_sent}d ago (−40)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_education(contact, profile, score, last_sent, last_email_date):
    """Policy 6: Education / Content — nurture without selling."""
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    total_orders = profile.total_orders if profile else 0
    web_engage = getattr(profile, "website_engagement_score", 0) or 0
    days_no_email = _days_since(last_email_date)
    days_since_order = getattr(profile, "days_since_last_order", 999) or 999
    avg_cycle = getattr(profile, "avg_days_between_orders", 0) or 0

    s = 25
    reason_parts = ["Content nurture"]

    if lifecycle == "prospect":
        s += 20
        reason_parts.append("prospect — nurture before selling")
    if lifecycle == "new_customer":
        s += 20
        reason_parts.append("new customer — educate about their purchase")
    if lifecycle == "active_buyer":
        s += 15
        reason_parts.append("active buyer — maintain relationship without overselling")
    if total_orders >= 1 and avg_cycle > 0 and days_since_order < avg_cycle * 0.5:
        s += 25
        reason_parts.append(f"recently purchased ({days_since_order}d ago), in reorder cooldown (cycle ~{avg_cycle}d)")
    if total_orders == 0 and web_engage >= 20:
        s += 15
        reason_parts.append(f"browsing (web engagement {web_engage}) but no purchase")
    if days_no_email >= 7:
        s += 10
        reason_parts.append(f"no email in {days_no_email}d — fill the gap")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 7:
        s -= 10
        reason_parts.append(f"education sent {days_since_sent}d ago (−10)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_loyalty_reward(contact, profile, score, last_sent):
    """Policy 7: Loyalty Reward — reward high-value customers."""
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    total_orders = profile.total_orders if profile else 0
    total_spent = profile.total_spent if profile else 0.0

    is_eligible = (lifecycle in ("loyal", "vip") or
                   (total_orders >= 5 and total_spent >= 200))

    if not is_eligible:
        if total_orders < 3:
            reason = "Not enough purchase history for loyalty recognition"
        else:
            reason = f"Only {total_orders} orders, ${total_spent:.0f} spent — not yet loyalty tier"
        return {"score": 0, "reason": "", "eligible": False, "rejection_reason": reason}

    s = 60
    reason_parts = [f"{lifecycle} customer, {total_orders} orders, ${total_spent:.0f} spent"]

    if total_orders >= 10:
        s += 20
        reason_parts.append("10+ orders — super loyal")
    if lifecycle == "vip":
        s += 15
        reason_parts.append("VIP status")
    days_since_sent = _days_since(last_sent)
    if days_since_sent >= 45:
        s += 10
        reason_parts.append(f"no loyalty email in {days_since_sent}d")
    if days_since_sent < 30:
        s -= 30
        reason_parts.append(f"loyalty sent {days_since_sent}d ago (−30)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_discount_offer(contact, profile, score, last_sent):
    """Policy 8: Discount Offer — price-sensitive nudge."""
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    total_orders = profile.total_orders if profile else 0
    disc_sens = getattr(profile, "discount_sensitivity", 0.0) or 0.0
    has_used = getattr(profile, "has_used_discount", False)
    intent = getattr(profile, "intent_score", 0) or 0

    is_eligible = (disc_sens >= 0.3 or
                   (lifecycle in ("at_risk", "churned") and total_orders >= 1))

    if not is_eligible:
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": f"Discount sensitivity {disc_sens:.0%} — not discount-responsive"}

    s = int(disc_sens * 60)
    reason_parts = [f"discount sensitivity {disc_sens:.0%}"]

    if lifecycle == "at_risk":
        s += 25
        reason_parts.append("at-risk — discount may prevent churn")
    if has_used:
        s += 15
        reason_parts.append("has used discounts before")
    if intent >= 40:
        s += 10
        reason_parts.append(f"intent {intent}/100")

    days_since_sent = _days_since(last_sent)
    if days_since_sent < 21:
        s -= 40
        reason_parts.append(f"discount sent {days_since_sent}d ago (−40)")
    if lifecycle == "vip":
        s -= 20
        reason_parts.append("VIP — shouldn't need discounts (−20)")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_wait(contact, profile, score, last_email_date):
    """Policy 9: Wait / Do Not Send — the safety valve."""
    fatigue = getattr(contact, "fatigue_score", 0) or 0
    spam_risk = getattr(contact, "spam_risk_score", 0) or 0
    emails_7d = getattr(contact, "emails_received_7d", 0) or 0
    engagement = score.engagement_score if score else 0
    total_orders = profile.total_orders if profile else 0
    is_suppressed = getattr(contact, "is_suppressed", False)

    s = 20
    reason_parts = []

    if is_suppressed:
        s += 80
        reason_parts.append(f"suppressed ({getattr(contact, 'suppression_reason', 'unknown')})")
    if emails_7d >= 3:
        s += 80
        reason_parts.append(f"received {emails_7d} emails this week")
    if fatigue >= 50:
        s += 40 if fatigue < 70 else 60
        reason_parts.append(f"fatigue {fatigue}/100")
    if spam_risk >= 60:
        s += 50
        reason_parts.append(f"spam risk {spam_risk}/100")

    days_since_email = _days_since(last_email_date)
    if days_since_email < 1:
        s += 40
        reason_parts.append("email sent within last 24h")
    if engagement == 0 and total_orders == 0:
        s += 30
        reason_parts.append("no engagement and no orders")

    if not reason_parts:
        reason_parts.append("no fatigue or suppression signals")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


def _score_switch_channel(contact, profile, score, last_sent):
    """Policy 10: Switch Channel — SMS or other channel may be better."""
    channel_pref = getattr(profile, "channel_preference", "email") or "email"
    sms_consent = getattr(contact, "sms_consent", False)
    phone = getattr(contact, "phone", "") or ""
    engagement = score.engagement_score if score else 0
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"

    if channel_pref not in ("sms", "both") or not sms_consent or not phone.strip():
        return {"score": 0, "reason": "", "eligible": False,
                "rejection_reason": "No SMS consent or phone number on file"}

    s = 30
    reason_parts = [f"channel preference: {channel_pref}"]

    if engagement < 20 and channel_pref == "sms":
        s += 30
        reason_parts.append(f"email engagement low ({engagement}/100) — SMS preferred")

    bounces_30d = _recent_bounces_30d(contact.id)
    if bounces_30d > 0:
        s += 20
        reason_parts.append(f"{bounces_30d} bounce(s) in last 30 days")

    if lifecycle in ("at_risk", "churned") and channel_pref == "sms":
        s += 15
        reason_parts.append(f"{lifecycle} — SMS may re-engage")

    return {"score": max(0, min(100, s)), "reason": ". ".join(reason_parts) + ".",
            "eligible": True, "rejection_reason": ""}


# ═══════════════════════════════════════════════════════════════════════════════
#  CORE DECISION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def decide_next_action(contact_id):
    """
    Compute the next-best-message decision for a single contact.
    Returns a dict with: action_type, action_score, action_reason,
    ranked_actions, rejections, and input snapshots.
    Also persists to MessageDecision (upsert) and MessageDecisionHistory (append).
    """
    from database import (Contact, CustomerProfile, ContactScore,
                           MessageDecision, MessageDecisionHistory, db)

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # ── Load data ────────────────────────────────────────────────────────────
    try:
        contact = Contact.get_by_id(contact_id)
    except Contact.DoesNotExist:
        return {"error": f"Contact {contact_id} not found"}

    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact_id)
    score = ContactScore.get_or_none(ContactScore.contact == contact_id)
    last_email_date = _last_any_email_date(contact_id)

    # ── Snapshot inputs ──────────────────────────────────────────────────────
    lifecycle = getattr(profile, "lifecycle_stage", "unknown") or "unknown"
    fatigue = getattr(contact, "fatigue_score", 0) or 0
    emails_7d = getattr(contact, "emails_received_7d", 0) or 0
    churn_risk = getattr(profile, "churn_risk_score", 0) or 0
    intent = getattr(profile, "intent_score", 0) or 0
    reorder_lk = getattr(profile, "reorder_likelihood", 0) or 0
    disc_sens = getattr(profile, "discount_sensitivity", 0.0) or 0.0
    days_since = getattr(profile, "days_since_last_order", 999) or 999
    spam_risk = getattr(contact, "spam_risk_score", 0) or 0
    is_suppressed = getattr(contact, "is_suppressed", False)

    # ── Hard gates → immediate WAIT ─────────────────────────────────────────
    hard_wait_reason = None
    if not contact.subscribed:
        hard_wait_reason = "Unsubscribed"
    elif is_suppressed:
        hard_wait_reason = f"Suppressed: {getattr(contact, 'suppression_reason', 'unknown')}"
    elif emails_7d >= 3:
        hard_wait_reason = f"Weekly email limit reached ({emails_7d} emails in 7 days)"
    elif fatigue >= 70:
        hard_wait_reason = f"High fatigue score ({fatigue}/100)"
    elif spam_risk >= 60:
        hard_wait_reason = f"High spam risk ({spam_risk}/100)"

    if hard_wait_reason:
        # All actions rejected, wait is the only option
        rejections = []
        for at in ACTION_TYPES:
            if at != "wait":
                rejections.append({"action_type": at,
                                   "rejection_reason": f"Blocked by hard gate: {hard_wait_reason}"})
        result = {
            "contact_id": contact_id,
            "action_type": "wait",
            "action_score": 100,
            "action_reason": hard_wait_reason,
            "action_email_purpose": "",
            "ranked_actions": [{"action_type": "wait", "score": 100, "reason": hard_wait_reason}],
            "rejections": rejections,
        }
        _persist_decision(contact, profile, score, result, today, now)
        return result

    # ── Score all 10 actions ─────────────────────────────────────────────────
    last_sent = {at: _last_executed_date(contact_id, at) for at in ACTION_TYPES}

    raw_scores = {
        "reorder_reminder": _score_reorder_reminder(contact, profile, score, last_sent["reorder_reminder"]),
        "cross_sell":       _score_cross_sell(contact, profile, score, last_sent["cross_sell"]),
        "upsell":           _score_upsell(contact, profile, score, last_sent["upsell"]),
        "new_product":      _score_new_product(contact, profile, score, last_sent["new_product"]),
        "winback":          _score_winback(contact, profile, score, last_sent["winback"]),
        "education":        _score_education(contact, profile, score, last_sent["education"], last_email_date),
        "loyalty_reward":   _score_loyalty_reward(contact, profile, score, last_sent["loyalty_reward"]),
        "discount_offer":   _score_discount_offer(contact, profile, score, last_sent["discount_offer"]),
        "wait":             _score_wait(contact, profile, score, last_email_date),
        "switch_channel":   _score_switch_channel(contact, profile, score, last_sent["switch_channel"]),
    }

    # ── Separate eligible vs rejected ────────────────────────────────────────
    ranked = []
    rejections = []

    for action_type, result in raw_scores.items():
        if result["eligible"]:
            ranked.append({
                "action_type": action_type,
                "score": result["score"],
                "reason": result["reason"],
            })
        else:
            rejections.append({
                "action_type": action_type,
                "rejection_reason": result["rejection_reason"],
            })

    # Sort by score descending, then by action type for stable tie-breaking
    ranked.sort(key=lambda x: (-x["score"], x["action_type"]))

    # Apply learned score adjustment from historical performance
    try:
        from strategy_optimizer import get_action_score_adjustment
        segment = score.rfm_segment if score else "unknown"
        for r in ranked:
            multiplier = get_action_score_adjustment(r["action_type"], segment)
            r["score"] = int(r["score"] * multiplier)
        ranked.sort(key=lambda x: (-x["score"], x["action_type"]))
    except Exception:
        pass  # Learning module not available — use raw scores

    # ── Build result ─────────────────────────────────────────────────────────
    top = ranked[0] if ranked else {"action_type": "wait", "score": 20, "reason": "No eligible actions."}

    result = {
        "contact_id": contact_id,
        "action_type": top["action_type"],
        "action_score": top["score"],
        "action_reason": top["reason"],
        "action_email_purpose": ACTION_TO_PURPOSE.get(top["action_type"], ""),
        "ranked_actions": ranked,
        "rejections": rejections,
    }

    _persist_decision(contact, profile, score, result, today, now)
    return result



def _compute_risk_level(fatigue, spam_risk, is_suppressed):
    """Compute risk level from fatigue, spam risk, and suppression. Rule 3 compliance."""
    if is_suppressed or fatigue > 80:
        return "critical"
    if fatigue > 60 or spam_risk > 50:
        return "high"
    if fatigue > 30 or spam_risk > 30:
        return "medium"
    return "low"


def _persist_decision(contact, profile, score, result, today, now):
    """Upsert MessageDecision and append MessageDecisionHistory."""
    from database import MessageDecision, MessageDecisionHistory, db

    lifecycle = getattr(profile, "lifecycle_stage", "unknown") if profile else "unknown"
    fatigue = getattr(contact, "fatigue_score", 0) or 0
    emails_7d = getattr(contact, "emails_received_7d", 0) or 0
    churn_risk = getattr(profile, "churn_risk_score", 0) if profile else 0
    intent = getattr(profile, "intent_score", 0) if profile else 0
    reorder_lk = getattr(profile, "reorder_likelihood", 0) if profile else 0
    disc_sens = getattr(profile, "discount_sensitivity", 0.0) if profile else 0.0
    days_since = getattr(profile, "days_since_last_order", 999) if profile else 999
    is_suppressed = getattr(contact, "is_suppressed", False)

    ranked_json = json.dumps(result["ranked_actions"])
    rejections_json = json.dumps(result["rejections"])

    # ── Upsert MessageDecision ───────────────────────────────────────────
    try:
        md = MessageDecision.get_or_none(MessageDecision.contact == contact.id)
        if md:
            md.email = contact.email
            md.action_type = result["action_type"]
            md.action_score = result["action_score"]
            md.action_reason = result["action_reason"]
            md.action_email_purpose = result.get("action_email_purpose", "")
            md.ranked_actions_json = ranked_json
            md.rejections_json = rejections_json
            md.lifecycle_stage = lifecycle
            md.fatigue_score = fatigue
            md.emails_received_7d = emails_7d
            md.churn_risk_score = churn_risk
            md.intent_score = intent
            md.reorder_likelihood = reorder_lk
            md.discount_sensitivity = disc_sens
            md.days_since_last_order = days_since
            md.suppression_active = is_suppressed
            md.risk_level = _compute_risk_level(fatigue, getattr(contact, "spam_risk_score", 0) or 0, is_suppressed)
            md.suppression_reason = getattr(contact, "suppression_reason", "") or "" if is_suppressed else ""
            md.decided_at = now
            md.expires_at = now + timedelta(hours=24)
            md.save()
        else:
            MessageDecision.create(
                contact=contact.id,
                email=contact.email,
                action_type=result["action_type"],
                action_score=result["action_score"],
                action_reason=result["action_reason"],
                action_email_purpose=result.get("action_email_purpose", ""),
                ranked_actions_json=ranked_json,
                rejections_json=rejections_json,
                lifecycle_stage=lifecycle,
                fatigue_score=fatigue,
                emails_received_7d=emails_7d,
                churn_risk_score=churn_risk,
                intent_score=intent,
                reorder_likelihood=reorder_lk,
                discount_sensitivity=disc_sens,
                days_since_last_order=days_since,
                suppression_active=is_suppressed,
                risk_level=_compute_risk_level(fatigue, getattr(contact, "spam_risk_score", 0) or 0, is_suppressed),
                suppression_reason=getattr(contact, "suppression_reason", "") or "" if is_suppressed else "",
                decided_at=now,
                expires_at=now + timedelta(hours=24),
            )
    except Exception as e:
        logger.error(f"Failed to persist MessageDecision for {contact.id}: {e}")

    # ── Append MessageDecisionHistory ────────────────────────────────────
    try:
        MessageDecisionHistory.create(
            contact=contact.id,
            email=contact.email,
            decision_date=today,
            action_type=result["action_type"],
            action_score=result["action_score"],
            action_reason=result["action_reason"],
            action_email_purpose=result.get("action_email_purpose", ""),
            ranked_actions_json=ranked_json,
            rejections_json=rejections_json,
            was_executed=False,
            executed_at=None,
            lifecycle_stage=lifecycle,
            fatigue_score=fatigue,
            churn_risk_score=churn_risk,
            intent_score=intent,
            reorder_likelihood=reorder_lk,
            decided_at=now,
        )
    except Exception as e:
        logger.error(f"Failed to persist MessageDecisionHistory for {contact.id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  BATCH FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def decide_all_contacts():
    """
    Compute next-best-message decisions for all contacts with a CustomerProfile.
    Returns the count of contacts processed.
    """
    from database import Contact, CustomerProfile, init_db
    init_db()

    contacts = (Contact.select(Contact.id)
                .join(CustomerProfile, on=(CustomerProfile.contact == Contact.id))
                .where(Contact.subscribed == True))

    count = 0
    errors = 0
    for c in contacts:
        try:
            decide_next_action(c.id)
            count += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.error(f"Error deciding for contact {c.id}: {e}")

    # Also process unsubscribed contacts (they'll all get "wait")
    unsub = (Contact.select(Contact.id)
             .join(CustomerProfile, on=(CustomerProfile.contact == Contact.id))
             .where(Contact.subscribed == False))
    for c in unsub:
        try:
            decide_next_action(c.id)
            count += 1
        except Exception:
            errors += 1

    logger.info(f"Decided for {count} contacts ({errors} errors)")
    return count


# ── CLI entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()

    if len(sys.argv) > 1:
        cid = int(sys.argv[1])
        result = decide_next_action(cid)
        print(json.dumps(result, indent=2, default=str))
    else:
        count = decide_all_contacts()
        print(f"\nDecisions complete: {count} contacts processed")
