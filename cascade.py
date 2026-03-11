"""
cascade.py — Real-Time Intelligence Cascade

When a contact's profile changes (Shopify webhook, pixel event, bounce, etc.),
this module runs the full intelligence chain for that single contact:

    score → intelligence → decision

This ensures downstream consumers always have fresh data instead of waiting
for the nightly batch pipeline.

Strategy stack order enforced: Rule 5
    customer scoring → customer intelligence → decision engine
"""

import logging
import threading
import time

logger = logging.getLogger("cascade")

# ── Debounce: don't cascade the same contact more than once per 5 minutes ──
_cascade_timestamps = {}
_DEBOUNCE_SECONDS = 300  # 5 minutes
_lock = threading.Lock()


def _should_cascade(contact_id):
    """Check if enough time has passed since last cascade for this contact."""
    now = time.time()
    with _lock:
        last = _cascade_timestamps.get(contact_id, 0)
        if now - last < _DEBOUNCE_SECONDS:
            return False
        _cascade_timestamps[contact_id] = now
        # Cleanup old entries (> 10 minutes)
        stale = [cid for cid, ts in _cascade_timestamps.items() if now - ts > 600]
        for cid in stale:
            del _cascade_timestamps[cid]
        return True


def cascade_contact(contact_id, trigger="unknown"):
    """
    Run the full intelligence chain for a single contact (in background thread).
    Debounced: skips if cascaded within the last 5 minutes.

    Steps:
        1. score_single_contact()    → ContactScore (RFM + engagement)
        2. compute_intelligence()    → CustomerProfile (lifecycle, intent, churn)
        3. decide_next_action()      → MessageDecision (next best action)
    """
    if not contact_id:
        return

    if not _should_cascade(contact_id):
        logger.debug(f"[Cascade] Debounced contact {contact_id} (trigger={trigger})")
        return

    def _run():
        import sys
        sys.path.insert(0, "/var/www/mailengine")
        start = time.time()
        steps_done = []

        try:
            # Step 0: Rebuild profile from Shopify data
            from database import Contact as _C
            _contact = _C.get_or_none(_C.id == contact_id)
            if _contact:
                from shopify_enrichment import _build_profile
                _build_profile(_contact)
                steps_done.append("profile=OK")
        except Exception as e:
            logger.error(f"[Cascade] Profile rebuild failed for {contact_id}: {e}")
            steps_done.append("profile=ERR")

        try:
            # Step 1: Score (RFM + engagement)
            from ai_engine import score_single_contact
            segment = score_single_contact(contact_id)
            steps_done.append(f"scored={segment}")
        except Exception as e:
            logger.error(f"[Cascade] Scoring failed for {contact_id}: {e}")
            steps_done.append(f"scored=ERR")

        try:
            # Step 2: Intelligence (lifecycle, intent, churn, send window)
            from customer_intelligence import compute_intelligence
            compute_intelligence(contact_id)
            steps_done.append("intelligence=OK")
        except Exception as e:
            logger.error(f"[Cascade] Intelligence failed for {contact_id}: {e}")
            steps_done.append("intelligence=ERR")

        try:
            # Step 3: Decision (next best action)
            from next_best_message import decide_next_action
            result = decide_next_action(contact_id)
            action = result.get("action_type", "?")
            steps_done.append(f"decision={action}")
        except Exception as e:
            logger.error(f"[Cascade] Decision failed for {contact_id}: {e}")
            steps_done.append("decision=ERR")

        elapsed = round(time.time() - start, 2)
        logger.info(f"[Cascade] contact={contact_id} trigger={trigger} "
                     f"steps=[{', '.join(steps_done)}] time={elapsed}s")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def cascade_contact_sync(contact_id, trigger="unknown"):
    """
    Same as cascade_contact() but runs synchronously (for testing / CLI).
    Still respects debounce.
    """
    if not contact_id:
        return

    if not _should_cascade(contact_id):
        return {"skipped": True, "reason": "debounced"}

    import sys
    sys.path.insert(0, "/var/www/mailengine")
    start = time.time()
    results = {}

    try:
        from database import Contact as _C
        _contact = _C.get_or_none(_C.id == contact_id)
        if _contact:
            from shopify_enrichment import _build_profile
            _build_profile(_contact)
            results["profile"] = "OK"
    except Exception as e:
        results["profile_error"] = str(e)

    try:
        from ai_engine import score_single_contact
        results["segment"] = score_single_contact(contact_id)
    except Exception as e:
        results["score_error"] = str(e)

    try:
        from customer_intelligence import compute_intelligence
        compute_intelligence(contact_id)
        results["intelligence"] = "OK"
    except Exception as e:
        results["intelligence_error"] = str(e)

    try:
        from next_best_message import decide_next_action
        result = decide_next_action(contact_id)
        results["action"] = result.get("action_type")
        results["score"] = result.get("action_score")
    except Exception as e:
        results["decision_error"] = str(e)

    results["elapsed"] = round(time.time() - start, 2)
    results["trigger"] = trigger
    return results


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/mailengine")
    from database import init_db
    init_db()

    if len(sys.argv) > 1:
        cid = int(sys.argv[1])
        print(f"Cascading contact {cid}...")
        result = cascade_contact_sync(cid, trigger="cli")
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print("Usage: python3 cascade.py <contact_id>")
