"""
Delivery Engine — separates action generation from actual sending.

Emails are enqueued into DeliveryQueue by the flow processor and campaign
sender. The queue processor runs every 30 seconds and drains the queue,
respecting warmup limits and delivery mode (live/shadow/sandbox).
"""

import os
import sys
from datetime import datetime, date, timedelta
from database import (
    DeliveryQueue, ActionLedger, SystemConfig,
    WarmupConfig, FlowEmail, CampaignEmail,
    FlowEnrollment, FlowStep, Flow, Contact,
    ContactScore, AutoEmail, db, get_system_config,
)
from action_ledger import update_ledger_status


# ── Priority mapping ─────────────────────────────────────────────────

TRIGGER_PRIORITY = {
    "checkout_abandoned": 10,
    "cart_abandonment":   20,
    "browse_abandonment": 30,
    "no_purchase_days":   40,
    "order_placed":       40,
    "contact_created":    50,
    "tag_added":          50,
    "manual":             50,
    "campaign":           50,
}


def get_priority_for_trigger(trigger_type):
    """Map a trigger type to a queue priority (lower = higher priority)."""
    return TRIGGER_PRIORITY.get(trigger_type, 50)


# ── Warmup phases (must match app.py WARMUP_PHASES) ─────────────────

WARMUP_PHASES = {
    1: {"label": "Ignition",      "daily_limit": 50,     "days": 3},
    2: {"label": "Spark",         "daily_limit": 150,    "days": 4},
    3: {"label": "Gaining Trust", "daily_limit": 350,    "days": 7},
    4: {"label": "Building",      "daily_limit": 750,    "days": 7},
    5: {"label": "Momentum",      "daily_limit": 1500,   "days": 7},
    6: {"label": "Scaling",       "daily_limit": 3000,   "days": 7},
    7: {"label": "High Volume",   "daily_limit": 7000,   "days": 7},
    8: {"label": "Full Send",     "daily_limit": 999999, "days": 99},
}


def _get_warmup_remaining():
    """Return how many more emails can be sent today under warmup limits.
    Returns None if warmup is disabled (unlimited)."""
    try:
        warmup = WarmupConfig.get_by_id(1)
    except WarmupConfig.DoesNotExist:
        return None

    if not warmup.is_active:
        return None

    # Reset daily counter if new day
    today_str = date.today().isoformat()
    if warmup.last_reset_date != today_str:
        warmup.emails_sent_today = 0
        warmup.last_reset_date = today_str
        warmup.save()

    phase_info = WARMUP_PHASES.get(warmup.current_phase, WARMUP_PHASES[8])
    remaining = phase_info["daily_limit"] - warmup.emails_sent_today
    return max(remaining, 0)


def _increment_warmup_counter():
    """Increment the warmup daily counter after a successful send."""
    try:
        warmup = WarmupConfig.get_by_id(1)
        if warmup.is_active:
            warmup.emails_sent_today += 1
            warmup.save()
    except Exception:
        pass


# ── Enqueue ──────────────────────────────────────────────────────────

def enqueue_email(contact, email_type, source_id, enrollment_id, step_id,
                  template_id, from_name, from_email, subject, html,
                  unsubscribe_url, priority, ledger_id, campaign_id=0,
                  scheduled_at=None, auto_email_id=0):
    """Add an email to the delivery queue and update the linked ledger entry.

    Args:
        contact:        Contact instance
        email_type:     "flow" | "campaign"
        source_id:      Flow.id or Campaign.id
        enrollment_id:  FlowEnrollment.id (0 for campaigns)
        step_id:        FlowStep.id (0 for campaigns)
        template_id:    EmailTemplate.id
        from_name:      Sender display name
        from_email:     Sender email address
        subject:        Email subject (already personalized)
        html:           Fully rendered HTML (already personalized)
        unsubscribe_url: Unsubscribe URL for this contact
        priority:       Queue priority (10=highest)
        ledger_id:      ActionLedger.id to link
        campaign_id:    Campaign.id for CampaignEmail backward compat
        auto_email_id:  AutoEmail.id if already created (auto-pilot sends)

    Returns:
        DeliveryQueue instance
    """
    _create_kwargs = dict(
        contact=contact,
        email=contact.email,
        email_type=email_type,
        source_id=source_id,
        enrollment_id=enrollment_id,
        step_id=step_id,
        template_id=template_id,
        from_name=from_name,
        from_email=from_email,
        subject=subject,
        html=html,
        unsubscribe_url=unsubscribe_url,
        priority=priority,
        status="queued",
        ledger_id=ledger_id,
        campaign_id=campaign_id,
    )
    if scheduled_at:
        _create_kwargs["scheduled_at"] = scheduled_at
    if auto_email_id:
        _create_kwargs["auto_email_id"] = auto_email_id
    item = DeliveryQueue.create(**_create_kwargs)

    # Update ledger to "queued"
    update_ledger_status(ledger_id, "queued")

    return item


# ── Queue processor ──────────────────────────────────────────────────

def process_queue():
    """Drain the delivery queue. Called every 30 seconds by the scheduler.

    - In shadow/sandbox mode: mark all queued items as shadowed (no SES calls)
    - In live mode: send up to warmup remaining, highest priority first
    """
    cfg = get_system_config()
    mode = cfg.delivery_mode  # live | shadow | sandbox

    # Get all queued items ready to send (skip future-scheduled items)
    _now = datetime.now()
    queued = list(
        DeliveryQueue.select()
        .where(
            DeliveryQueue.status == "queued",
            (DeliveryQueue.scheduled_at.is_null()) | (DeliveryQueue.scheduled_at <= _now)
        )
        .order_by(DeliveryQueue.priority.asc(), DeliveryQueue.created_at.asc())
    )

    if not queued:
        return 0

    if mode in ("shadow", "sandbox"):
        return _process_shadow(queued, mode)
    else:
        return _process_live(queued)


def _process_shadow(queued, mode):
    """Shadow/sandbox: mark all items as shadowed, create compat records."""
    reason = "shadow_mode" if mode == "shadow" else "sandbox_mode"
    processed = 0

    for item in queued:
        try:
            # Update queue item
            item.status = "shadowed"
            item.sent_at = datetime.now()
            item.save()

            # Update ledger
            update_ledger_status(item.ledger_id, "shadowed", reason_code=reason)

            # Create backward-compat records so existing dashboards still work
            _create_compat_record(item, status="shadowed")

            # Advance flow enrollment to next step (even in shadow mode)
            if item.email_type == "flow" and item.enrollment_id:
                _advance_flow_enrollment(item.enrollment_id, item.step_id, item.source_id)

            processed += 1
        except Exception as e:
            print("[DeliveryQueue] Shadow error for item %d: %s" % (item.id, e), file=sys.stderr)

    return processed


def _process_live(queued):
    """Live mode: send up to warmup limit via SES.
    Flow emails (triggered by customer action) bypass warmup limits so
    discount codes and time-sensitive messages always reach the customer.
    """
    remaining = _get_warmup_remaining()

    # Import the actual send function
    try:
        from email_sender import send_campaign_email
    except ImportError:
        print("[DeliveryQueue] Cannot import email_sender", file=sys.stderr)
        return 0

    # ── Separate flow emails (bypass warmup) from bulk emails (respect warmup) ──
    _flow_queue = []
    _bulk_queue = []
    _sunset_suppressed = 0
    for _item in queued:
        try:
            if _item.contact:
                _cs = ContactScore.get_or_none(ContactScore.contact == _item.contact)
                if _cs and _cs.sunset_score is not None:
                    if _cs.sunset_score >= 85:
                        _item.status = "suppressed"
                        _item.sent_at = datetime.now()
                        _item.error_msg = "sunset_score=%d" % _cs.sunset_score
                        _item.save()
                        update_ledger_status(_item.ledger_id, "suppressed",
                                             reason_code="sunset_suppression",
                                             reason_detail="sunset_score=%d >= 85" % _cs.sunset_score)
                        _sunset_suppressed += 1
                        continue
        except Exception:
            pass
        # Flow emails bypass warmup — they're triggered by customer action
        if _item.email_type == "flow":
            _flow_queue.append(_item)
        else:
            _bulk_queue.append(_item)

    if _sunset_suppressed:
        print("[DeliveryQueue] Sunset-suppressed %d contacts (score >= 85)" % _sunset_suppressed, file=sys.stderr)

    # ── Send ALL flow emails first (no warmup cap) ──
    processed = 0
    for item in _flow_queue:
        processed += _send_one(item, send_campaign_email)

    # ── Send bulk emails up to warmup remaining ──
    bulk_sent = 0
    if remaining is not None and remaining <= 0:
        pass  # warmup cap hit — skip bulk but flow emails already sent above
    else:
        for item in _bulk_queue:
            if remaining is not None and bulk_sent >= remaining:
                break
            if _send_one(item, send_campaign_email):
                bulk_sent += 1
            processed += 1

    return processed


def _send_one(item, send_fn):
    """Send a single queue item via SES. Returns 1 on success, 0 on failure."""
    try:
        item.status = "sending"
        item.save()

        success, error, msg_id = send_fn(
            to_email=item.email,
            to_name="",
            from_email=item.from_email,
            from_name=item.from_name,
            subject=item.subject,
            html_body=item.html,
            unsubscribe_url=item.unsubscribe_url,
            campaign_id=item.campaign_id or None,
            template_id=item.template_id if item.email_type == "auto" else None,
        )

        if success:
            item.status = "sent"
            item.sent_at = datetime.now()
            item.save()
            update_ledger_status(item.ledger_id, "sent", ses_message_id=msg_id or "")
            _create_compat_record(item, status="sent")
            if item.email_type == "auto" and msg_id and item.auto_email_id:
                try:
                    AutoEmail.update(ses_message_id=msg_id).where(
                        AutoEmail.id == item.auto_email_id
                    ).execute()
                except Exception:
                    pass
            _increment_warmup_counter()
            _increment_contact_counters(item.contact_id if item.contact else None)

            # Advance flow enrollment
            if item.email_type == "flow" and item.enrollment_id:
                _advance_flow_enrollment(item.enrollment_id, item.step_id, item.source_id)
            return 1
        else:
            item.status = "failed"
            item.error_msg = error or "Unknown error"
            item.sent_at = datetime.now()
            item.save()
            update_ledger_status(item.ledger_id, "failed", reason_code="ses_error",
                                 reason_detail=error or "")
            _create_compat_record(item, status="failed", error_msg=error or "")
            return 0
    except Exception as e:
        item.status = "failed"
        item.error_msg = str(e)
        item.save()
        update_ledger_status(item.ledger_id, "failed", reason_code="ses_error",
                             reason_detail=str(e))
        print("[DeliveryQueue] Send error for item %d: %s" % (item.id, e), file=sys.stderr)
        return 0


# ── Helpers ──────────────────────────────────────────────────────────

def _create_compat_record(item, status, error_msg=""):
    """Create FlowEmail or CampaignEmail record for backward compatibility
    with existing dashboards (campaign detail, flow stats, sent emails page)."""
    try:
        if item.email_type == "flow" and item.enrollment_id:
            FlowEmail.create(
                enrollment=item.enrollment_id,
                step=item.step_id,
                contact=item.contact,
                status=status if status != "shadowed" else "sent",  # show shadowed as sent in flow stats
            )
        elif item.email_type == "campaign" and item.campaign_id:
            CampaignEmail.create(
                campaign=item.campaign_id,
                contact=item.contact,
                status=status if status != "shadowed" else "sent",
                error_msg=error_msg,
            )
        elif item.email_type == "auto" and item.template_id:
            if item.auto_email_id:
                # AutoEmail was pre-created during scheduling — just update its status
                try:
                    AutoEmail.update(
                        status=status if status != "shadowed" else "sent",
                        error_msg=error_msg,
                    ).where(AutoEmail.id == item.auto_email_id).execute()
                except Exception:
                    pass
            else:
                ae = AutoEmail.create(
                    contact=item.contact,
                    template=item.template_id,
                    subject=item.subject or "",
                    status=status if status != "shadowed" else "sent",
                    error_msg=error_msg,
                    ses_message_id="",  # will be updated below
                    auto_run_date=datetime.now().date(),
                )
                # Store the auto_email_id on the queue item for tracking URL generation
                try:
                    item.auto_email_id = ae.id
                    item.save()
                except Exception:
                    pass
    except Exception as e:
        print("[DeliveryQueue] Compat record error: %s" % e, file=sys.stderr)


def _advance_flow_enrollment(enrollment_id, step_id, flow_id):
    """Advance a flow enrollment to its next step, or mark completed."""
    try:
        enrollment = FlowEnrollment.get_by_id(enrollment_id)
        next_step = (FlowStep.select()
                     .where(FlowStep.flow == flow_id,
                            FlowStep.step_order == enrollment.current_step + 1)
                     .first())
        if next_step:
            enrollment.current_step = next_step.step_order
            enrollment.next_send_at = datetime.now() + timedelta(hours=next_step.delay_hours)
            enrollment.save()
        else:
            enrollment.status = "completed"
            enrollment.save()
            # Resume any flows that were paused by this one
            _resume_paused(flow_id)
            # Hand over to AI Account Manager if no more active flows
            try:
                from account_manager import maybe_handover_from_flow
                maybe_handover_from_flow(enrollment.contact)
            except Exception as e:
                print("[DeliveryQueue] AM handover error: %s" % e, file=sys.stderr)
    except Exception as e:
        print("[DeliveryQueue] Enrollment advance error: %s" % e, file=sys.stderr)


def _resume_paused(completed_flow_id):
    """Resume enrollments that were paused by the completed flow."""
    try:
        paused = (FlowEnrollment.select()
                  .where(FlowEnrollment.paused_by_flow == completed_flow_id,
                         FlowEnrollment.status == "paused"))
        for enrollment in paused:
            enrollment.status = "active"
            enrollment.paused_by_flow = 0
            enrollment.next_send_at = datetime.now()
            enrollment.save()
    except Exception:
        pass


def _increment_contact_counters(contact_id):
    """Increment the 7d and 30d email counters on a contact."""
    if not contact_id:
        return
    try:
        Contact.update(
            emails_received_7d=Contact.emails_received_7d + 1,
            emails_received_30d=Contact.emails_received_30d + 1,
        ).where(Contact.id == contact_id).execute()
    except Exception:
        pass


# ── Queue status helpers ─────────────────────────────────────────────

def get_queue_stats():
    """Return queue status counts."""
    from peewee import fn
    rows = (DeliveryQueue
            .select(DeliveryQueue.status, fn.COUNT(DeliveryQueue.id).alias("cnt"))
            .group_by(DeliveryQueue.status)
            .dicts())
    return {r["status"]: r["cnt"] for r in rows}


def get_queue_items(status="queued", limit=50):
    """Return queue items for a given status."""
    return list(
        DeliveryQueue.select()
        .where(DeliveryQueue.status == status)
        .order_by(DeliveryQueue.priority.asc(), DeliveryQueue.created_at.asc())
        .limit(limit)
        .dicts()
    )
