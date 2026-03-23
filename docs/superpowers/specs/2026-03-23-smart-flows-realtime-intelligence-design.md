# Smart Flows: Real-Time Intelligence & Pre-Send Guards

**Date**: 2026-03-23
**Status**: Design approved, pending implementation

## Problem

Flows operate on a 24-hour-stale snapshot of customer intelligence. The nightly enrichment pipeline (3:30 AM) computes lifecycle stage, intent score, churn risk, and 40+ other fields — but flows don't see any of that until the next morning. This creates embarrassing scenarios:

- Cart recovery email sent to a customer who already bought
- Winback "we miss you" email sent to someone who just placed an order at 10 AM
- Flow template variants branch on yesterday's lifecycle stage, not today's reality
- Orphaned delivery queue items send after flow enrollment is cancelled
- No bounce/complaint check before flow sends

## Solution

Six interconnected fixes that make flows react to real-time customer behavior:

1. **Delayed profile refresh engine** — full recompute 15 minutes after a contact's last qualifying activity
2. **Pre-send enrollment guard** — verify enrollment is still active before sending
3. **Pre-send purchase guard** — check for orders placed since enrollment
4. **Pre-send bounce guard** — check for recent hard bounces/complaints
5. **Flow fitness evaluation** — auto-exit flows that no longer match the contact's refreshed profile
6. **Richer condition engine** — expose intent_score, churn_risk, reorder_likelihood, discount_sensitivity, fatigue_score to template variant conditions

## Architecture

### Relationship to Existing `_rebuild_stale_profiles()`

There is an existing scheduler job `_rebuild_stale_profiles()` in app.py (line ~7047) that runs every 5 minutes and recomputes profiles where `last_active_at > last_intelligence_at` with a 10-minute cooldown. **This new system replaces it.**

The existing job is a blunt instrument — it triggers on any activity update (including passive opens) with a fixed 10-minute cooldown. The new system is smarter:
- Only qualifies on high-intent events (not opens)
- Uses a 15-minute "silence after last activity" pattern to capture the full session
- Adds flow fitness evaluation after recompute
- Adds pre-send guards

**Migration**: Remove the `_rebuild_stale_profiles()` scheduler job and its function. The new `_process_profile_refresh_queue()` replaces it entirely. The nightly batch remains unchanged.

### Event Flow

```
Qualifying activity arrives (product_view, click, purchase, cart_abandon, checkout)
  |
  v
schedule_profile_refresh(contact, event_type)
  -> Sets CustomerProfile.refresh_scheduled_at = now() + 15 min
  -> If already set, overwrites (pushes timer forward)
  -> Near-zero cost: single field update
  |
  v
...contact continues browsing / buying / leaves...
  |
  v
15 minutes of silence pass
  |
  v
_process_profile_refresh_queue() [scheduler, every 60s]
  -> Finds contacts where refresh_scheduled_at <= now()
  -> For each:
     1. Run full compute_intelligence() for this one contact
     2. Run evaluate_flow_fitness(contact) — check/exit mismatched flows
     3. Clear refresh_scheduled_at = NULL
     4. Stamp last_intelligence_at = now()
     5. Stamp last_refresh_trigger = event_type
     6. Log to ActionLedger (RC_PROFILE_REFRESH)
  -> Error handling: if compute_intelligence() fails for a contact,
     log the error, clear refresh_scheduled_at (prevent infinite retry),
     and continue to next contact. The nightly batch will catch it.
```

### Pre-Send Guards (in delivery_engine._send_one())

Guards are inserted in the standalone function `_send_one(item, send_fn)` in `delivery_engine.py`, after the existing subscription check (line ~295). Each guard runs independently per queue item — no caching across items in the same batch.

```
Queue item ready to send (email_type == "flow")
  |
  v
Guard 1: Enrollment still active?
  -> Query FlowEnrollment by item.enrollment_id
  -> If status != "active": set item.status = "cancelled", log RC_ENROLLMENT_CANCELLED
  |
  v
Guard 2: Recent purchase since enrollment? (recovery flows only)
  -> Look up the Flow via FlowEnrollment to get trigger_type
  -> Only for trigger_type in: ["checkout_abandoned", "browse_abandonment"]
     (these are the actual Flow.trigger_type values used in the database;
      cart abandonment flows also use "checkout_abandoned" trigger_type)
  -> Query: ShopifyOrder.select().where(
       ShopifyOrder.email == item.email,
       ShopifyOrder.ordered_at >= enrollment.enrolled_at,
       ShopifyOrder.financial_status != "refunded",
       ShopifyOrder.financial_status != "voided"
     ).exists()
  -> If found: cancel queue item, cancel enrollment, log RC_PURCHASED_AFTER_ENROLL
  |
  v
Guard 3: Recent hard bounce or complaint?
  -> Query BounceLog.select().where(
       BounceLog.email == item.email,
       BounceLog.event_type.in_(["Bounce", "Complaint"]),
       BounceLog.timestamp >= now() - 7 days
     ).exists()
  -> If found:
     - Cancel this queue item
     - Add SuppressionEntry if not already present
     - Cancel ALL active FlowEnrollments for this contact (all flows)
     - Cancel ALL pending DeliveryQueue items for this contact
     - Log RC_BOUNCE_SUPPRESSED
  |
  v
Proceed with SES send
```

### Flow Fitness Evaluation

Called after every real-time profile recompute. Checks active enrollments against refreshed profile.

**Exit rules by flow trigger type:**

| Flow.trigger_type       | Auto-Exit Condition                                                                 |
|-------------------------|-------------------------------------------------------------------------------------|
| `checkout_abandoned`    | Contact has placed a non-refunded/non-voided order since enrollment                 |
| `browse_abandonment`    | Contact has placed a non-refunded/non-voided order since enrollment                 |
| `no_purchase_days`      | lifecycle_stage changed from churned/at_risk to active_buyer/loyal/vip              |
| `contact_created`       | lifecycle_stage changed from prospect/new_customer to active_buyer+                 |

Note: `cart_abandonment` is not a Flow.trigger_type in the database — cart recovery flows use `checkout_abandoned` as their trigger type. The trigger type values above match the actual values stored in the `Flow.trigger_type` field.

What it does NOT do:
- Does not enroll contacts in new flows
- Does not modify flow step timing
- Does not touch flows with trigger types not listed above (e.g. tag_added, order_placed)

Cancels pending DeliveryQueue items for any exited enrollment (query by enrollment_id, set status="cancelled").

Logs every exit to ActionLedger with `reason_code="RC_FLOW_FITNESS_EXIT"` and `reason_detail` explaining the change.

### Richer Condition Engine

Expand `condition_engine.get_contact_context()` to include:

| New Field              | Source                         | Type    | Use Case                                                     |
|------------------------|--------------------------------|---------|--------------------------------------------------------------|
| `intent_score`         | CustomerProfile.intent_score   | int     | Template variants for high-intent vs low-intent messaging    |
| `churn_risk_score`     | CustomerProfile.churn_risk_score | int   | Winback urgency — desperate tone for high churn, casual for low |
| `reorder_likelihood`   | CustomerProfile.reorder_likelihood | int | Skip contacts unlikely to repeat purchase                    |
| `top_category`         | Derived (see below)            | string  | Product recommendations matched to interests                 |
| `discount_sensitivity` | CustomerProfile.discount_sensitivity | float | Show discount block only for discount-responsive contacts |
| `fatigue_score`        | Contact.fatigue_score (NOT CustomerProfile) | int | Skip or simplify emails for fatigued contacts     |

**`top_category` derivation**: Parsed from `CustomerProfile.category_affinity_json` (a JSON string of `{"category": score}` pairs). Returns the key with the highest score. If `category_affinity_json` is NULL, empty string, `"{}"`, or malformed JSON, returns `"unknown"`. This is a string field for condition evaluation — works with `eq`, `neq`, `in`, `contains` operators.

**`fatigue_score` note**: This field lives on the `Contact` model (database.py line ~39), not on `CustomerProfile`. The `get_contact_context()` function already reads from Contact for `tags` and `source` — `fatigue_score` follows the same pattern.

No new operators needed — existing gt, lt, eq, in operators work for all numeric and string fields.

No changes to existing templates. New fields are only used when someone adds variant conditions referencing them.

## Qualifying Events

Events that schedule a profile refresh (15 min after last activity):

| Event                  | Source                          | Qualifies | Injection Point in app.py                        |
|------------------------|---------------------------------|-----------|--------------------------------------------------|
| `placed_order`         | Shopify order webhook           | Yes       | `webhook_shopify_order_create()` (line ~995)     |
| `completed_checkout`   | Shopify checkout webhook        | Yes       | `webhook_shopify_checkout_create()` (line ~918)   |
| `viewed_product`       | Website tracking pixel          | Yes       | `track_event()` API (line ~6627)                 |
| `product_search`       | Website tracking pixel          | Yes       | `track_event()` API (line ~6627)                 |
| `add_to_cart`          | Website tracking pixel          | Yes       | `track_event()` API (line ~6627)                 |
| `email_click`          | Click tracking endpoint         | Yes       | `track_flow_click()` (line ~2360), `track_auto_click()` (line ~2466) |
| `checkout_abandoned`   | Passive trigger detection       | Yes       | `_check_passive_triggers()` (line ~3651)         |
| `cart_abandoned`       | Passive trigger detection       | Yes       | `_check_passive_triggers()` (line ~3651)         |
| `email_open`           | Open tracking pixel             | No        | —                                                |
| `email_delivered`      | SES webhook                     | No        | —                                                |
| `page_view` (non-product) | Website tracking pixel       | No        | —                                                |

Each injection point adds a single call: `schedule_profile_refresh(contact_id, event_type)`. In `track_event()`, the call is conditional on event_type being in the qualifying list.

## Database Changes

Two new fields on `CustomerProfile`:

| Field                  | Type                   | Default | Purpose                                              |
|------------------------|------------------------|---------|------------------------------------------------------|
| `refresh_scheduled_at` | DateTimeField, null    | NULL    | When the 15-min-after-last-activity refresh is due   |
| `last_refresh_trigger` | CharField(50), null    | NULL    | What event caused the last refresh                   |

No new models. No new tables.

**Migration**: Add a new `_migrate_smart_flow_fields()` function in `database.py` following the existing `_migrate_intelligence_fields()` pattern (line ~945): use `PRAGMA table_info` to check for existing columns, then `ALTER TABLE ADD COLUMN` for missing ones. Call this function from `init_db()`.

## Files Modified

| File                      | Changes                                                                                       |
|---------------------------|-----------------------------------------------------------------------------------------------|
| `database.py`             | Add `refresh_scheduled_at` and `last_refresh_trigger` to CustomerProfile model + migration    |
| `customer_intelligence.py`| Add `schedule_profile_refresh()`, `refresh_contact_profile()`, `evaluate_flow_fitness()`       |
| `delivery_engine.py`      | Add 3 pre-send guards in `_send_one()` after existing subscription check (line ~295)          |
| `condition_engine.py`     | Expand `get_contact_context()` with 6 new fields (5 from CustomerProfile, 1 from Contact)     |
| `app.py`                  | Remove `_rebuild_stale_profiles()` and its scheduler entry. Add `_process_profile_refresh_queue()` scheduler job (every 60s). Add `schedule_profile_refresh()` calls at injection points listed above |

No new files created. All changes follow existing architecture (routes in app.py, models in database.py).

## Interaction with Existing Systems

- **`_rebuild_stale_profiles()`**: Removed. Replaced by `_process_profile_refresh_queue()`.
- **Nightly batch (3:30 AM)**: Continues unchanged as the authoritative full sweep. At the START of `enrich_all_contacts()`, clear all `refresh_scheduled_at` values (bulk update to NULL) to prevent the real-time job from re-processing contacts the nightly batch is about to handle. If a qualifying event fires DURING the nightly batch (unlikely but possible), the new `refresh_scheduled_at` will persist since the bulk clear already happened — the real-time job will pick it up after nightly completes. No conflict.
- **AI Engine / Next-Best-Message**: No changes. They already skip flow-enrolled contacts.
- **Flow processor**: No changes to enrollment/step-advancement logic. Flow fitness evaluation runs separately after profile refresh.
- **Existing templates**: Unchanged. New condition fields are additive — only used if template variants reference them.

## Volume Estimate

- ~100 active website visitors per hour at peak
- Each visitor generates 1 refresh (15 min after session ends)
- Full enrichment: ~10-15 seconds per contact
- Worst case: ~25 minutes of compute spread across an hour
- Scheduler processes them one at a time, every 60 seconds
- No risk of overload — nightly batch already processes ~6,000 contacts in one run

## Error Handling

- **`compute_intelligence()` failure**: Log the error, clear `refresh_scheduled_at` to prevent infinite retry loop, continue to next contact. The nightly batch at 3:30 AM will catch any missed profiles.
- **Guard query failure**: Log the error, allow the send to proceed (fail-open). Better to send a potentially stale email than silently drop it. The ActionLedger will show the guard error for debugging.
- **`evaluate_flow_fitness()` failure**: Log the error, do not exit the flow. Fail-safe — flows continue as-is rather than accidentally exiting.

## New ActionLedger Reason Codes

| Code                       | Meaning                                                    |
|----------------------------|------------------------------------------------------------|
| `RC_PROFILE_REFRESH`       | Profile recomputed due to real-time activity                |
| `RC_ENROLLMENT_CANCELLED`  | Queue item cancelled — enrollment no longer active          |
| `RC_PURCHASED_AFTER_ENROLL`| Queue item cancelled — contact purchased after enrollment   |
| `RC_BOUNCE_SUPPRESSED`     | Queue item cancelled — recent hard bounce/complaint         |
| `RC_FLOW_FITNESS_EXIT`     | Flow enrollment exited — profile no longer matches flow     |
