# Flow / Account Manager Handover Design

> **Date**: 2026-03-21
> **Status**: Draft
> **Scope**: 4 code changes across app.py, account_manager.py, next_best_message.py, ai_engine.py

## Problem

The customer lifecycle has three phases:
1. **Website visit** triggers behavioral Flows (welcome, browse/cart/checkout recovery)
2. **Flows complete** and hand off to **Account Manager** (AI-driven per-contact strategy)
3. **Customer returns** to website, triggering behavioral Flows again

The handover between Flows and AM has 5 gaps:
- Flows don't check AM enrollment before enrolling (AM contact gets double-messaged)
- AM skips flow-active contacts but doesn't pause its schedule or know what the flow did
- When flows complete for a returning AM contact, AM resumes the old strategy without considering what happened
- NBM and AI Engine are completely unaware of Flow/AM ownership (score and email everyone)
- No cross-system deduplication at delivery layer (deferred — upstream systems should prevent conflicts)

## Ownership Model

Every contact has an **owner** at any point:

```
UNMANAGED  → No active flows, no AM strategy
FLOW_OWNED → At least one active/paused FlowEnrollment
AM_OWNED   → ContactStrategy.enrolled == True, no active flows
```

Ownership is derived from existing DB state (no new columns needed for this):
- `FLOW_OWNED`: `FlowEnrollment.status IN ('active','paused')` exists
- `AM_OWNED`: `ContactStrategy.enrolled == True` AND no active/paused FlowEnrollment
- `UNMANAGED`: Neither

Transitions:
```
UNMANAGED  → FLOW_OWNED   (any trigger fires)
FLOW_OWNED → AM_OWNED     (all flows complete → maybe_handover_from_flow)
AM_OWNED   → FLOW_OWNED   (behavioral trigger fires → flows take over, AM pauses)
FLOW_OWNED → AM_OWNED     (flows complete → strategy resharpened with context)
```

## Change 1: Flow Enrollment Pauses AM

**File**: `app.py` — `_enroll_contact_in_flows()` (line 2760)

**Current behavior**: Enrolls contact in matching flows. No AM awareness.

**New behavior**: After successful FlowEnrollment.create(), check if contact has `ContactStrategy.enrolled == True`. If yes, **and only on the first flow enrollment** (guard: `"pause_context" not in strategy_data`), record the pause:

1. Store pause context in `strategy_json` (only if not already paused):
   ```python
   cs = ContactStrategy.get_or_none(ContactStrategy.contact == contact, ContactStrategy.enrolled == True)
   if cs:
       strategy_data = json.loads(cs.strategy_json or "{}")
       if "pause_context" not in strategy_data:  # Only first flow records the pause
           strategy_data["pause_context"] = {
               "paused_at": datetime.now().isoformat(),
               "paused_by_flow": flow.name,
               "flow_trigger": trigger_type,
               "previous_next_action_date": cs.next_action_date.isoformat() if cs.next_action_date else None,
               "previous_next_action_type": cs.next_action_type
           }
           cs.strategy_json = json.dumps(strategy_data)
           cs.next_action_type = "paused_for_flow"
           cs.save()
           log_action(contact=contact, email=contact.email, trigger_type="flow",
                      source_type="account_manager", status="paused",
                      reason_code="RC_AM_PAUSED", reason_detail=f"AM paused — contact entered {flow.name}")
   ```
2. If contact matches multiple flows in the same call, only the first writes `pause_context`. Subsequent enrollments skip (guard prevents overwrite).
3. The AM pause state is **derived from FlowEnrollment existence** at runtime, not from `pause_context`. The `pause_context` is metadata for the resharpen step — it records WHEN and WHY the pause started.

**Why `next_action_type = "paused_for_flow"`**: Makes pause visible in the AM dashboard. AM's nightly run already skips contacts with active flows (account_manager.py:874-881), so the existing skip logic still works. The `next_action_type` flag is informational — it tells the operator why AM isn't acting.

**Why not a new DB column**: `strategy_json` already holds freeform data. Adding `pause_context` as a key keeps the schema stable. The pause state is derived from FlowEnrollment existence, not from a flag — this prevents stale flags.

**ActionLedger convention**: Uses `trigger_type="flow"` (existing convention) with `source_type="account_manager"` and `reason_code="RC_AM_PAUSED"` to stay consistent with the documented trigger_type values (flow/campaign/ai_plan).

## Change 2: Flow Completion Resharpens AM Strategy

**File**: `account_manager.py` — `maybe_handover_from_flow()` (line 1228)

**Current behavior**: If contact has no remaining active/paused flows AND isn't enrolled in AM → creates new ContactStrategy. If already enrolled → returns None (no-op).

**New behavior**: Three paths:

### Path A — First-time handover (not AM-enrolled)
Existing behavior unchanged. Creates ContactStrategy with flow graduation context.

### Path B — Already enrolled, but NO `pause_context` (was never paused by flows)
Return None — no-op. This contact was already AM-managed and didn't go through flows. Existing behavior preserved.

### Path C — Returning AM contact (already enrolled, `pause_context` exists in strategy_json)

**Explicit guard:**
```python
existing = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
if existing and existing.enrolled:
    strategy_data = json.loads(existing.strategy_json or "{}")
    if "pause_context" not in strategy_data:
        return None  # Path B: already managed, no pause to resume from
    # Path C: resharpen strategy...
```
1. Gather **flow outcome context**:
   - Which flows ran (query FlowEnrollment where contact=contact, status='completed', enrolled_at >= pause_context.paused_at)
   - Per flow: count emails sent (FlowEmail), opens, clicks
   - Did the contact convert? (ShopifyOrder with ordered_at >= pause_context.paused_at)
   - What products were involved (from flow template families, trigger_data)

2. Gather **updated profile snapshot**:
   - Fresh CustomerProfile fields (lifecycle_stage, customer_type, intent_score, churn_risk, reorder_likelihood)
   - Updated Contact fields (total_orders, total_spent)

3. Gather **old strategy performance**:
   - total_approved, total_rejected, confidence_score
   - Which phase they were in (current_phase, current_phase_num)
   - What the old strategy summary was

4. Call Claude to **resharpen strategy**:
   ```
   System: You are a senior email marketing strategist for LDAS Electronics (ldas.ca).

   User prompt includes:
   - Previous strategy summary + performance metrics
   - Flow outcome: what flows ran, engagement, conversion
   - Updated customer profile
   - Signal: "Customer returned to website — previous strategy worked"

   Output: Updated strategy_json matching existing schema
   (phases, goals, product_focus, discount_approach, timing)
   ```

5. Update ContactStrategy:
   - `strategy_json` = new strategy (with `pause_context` cleared)
   - `current_phase` = first phase name from new strategy
   - `current_phase_num` = 1
   - `next_action_date` = now + `LearningConfig.get_val("am_resume_delay_days", 3)` days (configurable breathing room)
   - `next_action_type` = first action from new strategy
   - `strategy_version` += 1
   - `updated_at` = now

6. Log to ActionLedger: trigger_type="flow", source_type="account_manager", reason_code="RC_AM_RESUMED", reason_detail="AM resharpened — {flow_outcome_summary}"

**Thread safety note**: `maybe_handover_from_flow()` is called from `_process_flow_enrollments()` which runs in a single Flask scheduler thread. If two flows complete in the same tick, the `remaining = FlowEnrollment.select().where(status IN ['active','paused']).count()` check at line 1241 ensures only the last completed flow triggers the handover (earlier completions see remaining > 0). This is safe in the current single-threaded scheduler model.

**Cost**: 1 Claude API call per returning AM contact when flows complete. This fires on flow completion events, not nightly — volume is bounded by how many AM contacts re-trigger flows.

**Fallback**: If Claude API fails, restore previous strategy from `pause_context.previous_*` fields, set `next_action_date` = now + 3 days, log error. Contact still gets served — just with the old strategy.

## Change 3: NBM Skips Owned Contacts

**File**: `next_best_message.py` — `decide_all_contacts()` (line ~750)

**Current behavior**: Iterates all subscribed contacts with a CustomerProfile. Scores 10 action types per contact.

**New behavior**: Before the main loop, build two exclusion sets:

```python
# Contacts owned by AM
am_owned_ids = set(
    cs.contact_id for cs in
    ContactStrategy.select(ContactStrategy.contact)
    .where(ContactStrategy.enrolled == True)
)

# Contacts in active flows
flow_owned_ids = set(
    fe.contact_id for fe in
    FlowEnrollment.select(FlowEnrollment.contact)
    .where(FlowEnrollment.status.in_(["active", "paused"]))
)

owned_ids = am_owned_ids | flow_owned_ids
```

In the loop, skip contacts in `owned_ids`:
```python
if contact.id in owned_ids:
    skipped_owned += 1
    continue
```

Log at end: `"NBM: skipped {n} AM-owned, {n} flow-owned contacts"`

**Impact on Campaign Planner**: Since Campaign Planner groups NBM decisions into SuggestedCampaign rows, fewer decisions = smaller/fewer campaigns. This is correct — owned contacts shouldn't be in bulk campaigns.

**Long-term direction**: AM replaces NBM entirely. This skip filter is the first step toward phasing NBM out.

**Scaling note**: The exclusion sets load all AM/flow-owned contact IDs into memory as Python sets. This is consistent with the existing pattern in `decide_all_contacts()` which already iterates all contacts in-memory. For the current scale (~6,000 contacts), this is fine. If the contact base grows to tens of thousands, both the exclusion sets and the main loop should move to SQL-level filtering (subquery WHERE NOT IN). Not a blocker for implementation.

## Change 4: AI Engine Skips Owned Contacts

**File**: `ai_engine.py` — `execute_plan()` (line ~596)

**Current behavior**: Sends plan-based emails to RFM segments. Self-dedups via 3-day AIDecisionLog + CampaignEmail check. No awareness of Flows or AM.

**New behavior**: Build the same `owned_ids` set as Change 3. Add owned contacts to the `recently_emailed` exclusion set:

```python
# Existing dedup
recently_emailed = set()
for r in AIDecisionLog.select()...:
    recently_emailed.add(r.contact_id)
for r in CampaignEmail.select()...:
    recently_emailed.add(r.contact_id)

# NEW: Add owned contacts
recently_emailed |= owned_ids
```

When a contact in `owned_ids` is skipped, log to AIDecisionLog with `status="skipped"`, reason including "owned by AM" or "in active flow".

## What We're NOT Changing

1. **DeliveryQueue** — No cross-system frequency cap at delivery layer. Upstream systems (this design) prevent conflicts.
2. **NBM role** — We're adding a skip filter, not restructuring NBM. Full phase-out is a separate project.
3. **Flow enrollment logic** — Flows still enroll AM contacts (this is intentional — flows handle behavioral urgency). The change is that AM gets notified and pauses.
4. **Flow priority system** — `_pause_lower_priority_enrollments()` stays as-is. Only Flow-vs-Flow priority, not Flow-vs-AM.
5. **Fatigue scoring** — Stays as-is. With proper ownership, fatigue becomes a secondary safety net rather than the primary coordination mechanism.

## Data Flow Summary

```
Customer visits website
    ↓
Behavioral trigger (browse/cart/checkout)
    ↓
_enroll_contact_in_flows()
    ├─ Create FlowEnrollment (existing)
    ├─ [NEW] If AM-enrolled: pause AM, store pause_context
    └─ Pause lower-priority flows (existing)
    ↓
Flow emails send over days/weeks
    ↓
All flows complete
    ↓
maybe_handover_from_flow()
    ├─ [EXISTING] First-time: create ContactStrategy
    └─ [NEW] Returning AM: resharpen strategy with flow context
    ↓
AM resumes with fresh strategy
    ↓
Nightly NBM + AI Engine
    └─ [NEW] Skip AM-owned and flow-owned contacts
```

## Files Modified

| File | Function | Change |
|------|----------|--------|
| `app.py` | `_enroll_contact_in_flows()` ~line 2760 | Add AM pause logic after FlowEnrollment.create() |
| `account_manager.py` | `maybe_handover_from_flow()` ~line 1228 | Add Path B: resharpen strategy for returning AM contacts |
| `next_best_message.py` | `decide_all_contacts()` ~line 750 | Add owned_ids exclusion set before main loop |
| `ai_engine.py` | `execute_plan()` ~line 596 | Add owned_ids to recently_emailed exclusion set |

No new files. No new DB models. No new routes.
