"""
learning_engine.py — Nightly learning analysis for the self-learning layer.
Runs at 5:30 AM. Analyzes OutcomeLog and computes adjustments.
"""

import logging
from datetime import datetime, timedelta
from statistics import median

from learning_config import get_learning_enabled, get_learning_phase, set_learning_phase_override

logger = logging.getLogger(__name__)

SEGMENTS = ["champion", "loyal", "potential", "at_risk", "lapsed", "new"]


def compute_template_scoring():
    """
    2A: Compute rolling 30-day template performance with revenue.
    Updates TemplatePerformance and TemplateSegmentPerformance.
    """
    from database import (OutcomeLog, TemplatePerformance, EmailTemplate,
                          TemplateSegmentPerformance)

    cutoff = datetime.now() - timedelta(days=30)
    now = datetime.now()

    # Get all template_ids with outcomes in the window
    template_ids = set()
    for row in (OutcomeLog
                .select(OutcomeLog.template_id)
                .where(OutcomeLog.sent_at >= cutoff)
                .distinct()):
        if row.template_id:
            template_ids.add(row.template_id)

    for tid in template_ids:
        try:
            template = EmailTemplate.get_by_id(tid)
        except EmailTemplate.DoesNotExist:
            continue

        outcomes = list(
            OutcomeLog.select()
            .where(OutcomeLog.template_id == tid, OutcomeLog.sent_at >= cutoff)
        )
        if not outcomes:
            continue

        sample_size = len(outcomes)
        opens = sum(1 for o in outcomes if o.opened)
        clicks = sum(1 for o in outcomes if o.clicked)
        purchases = sum(1 for o in outcomes if o.purchased)
        total_revenue = sum(o.revenue for o in outcomes)

        open_rate = round(opens / sample_size, 4) if sample_size else 0.0
        click_rate = round(clicks / sample_size, 4) if sample_size else 0.0
        conversion_rate = round(purchases / sample_size, 4) if sample_size else 0.0
        revenue_per_send = round(total_revenue / sample_size, 4) if sample_size else 0.0
        learning_flag = sample_size < 50

        # Upsert TemplatePerformance
        perf, created = TemplatePerformance.get_or_create(
            template=template,
            defaults={
                "sends": sample_size,
                "opens": opens,
                "clicks": clicks,
                "open_rate": open_rate,
                "click_rate": click_rate,
                "revenue_total": total_revenue,
                "revenue_per_send": revenue_per_send,
                "conversion_rate": conversion_rate,
                "sample_size": sample_size,
                "learning_flag": learning_flag,
                "last_computed": now,
            },
        )
        if not created:
            perf.sends = sample_size
            perf.opens = opens
            perf.clicks = clicks
            perf.open_rate = open_rate
            perf.click_rate = click_rate
            perf.revenue_total = total_revenue
            perf.revenue_per_send = revenue_per_send
            perf.conversion_rate = conversion_rate
            perf.sample_size = sample_size
            perf.learning_flag = learning_flag
            perf.last_computed = now
            perf.save()

        # Per-segment breakdown
        for segment in SEGMENTS:
            seg_outcomes = [o for o in outcomes if o.segment == segment]
            if not seg_outcomes:
                continue

            seg_size = len(seg_outcomes)
            seg_opens = sum(1 for o in seg_outcomes if o.opened)
            seg_clicks = sum(1 for o in seg_outcomes if o.clicked)
            seg_purchases = sum(1 for o in seg_outcomes if o.purchased)
            seg_revenue = sum(o.revenue for o in seg_outcomes)

            # Upsert TemplateSegmentPerformance
            tsp, created = TemplateSegmentPerformance.get_or_create(
                template=template,
                segment=segment,
                defaults={
                    "sample_size": seg_size,
                    "open_rate": round(seg_opens / seg_size, 4),
                    "click_rate": round(seg_clicks / seg_size, 4),
                    "conversion_rate": round(seg_purchases / seg_size, 4),
                    "revenue_per_send": round(seg_revenue / seg_size, 4),
                    "last_computed": now,
                },
            )
            if not created:
                tsp.sample_size = seg_size
                tsp.open_rate = round(seg_opens / seg_size, 4)
                tsp.click_rate = round(seg_clicks / seg_size, 4)
                tsp.conversion_rate = round(seg_purchases / seg_size, 4)
                tsp.revenue_per_send = round(seg_revenue / seg_size, 4)
                tsp.last_computed = now
                tsp.save()

    logger.info("[LearningEngine] Template scoring done for %d templates", len(template_ids))


def compute_action_effectiveness():
    """
    2B: Compute rolling 30-day action effectiveness per segment.
    Updates ActionPerformance table.
    """
    from database import OutcomeLog, ActionPerformance

    cutoff = datetime.now() - timedelta(days=30)
    now = datetime.now()

    # Get all (action_type, segment) pairs
    pairs = set()
    for row in (OutcomeLog.select(OutcomeLog.action_type, OutcomeLog.segment)
                .where(OutcomeLog.sent_at >= cutoff)
                .distinct()):
        if row.action_type:
            pairs.add((row.action_type, row.segment))

    for action_type, segment in pairs:
        outcomes = list(
            OutcomeLog.select()
            .where(
                OutcomeLog.action_type == action_type,
                OutcomeLog.segment == segment,
                OutcomeLog.sent_at >= cutoff,
            )
        )
        if not outcomes:
            continue

        n = len(outcomes)
        opens = sum(1 for o in outcomes if o.opened)
        clicks = sum(1 for o in outcomes if o.clicked)
        purchases = sum(1 for o in outcomes if o.purchased)
        revenue = sum(o.revenue for o in outcomes)

        ap, created = ActionPerformance.get_or_create(
            action_type=action_type,
            segment=segment,
            defaults={
                "sample_size": n,
                "open_rate": round(opens / n, 4),
                "click_rate": round(clicks / n, 4),
                "conversion_rate": round(purchases / n, 4),
                "revenue_per_send": round(revenue / n, 4),
                "last_computed": now,
            },
        )
        if not created:
            ap.sample_size = n
            ap.open_rate = round(opens / n, 4)
            ap.click_rate = round(clicks / n, 4)
            ap.conversion_rate = round(purchases / n, 4)
            ap.revenue_per_send = round(revenue / n, 4)
            ap.last_computed = now
            ap.save()

    logger.info("[LearningEngine] Action effectiveness done for %d pairs", len(pairs))


def compute_optimal_frequency():
    """
    2C: For each contact, compute optimal sending gap from their outcome history.
    Updates ContactScore.optimal_gap_hours.
    """
    from database import OutcomeLog, ContactScore, Contact

    phase = get_learning_phase()
    now = datetime.now()
    cutoff_90d = now - timedelta(days=90)
    updated = 0

    # Get contacts with outcomes
    contact_ids = set()
    for row in (OutcomeLog.select(OutcomeLog.contact)
                .where(OutcomeLog.sent_at >= cutoff_90d)
                .distinct()):
        contact_ids.add(row.contact_id)

    for cid in contact_ids:
        outcomes = list(
            OutcomeLog.select()
            .where(OutcomeLog.contact == cid, OutcomeLog.sent_at >= cutoff_90d)
            .order_by(OutcomeLog.sent_at.asc())
        )

        if len(outcomes) < 3:
            optimal_gap = 48.0  # Default: every 2 days
        else:
            engaged = [o for o in outcomes
                       if (o.opened or o.purchased) and o.send_gap_hours is not None]
            disengaged = [o for o in outcomes
                          if not o.opened and not o.purchased
                          and o.send_gap_hours is not None]

            if engaged:
                optimal_gap = median([o.send_gap_hours for o in engaged])
            else:
                optimal_gap = 168.0  # Weekly for non-engagers

            # Fatigue detection: last 5 emails
            recent = outcomes[-5:]
            recent_open_rate = sum(1 for o in recent if o.opened) / len(recent)
            if recent_open_rate < 0.1:
                optimal_gap = max(optimal_gap, 120.0)  # At least 5 days

            # Hard bounds: 16 hours to 14 days
            optimal_gap = max(16.0, min(optimal_gap, 336.0))

        # Apply phase-based caps on change
        try:
            score = ContactScore.get(ContactScore.contact == cid)
            current_gap = score.optimal_gap_hours or 48.0

            if phase == "observation":
                # Don't change anything — just log
                continue
            elif phase == "conservative":
                # Cap change at +-2 hours per night
                diff = optimal_gap - current_gap
                diff = max(-2.0, min(diff, 2.0))
                optimal_gap = current_gap + diff

            score.optimal_gap_hours = round(optimal_gap, 1)
            score.save()
            updated += 1

        except ContactScore.DoesNotExist:
            pass  # Contact not scored yet — skip

    logger.info("[LearningEngine] Frequency model updated %d contacts (phase=%s)",
                updated, phase)


def compute_sunset_scores():
    """
    2D: For each contact, compute sunset score (0-100).
    Updates ContactScore.sunset_score.
    """
    from database import OutcomeLog, ContactScore, Contact, ShopifyOrder

    phase = get_learning_phase()
    now = datetime.now()
    updated = 0

    # Get all contacts with a ContactScore
    scores = list(ContactScore.select())

    for score in scores:
        try:
            contact = Contact.get_by_id(score.contact_id)
        except Contact.DoesNotExist:
            continue

        outcomes = list(
            OutcomeLog.select()
            .where(OutcomeLog.contact == contact.id)
            .order_by(OutcomeLog.sent_at.desc())
        )

        if not outcomes:
            score.sunset_score = 0
            score.save()
            continue

        # Count consecutive non-engaged emails from most recent
        consecutive_misses = 0
        for o in outcomes:
            if o.opened or o.clicked or o.purchased:
                break
            consecutive_misses += 1

        # Factor in historical value
        total_revenue = sum(o.revenue for o in outcomes)
        lifetime_orders = (ShopifyOrder
                           .select()
                           .where(ShopifyOrder.email == contact.email)
                           .count())

        # Base sunset score
        base = min(consecutive_misses * 15, 80)

        # Value discount
        if total_revenue > 100:
            base *= 0.5
        elif lifetime_orders > 0:
            base *= 0.7

        # Time factor
        days_since_last = (now - outcomes[0].sent_at).days if outcomes[0].sent_at else 0
        if days_since_last > 30 and consecutive_misses >= 3:
            base += 20

        sunset_score = min(int(base), 100)

        # Re-engagement check: if contact recently engaged, reset
        if outcomes and (outcomes[0].opened or outcomes[0].clicked or outcomes[0].purchased):
            if score.sunset_executed:
                # Contact re-engaged after sunset — restore!
                score.sunset_score = 0
                score.sunset_executed = False
                score.sunset_executed_at = None
                contact.subscribed = True
                contact.save()
                score.save()
                logger.info("[LearningEngine] Contact %s re-engaged, sunset reversed",
                            contact.email)
                updated += 1
                continue

        score.sunset_score = sunset_score
        score.save()
        updated += 1

    logger.info("[LearningEngine] Sunset scores updated for %d contacts (phase=%s)",
                updated, phase)


def _check_regression():
    """
    Guardrail #10: If open_rate drops >25% week-over-week for 2 consecutive weeks,
    revert to observation phase.
    """
    from database import OutcomeLog

    now = datetime.now()
    week1_start = now - timedelta(days=7)
    week2_start = now - timedelta(days=14)

    # This week's open rate
    this_week = list(OutcomeLog.select()
                     .where(OutcomeLog.sent_at >= week1_start))
    if len(this_week) < 20:
        return  # Not enough data

    this_week_or = sum(1 for o in this_week if o.opened) / len(this_week)

    # Last week's open rate
    last_week = list(OutcomeLog.select()
                     .where(OutcomeLog.sent_at >= week2_start,
                            OutcomeLog.sent_at < week1_start))
    if len(last_week) < 20:
        return

    last_week_or = sum(1 for o in last_week if o.opened) / len(last_week)

    # Two weeks ago
    week3_start = now - timedelta(days=21)
    two_weeks_ago = list(OutcomeLog.select()
                         .where(OutcomeLog.sent_at >= week3_start,
                                OutcomeLog.sent_at < week2_start))
    if len(two_weeks_ago) < 20:
        return

    two_weeks_ago_or = sum(1 for o in two_weeks_ago if o.opened) / len(two_weeks_ago)

    # Check for consecutive 25%+ drops
    if last_week_or > 0 and two_weeks_ago_or > 0:
        drop_1 = (two_weeks_ago_or - last_week_or) / two_weeks_ago_or
        drop_2 = (last_week_or - this_week_or) / last_week_or

        if drop_1 > 0.25 and drop_2 > 0.25:
            logger.warning(
                "[LearningEngine] REGRESSION DETECTED: open rate dropped >25%% "
                "for 2 consecutive weeks (%.1f%% → %.1f%% → %.1f%%). "
                "Reverting to observation phase.",
                two_weeks_ago_or * 100, last_week_or * 100, this_week_or * 100
            )
            set_learning_phase_override("observation")
            # Also log to ActionLedger
            try:
                from database import ActionLedger
                ActionLedger.create(
                    trigger_type="learning",
                    source_type="regression_detection",
                    status="detected",
                    reason_code="RC_LEARNING_REGRESSION",
                    reason_detail=(
                        "Open rate dropped >25%% for 2 consecutive weeks: "
                        "%.1f%% → %.1f%% → %.1f%%. Reverted to observation."
                        % (two_weeks_ago_or * 100, last_week_or * 100,
                           this_week_or * 100)
                    ),
                )
            except Exception:
                pass


def seed_model_weights():
    """Insert baseline ModelWeights row if none exists."""
    from database import ModelWeights
    if ModelWeights.select().count() == 0:
        ModelWeights.create(
            recency_weight=0.40,
            frequency_weight=0.40,
            monetary_weight=0.20,
            phase="baseline",
        )
        logger.info("[LearningEngine] Seeded baseline model weights (0.40/0.40/0.20)")


def run_learning_engine():
    """
    Main entry point — called nightly at 5:30 AM by APScheduler.
    Analyzes OutcomeLog and computes all learned adjustments.
    """
    if not get_learning_enabled():
        logger.info("[LearningEngine] Skipped — learning disabled")
        return

    from database import init_db
    init_db()

    phase = get_learning_phase()
    logger.info("[LearningEngine] Starting analysis (phase=%s)...", phase)

    # Always compute scores (even in observation — data is just stored)
    compute_template_scoring()
    compute_action_effectiveness()

    # Frequency and sunset only modify ContactScore in conservative/active phases
    compute_optimal_frequency()
    compute_sunset_scores()

    # Regression check
    _check_regression()

    # Seed weights if first run
    seed_model_weights()

    logger.info("[LearningEngine] Analysis complete (phase=%s)", phase)
