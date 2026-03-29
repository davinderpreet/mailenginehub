# Phase 3: Flows Pillar — Design Spec

> Created: 2026-03-29 | Status: Approved | Build order: Intelligence ✅ → Templates ✅ → **Flows** → AM → Campaigns

## Goal

Refactor the existing flow runtime so Flows become a clean reactive pillar on top of intelligence_layer, template_engine, and delivery_engine. No new flow engine — surgical migration of existing render/decision paths.

Flows own immediate, high-intent lifecycle moments. AM remains paused while a flow owns the contact. This phase does not rebuild AM, Campaigns, or the trigger system.

---

## 1. New Module: `flow_runtime.py`

One focused helper module (~400 lines) that centralizes flow render and decision logic.

### 1.1 Main Entry Point

```python
def build_flow_send_package(enrollment, step, contact, flow,
                            template=None, trigger_context=None):
    """
    Build a complete flow send package for one enrollment + step.

    Args:
        enrollment: FlowEnrollment instance
        step: FlowStep instance
        contact: Contact instance
        flow: Flow instance
        template: EmailTemplate override (e.g. from learning swap).
                  If None, uses step.template.
        trigger_context: dict with explicit trigger data, e.g.:
            - checkout_abandoned: {"cart_items": [...], "checkout_url": "..."}
            - browse_abandonment: {"viewed_products": [...]}
            - cart_abandonment:   {"cart_items": [...]}
            If None, flow_runtime resolves from database.

    Returns dict:
        {
            "status": "ready" | "suppressed" | "invalid" | "deferred",
            "reason": str,
            "objective": str,
            "urgency": "urgent" | "medium" | "lower",
            "candidate_products": list,
            "offer_context": dict or None,
            "render_result": dict or None,   # from template_engine.render_email()
            "subject": str or None,
            "html": str or None,
            "priority": int,
            "metadata": dict,
        }
    """
```

**Why `template` parameter:** The learning swap (`learning_context.get_best_template_for_family()`) happens in the caller (`_process_flow_enrollments`). The swapped template is passed into the helper so it doesn't need to know about learning internals.

**Why `trigger_context` parameter:** Cart/checkout/browse contexts are often already available at the call site (from AbandonedCheckout records, PendingTrigger event_data, etc.). Passing them in avoids redundant DB lookups. When None, flow_runtime resolves context from the database itself.

### 1.2 Internal Pipeline

`build_flow_send_package` runs these steps in order:

1. **`_resolve_objective(flow, step, template)`** — determines objective, urgency, discount purpose
2. **`_check_soft_timing_gate(contact, urgency)`** — urgency-aware deferral (soft gates only)
3. **`_resolve_products(contact, flow, enrollment, trigger_context)`** — product priority chain
4. **`_resolve_offer(contact, purpose, candidate_products)`** — intelligence-backed discount policy
5. **`_build_legacy_token_context(contact, flow, trigger_context, products, offer)`** — for legacy HTML templates
6. **`_render_via_template_engine(template, contact, objective, products, offer, mode="send")`** — template_engine contract + render
7. Return the assembled package

### 1.3 Trigger Urgency Map

```python
TRIGGER_URGENCY = {
    "checkout_abandoned": "urgent",
    "browse_abandonment": "urgent",
    "cart_abandonment":   "urgent",     # alias — maps to checkout_abandoned flows
    "contact_created":    "medium",
    "order_placed":       "medium",
    "tag_added":          "medium",
    "no_purchase_days":   "lower",
}
```

### 1.4 Objective Map

```python
TRIGGER_OBJECTIVE = {
    "checkout_abandoned": "checkout_recovery",
    "browse_abandonment": "browse_recovery",
    "cart_abandonment":   "checkout_recovery",  # alias to same objective
    "contact_created":    "welcome",
    "order_placed":       "post_purchase",
    "no_purchase_days":   "winback",
    "tag_added":          "engagement",
}
```

### 1.5 Trigger Alias Policy

`cart_abandonment` is an alias for `checkout_abandoned` in flow handling. The existing `TRIGGER_ALIASES` map in `_recover_pending_backlog()` (app.py ~L3900) already handles this: `"cart_abandonment" → "checkout_abandoned"`.

flow_runtime must treat these as the same runtime branch:
- Same objective (`checkout_recovery`)
- Same urgency (`urgent`)
- Same discount purpose (`cart_abandonment`)
- Same product selection path (cart context)

The objective map and urgency map above already reflect this. No separate branching needed for `cart_abandonment` vs `checkout_abandoned`.

### 1.6 Discount Purpose Map

```python
TRIGGER_DISCOUNT_PURPOSE = {
    "checkout_abandoned": "cart_abandonment",
    "browse_abandonment": "browse_abandonment",
    "cart_abandonment":   "cart_abandonment",
    "contact_created":    "welcome",
    "order_placed":       "post_purchase",
    "no_purchase_days":   "winback",
    "tag_added":          None,   # no default discount for tag-added
}
```

Authoritative override: if `template.template_family` is set and maps cleanly to a known purpose, use that instead of the trigger-based mapping. Template family is more specific than trigger type.

---

## 2. Soft Timing Gate (Only Soft Gates)

### 2.1 What `_check_soft_timing_gate()` Does

```python
def _check_soft_timing_gate(contact, urgency):
    """
    Check soft timing gates based on urgency level.
    Does NOT check hard guards (delivery_engine owns those).

    Returns:
        ("ok", None) — proceed
        ("deferred", next_available_at) — reschedule
    """
```

### 2.2 What It Checks

Calls `intelligence_layer.should_contact_now(contact.id)` and interprets the result based on urgency:

| Urgency | Behavior |
|---------|----------|
| **urgent** | Skip soft timing entirely. Checkout/browse/cart recovery is time-sensitive. Always returns `"ok"`. |
| **medium** | Respect `should_contact_now()` only for `reason == "weekly_cap"`. Ignore `"too_soon"` — welcome/order emails are expected. |
| **lower** | Fully respect `should_contact_now()`. If it says no, return `"deferred"` with `next_available_at`. |

### 2.3 What It Does NOT Check

These hard guards stay in delivery_engine.py and are NOT duplicated here:
- Enrollment active/cancelled/paused state
- Purchase-after-enrollment (recovery flow cancellation)
- Recent bounce/complaint suppression
- Unsubscribe-at-send-time check
- Warmup/daily ceiling limits

flow_runtime trusts delivery_engine to enforce these at queue-processing time.

### 2.4 Deferral Behavior

When `_check_soft_timing_gate` returns `"deferred"`:
- The caller updates `enrollment.next_send_at = next_available_at`
- The enrollment stays active (not cancelled)
- The scheduler will pick it up again at the new time
- ActionLedger records the deferral with reason

---

## 3. Product Selection

### 3.1 Priority Chain in `_resolve_products()`

```
1. Explicit trigger_context products (if provided by caller)
2. Database-resolved trigger products (if trigger_context is None)
3. intelligence_layer.get_next_products(contact.id) recommendations
4. Legacy fallback (CustomerProfile.top_products, ProductImageCache bestsellers)
```

### 3.2 Per-Trigger Product Resolution

**Checkout/cart recovery** (`checkout_abandoned`, `cart_abandonment`):
- If `trigger_context` has `cart_items`: use those directly
- Else: query `AbandonedCheckout` for latest unrecovered checkout → extract `line_items_json`
- Else: query `CustomerActivity` for recent `viewed_product` events as cart proxy
- Never replace cart-specific products with generic intelligence recs

**Browse abandonment** (`browse_abandonment`):
- If `trigger_context` has `viewed_products`: use those
- Else: query `CustomerActivity` for recent `viewed_product` events (deduped, max 4)
- Then: supplement with `intelligence_layer.get_next_products()` if < 2 products found

**Welcome / post-purchase / winback / tag-added:**
- Use `intelligence_layer.get_next_products(contact.id)` as primary source
- Extract `top_pick` + `cross_sells` + `reorders` from the response
- Legacy fallback only if intelligence returns empty

### 3.3 Product Format

Products are returned as a list of dicts matching what `template_engine.make_render_contract(product_context=...)` expects. Each product dict includes at minimum: `product_title`, `product_url`, `image_url`, `price` (resolved from ProductImageCache when available).

---

## 4. Offer Policy

### 4.1 Decision Flow

```
1. Determine discount_purpose from objective map (or template_family)
2. If purpose is None → no offer, skip
3. Call intelligence_layer.get_discount_policy(contact.id, purpose, candidate_products)
4. If offer_discount == False → no offer_context passed to template_engine
5. If offer_discount == True → call discount_engine.get_or_create_discount(email, purpose)
6. Build offer_context dict from resolved discount for template_engine
```

### 4.2 Offer Context Format

When an offer is approved and resolved:
```python
offer_context = {
    "code": "CART-ABCDEF",
    "value": "5",
    "discount_type": "percentage",
    "display_text": "5% off your order",
    "value_display": "5% OFF",
    "expires_text": "Expires in 2 days",
    "expires_at": datetime(...),
}
```

Built from `discount_engine.get_or_create_discount()` + `discount_engine.get_discount_display()`.

When no offer: `offer_context = None`. Template engine renders without discount blocks.

---

## 5. Legacy Token Context for HTML Templates

### 5.1 The Problem

Current flow path (app.py ~L3397-3565) injects 15+ personalization tokens into legacy HTML templates via string replacement. These tokens must continue to work in Phase 3.

### 5.2 Solution: `_build_legacy_token_context()`

A flow-specific function that assembles a complete token → value map for legacy templates:

```python
def _build_legacy_token_context(contact, flow, trigger_context,
                                candidate_products, offer_context):
    """
    Build token replacement map for legacy HTML flow templates.

    Returns dict: {"{{token_name}}": "resolved_value", ...}
    """
```

**Full token map:**

| Token | Source | Default |
|-------|--------|---------|
| `{{first_name}}` | `contact.first_name` | `"Friend"` |
| `{{last_name}}` | `contact.last_name` | `""` |
| `{{email}}` | `contact.email` | `""` |
| `{{discount_code}}` | `offer_context["code"]` if offer | `""` |
| `{{cart_items}}` | Rendered HTML from cart/checkout line items | `"Your selected items"` |
| `{{checkout_url}}` | `AbandonedCheckout.checkout_url` or trigger_context | `"https://ldas.ca/checkout"` |
| `{{last_viewed_product}}` | `CustomerProfile.last_viewed_product` | `"one of our popular items"` |
| `{{recently_browsed_html}}` | Rendered HTML from recent `viewed_product` activity | `"Your recently viewed items"` |
| `{{top_products_html}}` | Rendered HTML from `CustomerProfile.top_products` | `""` |
| `{{total_orders}}` | `CustomerProfile.total_orders` | `"0"` |
| `{{total_spent}}` | `CustomerProfile.total_spent` (formatted) | `"$0"` |
| `{{rfm_segment}}` | `ContactScore.rfm_segment` | `"new"` |
| `{{lifecycle_stage}}` | `CustomerProfile.lifecycle_stage` | `"prospect"` |
| `{{customer_type}}` | `CustomerProfile.customer_type` | `"valued customer"` |
| `{{top_category}}` | Top category from `category_affinity_json` | `"our top picks"` |
| `{{days_since_purchase}}` | Days since `CustomerProfile.last_order_at` | `"a while"` |
| `{{intent_level}}` | Bucketed from `CustomerProfile.intent_score` (high/medium/low) | `"medium"` |
| `{{unsubscribe_url}}` | Left for template_engine / shell to resolve | *(not replaced here)* |

### 5.3 Application

For block-based templates (`template_format == "blocks"`):
- `_build_legacy_token_context()` is NOT called
- template_engine handles all personalization through the render contract

For legacy HTML templates (`template_format != "blocks"`):
- `_build_legacy_token_context()` builds the map
- Tokens are applied to `html_body` before passing to `template_engine.make_render_contract()`
- Or: applied after `render_email()` returns the HTML, before enqueue
- Implementation detail: whichever path avoids double-substitution and keeps template_engine as the validation authority

### 5.4 Cart Items HTML Rendering

The `{{cart_items}}` and `{{recently_browsed_html}}` tokens require rendering product data as inline HTML. This stays in `_build_legacy_token_context()` — it's presentation logic specific to legacy templates. Same inline styles as current code to avoid visual regression.

---

## 6. Template Engine Integration

### 6.1 Block-Based Templates

```python
contract = template_engine.make_render_contract(
    template=resolved_template,
    source_system="flow",
    objective=objective,          # from _resolve_objective()
    contact_id=contact.id,
    product_context=candidate_products,
    offer_context=offer_context,  # or None
    mode="send",
)
result = template_engine.render_email(contract)
```

If `result["is_valid"]` is True → package status is `"ready"`, subject/html come from result.
If `result["is_valid"]` is False → package status is `"invalid"`, reason from `result["errors"]`.

### 6.2 Legacy HTML Templates

For legacy templates, flow_runtime:
1. Resolves the legacy token context
2. Applies token substitutions to `template.html_body`
3. Wraps in email shell if `shell_version >= 1`
4. Passes through `template_engine.validate_rendered_email()` for validation
5. If validation has errors in send mode → status `"invalid"`
6. If valid → status `"ready"`

### 6.3 Subject Personalization

Subject comes from `step.subject_override or template.subject`. Token substitution for `{{first_name}}`, `{{last_viewed_product}}`, `{{total_orders}}` applied by flow_runtime before packaging. Same as current behavior.

### 6.4 Tracking Pixel + Shell

- Tracking pixel appended by flow_runtime after render (same `_make_flow_tracking_pixel_url()` call)
- Email shell wrapping applied by flow_runtime if `template.shell_version >= 1`
- These happen after template_engine render, before final packaging

---

## 7. Migration Points

### 7.1 `_process_flow_enrollments()` (app.py ~L3240)

**Before:** ~200 lines of inline rendering with direct block_registry calls, manual str.replace, ad-hoc discount creation, inline product selection.

**After:**
```python
# Learning swap (stays in caller)
_swapped_template = learning_context.get_best_template_for_family(...)

# Build trigger context if available
_trigger_ctx = _build_trigger_context_from_enrollment(enrollment, contact, flow)

# Build package via flow_runtime
package = flow_runtime.build_flow_send_package(
    enrollment, step, contact, flow,
    template=_swapped_template,
    trigger_context=_trigger_ctx,
)

if package["status"] == "ready":
    delivery_engine.enqueue_email(
        contact=contact, email_type="flow",
        source_id=flow.id, enrollment_id=enrollment.id,
        step_id=step.id, template_id=template.id,
        from_name=..., from_email=...,
        subject=package["subject"], html=package["html"],
        unsubscribe_url=..., priority=package["priority"],
        ledger_id=ledger.id,
    )
    # cascade, ledger update, etc.

elif package["status"] == "deferred":
    enrollment.next_send_at = package["metadata"]["next_available_at"]
    enrollment.save()
    # ledger: deferred

elif package["status"] in ("suppressed", "invalid"):
    # ledger: suppressed/invalid with package["reason"]
    # cancel if suppressed
```

**Preserved in caller:** frequency cap check, dedup check, one-send-per-tick gating, learning swap resolution, cascade_contact() post-enqueue, ActionLedger writes, unsubscribe pre-check.

### 7.2 `_pause_lower_priority_enrollments()` (app.py ~L2934)

**Before:** Separate rendering branch for force-send Step 1 — direct block_registry + manual str.replace, plus manual `FlowEmail.create(status="queued")`.

**After:**
```python
# Force-send Step 1 before pausing
package = flow_runtime.build_flow_send_package(
    enrollment, first_step, contact, enrollment.flow,
    template=first_step.template,
    trigger_context=None,
)

if package["status"] == "ready":
    delivery_engine.enqueue_email(
        contact=contact, email_type="flow",
        source_id=enrollment.flow_id, enrollment_id=enrollment.id,
        step_id=first_step.id, template_id=first_step.template_id,
        subject=package["subject"], html=package["html"],
        unsubscribe_url=_unsub, priority=package["priority"],
        ledger_id=...,
    )
    # NO manual FlowEmail.create() here — delivery_engine._create_compat_record()
    # handles this when the queue item is processed
    enrollment.current_step = 2
```

**Key change (adjustment #5):** Remove the manual `FlowEmail.create(status="queued")` at app.py ~L3018. Let delivery_engine be the single authority for compat record creation via `_create_compat_record()` at queue processing time. This normalizes the force-send path to match the standard flow send path.

### 7.3 Delivery Engine — No Changes Required

delivery_engine.py stays as-is:
- `enqueue_email()` signature unchanged
- `_advance_flow_enrollment()` unchanged
- `_resume_paused()` unchanged
- `_create_compat_record()` unchanged
- Pre-send guards unchanged (they remain the hard guard authority)
- Queue processing (flow vs bulk separation, warmup bypass) unchanged

### 7.4 Ownership / Handover — Preserved

No changes to:
- `_pause_lower_priority_enrollments()` pause semantics (only render path changes)
- `_resume_paused_enrollments()` logic
- `_exit_flows_by_trigger_type()` exit + AM handover
- `account_manager.maybe_handover_from_flow()`
- `account_manager.add_flow_tag()`

---

## 8. Tests

New file: `tests/test_flow_runtime.py`

Uses existing conftest.py fixtures (in_memory_db, make_contact). Mocks intelligence_layer, template_engine, discount_engine, delivery_engine at function boundaries.

### Test Cases

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_flow_send_uses_template_engine` | `_process_flow_enrollments()` calls template_engine for both block and legacy templates; valid result gets queued |
| 2 | `test_invalid_render_no_enqueue` | template_engine returns errors → no DeliveryQueue entry created, ledger records failure reason |
| 3 | `test_urgent_flow_bypasses_soft_timing` | checkout_abandoned proceeds when `should_contact_now()` returns `can_send=False, reason="too_soon"` |
| 4 | `test_lower_urgency_respects_timing` | winback/no_purchase_days defers when `should_contact_now()` says not now; enrollment.next_send_at updated |
| 5 | `test_explicit_trigger_products_beat_intelligence` | checkout with cart_items uses those products, not intelligence recs; browse with viewed_products uses those |
| 6 | `test_discount_policy_respected` | When policy says no offer → no discount_engine call, no offer_context; when yes → resolved discount passed through |
| 7 | `test_force_send_step1_uses_same_engine` | Pause path calls build_flow_send_package, no manual FlowEmail.create; compat record created by delivery_engine only |
| 8 | `test_flow_am_ownership` | Enrolling in flow pauses AM; flow completion triggers maybe_handover_from_flow() |
| 9 | `test_paused_flow_resumes_after_completion` | Higher-priority flow completes → paused lower-priority enrollment resumes with status="active" |
| 10 | `test_purchase_after_enrollment_cancels_recovery` | Order placed after enrollment → delivery_engine guard cancels the queued recovery email |

### Test Strategy

- Mock `intelligence_layer` functions to return controlled responses
- Mock `template_engine.render_email()` to return valid/invalid results
- Mock `discount_engine.get_or_create_discount()` to return controlled discount dicts
- Mock `delivery_engine.enqueue_email()` to capture call args without SES
- Use real database models via in_memory_db fixture for enrollment/flow/step state
- Do not mock delivery_engine pre-send guards for test 10 — test the real guard logic

---

## 9. What Gets Removed

| Old Code Location | What | Replaced By |
|-------------------|------|-------------|
| app.py ~L3390-3396 | Inline `block_registry.render_template_blocks()` in `_process_flow_enrollments` | `flow_runtime.build_flow_send_package()` → template_engine |
| app.py ~L3397-3565 | Manual str.replace personalization (15+ tokens) | `flow_runtime._build_legacy_token_context()` |
| app.py ~L3393-3394 | Inline `discount_engine.get_or_create_discount()` | `flow_runtime._resolve_offer()` via intelligence_layer |
| app.py ~L2970-3016 | Separate render branch in force-send Step 1 | `flow_runtime.build_flow_send_package()` |
| app.py ~L3018-3023 | Manual `FlowEmail.create(status="queued")` in force-send | Removed — delivery_engine `_create_compat_record()` is the authority |

## 10. What Stays for Later Phases

| Code | Phase | Notes |
|------|-------|-------|
| `_enroll_contact_in_flows()` enrollment logic | Phase 4+ | Not a render path — enrollment/pause/AM-pause stays |
| `_check_abandoned_checkouts()` trigger detection | Phase 4+ | Trigger detection, not rendering |
| `_detect_behavioural_triggers()` + `_check_passive_triggers()` | Phase 4+ | Trigger detection pipeline |
| `_recover_pending_backlog()` | Phase 4+ | Backlog processor, uses `_enroll_contact_in_flows()` |
| AM rebuild (account_manager.py) | Phase 4 | Explicitly out of scope |
| Campaign rebuild | Phase 5 | Explicitly out of scope |
| Route handlers, dashboard pages, tracking endpoints | Phase 4+ | No UI changes this phase |

## 11. Files Changed Summary

| File | Change Type | Scope |
|------|------------|-------|
| `flow_runtime.py` | **NEW** | ~400 lines — flow send package builder |
| `app.py` | **MODIFIED** | `_process_flow_enrollments()` refactored to use flow_runtime; `_pause_lower_priority_enrollments()` force-send path refactored; manual FlowEmail.create removed from force-send |
| `tests/test_flow_runtime.py` | **NEW** | 10 test cases for flow pillar |

No changes to: `delivery_engine.py`, `template_engine.py`, `intelligence_layer.py`, `block_registry.py`, `discount_engine.py`, `database.py`, or any route/dashboard code.
