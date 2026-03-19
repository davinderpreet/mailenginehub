"""
outcome_tracker.py — Nightly outcome collection for the self-learning layer.
Runs at 5:00 AM. Records what happened after each email was sent.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _get_learning_enabled():
    """Check the emergency kill switch (DB-backed, no restart needed)."""
    from learning_config import get_learning_enabled
    return get_learning_enabled()


def _get_send_gap_hours(contact_id, sent_at):
    """Compute hours since the previous email to this contact."""
    from database import OutcomeLog
    prev = (OutcomeLog
            .select(OutcomeLog.sent_at)
            .where(OutcomeLog.contact == contact_id,
                   OutcomeLog.sent_at < sent_at)
            .order_by(OutcomeLog.sent_at.desc())
            .first())
    if prev and prev.sent_at:
        delta = (sent_at - prev.sent_at).total_seconds() / 3600
        return round(delta, 2)
    return None


def _attribute_purchase(contact, sent_at, window_hours=72):
    """
    Last-touch attribution: find purchases within window_hours of sent_at.
    Returns (purchased: bool, revenue: float, hours_to_purchase: float|None).
    """
    from database import ShopifyOrder
    cutoff = sent_at + timedelta(hours=window_hours)

    orders = list(
        ShopifyOrder
        .select()
        .where(
            ShopifyOrder.email == contact.email,
            ShopifyOrder.ordered_at >= sent_at,
            ShopifyOrder.ordered_at <= cutoff,
        )
        .order_by(ShopifyOrder.ordered_at.asc())
    )

    if not orders:
        return False, 0.0, None

    total_revenue = 0.0
    first_purchase_at = None
    for order in orders:
        try:
            total_revenue += float(order.order_total or 0)
        except (ValueError, TypeError):
            pass
        if first_purchase_at is None:
            first_purchase_at = order.ordered_at

    hours_to_purchase = None
    if first_purchase_at:
        hours_to_purchase = round(
            (first_purchase_at - sent_at).total_seconds() / 3600, 2
        )

    return True, round(total_revenue, 2), hours_to_purchase


def _get_contact_segment(contact_id):
    """Get the contact's current RFM segment from ContactScore."""
    from database import ContactScore
    try:
        score = ContactScore.get(ContactScore.contact == contact_id)
        return score.rfm_segment
    except ContactScore.DoesNotExist:
        return "unknown"


def _get_action_type(contact_id):
    """Get the most recent action_type from MessageDecision."""
    from database import MessageDecision
    try:
        md = MessageDecision.get(MessageDecision.contact == contact_id)
        return md.action_type
    except Exception:
        return ""


def _process_campaign_emails(lookback_hours=48):
    """Process CampaignEmail rows from the last lookback_hours."""
    from database import (CampaignEmail, Campaign, Contact, OutcomeLog,
                          EmailTemplate)
    from peewee import IntegrityError

    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    processed = 0
    errors = 0

    # Get sent campaign emails in the lookback window
    emails = list(
        CampaignEmail
        .select(CampaignEmail, Campaign)
        .join(Campaign)
        .where(
            CampaignEmail.status == "sent",
            CampaignEmail.created_at >= cutoff,
        )
    )

    for ce in emails:
        try:
            contact = Contact.get_by_id(ce.contact_id)
            sent_at = ce.created_at  # CampaignEmail has no sent_at; use created_at

            # Compute outcomes
            opened = bool(ce.opened)
            clicked = bool(ce.clicked)
            hours_to_open = None
            if opened and ce.opened_at and sent_at:
                hours_to_open = round(
                    (ce.opened_at - sent_at).total_seconds() / 3600, 2
                )

            # Check if unsubscribed AFTER this email was sent
            unsubscribed = (not contact.subscribed and
                            sent_at and contact.created_at and
                            contact.created_at < sent_at)
            purchased, revenue, hours_to_purchase = _attribute_purchase(
                contact, sent_at
            )

            # Get context
            segment = _get_contact_segment(contact.id)
            action_type = _get_action_type(contact.id)
            template_id = ce.campaign.template_id if ce.campaign else 0
            subject_line = ""
            try:
                tpl = EmailTemplate.get_by_id(template_id)
                subject_line = tpl.subject or ""
            except Exception:
                pass

            send_gap = _get_send_gap_hours(contact.id, sent_at)

            # Upsert OutcomeLog
            OutcomeLog.insert(
                email_type="campaign",
                email_id=ce.id,
                contact=contact.id,
                template_id=template_id,
                action_type=action_type,
                segment=segment,
                opened=opened,
                clicked=clicked,
                purchased=purchased,
                unsubscribed=unsubscribed,
                revenue=revenue,
                hours_to_open=hours_to_open,
                hours_to_purchase=hours_to_purchase,
                sent_at=sent_at,
                subject_line=subject_line[:200],
                send_gap_hours=send_gap,
            ).on_conflict(
                conflict_target=[OutcomeLog.email_type, OutcomeLog.email_id],
                update={
                    OutcomeLog.opened: opened,
                    OutcomeLog.clicked: clicked,
                    OutcomeLog.purchased: purchased,
                    OutcomeLog.unsubscribed: unsubscribed,
                    OutcomeLog.revenue: revenue,
                    OutcomeLog.hours_to_open: hours_to_open,
                    OutcomeLog.hours_to_purchase: hours_to_purchase,
                },
            ).execute()
            processed += 1

        except Exception as e:
            logger.error("OutcomeTracker campaign email %s error: %s", ce.id, e)
            errors += 1

    return processed, errors


def _process_flow_emails(lookback_hours=48):
    """Process FlowEmail rows from the last lookback_hours."""
    from database import (FlowEmail, FlowStep, Contact, OutcomeLog,
                          EmailTemplate)

    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    processed = 0
    errors = 0

    emails = list(
        FlowEmail
        .select(FlowEmail, FlowStep)
        .join(FlowStep)
        .where(
            FlowEmail.status == "sent",
            FlowEmail.sent_at >= cutoff,
        )
    )

    for fe in emails:
        try:
            contact = Contact.get_by_id(fe.contact_id)
            sent_at = fe.sent_at

            opened = bool(fe.opened)
            clicked = bool(getattr(fe, 'clicked', False))
            hours_to_open = None
            if opened and fe.opened_at and sent_at:
                hours_to_open = round(
                    (fe.opened_at - sent_at).total_seconds() / 3600, 2
                )

            unsubscribed = (not contact.subscribed and
                            sent_at and contact.created_at and
                            contact.created_at < sent_at)
            purchased, revenue, hours_to_purchase = _attribute_purchase(
                contact, sent_at
            )

            segment = _get_contact_segment(contact.id)
            action_type = _get_action_type(contact.id)
            template_id = fe.step.template_id if fe.step else 0
            subject_line = ""
            try:
                tpl = EmailTemplate.get_by_id(template_id)
                subject_line = tpl.subject or ""
            except Exception:
                pass

            send_gap = _get_send_gap_hours(contact.id, sent_at)

            OutcomeLog.insert(
                email_type="flow",
                email_id=fe.id,
                contact=contact.id,
                template_id=template_id,
                action_type=action_type,
                segment=segment,
                opened=opened,
                clicked=clicked,
                purchased=purchased,
                unsubscribed=unsubscribed,
                revenue=revenue,
                hours_to_open=hours_to_open,
                hours_to_purchase=hours_to_purchase,
                sent_at=sent_at,
                subject_line=subject_line[:200],
                send_gap_hours=send_gap,
            ).on_conflict(
                conflict_target=[OutcomeLog.email_type, OutcomeLog.email_id],
                update={
                    OutcomeLog.opened: opened,
                    OutcomeLog.clicked: clicked,
                    OutcomeLog.purchased: purchased,
                    OutcomeLog.unsubscribed: unsubscribed,
                    OutcomeLog.revenue: revenue,
                    OutcomeLog.hours_to_open: hours_to_open,
                    OutcomeLog.hours_to_purchase: hours_to_purchase,
                },
            ).execute()
            processed += 1

        except Exception as e:
            logger.error("OutcomeTracker flow email %s error: %s", fe.id, e)
            errors += 1

    return processed, errors


def _process_auto_emails(lookback_hours=48):
    """Process AutoEmail rows from the last lookback_hours."""
    from database import (AutoEmail, Contact, OutcomeLog, EmailTemplate)

    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    processed = 0
    errors = 0

    emails = list(
        AutoEmail
        .select()
        .where(
            AutoEmail.status == "sent",
            AutoEmail.sent_at >= cutoff,
        )
    )

    for ae in emails:
        try:
            contact = Contact.get_by_id(ae.contact_id)
            sent_at = ae.sent_at

            opened = bool(ae.opened)
            clicked = bool(ae.clicked)
            hours_to_open = None
            if opened and ae.opened_at and sent_at:
                hours_to_open = round(
                    (ae.opened_at - sent_at).total_seconds() / 3600, 2
                )

            unsubscribed = (not contact.subscribed and
                            sent_at and contact.created_at and
                            contact.created_at < sent_at)
            purchased, revenue, hours_to_purchase = _attribute_purchase(
                contact, sent_at
            )

            segment = _get_contact_segment(contact.id)
            action_type = _get_action_type(contact.id)
            template_id = ae.template_id if ae.template_id else 0
            subject_line = ae.subject or ""
            if not subject_line:
                try:
                    tpl = EmailTemplate.get_by_id(template_id)
                    subject_line = tpl.subject or ""
                except Exception:
                    pass

            send_gap = _get_send_gap_hours(contact.id, sent_at)

            OutcomeLog.insert(
                email_type="auto",
                email_id=ae.id,
                contact=contact.id,
                template_id=template_id,
                action_type=action_type,
                segment=segment,
                opened=opened,
                clicked=clicked,
                purchased=purchased,
                unsubscribed=unsubscribed,
                revenue=revenue,
                hours_to_open=hours_to_open,
                hours_to_purchase=hours_to_purchase,
                sent_at=sent_at,
                subject_line=subject_line[:200],
                send_gap_hours=send_gap,
            ).on_conflict(
                conflict_target=[OutcomeLog.email_type, OutcomeLog.email_id],
                update={
                    OutcomeLog.opened: opened,
                    OutcomeLog.clicked: clicked,
                    OutcomeLog.purchased: purchased,
                    OutcomeLog.unsubscribed: unsubscribed,
                    OutcomeLog.revenue: revenue,
                    OutcomeLog.hours_to_open: hours_to_open,
                    OutcomeLog.hours_to_purchase: hours_to_purchase,
                },
            ).execute()
            processed += 1

        except Exception as e:
            logger.error("OutcomeTracker auto email %s error: %s", ae.id, e)
            errors += 1

    return processed, errors


def run_outcome_tracker():
    """
    Main entry point — called nightly at 5:00 AM by APScheduler.
    Collects outcomes for emails sent in the last 48 hours.
    Re-checks 72-hour purchase window for emails sent 48-72h ago.
    """
    if not _get_learning_enabled():
        logger.info("[OutcomeTracker] Skipped — learning disabled")
        return

    from database import init_db
    init_db()

    logger.info("[OutcomeTracker] Starting outcome collection...")

    # Process last 48h of campaign emails
    camp_ok, camp_err = _process_campaign_emails(lookback_hours=48)

    # Process last 48h of flow emails
    flow_ok, flow_err = _process_flow_emails(lookback_hours=48)

    # Process last 48h of auto-pilot emails
    auto_ok, auto_err = _process_auto_emails(lookback_hours=48)

    # Re-check 72h window for purchase attribution on older emails
    camp_ok2, _ = _process_campaign_emails(lookback_hours=72)
    flow_ok2, _ = _process_flow_emails(lookback_hours=72)
    auto_ok2, _ = _process_auto_emails(lookback_hours=72)

    logger.info(
        "[OutcomeTracker] Done — campaigns: %d processed (%d errors), "
        "flows: %d processed (%d errors), "
        "auto: %d processed (%d errors), "
        "72h re-check: %d campaign, %d flow, %d auto",
        camp_ok, camp_err, flow_ok, flow_err, auto_ok, auto_err,
        camp_ok2, flow_ok2, auto_ok2
    )
