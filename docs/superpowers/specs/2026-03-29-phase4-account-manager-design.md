# Phase 4: Account Manager Pillar — Design Spec

> Created: 2026-03-29 | Status: Approved | Build order: Intelligence ✅ → Templates ✅ → Flows ✅ → **AM** → Campaigns

## Goal

Refactor AM into a real decision-driven 1:1 revenue system. AM decides whether to act or wait, what action to take, what products to focus on, whether to offer a discount, and when to send — all backed by shared intelligence, rendered through template_engine, queued through delivery_engine, with trustworthy send-time metadata for learning.

This phase does not rebuild Campaigns, Flows, or the trigger system.

---

## 1. New Module: `am_runtime.py`

One focused module (~500-600 lines) that owns the decision layer for AM. Replaces decision logic currently embedded in `run_account_manager()` and `generate_am_email_from_template()`.

### 1.1 Two Entry Points

```python
def build_am_decision(contact, strategy, intelligence=None):
    """
    Evaluate a due contact and decide the best action (or wait).
    Purely about deciding — no rendering, no side effects.

    Args:
        contact: Contact instance
        strategy: ContactStrategy instance
        intelligence: pre-fetched intelligence dict (optional, fetched if None)

    Returns dict:
        {
            "should_act": bool,
            "status": "ready" | "wait" | "skipped",
            "action_type": str,          # education, reorder_reminder, cross_sell, winback, loyalty, product_recommendation
            "objective": str,            # template family objective
            "strategy_phase": str,       # current phase name
            "candidate_products": list,  # concrete ProductImageCache-backed dicts
            "offer_context": dict|None,  # resolved discount or None
            "template_family": str,      # AM template family to use
            "scheduled_at": datetime,    # preferred send time
            "expected_value": float,     # 0-1 composite score
            "confidence": float,         # 0-1 confidence in this decision
            "reasoning": str,            # human-readable decision explanation
            "wait_until": datetime|None, # if status=wait, when to revisit
            "metadata": dict,            # for logging/learning
        }
    """


def execute_am_decision(contact, strategy, decision, template=None):
    """
    Execute a decided AM action: select template, generate AI copy,
    render via template_engine, create review row or queue autonomously.

    Args:
        contact: Contact instance
        strategy: ContactStrategy instance
        decision: dict from build_am_decision()
        template: EmailTemplate override (optional, for learning swap)

    Returns dict:
        {
            "status": "rendered" | "invalid" | "skipped",
            "subject": str|None,
            "html": str|None,
            "template_id": int,
            "render_result": dict|None,
            "ai_tokens": {"input": int, "output": int},
            "reason": str,
        }
    """
```

**Why two functions:** `build_am_decision` is purely about deciding — no rendering, no API calls, no side effects. `execute_am_decision` owns the template selection, AI copy generation, block overrides, template_engine rendering, and validation. This separation keeps the decision logic testable without mocking Claude or template_engine.

### 1.2 Decision Pipeline (build_am_decision)

1. **`_normalize_strategy(strategy_json)`** — convert legacy narrative to executable state
2. **`_check_preconditions(contact)`** — flow ownership, suppression, unsubscribe, sunset
3. **`_check_timing(contact, intelligence)`** — should_contact_now + preferred_send_hour/dow
4. **`_evaluate_candidates(contact, strategy_state, intelligence)`** — score each action type
5. **`_resolve_products(contact, action_type, intelligence)`** — concrete products from intelligence
6. **`_resolve_offer(contact, action_type, products)`** — intelligence_layer.get_discount_policy
7. Return assembled decision package

### 1.3 Execution Pipeline (execute_am_decision)

1. **Template selection** — AM_ACTION_TO_FAMILY map + learning swap
2. **AI copy generation** — call Claude with structured intelligence for block text
3. **Block overrides** — deep-copy template blocks_json, inject AI text into hero/text/cta
4. **Render** — template_engine.make_render_contract(source_system="am") + render_email()
5. **Validate** — if invalid, return status="invalid"
6. **Return** rendered subject/html/template_id for caller to create review row or enqueue

---

## 2. Action Ranking System

### 2.1 Candidate Evaluation

`_evaluate_candidates()` scores each action type 0.0-1.0 based on intelligence signals:

| Action | Primary Signal | Wins When |
|--------|---------------|-----------|
| `reorder_reminder` | `reorder_likelihood >= 0.6` + cycle approaching (`days_since_last_order / avg_days_between_orders >= 0.7`) | Reorder cycle is due |
| `cross_sell` | Strong category affinity in unbought categories + `intent_score >= 40` | Clear product opportunity exists |
| `winback` | `lifecycle_stage in ("lapsed", "at_risk")` or `churn_risk_score >= 60` | Customer is dormant/churning |
| `product_recommendation` | `intent_score >= 50` + `website_engagement_score >= 30` + recent browsing | Engaged browser, not buying |
| `loyalty` | `customer_type in ("vip", "loyal", "champion")` + low `discount_sensitivity` | Relationship > discount |
| `education` | New customer or low engagement, no strong signal | Default safe option |
| `wait` | All scores < threshold, or fatigue high, or recent send | Better to wait |

### 2.2 Strategy State Influence

- `allowed_actions` in executable strategy filters which actions are candidates
- `cadence_policy.preferred_gap_days` affects wait threshold
- `category_focus` / `product_focus` bias cross_sell and product_recommendation scoring
- `offer_policy.allow_discount` gates discount-dependent actions

### 2.3 Wait Threshold

If the best action score < 0.3, `wait` wins. Wait is a valid successful outcome — it means AM chose not to send rather than sending a mediocre email.

### 2.4 Performance Data Usage

Use `ActionPerformance` / `TemplatePerformance` / `TemplateSegmentPerformance` to boost scores for action types with proven performance for this contact's segment.

**Minimum sample size rule:** Only use learned performance data when `sample_size >= 20`. Below that threshold, fall back to heuristics from intelligence + strategy state. This prevents fake precision from thin data.

---

## 3. Executable Strategy State

### 3.1 Machine-Usable Schema

`_normalize_strategy()` reads `ContactStrategy.strategy_json` and returns executable state:

```python
{
    # Machine state — used by am_runtime for decisions
    "overall_goal": "maximize repeat purchase from electronics buyer",
    "current_phase_name": "Phase 1: Build Trust",
    "current_phase_goal": "education + product discovery",
    "allowed_actions": ["education", "product_recommendation", "cross_sell", "loyalty"],
    "cadence_policy": {
        "min_gap_days": 5,
        "max_gap_days": 14,
        "preferred_gap_days": 7
    },
    "offer_policy": {
        "allow_discount": false,
        "max_discount_pct": 10
    },
    "category_focus": "Cables",
    "product_focus": null,
    "next_action_type": "education",
    "next_action_date": "2026-04-05",
    "hypothesis": "customer responds to product tips",
    "last_outcome_summary": "last email opened, no click",

    # Flow graduation context (preserved from handover)
    "flow_graduation": {...},
    "pause_context": null,

    # Legacy compatibility — preserved for dashboard display, not used for runtime
    "phases": [...],
    "product_focus_legacy": "...",
    "discount_approach": "..."
}
```

### 3.2 Normalization Rules

When `_normalize_strategy()` encounters a legacy strategy_json:

1. If `phases` exists but no `allowed_actions` → extract from current phase tactic:
   - `"education"` tactic → `["education", "product_recommendation"]`
   - `"product_rec"` → `["product_recommendation", "cross_sell"]`
   - `"winback"` → `["winback", "loyalty", "education"]`
   - `"loyalty"` → `["loyalty", "education", "cross_sell"]`
   - Default → `["education", "product_recommendation", "cross_sell", "loyalty"]`
2. If no `cadence_policy` → populate defaults: `min_gap=5, max_gap=14, preferred_gap=7`
3. If no `offer_policy` → derive from `discount_approach` text or default `allow_discount=true, max_pct=10`
4. If no `current_phase_name` → use `current_phase` field from ContactStrategy model
5. Preserve all existing fields — never delete data from strategy_json

Normalization is idempotent — running it twice produces the same output.

### 3.3 Strategy Update on Wait

When AM decides `wait`:
- Update `ContactStrategy.next_action_date` to `decision["wait_until"]`
- Preserve `next_action_type` (the planned action hasn't changed, just deferred)
- Log the wait decision in ActionLedger with reason

When AM decides to act:
- After execution, advance strategy:
  - Update `next_action_type` based on cadence policy and action ranking
  - Update `next_action_date` based on `cadence_policy.preferred_gap_days`
  - Update `last_outcome_summary` with what was sent

---

## 4. AM Rendering via template_engine

### 4.1 Deprecation

`generate_am_email_from_template()` is deprecated. Phase 4 replaces it with `execute_am_decision()`. The old function can remain in account_manager.py with a deprecation comment for backward compatibility but is no longer called by the main nightly run.

### 4.2 Template Selection

```python
AM_ACTION_TO_FAMILY = {
    "education":              ("AM: Education", "post_purchase"),
    "product_recommendation": ("AM: Product Recommendation", "promo"),
    "winback":                ("AM: Win-Back", "winback"),
    "reorder_reminder":       ("AM: Reorder Reminder", "post_purchase"),
    "loyalty":                ("AM: Loyalty", "post_purchase"),
    "cross_sell":             ("AM: Cross-Sell", "promo"),
}
```

Template is selected by action_type. Learning swap (get_best_template_for_family) applied if available.

### 4.3 AI Copy Generation

Claude is called with **structured intelligence** (not `gather_contact_profile` prose):

```python
intel_text = intelligence_layer.format_intelligence_for_prompt(contact.id)
```

Plus decision context (action_type, products, offer, strategy phase). Claude returns JSON:
```json
{
    "hero_headline": "...",
    "hero_subheadline": "...",
    "paragraphs": ["...", "..."],
    "cta_text": "...",
    "cta_url": "..."
}
```

### 4.4 Block Override Approach (Option A — no template_engine changes)

1. Deep-copy template's `blocks_json`
2. Inject AI text into matching block types:
   - `hero` block → `headline`, `subheadline`
   - `text` block → `paragraphs`
   - `cta` block → `text`, `url`
3. Create a temporary template-like object with the modified blocks_json
4. Pass through `template_engine.make_render_contract(source_system="am", ...)`
5. Call `template_engine.render_email(contract)`

This requires zero template_engine changes. template_engine renders the modified blocks as if they were authored that way.

### 4.5 Render Validation

If `render_result["is_valid"]` is False → `execute_am_decision` returns `status="invalid"`. The caller does NOT create a pending review or enqueue. Invalid renders are logged with reason.

---

## 5. Send-Time Metadata for Learning

### 5.1 The Problem

`outcome_tracker._get_action_type()` (line 84) reads `ContactStrategy.next_action_type` at outcome-tracking time. But by then, AM may have advanced the strategy to a different action. This means the learning loop attributes outcomes to the wrong action type.

### 5.2 Additive Schema Changes

**AutoEmail — add 2 fields:**
```python
action_type    = CharField(default="")     # frozen at decision time
decision_json  = TextField(default="{}")   # snapshot of build_am_decision output
```

**AMPendingReview — add 2 fields:**
```python
decision_json  = TextField(default="{}")   # snapshot of build_am_decision output
template_id    = IntegerField(default=0)   # template used for this email
```

**DeliveryQueue — add 1 optional field:**
```python
decision_json  = TextField(default="{}")   # AM decision snapshot (optional, "" for flows/campaigns)
```

### 5.3 Autonomous Send Flow

For autonomous AM sends, pre-create the AutoEmail record with frozen metadata:

```python
auto_email = AutoEmail.create(
    contact=contact,
    template=template,
    subject=result["subject"],
    status="queued",
    auto_run_date=date.today(),
    action_type=decision["action_type"],         # frozen
    decision_json=json.dumps(decision["metadata"]),  # frozen
)

enqueue_email(
    contact=contact,
    email_type="auto",
    ...
    auto_email_id=auto_email.id,   # links back
    scheduled_at=decision["scheduled_at"],
)
```

This way the exact AM decision survives approval, queueing, send, and outcome tracking.

### 5.4 Review Send Flow

For review-mode AM sends:

```python
AMPendingReview.create(
    contact=contact,
    strategy=strategy,
    subject=result["subject"],
    body_html=result["html"],
    action_type=decision["action_type"],
    template_id=result["template_id"],
    decision_json=json.dumps(decision["metadata"]),
    status="pending",
)
```

On approval, `approve_email()` creates the AutoEmail with frozen action_type from the pending review record.

### 5.5 Outcome Tracker Update

Update `outcome_tracker._get_action_type()` to prefer frozen metadata:

```python
def _get_action_type(contact_id, auto_email_id=None):
    # 1. Prefer frozen action_type from AutoEmail
    if auto_email_id:
        ae = AutoEmail.get_or_none(AutoEmail.id == auto_email_id)
        if ae and ae.action_type:
            return ae.action_type

    # 2. Fall back to ContactStrategy (legacy path)
    cs = ContactStrategy.get_or_none(ContactStrategy.contact == contact_id)
    if cs and cs.next_action_type:
        return cs.next_action_type

    # 3. Fall back to flow
    ...
```

### 5.6 Backward Compatibility

All new fields default to empty string / "{}" / 0. Existing flows and campaigns are unaffected:
- `DeliveryQueue.decision_json` defaults to "{}" — flows and campaigns never set it
- `AutoEmail.action_type` defaults to "" — old records continue using the ContactStrategy fallback
- `enqueue_email()` signature adds `decision_json=""` as optional kwarg — no callers break

---

## 6. Timing

### 6.1 `_check_timing()` in am_runtime

```python
def _check_timing(contact, intelligence):
    """
    Determine if now is a good time to contact and compute optimal send time.
    AM is NOT urgent — always respects timing fully.

    Returns:
        ("ok", scheduled_at) — proceed with send at this time
        ("wait", wait_until) — defer, try again at wait_until
    """
```

**Logic:**
1. Call `intelligence_layer.should_contact_now(contact.id)`
2. If `can_send == False` for any reason (too_soon, weekly_cap, etc.) → return `("wait", next_available_at)`
3. Resolve preferred send time:
   - `preferred_send_hour` from intelligence (or 10 AM default)
   - `preferred_send_dow` from intelligence (or any day)
   - Build next valid send datetime at preferred hour/dow
4. If computed send time is in the past → schedule for tomorrow at preferred hour
5. Return `("ok", scheduled_at)`

AM always respects timing — unlike flows, there is no urgency bypass.

---

## 7. Product Selection

### 7.1 `_resolve_products()` in am_runtime

Same pattern as `flow_runtime._get_intelligence_products()`:
1. Call `intelligence_layer.get_next_products(contact.id)`
2. Extract keys: `product_key`, `target_key`, `to_product`, `category`
3. Resolve against `ProductImageCache` with `ProductCommercial` out-of-stock filter
4. Return concrete product dicts: `{product_title, product_url, image_url, price}`
5. Action-type-aware prioritization:
   - `reorder_reminder` → prioritize `reorders` from intelligence
   - `cross_sell` → prioritize `cross_sells` and `accessories`
   - `product_recommendation` → prioritize `top_pick` and `upgrades`
   - Others → balanced selection

---

## 8. Offer Policy

### 8.1 `_resolve_offer()` in am_runtime

```python
def _resolve_offer(contact, action_type, candidate_products):
    """
    Determine whether to offer a discount using intelligence_layer.

    Returns offer_context dict or None.
    """
```

**Logic:**
1. Map action_type to discount purpose:
   - `winback` → `"winback"`
   - `cross_sell` → `"cross_sell"`
   - `reorder_reminder` → `"reorder_reminder"`
   - `loyalty` → `"loyalty_reward"`
   - `education`, `product_recommendation` → None (no discount by default)
2. Check strategy `offer_policy.allow_discount` — if false, return None
3. Call `intelligence_layer.get_discount_policy(contact.id, purpose, products)`
4. If `offer_discount == False` → return None
5. If `offer_discount == True` → call `discount_engine.get_or_create_discount()` + `get_discount_display()`
6. Return resolved offer_context

---

## 9. Flow Ownership — Preserved

### 9.1 Precondition Check

`_check_preconditions()` in am_runtime:
- If `FlowEnrollment.status in ["active", "paused"]` for this contact → status = "skipped", reason = "active flow"
- If `contact.subscribed == False` → skipped
- If `SuppressionEntry` exists → skipped
- If `ContactScore.sunset_score >= 85` → skipped

### 9.2 Handover — Preserved

No changes to:
- `maybe_handover_from_flow(contact)` — stays in account_manager.py
- `_resharpen_strategy()` — stays, but now produces executable state via `_normalize_strategy()`
- `add_flow_tag()` — unchanged

On handover, the new/resharpened strategy is normalized into executable state. `_normalize_strategy()` ensures machine-usable fields are present.

---

## 10. run_account_manager() Refactored

### 10.1 New Flow

```python
def run_account_manager():
    # 1. Check am_enabled, read settings
    # 2. Query due contacts (same as today)
    # 3. For each contact:
    #    a. build_am_decision(contact, strategy)
    #    b. If decision["status"] == "wait":
    #       - Update strategy.next_action_date = decision["wait_until"]
    #       - Log wait in ActionLedger
    #       - continue
    #    c. If decision["status"] == "skipped":
    #       - Log skip reason
    #       - continue
    #    d. If decision["status"] == "ready":
    #       - execute_am_decision(contact, strategy, decision)
    #       - If result["status"] == "invalid":
    #         - Log render failure, do not enqueue/review
    #         - Defer next_action_date by 1 day
    #         - continue
    #       - If autonomous:
    #         - Pre-create AutoEmail with frozen metadata
    #         - enqueue_email() with auto_email_id
    #       - If review:
    #         - Create AMPendingReview with frozen decision context
    #       - Advance strategy: update next_action_type, next_action_date
    # 4. Log AMRunLog
```

### 10.2 Wait Updates Strategy

When AM decides wait, `run_account_manager()` writes back:
```python
cs.next_action_date = decision["wait_until"]
cs.save()
```

This prevents the contact from being "due" again on the next run. The wait_until is computed from cadence_policy: typically `now + min_gap_days` or `next_available_at` from should_contact_now(), whichever is later.

### 10.3 Strategy Advancement After Send

After a successful send (or review row creation):
```python
strategy_state = _normalize_strategy(cs.strategy_json)
cadence = strategy_state.get("cadence_policy", {})
gap_days = cadence.get("preferred_gap_days", 7)

cs.next_action_date = datetime.now() + timedelta(days=gap_days)
cs.next_action_type = _suggest_next_action(strategy_state, decision)
cs.last_reviewed_at = datetime.now()
cs.save()
```

`_suggest_next_action()` picks the next action based on strategy phase and what was just sent — avoids repeating the same action type consecutively unless the strategy demands it.

---

## 11. Preserved Surfaces

No changes to:
- AM dashboard routes (`/account-manager`, `/account-manager/settings`, etc.)
- `approve_email()` / `reject_email()` — adjusted to use frozen metadata from AMPendingReview
- `seed_am_templates()` — unchanged
- `am_enabled` / `am_max_daily_contacts` / `am_autonomous` settings
- APScheduler scheduling (4:10 AM daily)
- AMRunLog cost tracking
- `_recalculate_confidence()` — unchanged
- AM prompt management routes

`approve_email()` gets one small update: when creating AutoEmail on approval, copy `action_type` and `decision_json` from the AMPendingReview record.

---

## 12. Tests

New file: `tests/test_am_runtime.py`

### Test Cases (11 tests)

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_wait_when_weak_opportunity` | Due contact with low scores across all actions → status="wait", no review/queue created |
| 2 | `test_active_flow_skips_am` | Contact with active FlowEnrollment → status="skipped" |
| 3 | `test_reorder_beats_cross_sell` | High reorder_likelihood contact → action_type="reorder_reminder" wins |
| 4 | `test_winback_for_dormant` | Lapsed lifecycle + high churn → action_type="winback" wins |
| 5 | `test_loyalty_when_discount_inappropriate` | VIP with low discount_sensitivity → loyalty chosen over winback |
| 6 | `test_concrete_products_from_intelligence` | AM decision picks real ProductImageCache products, no OOS |
| 7 | `test_discount_policy_respected` | Policy says no → no offer_context; policy says yes → resolved discount |
| 8 | `test_template_engine_integration` | execute_am_decision renders through template_engine; invalid render returns status="invalid" |
| 9 | `test_review_mode_stores_decision_metadata` | Review-mode AM stores action_type + decision_json in AMPendingReview |
| 10 | `test_autonomous_mode_prefreezes_auto_email` | Autonomous AM pre-creates AutoEmail with frozen action_type before enqueue |
| 11 | `test_outcome_tracker_prefers_frozen_action_type` | outcome_tracker uses AutoEmail.action_type over ContactStrategy.next_action_type |
| 12 | `test_flow_handoff_produces_executable_strategy` | maybe_handover_from_flow + normalize → strategy has allowed_actions, cadence_policy |
| 13 | `test_legacy_strategy_normalized` | Old narrative-only strategy_json normalized to executable state without crash |
| 14 | `test_wait_updates_next_action_date` | Wait decision writes back wait_until to ContactStrategy.next_action_date |
| 15 | `test_performance_data_ignored_below_sample_threshold` | ActionPerformance with sample_size < 20 not used for scoring |

### Test Strategy

- Mock `intelligence_layer` functions to return controlled responses
- Mock `template_engine` (via `te`) for render calls
- Mock AI provider for copy generation
- Use real database models via in_memory_db fixture
- Mock `delivery_engine.enqueue_email` to capture call args

---

## 13. What Gets Deprecated

| Old Code | Replaced By |
|----------|------------|
| `generate_am_email_from_template()` | `execute_am_decision()` — decision-driven render path |
| `gather_contact_profile()` as decision source | `intelligence_layer.get_contact_intelligence()` + `format_intelligence_for_prompt()` |
| Random 7-14 day strategy advancement | `cadence_policy.preferred_gap_days` from executable strategy |
| Inline discount logic in AM prompt | `intelligence_layer.get_discount_policy()` via `_resolve_offer()` |
| `_get_optimal_send_time()` (simplistic) | `_check_timing()` using full intelligence timing |
| `_get_action_type()` from mutable ContactStrategy | Frozen `AutoEmail.action_type` |

`generate_am_email_from_template()` and `gather_contact_profile()` remain in account_manager.py with deprecation comments. They are no longer called by the main nightly run but preserved for any external callers.

## 14. What Stays for Campaigns Later

| Code | Phase | Notes |
|------|-------|-------|
| Campaign model + CampaignEmail | Phase 5 | Untouched |
| Campaign routes | Phase 5 | Untouched |
| Campaign send in delivery_engine | Phase 5 | Untouched |
| Campaign outcome tracking | Phase 5 | Untouched |

## 15. Files Changed Summary

| File | Change Type | Scope |
|------|------------|-------|
| `am_runtime.py` | **NEW** | ~500-600 lines — decision engine + execution helper |
| `account_manager.py` | **MODIFIED** | run_account_manager refactored to use am_runtime; generate_am_email_from_template deprecated; approve_email updated for frozen metadata |
| `database.py` | **MODIFIED** | Add action_type + decision_json to AutoEmail; decision_json + template_id to AMPendingReview; decision_json to DeliveryQueue + migration helpers |
| `delivery_engine.py` | **MODIFIED** | Add optional decision_json="" to enqueue_email signature |
| `outcome_tracker.py` | **MODIFIED** | _get_action_type prefers AutoEmail.action_type over ContactStrategy |
| `generate-context.py` | **MODIFIED** | Add am_runtime.py description |
| `tests/test_am_runtime.py` | **NEW** | 15 test cases |

No changes to: `template_engine.py`, `intelligence_layer.py`, `flow_runtime.py`, `block_registry.py`, routes (except approve_email internals).
