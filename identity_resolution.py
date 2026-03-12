"""
Identity Resolution — canonical service for turning anonymous activity into known customer journeys.

Every identity entry point (popup subscribe, /api/identify, /api/track, Shopify webhooks,
email link clicks) calls resolve_identity(). No other file should contain inline session
stitching logic.

Functions:
    resolve_identity()            — single canonical entry point
    _promote_visitor_to_contact() — find or create Contact + CustomerProfile
    _stitch_anonymous_activity()  — link anonymous CustomerActivity rows to a contact
    _evaluate_welcome_eligibility() — enroll in welcome flow if first meaningful resolution
    _replay_behavioral_triggers() — re-evaluate browse/cart/checkout triggers post-stitch
"""

import logging
import threading
from datetime import datetime, timedelta

from database import (
    Contact, CustomerActivity, CustomerProfile, FlowEnrollment,
    Flow, FlowStep, PendingTrigger, AbandonedCheckout, ActionLedger,
)
from action_ledger import (
    log_action,
    RC_IDENTITY_RESOLVED, RC_IDENTITY_STITCHED, RC_IDENTITY_NEW_CONTACT,
    RC_IDENTITY_NO_OP, RC_IDENTITY_REPLAY, RC_WELCOME_POST_RESOLVE,
)

logger = logging.getLogger("identity_resolution")


# ── Main entry point ────────────────────────────────────────────────

def resolve_identity(
    email,
    session_id="",
    shopify_id="",
    source="unknown",
    first_name="",
    last_name="",
    phone="",
    subscribe=False,
    create_if_missing=True,
):
    """
    Canonical identity resolution. All identity entry points call this.

    Args:
        email:            Email address (required, will be lowercased/stripped)
        session_id:       Browser session ID from localStorage (for stitching)
        shopify_id:       Shopify customer ID (from webhooks)
        source:           Origin of this resolution (popup_subscribe, pixel_identify,
                          email_click, shopify_checkout, shopify_order, shopify_customer, api_track)
        first_name:       Contact first name (backfills if empty)
        last_name:        Contact last name (backfills if empty)
        phone:            Contact phone (backfills if empty)
        subscribe:        True only for popup_subscribe (sets subscribed=True)
        create_if_missing: Create Contact if email not found (False for passive events)

    Returns:
        dict with keys:
            contact          — Contact instance or None
            created          — bool, True if new contact was created
            stitched         — int, number of anonymous events linked
            already_resolved — bool, True if no work was needed
            welcome_enrolled — bool, True if enrolled in welcome flow
            triggers_replayed — list of trigger_type strings replayed
            confidence       — "exact" | "anonymous_only"
            identifiers_matched — list of identifier types used
    """
    result = {
        "contact": None,
        "created": False,
        "stitched": 0,
        "already_resolved": False,
        "welcome_enrolled": False,
        "triggers_replayed": [],
        "confidence": "anonymous_only",
        "identifiers_matched": [],
    }

    # ── 1. Validate email ──
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        logger.debug("resolve_identity: no valid email, returning early")
        result["already_resolved"] = True
        return result

    result["identifiers_matched"].append("email")
    result["confidence"] = "exact"

    if session_id:
        result["identifiers_matched"].append("session_id")
    if shopify_id:
        result["identifiers_matched"].append("shopify_id")

    # ── 2. Find or create Contact ──
    contact, created = _promote_visitor_to_contact(
        email=email,
        source=source,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        shopify_id=shopify_id,
        subscribe=subscribe,
        create_if_missing=create_if_missing,
    )

    result["contact"] = contact
    result["created"] = created

    if not contact:
        # Contact not found and create_if_missing=False
        result["already_resolved"] = True
        _log_identity_event(None, email, source, "identity_no_op",
                            RC_IDENTITY_NO_OP,
                            "Contact not found and create_if_missing=False",
                            0, result["identifiers_matched"])
        return result

    # ── 3. Stitch anonymous activity ──
    stitched = 0
    if session_id:
        stitched = _stitch_anonymous_activity(
            email=email,
            contact_id=contact.id,
            session_id=session_id,
            source=source,
        )
    result["stitched"] = stitched

    # ── 4. Audit log ──
    if created:
        _log_identity_event(contact, email, source, "identity_new_contact",
                            RC_IDENTITY_NEW_CONTACT,
                            "New contact created via %s (stitched %d events)" % (source, stitched),
                            stitched, result["identifiers_matched"])
    elif stitched > 0:
        _log_identity_event(contact, email, source, "identity_stitched",
                            RC_IDENTITY_STITCHED,
                            "Stitched %d anonymous events via %s" % (stitched, source),
                            stitched, result["identifiers_matched"])
    else:
        result["already_resolved"] = True
        # Only log no-op for explicit identity calls, not every track call
        if source in ("pixel_identify", "popup_subscribe", "email_click"):
            _log_identity_event(contact, email, source, "identity_no_op",
                                RC_IDENTITY_NO_OP,
                                "No anonymous events to stitch",
                                0, result["identifiers_matched"])

    # ── 5. Welcome flow eligibility ──
    welcome_enrolled = _evaluate_welcome_eligibility(
        contact=contact,
        created=created,
        source=source,
        subscribe=subscribe,
    )
    result["welcome_enrolled"] = welcome_enrolled

    # ── 6. Background: replay triggers + enrich profile + cascade ──
    if stitched > 0 or created:
        _email_copy = email
        _contact_id = contact.id
        _source_copy = source

        def _background_work():
            try:
                # Replay behavioral triggers for newly-stitched activity
                replayed = _replay_behavioral_triggers(_contact_id, _email_copy, _source_copy)
                # Note: replayed list not returned to caller (async)

                # Profile enrichment
                try:
                    from activity_sync import enrich_single_profile
                    enrich_single_profile(_email_copy)
                except Exception as e:
                    logger.warning("Identity resolution: enrichment failed for %s: %s" % (_email_copy, e))

                # Intelligence cascade (2s delay so enrichment finishes first)
                import time
                time.sleep(2)
                try:
                    from cascade import cascade_contact
                    cascade_contact(_contact_id, trigger="identity_%s" % _source_copy)
                except Exception as e:
                    logger.warning("Identity resolution: cascade failed for %s: %s" % (_email_copy, e))

            except Exception as e:
                logger.error("Identity resolution background work failed for %s: %s" % (_email_copy, e))

        threading.Thread(target=_background_work, daemon=True).start()

    logger.info(
        "resolve_identity: %s via %s (created=%s, stitched=%d, welcome=%s)"
        % (email, source, created, stitched, welcome_enrolled)
    )

    return result


# ── Internal functions ──────────────────────────────────────────────

def _promote_visitor_to_contact(email, source, first_name="", last_name="",
                                 phone="", shopify_id="", subscribe=False,
                                 create_if_missing=True):
    """
    Find or create a Contact for the given email.

    Returns:
        (Contact, created_bool) — Contact may be None if not found and create_if_missing=False
    """
    contact = Contact.get_or_none(Contact.email == email)

    if contact:
        # Backfill empty fields from new data
        changed = False
        if shopify_id and not contact.shopify_id:
            contact.shopify_id = shopify_id
            changed = True
        if first_name and not contact.first_name:
            contact.first_name = first_name
            changed = True
        if last_name and not contact.last_name:
            contact.last_name = last_name
            changed = True
        if phone and not contact.phone:
            contact.phone = phone
            changed = True
        if subscribe and not contact.subscribed:
            contact.subscribed = True
            changed = True
            logger.info("Identity resolution: re-subscribed %s via %s" % (email, source))
        if changed:
            contact.save()
        return contact, False

    if not create_if_missing:
        return None, False

    # Create new contact
    try:
        contact = Contact.create(
            email=email,
            source=source if source != "api_track" else "pixel_capture",
            subscribed=subscribe,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            shopify_id=shopify_id,
            created_at=datetime.now(),
        )
        # Create CustomerProfile stub
        CustomerProfile.get_or_create(
            contact=contact,
            defaults={"email": email, "last_computed_at": datetime.now()}
        )
        logger.info("Identity resolution: new contact %s via %s" % (email, source))
        return contact, True
    except Exception as e:
        # Unique constraint — contact created by a race condition
        logger.warning("Identity resolution: contact create race for %s: %s" % (email, e))
        contact = Contact.get_or_none(Contact.email == email)
        return contact, False


def _stitch_anonymous_activity(email, contact_id, session_id, source):
    """
    Link anonymous CustomerActivity rows to a known contact.
    Uses stitched_at IS NULL guard to prevent re-stitching.

    Returns:
        int — number of rows stitched
    """
    if not session_id:
        return 0

    now = datetime.now()
    count = (
        CustomerActivity.update(
            email=email,
            contact_id=contact_id,
            stitched_at=now,
            stitched_by=source,
        )
        .where(
            CustomerActivity.session_id == session_id,
            CustomerActivity.email == "",
            CustomerActivity.stitched_at.is_null(),
        )
        .execute()
    )

    if count > 0:
        logger.info("Stitched %d anonymous events (session %s) to %s via %s"
                     % (count, session_id[:8], email, source))

    return count


def _evaluate_welcome_eligibility(contact, created, source, subscribe):
    """
    Enroll contact in welcome flow if this is their first meaningful identity resolution
    and they are subscribable. Not gated by "who created the contact."

    Returns:
        bool — True if enrolled
    """
    # Only enroll for subscription-type sources
    if not subscribe and not (source == "shopify_customer" and created):
        return False

    if not contact or not contact.subscribed:
        return False

    # Check if already enrolled in any active contact_created flow
    try:
        welcome_flows = list(Flow.select().where(
            Flow.is_active == True,
            Flow.trigger_type == "contact_created",
        ))

        for wf in welcome_flows:
            already = FlowEnrollment.select().where(
                FlowEnrollment.flow == wf,
                FlowEnrollment.contact == contact,
            ).count()
            if already > 0:
                logger.debug("Welcome: %s already enrolled in flow #%d" % (contact.email, wf.id))
                return False

        # Enroll in all matching welcome flows
        enrolled_any = False
        for flow in welcome_flows:
            first_step = (FlowStep.select()
                          .where(FlowStep.flow == flow)
                          .order_by(FlowStep.step_order)
                          .first())
            if not first_step:
                continue
            next_send = datetime.now() + timedelta(hours=first_step.delay_hours)
            try:
                FlowEnrollment.create(
                    flow=flow,
                    contact=contact,
                    current_step=1,
                    next_send_at=next_send,
                    status="active",
                )
                enrolled_any = True
                logger.info("Welcome: enrolled %s in '%s' via identity resolution (%s)"
                            % (contact.email, flow.name, source))
            except Exception:
                pass  # Unique constraint — already enrolled

        if enrolled_any:
            # Log to ActionLedger
            log_action(
                contact=contact,
                trigger_type="identity",
                source_id=0,
                status="detected",
                reason_code=RC_WELCOME_POST_RESOLVE,
                source_type="identity_resolution",
                reason_detail="Welcome flow enrolled after identity resolution via %s" % source,
            )

        return enrolled_any

    except Exception as e:
        logger.warning("Welcome eligibility check failed for %s: %s" % (contact.email, e))
        return False


def _replay_behavioral_triggers(contact_id, email, source):
    """
    Re-evaluate behavioral triggers after stitching anonymous activity.
    Runs in background thread. Creates PendingTrigger rows for any qualified triggers.

    Returns:
        list of trigger_type strings that were replayed
    """
    replayed = []
    now = datetime.now()
    cutoff_48h = now - timedelta(hours=48)
    cutoff_7d = now - timedelta(days=7)

    try:
        contact = Contact.get_or_none(Contact.id == contact_id)
        if not contact or not contact.subscribed:
            return replayed

        # ── Browse abandonment ──
        try:
            product_views = (CustomerActivity.select()
                .where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type == "viewed_product",
                    CustomerActivity.occurred_at >= cutoff_48h,
                ).count())

            if product_views >= 2:
                # Dedup: no existing browse trigger in last 7 days
                existing = PendingTrigger.select().where(
                    PendingTrigger.email == email,
                    PendingTrigger.trigger_type == "browse_abandonment",
                    PendingTrigger.detected_at >= cutoff_7d,
                ).count()

                # No recent purchase
                recent_order = CustomerActivity.select().where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type.in_(["placed_order", "completed_checkout"]),
                    CustomerActivity.occurred_at >= cutoff_48h,
                ).count()

                if existing == 0 and recent_order == 0:
                    # Get the last viewed product for trigger_data
                    import json
                    last_product = (CustomerActivity.select()
                        .where(
                            CustomerActivity.email == email,
                            CustomerActivity.event_type == "viewed_product",
                            CustomerActivity.occurred_at >= cutoff_48h,
                        )
                        .order_by(CustomerActivity.occurred_at.desc())
                        .first())

                    product_data = {}
                    if last_product:
                        try:
                            product_data = json.loads(last_product.event_data or "{}")
                        except Exception:
                            pass

                    PendingTrigger.create(
                        email=email,
                        contact=contact,
                        trigger_type="browse_abandonment",
                        trigger_data=json.dumps({
                            "product_title": product_data.get("product_title", ""),
                            "product_url": product_data.get("url", ""),
                            "view_count": product_views,
                            "source": "identity_replay_%s" % source,
                        }),
                        detected_at=now,
                        status="pending",
                    )
                    replayed.append("browse_abandonment")
                    logger.info("Replay: browse_abandonment trigger for %s (%d views)" % (email, product_views))
        except Exception as e:
            logger.warning("Replay browse check failed for %s: %s" % (email, e))

        # ── Cart abandonment (viewed_cart) ──
        try:
            cart_views = (CustomerActivity.select()
                .where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type == "viewed_cart",
                    CustomerActivity.occurred_at >= cutoff_48h,
                ).count())

            if cart_views >= 1:
                existing = PendingTrigger.select().where(
                    PendingTrigger.email == email,
                    PendingTrigger.trigger_type == "cart_abandonment",
                    PendingTrigger.detected_at >= cutoff_7d,
                ).count()

                # Skip if abandoned_checkout exists (richer data from Shopify)
                has_checkout = CustomerActivity.select().where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type == "abandoned_checkout",
                    CustomerActivity.occurred_at >= cutoff_48h,
                ).count()

                # Skip if purchased since last cart view
                last_cart = (CustomerActivity.select()
                    .where(
                        CustomerActivity.email == email,
                        CustomerActivity.event_type == "viewed_cart",
                        CustomerActivity.occurred_at >= cutoff_48h,
                    )
                    .order_by(CustomerActivity.occurred_at.desc())
                    .first())

                purchased_after = False
                if last_cart:
                    purchased_after = CustomerActivity.select().where(
                        CustomerActivity.email == email,
                        CustomerActivity.event_type.in_(["completed_checkout", "placed_order"]),
                        CustomerActivity.occurred_at >= last_cart.occurred_at,
                    ).count() > 0

                if existing == 0 and has_checkout == 0 and not purchased_after:
                    import json
                    PendingTrigger.create(
                        email=email,
                        contact=contact,
                        trigger_type="cart_abandonment",
                        trigger_data=json.dumps({
                            "source": "viewed_cart_replay_%s" % source,
                            "cart_views": cart_views,
                        }),
                        detected_at=now,
                        status="pending",
                    )
                    replayed.append("cart_abandonment")
                    logger.info("Replay: cart_abandonment trigger for %s (%d cart views)" % (email, cart_views))
        except Exception as e:
            logger.warning("Replay cart check failed for %s: %s" % (email, e))

        # ── Checkout abandonment ──
        try:
            open_checkout = AbandonedCheckout.select().where(
                AbandonedCheckout.email == email,
                AbandonedCheckout.recovered == False,
            ).first()

            if open_checkout:
                # Check if already enrolled in checkout flow
                checkout_flows = list(Flow.select().where(
                    Flow.is_active == True,
                    Flow.trigger_type == "checkout_abandoned",
                ))
                already_enrolled = False
                for cf in checkout_flows:
                    if FlowEnrollment.select().where(
                        FlowEnrollment.flow == cf,
                        FlowEnrollment.contact == contact,
                    ).count() > 0:
                        already_enrolled = True
                        break

                existing_trigger = PendingTrigger.select().where(
                    PendingTrigger.email == email,
                    PendingTrigger.trigger_type == "cart_abandonment",
                    PendingTrigger.detected_at >= cutoff_7d,
                ).count()

                if not already_enrolled and existing_trigger == 0:
                    import json
                    PendingTrigger.create(
                        email=email,
                        contact=contact,
                        trigger_type="cart_abandonment",
                        trigger_data=json.dumps({
                            "source": "checkout_replay_%s" % source,
                            "checkout_id": open_checkout.shopify_checkout_id,
                            "total": str(open_checkout.total_price),
                            "checkout_url": open_checkout.checkout_url or "",
                        }),
                        detected_at=now,
                        status="pending",
                    )
                    replayed.append("checkout_abandonment")
                    logger.info("Replay: checkout_abandonment trigger for %s" % email)
        except Exception as e:
            logger.warning("Replay checkout check failed for %s: %s" % (email, e))

        # Log replay results to ActionLedger
        if replayed:
            log_action(
                contact=contact,
                trigger_type="identity",
                source_id=0,
                status="detected",
                reason_code=RC_IDENTITY_REPLAY,
                source_type="identity_resolution",
                reason_detail="Replayed triggers after stitch: %s (via %s)" % (
                    ", ".join(replayed), source),
            )

    except Exception as e:
        logger.error("Replay behavioral triggers failed for %s: %s" % (email, e))

    return replayed


def _log_identity_event(contact, email, source, status, reason_code,
                         reason_detail, stitched_count, identifiers):
    """Log an identity resolution event to ActionLedger."""
    try:
        log_action(
            contact=contact,
            trigger_type="identity",
            source_id=0,
            status=status,
            reason_code=reason_code,
            source_type="identity_resolution",
            reason_detail="%s | identifiers=%s | stitched=%d" % (
                reason_detail, ",".join(identifiers), stitched_count),
        )
    except Exception as e:
        logger.warning("Identity audit log failed: %s" % e)
