# Flow / AM Handover Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinate the lifecycle handover between Flows (behavioral triggers) and Account Manager (AI-driven strategy) so they never step on each other's toes.

**Architecture:** Four surgical changes — (1) Flow enrollment pauses AM, (2) Flow completion resharpens AM strategy via Claude, (3) NBM skips owned contacts, (4) AI Engine skips owned contacts. No new DB models, no new files, no new routes.

**Tech Stack:** Python/Flask, Peewee ORM, Claude API (via ai_provider.py), existing ActionLedger logging.

**Spec:** `docs/superpowers/specs/2026-03-21-flow-am-handover-design.md`

---

## Chunk 1: Flow Enrollment Pauses AM

### Task 1: Add AM pause logic to `_enroll_contact_in_flows()`

**Files:**
- Modify: `app.py:2792-2809` (inside the try block after FlowEnrollment.create)

- [ ] **Step 1: Read the current function to confirm exact insertion point**

Confirm `app.py` lines 2792-2809 — the try block wrapping `FlowEnrollment.create()`. The new code goes after line 2807 (`_pause_lower_priority_enrollments(contact, flow)`) and before line 2808 (`except Exception:`).

- [ ] **Step 2: Add the AM pause logic**

After line 2807 (`_pause_lower_priority_enrollments(contact, flow)`), insert:

```python
            # ── Pause AM if contact is AM-managed ──
            try:
                from database import ContactStrategy
                import json as _json
                _cs = ContactStrategy.get_or_none(
                    ContactStrategy.contact == contact,
                    ContactStrategy.enrolled == True
                )
                if _cs:
                    _sd = {}
                    try:
                        _sd = _json.loads(_cs.strategy_json) if _cs.strategy_json and _cs.strategy_json != "{}" else {}
                    except Exception:
                        _sd = {}
                    if "pause_context" not in _sd:
                        _sd["pause_context"] = {
                            "paused_at": datetime.now().isoformat(),
                            "paused_by_flow": flow.name,
                            "flow_trigger": trigger_type,
                            "previous_next_action_date": _cs.next_action_date.isoformat() if _cs.next_action_date else None,
                            "previous_next_action_type": _cs.next_action_type,
                        }
                        _cs.strategy_json = _json.dumps(_sd)
                        _cs.next_action_type = "paused_for_flow"
                        _cs.save()
                        try:
                            from action_ledger import log_action
                            log_action(
                                contact=contact,
                                trigger_type="flow", source_id=flow.id,
                                status="paused", reason_code="RC_AM_PAUSED",
                                source_type="account_manager",
                                reason_detail="AM paused — contact entered %s" % flow.name,
                            )
                        except Exception:
                            pass
                        app.logger.info("[FlowEnroll] AM paused for %s — entering flow '%s'",
                                        contact.email, flow.name)
            except Exception:
                pass  # AM pause is best-effort, don't block flow enrollment
```

- [ ] **Step 3: Verify the edit compiles**

Run: `cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo' && python -c "import app"`
Expected: No import errors.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: flow enrollment pauses AM strategy when AM contact enters a flow"
```

---

## Chunk 2: Flow Completion Resharpens AM Strategy

### Task 2: Enhance `maybe_handover_from_flow()` with Path B/C logic

**Files:**
- Modify: `account_manager.py:1248-1251` (the "already enrolled" check)
- Modify: `account_manager.py` (add new helper `_resharpen_strategy()`)

- [ ] **Step 1: Replace the early return for enrolled contacts with Path B/C routing**

In `account_manager.py`, replace lines 1248-1251:
```python
    # Check if already enrolled
    existing = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
    if existing and existing.enrolled:
        return None  # Already managed by AM
```

With:
```python
    # Check if already enrolled
    existing = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
    if existing and existing.enrolled:
        # Path B/C: already AM-managed — check if returning from flow pause
        try:
            strategy_data = json.loads(existing.strategy_json) if existing.strategy_json and existing.strategy_json != "{}" else {}
        except Exception:
            strategy_data = {}
        if "pause_context" not in strategy_data:
            return None  # Path B: was never paused, no-op
        # Path C: returning AM contact — resharpen strategy
        return _resharpen_strategy(contact, existing, strategy_data)
```

- [ ] **Step 2: Add the `_resharpen_strategy()` function**

Add this function above `maybe_handover_from_flow()` (around line 1220):

```python
def _resharpen_strategy(contact, cs, strategy_data):
    """Resharpen an AM contact's strategy after they return from flows.

    Called when all flows complete for a contact that was previously AM-managed.
    Gathers flow outcome, updated profile, and old strategy performance,
    then calls Claude to generate a fresh strategy.
    """
    from database import (FlowEnrollment, FlowEmail, ShopifyOrder,
                          CustomerProfile, LearningConfig)
    from ai_provider import get_provider

    pause_ctx = strategy_data.get("pause_context", {})
    paused_at_str = pause_ctx.get("paused_at", "")
    try:
        paused_at = datetime.fromisoformat(paused_at_str)
    except Exception:
        paused_at = datetime.now() - timedelta(days=30)

    # ── Gather flow outcome ──
    completed_flows = (FlowEnrollment.select()
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status.in_(["completed", "cancelled"]),
                              FlowEnrollment.enrolled_at >= paused_at)
                       .order_by(FlowEnrollment.enrolled_at.desc()))
    flow_outcomes = []
    for fe in completed_flows:
        try:
            flow_name = fe.flow.name
        except Exception:
            flow_name = "Unknown"
        sent = FlowEmail.select().where(FlowEmail.enrollment == fe, FlowEmail.status == "sent").count()
        opened = FlowEmail.select().where(FlowEmail.enrollment == fe, FlowEmail.opened == True).count()
        clicked = FlowEmail.select().where(FlowEmail.enrollment == fe, FlowEmail.clicked == True).count()
        flow_outcomes.append({
            "flow": flow_name,
            "status": fe.status,
            "emails_sent": sent,
            "emails_opened": opened,
            "emails_clicked": clicked,
        })

    # Did they convert during flows?
    purchases_during_flow = (ShopifyOrder.select()
                             .where(ShopifyOrder.contact == contact,
                                    ShopifyOrder.ordered_at >= paused_at)
                             .count())
    purchase_revenue = 0
    if purchases_during_flow > 0:
        for o in ShopifyOrder.select().where(ShopifyOrder.contact == contact,
                                              ShopifyOrder.ordered_at >= paused_at):
            try:
                purchase_revenue += float(o.order_total or 0)
            except Exception:
                pass

    # ── Gather updated profile ──
    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact)
    profile_snapshot = {}
    if profile:
        profile_snapshot = {
            "lifecycle_stage": profile.lifecycle_stage or "unknown",
            "customer_type": profile.customer_type or "unknown",
            "intent_score": profile.intent_score or 0,
            "churn_risk": profile.churn_risk_score or 0,
            "reorder_likelihood": profile.reorder_likelihood or 0,
            "total_orders": contact.total_orders or 0,
            "total_spent": float(contact.total_spent or 0),
            "days_since_last_order": profile.days_since_last_order or 999,
        }

    # ── Old strategy performance ──
    old_strategy_summary = {
        "phase_when_paused": pause_ctx.get("previous_next_action_type", "unknown"),
        "total_approved": cs.total_approved or 0,
        "total_rejected": cs.total_rejected or 0,
        "confidence_score": cs.confidence_score or 0,
        "current_phase": cs.current_phase or "unknown",
    }

    # ── Call Claude to resharpen ──
    flow_outcome_text = "No flows completed." if not flow_outcomes else ""
    for fo in flow_outcomes:
        flow_outcome_text += "%s (%s): %d emails sent, %d opened, %d clicked. " % (
            fo["flow"], fo["status"], fo["emails_sent"], fo["emails_opened"], fo["emails_clicked"])
    if purchases_during_flow > 0:
        flow_outcome_text += "Customer CONVERTED during flows: %d order(s), $%.2f revenue." % (
            purchases_during_flow, purchase_revenue)
    else:
        flow_outcome_text += "Customer did NOT convert during flows."

    system_prompt = (
        "You are a senior email marketing strategist for LDAS Electronics (ldas.ca), "
        "a Shopify store selling trucker electronics (headsets, dash cams, accessories). "
        "You are resharpening a per-contact email marketing strategy. "
        "The customer was previously managed by your strategy, left for behavioral flows, "
        "and has now returned. The fact that they returned means the previous strategy worked.\n\n"
        "Output ONLY valid JSON matching this schema:\n"
        '{"overall_goal": "string", "phases": [{"name": "string", "months": "string", '
        '"goal": "string", "tactic": "string"}], "product_focus": "string", '
        '"discount_approach": "string", "first_action_type": "string", "first_action_days": int}'
    )

    user_prompt = (
        "PREVIOUS STRATEGY PERFORMANCE:\n"
        "Phase when paused: %s\n"
        "Emails approved: %d, rejected: %d, confidence: %d%%\n\n"
        "WHAT JUST HAPPENED (FLOW OUTCOMES):\n%s\n\n"
        "UPDATED CUSTOMER PROFILE:\n%s\n\n"
        "Generate an updated strategy that:\n"
        "- Builds on what was working before (they came back!)\n"
        "- Accounts for the flow outcome above\n"
        "- Adjusts timing and approach based on whether they converted\n"
        "- Has 2-4 phases over 3-6 months\n"
    ) % (
        old_strategy_summary["phase_when_paused"],
        old_strategy_summary["total_approved"],
        old_strategy_summary["total_rejected"],
        old_strategy_summary["confidence_score"],
        flow_outcome_text,
        json.dumps(profile_snapshot, indent=2),
    )

    try:
        provider = get_provider()
        response = provider.complete(system_prompt, user_prompt, max_tokens=1500)
        # Parse JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            new_strategy = json.loads(json_match.group())
        else:
            raise ValueError("No JSON found in Claude response")
    except Exception as e:
        # Fallback: restore previous strategy, just reset timing
        logger.warning("[AccountManager] Resharpen failed for contact #%s: %s — restoring old strategy",
                       contact.id, str(e))
        del strategy_data["pause_context"]
        cs.strategy_json = json.dumps(strategy_data)
        resume_delay = int(LearningConfig.get_val("am_resume_delay_days", "3"))
        cs.next_action_date = datetime.now() + timedelta(days=resume_delay)
        cs.next_action_type = pause_ctx.get("previous_next_action_type", "education")
        cs.updated_at = datetime.now()
        cs.save()
        return cs

    # ── Update ContactStrategy with new strategy ──
    resume_delay = int(LearningConfig.get_val("am_resume_delay_days", "3"))

    # Preserve approval/rejection history, replace strategy content
    new_strategy["flow_graduation"] = {
        "resharpened_at": datetime.now().strftime("%Y-%m-%d"),
        "flow_outcomes": flow_outcomes,
        "converted_during_flow": purchases_during_flow > 0,
        "previous_confidence": old_strategy_summary["confidence_score"],
    }
    # pause_context is NOT carried forward — it's cleared by omission

    cs.strategy_json = json.dumps(new_strategy)
    cs.current_phase = new_strategy.get("phases", [{}])[0].get("name", "Phase 1") if new_strategy.get("phases") else "Phase 1"
    cs.current_phase_num = 1
    cs.next_action_date = datetime.now() + timedelta(days=resume_delay)
    cs.next_action_type = new_strategy.get("first_action_type", "education")
    cs.strategy_version = (cs.strategy_version or 0) + 1
    cs.updated_at = datetime.now()
    cs.save()

    # Log the resharpen
    try:
        from action_ledger import log_action
        log_action(
            contact=contact,
            trigger_type="flow", source_id=0,
            status="resharpened", reason_code="RC_AM_RESUMED",
            source_type="account_manager",
            reason_detail="AM resharpened after flows — converted=%s, flows=%d" % (
                purchases_during_flow > 0, len(flow_outcomes)),
        )
    except Exception:
        pass

    logger.info("[AccountManager] Strategy resharpened for contact #%s — %d flows completed, converted=%s",
                contact.id, len(flow_outcomes), purchases_during_flow > 0)
    return cs
```

- [ ] **Step 3: Verify the edit compiles**

Run: `cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo' && python -c "from account_manager import maybe_handover_from_flow"`
Expected: No import errors.

- [ ] **Step 4: Commit**

```bash
git add account_manager.py
git commit -m "feat: resharpen AM strategy when contact returns from flows"
```

---

## Chunk 3: NBM and AI Engine Skip Owned Contacts

### Task 3: Add ownership skip to `decide_all_contacts()`

**Files:**
- Modify: `next_best_message.py:750-785` (`decide_all_contacts()`)

- [ ] **Step 1: Add ownership exclusion sets**

In `next_best_message.py`, after the imports in `decide_all_contacts()` (after line 756 `init_db()`), add:

```python
    # Skip contacts owned by AM or in active flows — they have their own messaging
    from database import ContactStrategy, FlowEnrollment
    am_owned_ids = set(
        cs.contact_id for cs in
        ContactStrategy.select(ContactStrategy.contact)
        .where(ContactStrategy.enrolled == True)
    )
    flow_owned_ids = set(
        fe.contact_id for fe in
        FlowEnrollment.select(FlowEnrollment.contact)
        .where(FlowEnrollment.status.in_(["active", "paused"]))
    )
    owned_ids = am_owned_ids | flow_owned_ids
    logger.info("NBM: skipping %d AM-owned + %d flow-owned contacts",
                len(am_owned_ids), len(flow_owned_ids))
```

- [ ] **Step 2: Add skip check in the subscribed contacts loop**

In the first `for c in contacts:` loop (line 764), add the skip check as the first line inside the try block:

Change:
```python
    for c in contacts:
        try:
            decide_next_action(c.id)
            count += 1
```

To:
```python
    skipped_owned = 0
    for c in contacts:
        try:
            if c.id in owned_ids:
                skipped_owned += 1
                continue
            decide_next_action(c.id)
            count += 1
```

- [ ] **Step 3: Add skip check in the unsubscribed contacts loop**

Same pattern for the `for c in unsub:` loop (line 777):

Change:
```python
    for c in unsub:
        try:
            decide_next_action(c.id)
            count += 1
```

To:
```python
    for c in unsub:
        try:
            if c.id in owned_ids:
                skipped_owned += 1
                continue
            decide_next_action(c.id)
            count += 1
```

- [ ] **Step 4: Update the final log line**

Change line 784:
```python
    logger.info(f"Decided for {count} contacts ({errors} errors)")
```

To:
```python
    logger.info(f"Decided for {count} contacts ({errors} errors, {skipped_owned} owned-skipped)")
```

- [ ] **Step 5: Verify it compiles**

Run: `cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo' && python -c "from next_best_message import decide_all_contacts"`
Expected: No import errors.

- [ ] **Step 6: Commit**

```bash
git add next_best_message.py
git commit -m "feat: NBM skips AM-owned and flow-active contacts"
```

### Task 4: Add ownership skip to `execute_plan()`

**Files:**
- Modify: `ai_engine.py:635-642` (the recently_emailed set construction)

- [ ] **Step 1: Add owned contacts to the exclusion set**

In `ai_engine.py`, after line 642 (after the CampaignEmail recently_emailed loop), add:

```python
    # Skip contacts owned by AM or in active flows
    from database import ContactStrategy, FlowEnrollment
    am_owned_ids = set(
        cs.contact_id for cs in
        ContactStrategy.select(ContactStrategy.contact)
        .where(ContactStrategy.enrolled == True)
    )
    flow_owned_ids = set(
        fe.contact_id for fe in
        FlowEnrollment.select(FlowEnrollment.contact)
        .where(FlowEnrollment.status.in_(["active", "paused"]))
    )
    owned_ids = am_owned_ids | flow_owned_ids
    recently_emailed |= owned_ids
    logger.info("[AI Engine] Excluding %d AM-owned + %d flow-owned contacts from plan execution",
                len(am_owned_ids), len(flow_owned_ids))
```

- [ ] **Step 2: Verify it compiles**

Run: `cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo' && python -c "from ai_engine import execute_plan"`
Expected: No import errors.

- [ ] **Step 3: Commit**

```bash
git add ai_engine.py
git commit -m "feat: AI engine skips AM-owned and flow-active contacts"
```

---

## Chunk 4: Final Verification and Deploy

### Task 5: End-to-end verification

- [ ] **Step 1: Verify all 4 modified files import cleanly**

Run:
```bash
cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo'
python -c "import app; from account_manager import maybe_handover_from_flow; from next_best_message import decide_all_contacts; from ai_engine import execute_plan; print('All imports OK')"
```
Expected: `All imports OK`

- [ ] **Step 2: Run generate-context.py to update CLAUDE.md and REFERENCE.md**

Run: `cd 'C:\Users\davin\Claude Work Folder\mailenginehub-repo' && python generate-context.py`

- [ ] **Step 3: Final commit with updated context**

```bash
git add CLAUDE.md REFERENCE.md
git commit -m "docs: regenerate context after flow/AM handover implementation"
```

- [ ] **Step 4: Ask user if they want to deploy**

"Implementation complete. All 4 changes are committed. Do you want to deploy to VPS now with `bash deploy.sh`?"
