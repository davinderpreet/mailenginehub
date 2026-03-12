"""
Action Ledger — append-only audit log for every automation decision.

Every send, skip, suppress, or failure is recorded with a machine-readable
reason code so that "nothing happened" always has an explanation.
"""

from datetime import datetime, date
from peewee import fn
from database import ActionLedger, db


# ── Reason code constants ────────────────────────────────────────────

RC_OK               = "ok"
RC_WARMUP_LIMIT     = "warmup_limit"
RC_COOLDOWN_ACTIVE  = "cooldown_active"       # freq cap or priority gate
RC_DUPLICATE        = "duplicate_trigger"
RC_UNSUBSCRIBED     = "unsubscribed"
RC_BOUNCED          = "bounced"
RC_SUPPRESSED_ENTRY = "suppressed_entry"       # on suppression list
RC_NO_STEP          = "no_step_found"
RC_NO_TEMPLATE      = "no_template"
RC_NO_CONTENT       = "no_content"
RC_NO_FLOW_MATCH    = "no_flow_match"
RC_SES_ERROR        = "ses_error"
RC_SANDBOX          = "sandbox_mode"
RC_MISSING_IDENTITY = "missing_identity"

# Flow exit reason codes — logged when a flow is auto-cancelled due to conversion
RC_EXIT_PURCHASE           = "flow_exit_purchase"            # customer placed an order
RC_EXIT_CHECKOUT_STARTED   = "flow_exit_checkout_started"    # customer started checkout
RC_EXIT_ORDER_COMPLETED    = "flow_exit_order_completed"     # order confirmed / fulfilled
RC_EXIT_MANUAL             = "flow_exit_manual"              # admin manually cancelled

# Identity resolution reason codes
RC_IDENTITY_RESOLVED    = "identity_resolved"                  # successful resolution
RC_IDENTITY_STITCHED    = "identity_stitched"                  # anonymous events linked
RC_IDENTITY_NEW_CONTACT = "identity_new_contact"               # new contact created from resolution
RC_IDENTITY_NO_OP       = "identity_no_op"                     # already resolved, no work needed
RC_IDENTITY_REPLAY      = "identity_trigger_replay"            # behavioral triggers replayed post-stitch
RC_WELCOME_POST_RESOLVE = "welcome_enrolled_after_resolution"  # welcome flow after identity resolution
RC_IDENTITY_REPLAY_SKIP = "identity_replay_skip"              # trigger evaluated but skipped (dedup/purchase)
RC_IDENTITY_PROBABLE    = "identity_probable_match"            # non-email identifier matched (cart/checkout token)
RC_IDENTITY_MULTI_STITCH = "identity_multi_stitch"             # multiple identifier types stitched in one call


# ── Core logging function ────────────────────────────────────────────

def log_action(contact, trigger_type, source_id, status, reason_code,
               source_type="", enrollment_id=0, step_id=0, template_id=0,
               subject="", preview_text="", html="", reason_detail="",
               priority=50, ses_message_id=""):
    """Create an ActionLedger entry. Called at every decision point.

    Args:
        contact:       Contact model instance (or None)
        trigger_type:  "flow" | "campaign" | "ai_plan"
        source_id:     Flow.id or Campaign.id
        status:        "detected" | "qualified" | "suppressed" | "queued"
                       | "rendered" | "sent" | "shadowed" | "failed"
        reason_code:   Machine-readable code (use RC_* constants)
        source_type:   Human-readable name (flow name, campaign name)
        enrollment_id: FlowEnrollment.id (0 for campaigns)
        step_id:       FlowStep.id (0 for campaigns)
        template_id:   EmailTemplate.id
        subject:       Email subject line
        preview_text:  Email preheader
        html:          Full rendered HTML (stored for shadow review)
        reason_detail: Human-readable explanation
        priority:      Send priority (10=checkout, 20=cart, 30=browse, 40=winback, 50=promo)
        ses_message_id: SES message ID (populated after successful send)

    Returns:
        ActionLedger instance
    """
    try:
        return ActionLedger.create(
            contact=contact,
            email=contact.email if contact else "",
            trigger_type=trigger_type,
            source_type=source_type,
            source_id=source_id,
            enrollment_id=enrollment_id,
            step_id=step_id,
            status=status,
            reason_code=reason_code,
            reason_detail=reason_detail,
            template_id=template_id,
            subject=subject,
            preview_text=preview_text,
            generated_html=html,
            ses_message_id=ses_message_id,
            priority=priority,
        )
    except Exception as e:
        # Ledger logging must never break the send pipeline
        import sys
        print("[ActionLedger] Error logging: %s" % e, file=sys.stderr)
        return None


def update_ledger_status(ledger_id, status, reason_code="", reason_detail="",
                         ses_message_id=""):
    """Update an existing ledger entry's status (e.g. queued → sent)."""
    if not ledger_id:
        return
    try:
        updates = {"status": status}
        if reason_code:
            updates["reason_code"] = reason_code
        if reason_detail:
            updates["reason_detail"] = reason_detail
        if ses_message_id:
            updates["ses_message_id"] = ses_message_id
        ActionLedger.update(**updates).where(ActionLedger.id == ledger_id).execute()
    except Exception:
        pass


# ── Dashboard query helpers ──────────────────────────────────────────

def get_today_stats():
    """Return dict of 8 stat counts for today."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    base = ActionLedger.select().where(ActionLedger.created_at >= today_start)

    return {
        "events_ingested":    base.count(),
        "triggers_detected":  base.count(),  # same as ingested — every entry starts as detected
        "qualified":          base.where(ActionLedger.status.in_(
                                  ["qualified", "queued", "rendered", "sent", "shadowed"])).count(),
        "suppressed":         base.where(ActionLedger.status == "suppressed").count(),
        "queued":             base.where(ActionLedger.status == "queued").count(),
        "shadowed":           base.where(ActionLedger.status == "shadowed").count(),
        "sent":               base.where(ActionLedger.status == "sent").count(),
        "failed":             base.where(ActionLedger.status == "failed").count(),
    }


def get_top_reasons(status, limit=10):
    """Return top reason_codes for a given status (e.g. 'suppressed')."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    rows = (ActionLedger
            .select(ActionLedger.reason_code, fn.COUNT(ActionLedger.id).alias("cnt"))
            .where(ActionLedger.status == status,
                   ActionLedger.created_at >= today_start,
                   ActionLedger.reason_code != "")
            .group_by(ActionLedger.reason_code)
            .order_by(fn.COUNT(ActionLedger.id).desc())
            .limit(limit)
            .dicts())
    return list(rows)


def get_recent_entries(page=1, per_page=50, contact_email=None,
                       trigger_type=None, status=None):
    """Return paginated ledger entries with optional filters."""
    query = ActionLedger.select().order_by(ActionLedger.created_at.desc())
    if contact_email:
        query = query.where(ActionLedger.email == contact_email)
    if trigger_type:
        query = query.where(ActionLedger.trigger_type == trigger_type)
    if status:
        query = query.where(ActionLedger.status == status)
    total = query.count()
    entries = list(query.paginate(page, per_page).dicts())
    return {"entries": entries, "total": total, "page": page, "per_page": per_page}
