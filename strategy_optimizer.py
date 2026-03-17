"""
strategy_optimizer.py — Apply learned insights to the decision pipeline.
Runs at 6:00 AM. Outputs are consumed by the next night's 1:00-4:15 AM pipeline.
"""

import logging
from datetime import datetime, timedelta

from learning_config import get_learning_enabled, get_learning_phase

logger = logging.getLogger(__name__)


def _get_optimization_target():
    """Return 'engagement' or 'revenue' based on warmup phase."""
    from database import WarmupConfig
    try:
        config = WarmupConfig.get_by_id(1)
        if config.is_active and config.current_phase <= 4:
            return "engagement"
    except Exception:
        pass
    return "revenue"


def get_template_recommendations(segment):
    """
    Return templates ranked by performance for a given segment.
    Used by ai_engine._build_context() to inject into AI plan prompt.
    """
    from database import TemplateSegmentPerformance, EmailTemplate

    target = _get_optimization_target()
    sort_field = (TemplateSegmentPerformance.open_rate if target == "engagement"
                  else TemplateSegmentPerformance.revenue_per_send)

    perfs = list(
        TemplateSegmentPerformance
        .select()
        .where(
            TemplateSegmentPerformance.segment == segment,
            TemplateSegmentPerformance.sample_size >= 20,
        )
        .order_by(sort_field.desc())
    )

    recommendations = []
    for p in perfs:
        try:
            template = EmailTemplate.get_by_id(p.template_id)
        except EmailTemplate.DoesNotExist:
            continue

        recommendations.append({
            "template_id": p.template_id,
            "name": template.name,
            "revenue_per_send": round(p.revenue_per_send, 4),
            "open_rate": round(p.open_rate, 4),
            "conversion_rate": round(p.conversion_rate, 4),
            "sample_size": p.sample_size,
            "confidence": "high" if p.sample_size >= 50 else "learning",
        })

    return recommendations


def get_contact_frequency_cap(contact_id):
    """
    Personalized frequency cap for a contact.
    Replaces the static FREQ_CAP_HOURS = 16 in app.py flow processor.
    """
    from database import ContactScore

    try:
        score = ContactScore.get(ContactScore.contact == contact_id)
        gap = score.optimal_gap_hours or 48.0
    except ContactScore.DoesNotExist:
        gap = 48.0  # Default for unscored contacts

    # Hard floor: never faster than 16 hours
    return max(gap, 16.0)


def get_action_score_adjustment(action_type, segment):
    """
    Return a score multiplier for an action based on historical performance.
    Used by next_best_message.py to boost/penalize actions.
    """
    from database import ActionPerformance

    try:
        ap = ActionPerformance.get(
            (ActionPerformance.action_type == action_type) &
            (ActionPerformance.segment == segment)
        )
    except ActionPerformance.DoesNotExist:
        return 1.0  # No data — neutral

    if ap.sample_size < 20:
        return 1.0  # Not enough data

    # Use conversion rate to compute multiplier
    # Baseline: 2% conversion rate = 1.0x
    baseline = 0.02
    if ap.conversion_rate > baseline * 2:
        return 1.3  # Strong performer: +30%
    elif ap.conversion_rate > baseline:
        return 1.1  # Good performer: +10%
    elif ap.conversion_rate < baseline * 0.25:
        return 0.7  # Poor performer: -30%
    elif ap.conversion_rate < baseline * 0.5:
        return 0.9  # Below average: -10%

    return 1.0


def execute_sunset_policy():
    """
    Tag contacts for sunset based on learned sunset_score.
    Guardrails: purchase protection, volume cap, phase-awareness.
    """
    from database import (ContactScore, Contact, ShopifyOrder, ActionLedger,
                          OutcomeLog)

    phase = get_learning_phase()

    if phase == "observation":
        # Log what WOULD happen, but don't act
        would_sunset = (ContactScore
                        .select()
                        .where(ContactScore.sunset_score >= 85,
                               ContactScore.sunset_executed == False)
                        .count())
        if would_sunset > 0:
            logger.info("[StrategyOptimizer] SHADOW: Would sunset %d contacts "
                        "(observation mode — no action taken)", would_sunset)
            try:
                ActionLedger.create(
                    trigger_type="learning",
                    source_type="sunset_shadow",
                    status="shadowed",
                    reason_code="RC_LEARNING_ADJUSTMENT",
                    reason_detail="Would sunset %d contacts (observation mode)" % would_sunset,
                )
            except Exception:
                pass
        return

    # Determine sunset threshold based on phase
    threshold = 90 if phase == "conservative" else 85

    # Guardrail #7: Volume cap — never sunset more than 2% of active list
    active_count = Contact.select().where(Contact.subscribed == True).count()
    max_sunsets = max(1, int(active_count * 0.02))

    candidates = list(
        ContactScore
        .select()
        .where(
            ContactScore.sunset_score >= threshold,
            ContactScore.sunset_executed == False,
        )
        .order_by(ContactScore.sunset_score.desc())
        .limit(max_sunsets)
    )

    sunset_count = 0
    now = datetime.now()
    ninety_days_ago = now - timedelta(days=90)

    for score in candidates:
        try:
            contact = Contact.get_by_id(score.contact_id)
        except Contact.DoesNotExist:
            continue

        if not contact.subscribed:
            continue

        # Guardrail #3: Purchase protection — skip recent purchasers
        recent_orders = (ShopifyOrder
                         .select()
                         .where(
                             ShopifyOrder.email == contact.email,
                             ShopifyOrder.ordered_at >= ninety_days_ago,
                         )
                         .count())
        if recent_orders > 0:
            logger.info("[StrategyOptimizer] Skipping sunset for %s — "
                        "%d orders in last 90 days", contact.email, recent_orders)
            continue

        # Queue final "we miss you" email using template id=16
        try:
            _enqueue_sunset_email(contact)
        except Exception as e:
            logger.error("[StrategyOptimizer] Failed to send sunset email to %s: %s",
                         contact.email, e)

        score.sunset_executed = True
        score.sunset_executed_at = now
        score.save()
        sunset_count += 1

        # Audit log
        try:
            ActionLedger.create(
                contact=contact,
                email=contact.email,
                trigger_type="learning",
                source_type="sunset_policy",
                status="detected",
                reason_code="RC_LEARNING_ADJUSTMENT",
                reason_detail="Sunset executed: score=%d, consecutive misses, "
                              "phase=%s" % (score.sunset_score, phase),
            )
        except Exception:
            pass

    if sunset_count > 0:
        logger.info("[StrategyOptimizer] Sunset policy: %d contacts processed "
                    "(threshold=%d, phase=%s, max=%d)",
                    sunset_count, threshold, phase, max_sunsets)


def _enqueue_sunset_email(contact):
    """Send the final win-back email (template id=16) to a sunset contact."""
    from database import EmailTemplate, FlowEmail

    try:
        template = EmailTemplate.get_by_id(16)
    except EmailTemplate.DoesNotExist:
        logger.warning("[StrategyOptimizer] Sunset template id=16 not found — "
                       "silently sunsetting %s", contact.email)
        return

    # Use the existing delivery queue or direct send
    # For now, log the intent — actual send integration depends on delivery mode
    try:
        from database import ActionLedger
        ActionLedger.create(
            contact=contact,
            email=contact.email,
            trigger_type="learning",
            source_type="sunset_final_email",
            template_id=16,
            subject=template.subject or "We miss you",
            status="queued",
            reason_code="RC_LEARNING_ADJUSTMENT",
            reason_detail="Final sunset email queued (template id=16)",
        )
    except Exception as e:
        logger.error("[StrategyOptimizer] Failed to queue sunset email: %s", e)


def _log_weekly_digest():
    """Guardrail #11: Log a summary of what changed this week."""
    from database import (OutcomeLog, TemplatePerformance, ContactScore,
                          ActionLedger)

    now = datetime.now()
    week_ago = now - timedelta(days=7)

    # This week's outcomes
    outcomes = list(OutcomeLog.select().where(OutcomeLog.sent_at >= week_ago))
    if not outcomes:
        return

    total = len(outcomes)
    opens = sum(1 for o in outcomes if o.opened)
    clicks = sum(1 for o in outcomes if o.clicked)
    purchases = sum(1 for o in outcomes if o.purchased)
    revenue = sum(o.revenue for o in outcomes)

    digest = (
        "Weekly Learning Digest (%s):\n"
        "  Emails tracked: %d\n"
        "  Open rate: %.1f%%\n"
        "  Click rate: %.1f%%\n"
        "  Conversion rate: %.1f%%\n"
        "  Revenue attributed: $%.2f\n"
        "  Phase: %s\n"
        % (
            now.strftime("%Y-%m-%d"),
            total,
            (opens / total * 100) if total else 0,
            (clicks / total * 100) if total else 0,
            (purchases / total * 100) if total else 0,
            revenue,
            get_learning_phase(),
        )
    )

    # Sunset summary
    sunset_count = (ContactScore.select()
                    .where(ContactScore.sunset_executed == True)
                    .count())
    digest += "  Contacts sunset: %d\n" % sunset_count

    logger.info("[StrategyOptimizer] %s", digest)

    try:
        ActionLedger.create(
            trigger_type="learning",
            source_type="weekly_digest",
            status="detected",
            reason_code="RC_LEARNING_ADJUSTMENT",
            reason_detail=digest,
        )
    except Exception:
        pass


def run_strategy_optimizer():
    """
    Main entry point — called nightly at 6:00 AM by APScheduler.
    Applies learned insights and executes policies.
    """
    if not get_learning_enabled():
        logger.info("[StrategyOptimizer] Skipped — learning disabled")
        return

    from database import init_db
    init_db()

    phase = get_learning_phase()
    logger.info("[StrategyOptimizer] Starting optimization (phase=%s)...", phase)

    # Execute sunset policy
    execute_sunset_policy()

    # Weekly digest (Mondays only)
    if datetime.now().weekday() == 0:
        _log_weekly_digest()

    logger.info("[StrategyOptimizer] Optimization complete (phase=%s)", phase)
