"""
Identity Resolution — canonical service for turning anonymous activity into known customer journeys.

Every identity entry point (popup subscribe, /api/identify, /api/track, Shopify webhooks,
email link clicks) calls resolve_identity(). No other file should contain inline session
stitching logic.

v2 — Hardening pass:
    - Multi-identifier stitching (session_id, checkout_token, cart_token, shopify_id, email)
    - Real confidence levels (exact / probable / anonymous_only)
    - Durable job queue (IdentityJob) replaces daemon threads
    - Per-trigger observability in ActionLedger

Functions:
    resolve_identity()              — single canonical entry point
    _promote_visitor_to_contact()   — find or create Contact + CustomerProfile
    _stitch_by_identifiers()        — prioritized multi-identifier stitching cascade
    _evaluate_welcome_eligibility() — enroll in welcome flow if first meaningful resolution
    _replay_behavioral_triggers()   — re-evaluate browse/cart/checkout triggers post-stitch
    process_identity_jobs()         — durable job processor (called by APScheduler)
"""

import json
import logging
from datetime import datetime, timedelta

from database import (
    Contact, CustomerActivity, CustomerProfile, FlowEnrollment,
    Flow, FlowStep, PendingTrigger, AbandonedCheckout, ActionLedger,
    IdentityJob,
)
from action_ledger import (
    log_action,
    RC_IDENTITY_RESOLVED, RC_IDENTITY_STITCHED, RC_IDENTITY_NEW_CONTACT,
    RC_IDENTITY_NO_OP, RC_IDENTITY_REPLAY, RC_WELCOME_POST_RESOLVE,
    RC_IDENTITY_REPLAY_SKIP, RC_IDENTITY_PROBABLE, RC_IDENTITY_MULTI_STITCH,
)

logger = logging.getLogger("identity_resolution")

# Placeholder emails that should be treated as anonymous (stitchable)
_PLACEHOLDER_EMAILS = {"anonymous@placeholder.invalid", "guest@placeholder.invalid", ""}


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
    cart_token="",
    checkout_token="",
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
        cart_token:       Shopify cart token (from checkout/order webhooks)
        checkout_token:   Shopify checkout token (from checkout/order webhooks)

    Returns:
        dict with keys:
            contact            — Contact instance or None
            created            — bool, True if new contact was created
            stitched           — int, number of anonymous events linked
            stitch_breakdown   — dict, {identifier_type: count} for each identifier that stitched
            already_resolved   — bool, True if no work was needed
            welcome_enrolled   — bool, True if enrolled in welcome flow
            triggers_replayed  — list of trigger_type strings replayed (populated by job processor)
            confidence         — "exact" | "probable" | "anonymous_only"
            identifiers_matched — list of identifier types that were provided
    """
    result = {
        "contact": None,
        "created": False,
        "stitched": 0,
        "stitch_breakdown": {},
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
    if cart_token:
        result["identifiers_matched"].append("cart_token")
    if checkout_token:
        result["identifiers_matched"].append("checkout_token")

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
                            0, {}, result["confidence"], result["identifiers_matched"])
        return result

    # ── 3. Multi-identifier stitching cascade ──
    total_stitched, breakdown = _stitch_by_identifiers(
        email=email,
        contact_id=contact.id,
        session_id=session_id,
        shopify_id=shopify_id,
        checkout_token=checkout_token,
        cart_token=cart_token,
        source=source,
    )
    result["stitched"] = total_stitched
    result["stitch_breakdown"] = breakdown

    # ── 3b. Confidence: downgrade to probable if stitched via token without session ──
    if total_stitched > 0:
        token_stitched = breakdown.get("checkout_token", 0) + breakdown.get("cart_token", 0)
        session_stitched = breakdown.get("session_id", 0) + breakdown.get("email", 0)
        if token_stitched > 0 and session_stitched == 0:
            result["confidence"] = "probable"

    # ── 4. Audit log ──
    breakdown_str = ", ".join("%s=%d" % (k, v) for k, v in breakdown.items() if v > 0)
    if created:
        _log_identity_event(contact, email, source, "identity_new_contact",
                            RC_IDENTITY_NEW_CONTACT,
                            "New contact created via %s (stitched %d events)" % (source, total_stitched),
                            total_stitched, breakdown, result["confidence"],
                            result["identifiers_matched"])
    elif total_stitched > 0:
        rc = RC_IDENTITY_MULTI_STITCH if len([v for v in breakdown.values() if v > 0]) > 1 else RC_IDENTITY_STITCHED
        _log_identity_event(contact, email, source, "identity_stitched",
                            rc,
                            "Stitched %d anonymous events via %s | breakdown: %s" % (
                                total_stitched, source, breakdown_str or "none"),
                            total_stitched, breakdown, result["confidence"],
                            result["identifiers_matched"])
    else:
        result["already_resolved"] = True
        # Only log no-op for explicit identity calls, not every track call
        if source in ("pixel_identify", "popup_subscribe", "email_click"):
            _log_identity_event(contact, email, source, "identity_no_op",
                                RC_IDENTITY_NO_OP,
                                "No anonymous events to stitch",
                                0, {}, result["confidence"], result["identifiers_matched"])

    # ── 5. Welcome flow eligibility ──
    welcome_enrolled = _evaluate_welcome_eligibility(
        contact=contact,
        created=created,
        source=source,
        subscribe=subscribe,
    )
    result["welcome_enrolled"] = welcome_enrolled

    # ── 6. Enqueue durable jobs (replaces daemon threads) ──
    if total_stitched > 0 or created:
        try:
            IdentityJob.create(
                contact_id=contact.id, email=email, source=source,
                job_type="trigger_replay", status="pending",
            )
            IdentityJob.create(
                contact_id=contact.id, email=email, source=source,
                job_type="enrichment", status="pending",
            )
            IdentityJob.create(
                contact_id=contact.id, email=email, source=source,
                job_type="cascade", status="pending",
            )
            logger.debug("Enqueued 3 identity jobs for %s via %s" % (email, source))
        except Exception as e:
            logger.warning("Failed to enqueue identity jobs for %s: %s" % (email, e))

    logger.info(
        "resolve_identity: %s via %s (created=%s, stitched=%d, breakdown=%s, "
        "confidence=%s, welcome=%s)"
        % (email, source, created, total_stitched, breakdown_str or "none",
           result["confidence"], welcome_enrolled)
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


def _stitch_by_identifiers(email, contact_id, session_id="", shopify_id="",
                            checkout_token="", cart_token="", source=""):
    """
    Prioritized multi-identifier stitching cascade.

    Runs each identifier match in priority order. The stitched_at IS NULL guard
    prevents any row from being stitched twice (earlier priorities win).

    Returns:
        (total_stitched, breakdown_dict)
        breakdown_dict: {identifier_type: count} e.g. {"session_id": 3, "checkout_token": 1}
    """
    now = datetime.now()
    breakdown = {}
    total = 0

    # ── Priority 1: email — rows that have this email but no contact_id ──
    try:
        count = (
            CustomerActivity.update(
                contact_id=contact_id,
                stitched_at=now,
                stitched_by="email:%s" % source,
            )
            .where(
                CustomerActivity.email == email,
                CustomerActivity.contact_id.is_null(),
                CustomerActivity.stitched_at.is_null(),
            )
            .execute()
        )
        if count > 0:
            breakdown["email"] = count
            total += count
            logger.info("Stitched %d events by email match for %s" % (count, email))
    except Exception as e:
        logger.warning("Stitch by email failed for %s: %s" % (email, e))

    # ── Priority 2: session_id — anonymous rows with matching session ──
    if session_id:
        try:
            count = (
                CustomerActivity.update(
                    email=email,
                    contact_id=contact_id,
                    stitched_at=now,
                    stitched_by="session_id:%s" % source,
                )
                .where(
                    CustomerActivity.session_id == session_id,
                    CustomerActivity.email.in_(_PLACEHOLDER_EMAILS),
                    CustomerActivity.stitched_at.is_null(),
                )
                .execute()
            )
            if count > 0:
                breakdown["session_id"] = count
                total += count
                logger.info("Stitched %d events by session_id (%s) for %s"
                            % (count, session_id[:8], email))
        except Exception as e:
            logger.warning("Stitch by session_id failed for %s: %s" % (email, e))

    # ── Priority 3: shopify_id — rows belonging to other contacts with same shopify_id ──
    # (This catches cases where Shopify customer ID was known before email.)
    # Skipped for now — shopify_id is on Contact, not CustomerActivity.
    # The email-based stitch (Priority 1) already handles this indirectly.

    # ── Priority 4: checkout_token — anonymous rows whose event_data contains the token ──
    if checkout_token:
        try:
            count = (
                CustomerActivity.update(
                    email=email,
                    contact_id=contact_id,
                    stitched_at=now,
                    stitched_by="checkout_token:%s" % source,
                )
                .where(
                    CustomerActivity.event_data.contains(checkout_token),
                    CustomerActivity.email.in_(_PLACEHOLDER_EMAILS),
                    CustomerActivity.stitched_at.is_null(),
                    CustomerActivity.event_type.in_(
                        ["abandoned_checkout", "started_checkout", "completed_checkout"]
                    ),
                )
                .execute()
            )
            if count > 0:
                breakdown["checkout_token"] = count
                total += count
                logger.info("Stitched %d events by checkout_token for %s" % (count, email))
        except Exception as e:
            logger.warning("Stitch by checkout_token failed for %s: %s" % (email, e))

    # ── Priority 5: cart_token — anonymous rows whose event_data contains the token ──
    if cart_token:
        try:
            count = (
                CustomerActivity.update(
                    email=email,
                    contact_id=contact_id,
                    stitched_at=now,
                    stitched_by="cart_token:%s" % source,
                )
                .where(
                    CustomerActivity.event_data.contains(cart_token),
                    CustomerActivity.email.in_(_PLACEHOLDER_EMAILS),
                    CustomerActivity.stitched_at.is_null(),
                    CustomerActivity.event_type.in_(
                        ["viewed_cart", "started_checkout", "abandoned_checkout"]
                    ),
                )
                .execute()
            )
            if count > 0:
                breakdown["cart_token"] = count
                total += count
                logger.info("Stitched %d events by cart_token for %s" % (count, email))
        except Exception as e:
            logger.warning("Stitch by cart_token failed for %s: %s" % (email, e))

    if total > 0:
        logger.info("Total stitched for %s: %d (breakdown: %s)" % (email, total, breakdown))

    return total, breakdown


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
    Creates PendingTrigger rows for any qualified triggers.
    Logs individual ActionLedger entries per trigger (replayed or skipped).

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
                    # Per-trigger ActionLedger entry
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="detected", reason_code=RC_IDENTITY_REPLAY,
                        source_type="identity_resolution",
                        reason_detail="Replayed browse_abandonment: %d products in 48h (via %s)" % (
                            product_views, source),
                    )
                    logger.info("Replay: browse_abandonment for %s (%d views)" % (email, product_views))
                else:
                    skip_reason = "dedup" if existing > 0 else "recent_purchase"
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="suppressed", reason_code=RC_IDENTITY_REPLAY_SKIP,
                        source_type="identity_resolution",
                        reason_detail="Skipped browse_abandonment: %s (via %s)" % (skip_reason, source),
                    )
            elif product_views > 0:
                # Some views but below threshold
                log_action(
                    contact=contact, trigger_type="identity", source_id=0,
                    status="suppressed", reason_code=RC_IDENTITY_REPLAY_SKIP,
                    source_type="identity_resolution",
                    reason_detail="Skipped browse_abandonment: only %d views (need 2+) via %s" % (
                        product_views, source),
                )
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
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="detected", reason_code=RC_IDENTITY_REPLAY,
                        source_type="identity_resolution",
                        reason_detail="Replayed cart_abandonment: %d cart views in 48h (via %s)" % (
                            cart_views, source),
                    )
                    logger.info("Replay: cart_abandonment for %s (%d cart views)" % (email, cart_views))
                else:
                    skip_reason = ("dedup" if existing > 0
                                   else "has_checkout" if has_checkout > 0
                                   else "purchased_after")
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="suppressed", reason_code=RC_IDENTITY_REPLAY_SKIP,
                        source_type="identity_resolution",
                        reason_detail="Skipped cart_abandonment: %s (via %s)" % (skip_reason, source),
                    )
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
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="detected", reason_code=RC_IDENTITY_REPLAY,
                        source_type="identity_resolution",
                        reason_detail="Replayed checkout_abandonment: checkout %s, $%s (via %s)" % (
                            open_checkout.shopify_checkout_id[:12],
                            open_checkout.total_price, source),
                    )
                    logger.info("Replay: checkout_abandonment for %s" % email)
                else:
                    skip_reason = "already_enrolled" if already_enrolled else "dedup"
                    log_action(
                        contact=contact, trigger_type="identity", source_id=0,
                        status="suppressed", reason_code=RC_IDENTITY_REPLAY_SKIP,
                        source_type="identity_resolution",
                        reason_detail="Skipped checkout_abandonment: %s (via %s)" % (skip_reason, source),
                    )
        except Exception as e:
            logger.warning("Replay checkout check failed for %s: %s" % (email, e))

    except Exception as e:
        logger.error("Replay behavioral triggers failed for %s: %s" % (email, e))

    return replayed


def _log_identity_event(contact, email, source, status, reason_code,
                         reason_detail, stitched_count, breakdown,
                         confidence, identifiers):
    """Log an identity resolution event to ActionLedger."""
    try:
        breakdown_str = ", ".join("%s=%d" % (k, v) for k, v in breakdown.items() if v > 0)
        log_action(
            contact=contact,
            trigger_type="identity",
            source_id=0,
            status=status,
            reason_code=reason_code,
            source_type="identity_resolution",
            reason_detail="%s | confidence=%s | identifiers=%s | stitched=%d | breakdown=%s" % (
                reason_detail, confidence, ",".join(identifiers),
                stitched_count, breakdown_str or "none"),
        )
    except Exception as e:
        logger.warning("Identity audit log failed: %s" % e)


# ── Durable Job Processor ──────────────────────────────────────────

def process_identity_jobs(batch_size=20):
    """
    Process pending identity jobs from the IdentityJob queue.
    Called by APScheduler every 30 seconds.

    Job types:
        trigger_replay  — re-evaluate browse/cart/checkout triggers
        enrichment      — enrich customer profile
        cascade         — run intelligence cascade (depends on enrichment)
    """
    try:
        jobs = list(IdentityJob.select().where(
            IdentityJob.status == "pending",
            IdentityJob.attempts < IdentityJob.max_attempts,
        ).order_by(IdentityJob.created_at).limit(batch_size))
    except Exception as e:
        logger.error("Failed to fetch identity jobs: %s" % e)
        return

    processed = 0
    for job in jobs:
        try:
            IdentityJob.update(
                status="processing",
                started_at=datetime.now(),
                attempts=IdentityJob.attempts + 1,
            ).where(IdentityJob.id == job.id).execute()

            result_data = {}

            if job.job_type == "trigger_replay":
                replayed = _replay_behavioral_triggers(job.contact_id, job.email, job.source)
                result_data = {"replayed": replayed}

            elif job.job_type == "enrichment":
                try:
                    from activity_sync import enrich_single_profile
                    enrich_single_profile(job.email)
                    result_data = {"enriched": True}
                except Exception as e:
                    result_data = {"enriched": False, "error": str(e)[:200]}
                    logger.warning("Identity job enrichment failed for %s: %s" % (job.email, e))

            elif job.job_type == "cascade":
                # Only run if enrichment for same contact is completed
                pending_enrich = IdentityJob.select().where(
                    IdentityJob.contact_id == job.contact_id,
                    IdentityJob.job_type == "enrichment",
                    IdentityJob.status.in_(["pending", "processing"]),
                ).count()
                if pending_enrich > 0:
                    # Re-queue: enrichment not done yet, try again next tick
                    IdentityJob.update(
                        status="pending",
                        started_at=None,
                    ).where(IdentityJob.id == job.id).execute()
                    continue

                try:
                    from cascade import cascade_contact
                    cascade_contact(job.contact_id, trigger="identity_%s" % job.source)
                    result_data = {"cascaded": True}
                except Exception as e:
                    result_data = {"cascaded": False, "error": str(e)[:200]}
                    logger.warning("Identity job cascade failed for %s: %s" % (job.email, e))

            IdentityJob.update(
                status="completed",
                completed_at=datetime.now(),
                result=json.dumps(result_data),
            ).where(IdentityJob.id == job.id).execute()
            processed += 1

        except Exception as e:
            try:
                new_status = "failed" if job.attempts + 1 >= job.max_attempts else "pending"
                IdentityJob.update(
                    status=new_status,
                    error_msg=str(e)[:500],
                ).where(IdentityJob.id == job.id).execute()
            except Exception:
                pass
            logger.error("Identity job #%d (%s) failed: %s" % (job.id, job.job_type, e))

    if processed > 0:
        logger.info("Processed %d/%d identity jobs" % (processed, len(jobs)))
