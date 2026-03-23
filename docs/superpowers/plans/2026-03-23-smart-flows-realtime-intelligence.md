# Smart Flows: Real-Time Intelligence & Pre-Send Guards — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make flows react to real-time customer behavior — recompute profiles on qualifying activity, add pre-send safety guards, expose richer intelligence to template conditions, and auto-exit flows that no longer match.

**Architecture:** Event-driven profile refresh with a 15-minute "silence after last activity" debounce. Pre-send guards in delivery_engine catch orphaned queue items, post-purchase sends, and bounced contacts. Flow fitness evaluation auto-exits mismatched enrollments after profile refresh.

**Tech Stack:** Flask, Peewee ORM, SQLite, APScheduler

**Spec:** `docs/superpowers/specs/2026-03-23-smart-flows-realtime-intelligence-design.md`

---

## Chunk 1: Database Migration + Profile Refresh Scheduling

### Task 1: Add new fields to CustomerProfile model

**Files:**
- Modify: `database.py:319-401` (CustomerProfile model)
- Modify: `database.py:945-975` (migration helpers)

- [ ] **Step 1: Add fields to CustomerProfile model**

In `database.py`, add two new fields to the `CustomerProfile` class after `last_intelligence_at` (around line 397):

```python
    last_intelligence_at     = DateTimeField(null=True)
    # ── Real-time refresh fields ──────────────────────────
    refresh_scheduled_at     = DateTimeField(null=True, index=True)
    last_refresh_trigger     = CharField(max_length=50, null=True)
```

The `index=True` on `refresh_scheduled_at` is critical — the scheduler job queries this field every 60 seconds.

- [ ] **Step 2: Add migration function**

Add a new migration function after `_migrate_intelligence_fields()` (around line 975):

```python
def _migrate_smart_flow_fields():
    """Add real-time profile refresh columns to customer_profiles."""
    new_cols = [
        ("refresh_scheduled_at", "DATETIME"),
        ("last_refresh_trigger", "VARCHAR(50)"),
    ]
    cursor = db.execute_sql("PRAGMA table_info(customer_profiles)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in new_cols:
        if col_name not in existing:
            db.execute_sql(f"ALTER TABLE customer_profiles ADD COLUMN {col_name} {col_def}")
```

- [ ] **Step 3: Register migration in init_db()**

Find `init_db()` and add a call to `_migrate_smart_flow_fields()` after the existing migration calls (look for where `_migrate_intelligence_fields()` is called):

```python
    _migrate_smart_flow_fields()
```

- [ ] **Step 4: Verify migration works**

Run:
```bash
cd "/c/Users/davin/Claude Work Folder/mailenginehub-repo"
python -c "from database import init_db; init_db(); print('Migration OK')"
```
Expected: `Migration OK` with no errors.

- [ ] **Step 5: Commit**

```bash
git add database.py
git commit -m "feat: add refresh_scheduled_at and last_refresh_trigger to CustomerProfile"
```

---

### Task 2: Create schedule_profile_refresh() function

**Files:**
- Modify: `customer_intelligence.py`

- [ ] **Step 1: Add the scheduling function**

Add at the top of `customer_intelligence.py` after the imports:

```python
def schedule_profile_refresh(contact_id, trigger_event):
    """
    Schedule a full profile recompute for 15 minutes after the contact's last
    qualifying activity. If already scheduled, the timer resets (pushes forward).

    Qualifying events: placed_order, completed_checkout, viewed_product,
    product_search, add_to_cart, email_click, checkout_abandoned, cart_abandoned.

    Args:
        contact_id: int — the Contact.id
        trigger_event: str — the event type that triggered this (e.g. "placed_order")
    """
    from database import CustomerProfile
    refresh_at = datetime.now() + timedelta(minutes=15)
    updated = CustomerProfile.update(
        refresh_scheduled_at=refresh_at,
        last_refresh_trigger=trigger_event,
    ).where(CustomerProfile.contact_id == contact_id).execute()
    if updated:
        logger.info("[ProfileRefresh] Scheduled refresh for contact #%s at %s (trigger: %s)",
                     contact_id, refresh_at.strftime("%H:%M:%S"), trigger_event)
    else:
        logger.debug("[ProfileRefresh] No profile found for contact #%s, skipping", contact_id)
```

Make sure `datetime` and `timedelta` are imported at the top of the file (they likely already are).

- [ ] **Step 2: Verify it works**

Run:
```bash
python -c "
from database import init_db, CustomerProfile, Contact
init_db()
c = Contact.select().first()
if c:
    from customer_intelligence import schedule_profile_refresh
    schedule_profile_refresh(c.id, 'test_event')
    p = CustomerProfile.get_or_none(CustomerProfile.contact_id == c.id)
    if p and p.refresh_scheduled_at:
        print(f'OK: refresh scheduled at {p.refresh_scheduled_at}')
        # Clean up
        p.refresh_scheduled_at = None
        p.last_refresh_trigger = None
        p.save()
    else:
        print('FAIL: no refresh_scheduled_at set')
else:
    print('No contacts in DB')
"
```
Expected: `OK: refresh scheduled at <timestamp>`

- [ ] **Step 3: Commit**

```bash
git add customer_intelligence.py
git commit -m "feat: add schedule_profile_refresh() for delayed recompute"
```

---

### Task 3: Wire up schedule_profile_refresh() to qualifying events in app.py

**Files:**
- Modify: `app.py`

The qualifying events and their injection points:

| Event | Function | Line |
|-------|----------|------|
| placed_order | `webhook_shopify_order_create()` | ~996 |
| completed_checkout | `webhook_shopify_checkout_create()` | ~919 |
| viewed_product, product_search, add_to_cart | `track_event()` | ~6628 |
| email_click | `track_flow_click()` | ~2361 |
| email_click | `track_auto_click()` | ~2466 |
| checkout_abandoned, cart_abandoned | `_check_passive_triggers()` | ~3651 |

- [ ] **Step 1: Add import at top of app.py**

Find the imports section and add:

```python
from customer_intelligence import schedule_profile_refresh
```

If `customer_intelligence` is already imported, add `schedule_profile_refresh` to the existing import.

- [ ] **Step 2: Wire into webhook_shopify_order_create()**

Find `webhook_shopify_order_create()` (line ~996). After the line that calls `_exit_flows_by_trigger_type()` (line ~1046), add:

```python
            # Schedule real-time profile refresh (15 min after last activity)
            try:
                schedule_profile_refresh(contact.id, "placed_order")
            except Exception as _e:
                app.logger.warning("[ProfileRefresh] Failed to schedule for order: %s", _e)
```

- [ ] **Step 3: Wire into webhook_shopify_checkout_create()**

Find `webhook_shopify_checkout_create()` (line ~919). After the `AbandonedCheckout` record is created, add:

```python
            # Schedule real-time profile refresh
            try:
                schedule_profile_refresh(contact.id, "completed_checkout")
            except Exception as _e:
                app.logger.warning("[ProfileRefresh] Failed to schedule for checkout: %s", _e)
```

- [ ] **Step 4: Wire into track_event()**

Find `track_event()` (line ~6628). After the event is logged to `CustomerActivity`, add a conditional call:

```python
        # Schedule profile refresh for qualifying events
        _qualifying_events = {"viewed_product", "product_search", "add_to_cart"}
        if event_type in _qualifying_events and contact:
            try:
                schedule_profile_refresh(contact.id, event_type)
            except Exception as _e:
                app.logger.debug("[ProfileRefresh] Failed to schedule for %s: %s", event_type, _e)
```

- [ ] **Step 5: Wire into track_flow_click()**

Find `track_flow_click()` (line ~2361). After the click is logged, add:

```python
            # Schedule profile refresh on email click
            try:
                schedule_profile_refresh(contact.id, "email_click")
            except Exception as _e:
                app.logger.debug("[ProfileRefresh] click schedule failed: %s", _e)
```

- [ ] **Step 6: Wire into track_auto_click()**

Find `track_auto_click()` (line ~2466). After the click is logged, add the same pattern:

```python
            # Schedule profile refresh on email click
            try:
                schedule_profile_refresh(contact.id, "email_click")
            except Exception as _e:
                app.logger.debug("[ProfileRefresh] click schedule failed: %s", _e)
```

- [ ] **Step 7: Wire into _check_passive_triggers()**

Find `_check_passive_triggers()` (line ~3651). When a new `PendingTrigger` is created for browse or cart abandonment, add:

```python
                # Schedule profile refresh for abandonment detection
                try:
                    schedule_profile_refresh(contact.id, trigger_type)
                except Exception as _e:
                    app.logger.debug("[ProfileRefresh] trigger schedule failed: %s", _e)
```

- [ ] **Step 8: Commit**

```bash
git add app.py
git commit -m "feat: wire schedule_profile_refresh into all qualifying event endpoints"
```

---

## Chunk 2: Profile Refresh Processor + Replace _rebuild_stale_profiles

### Task 4: Create refresh_contact_profile() wrapper

**Files:**
- Modify: `customer_intelligence.py`

- [ ] **Step 1: Add the refresh function**

Add after `schedule_profile_refresh()`:

```python
def refresh_contact_profile(contact_id):
    """
    Run full intelligence enrichment for a single contact and evaluate
    flow fitness afterward. Called by the scheduler when
    refresh_scheduled_at has elapsed.

    Returns dict with computed values, or None on error.
    """
    from database import CustomerProfile

    try:
        result = compute_intelligence(contact_id)
        if result and "error" not in result:
            # Stamp the refresh
            CustomerProfile.update(
                refresh_scheduled_at=None,
                last_intelligence_at=datetime.now(),
            ).where(CustomerProfile.contact_id == contact_id).execute()
            logger.info("[ProfileRefresh] Refreshed contact #%s successfully", contact_id)

            # Evaluate flow fitness with new profile
            try:
                evaluate_flow_fitness(contact_id)
            except Exception as e:
                logger.warning("[ProfileRefresh] Flow fitness eval failed for #%s: %s", contact_id, e)

            return result
        else:
            # compute_intelligence returned an error — clear schedule to prevent retry loop
            CustomerProfile.update(
                refresh_scheduled_at=None,
            ).where(CustomerProfile.contact_id == contact_id).execute()
            logger.warning("[ProfileRefresh] compute_intelligence failed for #%s: %s",
                           contact_id, result)
            return None
    except Exception as e:
        # Clear schedule to prevent infinite retry — nightly batch will catch it
        CustomerProfile.update(
            refresh_scheduled_at=None,
        ).where(CustomerProfile.contact_id == contact_id).execute()
        logger.error("[ProfileRefresh] Exception for contact #%s: %s", contact_id, e)
        return None
```

- [ ] **Step 2: Add placeholder for evaluate_flow_fitness()**

Add a stub — we'll implement it fully in Task 6:

```python
def evaluate_flow_fitness(contact_id):
    """
    Check if contact's active flow enrollments still match their refreshed profile.
    Auto-exits flows that no longer make sense.
    Implemented in Task 6.
    """
    pass
```

- [ ] **Step 3: Commit**

```bash
git add customer_intelligence.py
git commit -m "feat: add refresh_contact_profile() wrapper with error handling"
```

---

### Task 5: Add scheduler job and remove _rebuild_stale_profiles

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add _process_profile_refresh_queue() function**

Add near the other scheduler helper functions (before the scheduler configuration section at line ~6978):

```python
def _process_profile_refresh_queue():
    """
    Every 60s: find contacts whose refresh_scheduled_at has elapsed
    (15 min of silence after last qualifying activity) and run full
    intelligence recompute + flow fitness evaluation.

    Replaces the old _rebuild_stale_profiles() job.
    """
    try:
        from database import CustomerProfile, init_db
        from customer_intelligence import refresh_contact_profile
        init_db()
        now = datetime.now()

        due = list(CustomerProfile.select(
            CustomerProfile.contact_id, CustomerProfile.email, CustomerProfile.last_refresh_trigger
        ).where(
            CustomerProfile.refresh_scheduled_at.is_null(False),
            CustomerProfile.refresh_scheduled_at <= now,
        ).limit(10))  # Cap at 10 per cycle to avoid overload

        if not due:
            return

        refreshed = 0
        for p in due:
            try:
                result = refresh_contact_profile(p.contact_id)
                if result:
                    refreshed += 1
            except Exception as _e:
                app.logger.error("[ProfileRefreshQueue] Failed for %s: %s", p.email, _e)

        if refreshed:
            app.logger.info("[ProfileRefreshQueue] Refreshed %d profiles", refreshed)
    except Exception as _e:
        app.logger.error("[ProfileRefreshQueue] Error: %s", _e)
```

- [ ] **Step 2: Remove _rebuild_stale_profiles() function**

Delete the entire `_rebuild_stale_profiles()` function (lines ~7051-7083).

- [ ] **Step 3: Replace scheduler registration**

In the scheduler configuration section (line ~7082), find:

```python
    _scheduler.add_job(_rebuild_stale_profiles, "interval", minutes=5,
                       id="rebuild_stale_profiles", replace_existing=True)
```

Replace with:

```python
    _scheduler.add_job(_process_profile_refresh_queue, "interval", seconds=60,
                       id="profile_refresh_queue", replace_existing=True)
```

- [ ] **Step 4: Clear refresh_scheduled_at in nightly batch**

Find the function that calls `enrich_all_contacts()` or `compute_all_intelligence()` for the nightly run (search for `_run_nightly_intelligence`). At the START of that function, before the batch enrichment begins, add:

```python
        # Clear pending real-time refreshes — nightly batch handles everything
        from database import CustomerProfile
        CustomerProfile.update(refresh_scheduled_at=None).execute()
        app.logger.info("[NightlyIntelligence] Cleared pending real-time refresh queue")
```

- [ ] **Step 5: Verify app starts without errors**

Run:
```bash
python -c "from app import app; print('App imports OK')"
```
Expected: `App imports OK`

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: replace _rebuild_stale_profiles with _process_profile_refresh_queue (15-min silence pattern)"
```

---

## Chunk 3: Pre-Send Guards in Delivery Engine

### Task 6: Add three pre-send guards to _send_one()

**Files:**
- Modify: `delivery_engine.py:292-308`

- [ ] **Step 1: Add imports to delivery_engine.py**

At the top of `delivery_engine.py`, find the existing `from database import ...` line and add the models that aren't already imported. `FlowEnrollment`, `Flow`, `Contact`, and `DeliveryQueue` are already imported. Add only the missing ones:

```python
from database import (...existing imports..., ShopifyOrder, BounceLog, SuppressionEntry)
```

- [ ] **Step 2: Add Guard 1 — Enrollment still active?**

In `_send_one()` (line 292), after the existing subscription check block (ends around line 308), add:

```python
          # ── Guard 1: Enrollment still active? ──
          _enrollment = None  # shared by Guard 1 and Guard 2
          if item.email_type == "flow" and item.enrollment_id:
              try:
                  _enrollment = FlowEnrollment.get_or_none(
                      FlowEnrollment.id == item.enrollment_id)
                  if _enrollment and _enrollment.status != "active":
                      item.status = "cancelled"
                      item.error_msg = "enrollment_%s" % _enrollment.status
                      item.sent_at = datetime.now()
                      item.save()
                      update_ledger_status(item.ledger_id, "cancelled",
                                           reason_code="enrollment_cancelled_at_send",
                                           reason_detail="Enrollment status is '%s', not active" % _enrollment.status)
                      logger.info("[Guard1] Cancelled queue #%s — enrollment #%s is %s",
                                  item.id, item.enrollment_id, _enrollment.status)
                      return 0
              except Exception:
                  pass  # Fail-open: if check fails, proceed with send
```

Note: `_enrollment` is initialized to `None` BEFORE the if-block so Guard 2 can reuse it safely.

- [ ] **Step 3: Add Guard 2 — Recent purchase since enrollment?**

After Guard 1, add:

```python
          # ── Guard 2: Purchase since enrollment? (recovery flows only) ──
          if item.email_type == "flow" and item.enrollment_id:
              try:
                  if not _enrollment:
                      _enrollment = FlowEnrollment.get_or_none(
                          FlowEnrollment.id == item.enrollment_id)
                  if _enrollment:
                      _flow = Flow.get_or_none(Flow.id == _enrollment.flow_id)
                      if _flow and _flow.trigger_type in ("checkout_abandoned", "browse_abandonment"):
                          _has_order = ShopifyOrder.select().where(
                              ShopifyOrder.email == item.email,
                              ShopifyOrder.ordered_at >= _enrollment.enrolled_at,
                              ShopifyOrder.financial_status.not_in(["refunded", "voided"]),
                          ).exists()
                          if _has_order:
                              item.status = "cancelled"
                              item.error_msg = "purchased_after_enrollment"
                              item.sent_at = datetime.now()
                              item.save()
                              _enrollment.status = "cancelled"
                              _enrollment.save()
                              update_ledger_status(item.ledger_id, "cancelled",
                                                   reason_code="purchased_after_enrollment",
                                                   reason_detail="Order found after enrollment at %s" % _enrollment.enrolled_at)
                              logger.info("[Guard2] Cancelled queue #%s — purchase found after enrollment", item.id)
                              return 0
              except Exception:
                  pass  # Fail-open
```

- [ ] **Step 4: Add Guard 3 — Recent hard bounce or complaint?**

After Guard 2, add:

```python
          # ── Guard 3: Recent hard bounce or complaint? ──
          if item.email_type == "flow":
              try:
                  _seven_days_ago = datetime.now() - timedelta(days=7)
                  _has_bounce = BounceLog.select().where(
                      BounceLog.email == item.email,
                      BounceLog.event_type.in_(["Bounce", "Complaint"]),
                      BounceLog.timestamp >= _seven_days_ago,
                  ).exists()
                  if _has_bounce:
                      item.status = "cancelled"
                      item.error_msg = "recent_bounce_or_complaint"
                      item.sent_at = datetime.now()
                      item.save()
                      # Add suppression if not present
                      SuppressionEntry.get_or_create(
                          email=item.email,
                          defaults={"reason": "bounce", "source": "delivery_guard", "detail": "Auto-suppressed by pre-send guard"})
                      # Cancel ALL active enrollments for this contact (null-safe)
                      if item.contact:
                          FlowEnrollment.update(status="cancelled").where(
                              FlowEnrollment.contact == item.contact,
                              FlowEnrollment.status.in_(["active", "paused"]),
                          ).execute()
                      # Cancel ALL pending queue items for this email
                      DeliveryQueue.update(status="cancelled", error_msg="bounce_suppressed").where(
                          DeliveryQueue.email == item.email,
                          DeliveryQueue.status == "queued",
                          DeliveryQueue.id != item.id,
                      ).execute()
                      update_ledger_status(item.ledger_id, "cancelled",
                                           reason_code="bounce_suppressed",
                                           reason_detail="Hard bounce or complaint in last 7 days")
                      logger.info("[Guard3] Bounce-suppressed queue #%s for %s", item.id, item.email)
                      return 0
              except Exception:
                  pass  # Fail-open
```

Note: Guard 3 uses `if item.contact:` null-check before cancelling enrollments (DeliveryQueue.contact can be NULL). The queue item cancellation falls back to email-based matching which always works.

- [ ] **Step 4: Verify delivery_engine imports cleanly**

Run:
```bash
python -c "from delivery_engine import process_queue; print('delivery_engine OK')"
```
Expected: `delivery_engine OK`

- [ ] **Step 5: Commit**

```bash
git add delivery_engine.py
git commit -m "feat: add 3 pre-send guards — enrollment check, purchase check, bounce check"
```

---

## Chunk 4: Flow Fitness Evaluation + Richer Condition Engine

### Task 7: Implement evaluate_flow_fitness()

**Files:**
- Modify: `customer_intelligence.py` (replace the stub from Task 4)

- [ ] **Step 1: Implement the full function**

Replace the `evaluate_flow_fitness()` stub with:

```python
def evaluate_flow_fitness(contact_id):
    """
    Check if contact's active flow enrollments still match their refreshed profile.
    Auto-exits flows that no longer make sense after a profile recompute.

    Exit rules:
    - checkout_abandoned / browse_abandonment: exit if order placed since enrollment
    - no_purchase_days (winback): exit if lifecycle changed from churned/at_risk to active+
    - contact_created (welcome): exit if lifecycle changed from prospect/new_customer to active+
    """
    from database import (Contact, CustomerProfile, Flow, FlowEnrollment,
                          ShopifyOrder, DeliveryQueue, init_db)
    from action_ledger import log_action
    init_db()

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return

    profile = CustomerProfile.get_or_none(CustomerProfile.contact_id == contact_id)
    if not profile:
        return

    # Get all active/paused enrollments
    enrollments = list(FlowEnrollment.select(
        FlowEnrollment, Flow
    ).join(Flow).where(
        FlowEnrollment.contact == contact,
        FlowEnrollment.status.in_(["active", "paused"]),
    ))

    active_lifecycle = profile.lifecycle_stage or "unknown"
    positive_stages = {"active_buyer", "loyal", "vip", "reactivated"}

    for enrollment in enrollments:
        flow = enrollment.flow
        should_exit = False
        exit_reason = ""

        if flow.trigger_type in ("checkout_abandoned", "browse_abandonment"):
            # Exit if order placed since enrollment
            has_order = ShopifyOrder.select().where(
                ShopifyOrder.email == contact.email,
                ShopifyOrder.ordered_at >= enrollment.enrolled_at,
                ShopifyOrder.financial_status.not_in(["refunded", "voided"]),
            ).exists()
            if has_order:
                should_exit = True
                exit_reason = "Order placed since enrollment (trigger: %s)" % flow.trigger_type

        elif flow.trigger_type == "no_purchase_days":
            # Winback: exit if lifecycle improved
            if active_lifecycle in positive_stages:
                should_exit = True
                exit_reason = "Lifecycle improved to '%s' (winback no longer relevant)" % active_lifecycle

        elif flow.trigger_type == "contact_created":
            # Welcome: exit if they became a buyer
            if active_lifecycle in positive_stages:
                should_exit = True
                exit_reason = "Lifecycle changed to '%s' (no longer a new prospect)" % active_lifecycle

        if should_exit:
            old_status = enrollment.status
            enrollment.status = "cancelled"
            enrollment.save()

            # Cancel pending queue items for this enrollment
            DeliveryQueue.update(
                status="cancelled", error_msg="flow_fitness_exit"
            ).where(
                DeliveryQueue.enrollment_id == enrollment.id,
                DeliveryQueue.status == "queued",
            ).execute()

            log_action(
                contact, "flow", flow.id, "cancelled", "flow_fitness_exit",
                source_type=flow.name,
                enrollment_id=enrollment.id,
                reason_detail=exit_reason,
            )
            logger.info("[FlowFitness] Exited '%s' for contact #%s (was %s) — %s",
                        flow.name, contact_id, old_status, exit_reason)
```

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
python -c "from customer_intelligence import evaluate_flow_fitness; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add customer_intelligence.py
git commit -m "feat: implement evaluate_flow_fitness() — auto-exit mismatched flow enrollments"
```

---

### Task 8: Expand condition_engine.get_contact_context() with 6 new fields

**Files:**
- Modify: `condition_engine.py:213-255`

- [ ] **Step 1: Add new fields to the context dict**

In `get_contact_context()` (line 213), expand the initial `ctx` dict to include new fields with safe defaults:

```python
    ctx = {
        "lifecycle_stage": "unknown",
        "customer_type": "unknown",
        "total_orders": 0,
        "total_spent": 0.0,
        "days_since_last_order": 999,
        "has_used_discount": False,
        "tags": getattr(contact, "tags", "") or "",
        "source": getattr(contact, "source", "manual") or "manual",
        # ── New intelligence fields ──
        "intent_score": 0,
        "churn_risk_score": 0,
        "reorder_likelihood": 0,
        "top_category": "unknown",
        "discount_sensitivity": 0.0,
        "fatigue_score": 0,
    }
```

- [ ] **Step 2: Populate fatigue_score from Contact model**

After the existing Contact field reads (around the `ctx["source"]` line), add:

```python
    ctx["fatigue_score"] = getattr(contact, "fatigue_score", 0) or 0
```

- [ ] **Step 3: Populate new fields from CustomerProfile**

Inside the `try: profile = CustomerProfile.get(...)` block, after the existing profile field reads, add:

```python
        # New intelligence fields
        ctx["intent_score"] = profile.intent_score or 0
        ctx["churn_risk_score"] = profile.churn_risk_score or 0
        ctx["reorder_likelihood"] = profile.reorder_likelihood or 0
        ctx["discount_sensitivity"] = float(profile.discount_sensitivity or 0.0)

        # Derive top_category from category_affinity_json
        try:
            import json
            affinity = json.loads(profile.category_affinity_json or "{}")
            if affinity and isinstance(affinity, dict):
                ctx["top_category"] = max(affinity, key=affinity.get)
            else:
                ctx["top_category"] = "unknown"
        except (json.JSONDecodeError, ValueError):
            ctx["top_category"] = "unknown"
```

- [ ] **Step 4: Verify it works**

Run:
```bash
python -c "
from database import init_db, Contact
from condition_engine import get_contact_context
init_db()
c = Contact.select().first()
if c:
    ctx = get_contact_context(c)
    new_fields = ['intent_score', 'churn_risk_score', 'reorder_likelihood', 'top_category', 'discount_sensitivity', 'fatigue_score']
    for f in new_fields:
        print(f'{f}: {ctx.get(f)}')
    print('OK')
else:
    print('No contacts')
"
```
Expected: All 6 fields printed with values, ending with `OK`.

- [ ] **Step 5: Commit**

```bash
git add condition_engine.py
git commit -m "feat: expand condition engine with intent, churn, reorder, category, discount, fatigue fields"
```

---

## Chunk 5: Final Integration + Verification

### Task 9: Add new ActionLedger reason codes

**Files:**
- Modify: `action_ledger.py`

- [ ] **Step 1: Add reason code constants**

Find the existing `RC_` constants in `action_ledger.py` and add:

```python
RC_PROFILE_REFRESH = "profile_refresh"
RC_ENROLLMENT_CANCELLED = "enrollment_cancelled_at_send"
RC_PURCHASED_AFTER_ENROLL = "purchased_after_enrollment"
RC_BOUNCE_SUPPRESSED = "bounce_suppressed"
RC_FLOW_FITNESS_EXIT = "flow_fitness_exit"
```

If there are no existing constants and reason codes are used as inline strings, match that pattern instead — use the strings directly in the code (already done in previous tasks).

- [ ] **Step 2: Commit**

```bash
git add action_ledger.py
git commit -m "feat: add RC_ constants for smart flow reason codes"
```

---

### Task 10: End-to-end verification

- [ ] **Step 1: Verify all imports work together**

Run:
```bash
python -c "
from database import init_db
init_db()
from customer_intelligence import schedule_profile_refresh, refresh_contact_profile, evaluate_flow_fitness
from delivery_engine import process_queue
from condition_engine import get_contact_context
from app import app
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 2: Verify the full refresh flow**

Run:
```bash
python -c "
from database import init_db, Contact, CustomerProfile
init_db()
c = Contact.select().first()
if c:
    from customer_intelligence import schedule_profile_refresh
    schedule_profile_refresh(c.id, 'test_verification')
    p = CustomerProfile.get_or_none(CustomerProfile.contact_id == c.id)
    print(f'Scheduled: {p.refresh_scheduled_at}')
    print(f'Trigger: {p.last_refresh_trigger}')
    # Clean up
    p.refresh_scheduled_at = None
    p.last_refresh_trigger = None
    p.save()
    print('Verification PASSED')
else:
    print('No contacts')
"
```
Expected: `Verification PASSED`

- [ ] **Step 3: Final commit with all files**

```bash
git add -A
git status
git commit -m "feat: smart flows — real-time intelligence, pre-send guards, flow fitness evaluation

- Real-time profile refresh: 15-min silence-after-last-activity debounce
- 3 pre-send guards: enrollment check, purchase check, bounce check
- Flow fitness evaluation: auto-exit mismatched enrollments
- Richer condition engine: 6 new intelligence fields for template variants
- Replaces _rebuild_stale_profiles with smarter _process_profile_refresh_queue"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Database migration (2 new fields) | database.py |
| 2 | schedule_profile_refresh() | customer_intelligence.py |
| 3 | Wire to 7 qualifying event endpoints | app.py |
| 4 | refresh_contact_profile() wrapper | customer_intelligence.py |
| 5 | Scheduler job + remove old rebuilder | app.py |
| 6 | 3 pre-send guards | delivery_engine.py |
| 7 | evaluate_flow_fitness() | customer_intelligence.py |
| 8 | 6 new condition engine fields | condition_engine.py |
| 9 | ActionLedger reason codes | action_ledger.py |
| 10 | End-to-end verification | all |
