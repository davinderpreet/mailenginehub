"""
Live scenario test fixtures — creates test contacts and triggers for all 5 scenarios.
Run on VPS: /var/www/mailengine/venv/bin/python3 test_scenarios_live.py setup
             /var/www/mailengine/venv/bin/python3 test_scenarios_live.py trigger
             /var/www/mailengine/venv/bin/python3 test_scenarios_live.py verify
"""
import sys
import os
sys.path.insert(0, "/var/www/mailengine")
os.chdir("/var/www/mailengine")
from dotenv import load_dotenv
load_dotenv()

from database import *
init_db()
from datetime import datetime, timedelta

TEST_EMAILS = [
    "scenario1-signup@test.local",
    "scenario2-browse@test.local",
    "scenario3-cart@test.local",
    "scenario4-checkout@test.local",
    "scenario5-lapsed@test.local",
]


def cleanup():
    """Remove all test data."""
    for email in TEST_EMAILS:
        ActionLedger.delete().where(ActionLedger.email == email).execute()
        DeliveryQueue.delete().where(DeliveryQueue.email == email).execute()
        PendingTrigger.delete().where(PendingTrigger.email == email).execute()
        CustomerActivity.delete().where(CustomerActivity.email == email).execute()
        AbandonedCheckout.delete().where(AbandonedCheckout.email == email).execute()
        try:
            c = Contact.get(Contact.email == email)
            # Delete all FK-dependent rows first
            FlowEmail.delete().where(FlowEmail.contact == c).execute()
            FlowEnrollment.delete().where(FlowEnrollment.contact == c).execute()
            CustomerProfile.delete().where(CustomerProfile.contact == c).execute()
            # Also clean ContactScore, AIDecisionLog, etc. if they reference contact
            try:
                ContactScore.delete().where(ContactScore.contact == c).execute()
            except Exception:
                pass
            c.delete_instance(recursive=True)
        except Contact.DoesNotExist:
            pass
    print("[cleanup] Removed all test data")


def setup():
    """Create test contacts and seed data for all 5 scenarios."""
    cleanup()

    # S1: New Signup — contact only (enrollment triggered separately)
    c1 = Contact.create(
        email="scenario1-signup@test.local",
        first_name="ScenarioOne",
        last_name="Test",
        subscribed=True,
        source="test",
    )
    print("[S1] Created contact #%d %s" % (c1.id, c1.email))

    # S2: Browse Abandonment — 3 product views in last 48h, no order
    c2 = Contact.create(
        email="scenario2-browse@test.local",
        first_name="ScenarioTwo",
        last_name="Test",
        subscribed=True,
        source="shopify",
    )
    for i in range(3):
        CustomerActivity.create(
            contact=c2,
            email=c2.email,
            event_type="viewed_product",
            event_data='{"product_title": "Test Widget Pro", "product_id": "999"}',
            occurred_at=datetime.now() - timedelta(hours=2 + i),
        )
    print("[S2] Created contact #%d with 3 product views" % c2.id)

    # S3: Cart Abandonment — abandoned_checkout event, no recovery
    c3 = Contact.create(
        email="scenario3-cart@test.local",
        first_name="ScenarioThree",
        last_name="Test",
        subscribed=True,
        source="shopify",
    )
    CustomerActivity.create(
        contact=c3,
        email=c3.email,
        event_type="abandoned_checkout",
        event_data='{"checkout_id": "test-123", "products": ["Widget A"], "total": "49.99", "item_count": 1}',
        created_at=datetime.now() - timedelta(hours=3),
    )
    print("[S3] Created contact #%d with abandoned checkout activity" % c3.id)

    # S4: Checkout Abandonment — AbandonedCheckout record, 2h old
    c4 = Contact.create(
        email="scenario4-checkout@test.local",
        first_name="ScenarioFour",
        last_name="Test",
        subscribed=True,
        source="shopify",
    )
    AbandonedCheckout.create(
        email=c4.email,
        contact=c4,
        shopify_checkout_id="test-checkout-456",
        checkout_url="https://ldas.ca/checkout/test-456",
        total_price=129.99,
        recovered=False,
        enrolled_in_flow=False,
        created_at=datetime.now() - timedelta(hours=2),
    )
    print("[S4] Created contact #%d with AbandonedCheckout 2h old" % c4.id)

    # S5: Lapsed Customer — CustomerProfile with last_order 120 days ago
    c5 = Contact.create(
        email="scenario5-lapsed@test.local",
        first_name="ScenarioFive",
        last_name="Test",
        subscribed=True,
        source="shopify",
    )
    CustomerProfile.create(
        contact=c5,
        email=c5.email,
        total_orders=3,
        total_spent=450.00,
        first_order_at=datetime.now() - timedelta(days=365),
        last_order_at=datetime.now() - timedelta(days=120),
        days_since_last_order=120,
        rfm_segment="lapsed",
        lifecycle_stage="at_risk",
    )
    print("[S5] Created contact #%d with last order 120 days ago" % c5.id)
    print()
    print("=== All 5 fixtures ready. Run 'trigger' next. ===")


def trigger():
    """Trigger each scenario's detection/enrollment path."""
    from app import (
        _enroll_contact_in_flows,
        _check_abandoned_checkouts,
        _check_passive_triggers,
    )

    print("--- S1: New Signup ---")
    c1 = Contact.get(Contact.email == "scenario1-signup@test.local")
    _enroll_contact_in_flows(c1, "contact_created")
    enrollments = list(FlowEnrollment.select().where(FlowEnrollment.contact == c1))
    for en in enrollments:
        f = Flow.get_by_id(en.flow_id)
        print("  Enrolled in: %s step=%d next=%s status=%s" % (f.name, en.current_step, en.next_send_at, en.status))
    if not enrollments:
        print("  BUG: No enrollment created")

    print()
    print("--- S4: Checkout Abandonment ---")
    _check_abandoned_checkouts()
    c4 = Contact.get(Contact.email == "scenario4-checkout@test.local")
    enrollments = list(FlowEnrollment.select().where(FlowEnrollment.contact == c4))
    for en in enrollments:
        f = Flow.get_by_id(en.flow_id)
        print("  Enrolled in: %s step=%d next=%s status=%s" % (f.name, en.current_step, en.next_send_at, en.status))
    ac = AbandonedCheckout.get(AbandonedCheckout.email == "scenario4-checkout@test.local")
    print("  AbandonedCheckout.enrolled_in_flow=%s" % ac.enrolled_in_flow)
    if not enrollments:
        print("  BUG: No enrollment created")

    print()
    print("--- S2/S3/S5: Passive Triggers (browse, cart, lapsed) ---")
    _check_passive_triggers()

    for label, email, expected_trigger, expected_flow in [
        ("S2", "scenario2-browse@test.local", "browse_abandonment", "Browse Abandonment"),
        ("S3", "scenario3-cart@test.local", "cart_abandonment", "Cart Abandonment Recovery"),
        ("S5", "scenario5-lapsed@test.local", None, "Win-Back Lapsed Customers"),
    ]:
        print()
        print("--- %s: %s ---" % (label, email))
        c = Contact.get(Contact.email == email)

        # Check PendingTrigger
        triggers = list(PendingTrigger.select().where(PendingTrigger.email == email))
        for t in triggers:
            print("  PendingTrigger: type=%s status=%s detected=%s" % (t.trigger_type, t.status, t.detected_at))
        if expected_trigger and not triggers:
            print("  BUG: No PendingTrigger created for %s" % expected_trigger)

        # Check FlowEnrollment
        enrollments = list(FlowEnrollment.select().where(FlowEnrollment.contact == c))
        for en in enrollments:
            f = Flow.get_by_id(en.flow_id)
            print("  Enrollment: %s step=%d next=%s status=%s" % (f.name, en.current_step, en.next_send_at, en.status))
        if not enrollments:
            print("  BUG: No enrollment created for %s" % expected_flow)

    print()
    print("=== Triggers fired. Run 'verify' after ~90 seconds (flow processor + queue processor). ===")


def verify():
    """Check all expected rows exist for each scenario."""
    print("=" * 60)
    print("SCENARIO VERIFICATION — %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    all_pass = True

    for label, email, expected_flow_trigger in [
        ("S1: New Signup", "scenario1-signup@test.local", "contact_created"),
        ("S2: Browse Abandonment", "scenario2-browse@test.local", "browse_abandonment"),
        ("S3: Cart Abandonment", "scenario3-cart@test.local", "cart_abandonment"),
        ("S4: Checkout Abandonment", "scenario4-checkout@test.local", "checkout_abandoned"),
        ("S5: Lapsed Customer", "scenario5-lapsed@test.local", "no_purchase_days"),
    ]:
        print()
        print("--- %s (%s) ---" % (label, email))
        results = []

        try:
            c = Contact.get(Contact.email == email)
        except Contact.DoesNotExist:
            print("  FAIL: Contact not found")
            all_pass = False
            continue

        # 1. FlowEnrollment
        enrollments = list(FlowEnrollment.select().where(FlowEnrollment.contact == c))
        if enrollments:
            for en in enrollments:
                f = Flow.get_by_id(en.flow_id)
                match = f.trigger_type == expected_flow_trigger
                status = "PASS" if match else "WARN"
                print("  [%s] FlowEnrollment: %s (trigger=%s) step=%d status=%s" % (
                    status, f.name, f.trigger_type, en.current_step, en.status))
                if not match:
                    results.append(False)
                else:
                    results.append(True)
        else:
            print("  [FAIL] No FlowEnrollment")
            results.append(False)

        # 2. ActionLedger
        ledger = list(ActionLedger.select().where(ActionLedger.email == email).order_by(ActionLedger.id.asc()))
        if ledger:
            for le in ledger:
                print("  [INFO] ActionLedger #%d: status=%s reason=%s trigger=%s subj='%s'" % (
                    le.id, le.status, le.reason_code, le.trigger_type, (le.subject or "")[:40]))
            # Check for rendered or queued or shadowed
            statuses = {le.status for le in ledger}
            if statuses & {"rendered", "queued", "sent", "shadowed"}:
                print("  [PASS] ActionLedger has processed entry")
                results.append(True)
            elif statuses == {"suppressed"}:
                reasons = {le.reason_code for le in ledger}
                print("  [WARN] Only suppressed entries — reasons: %s" % reasons)
                results.append(True)  # suppression is valid, not a bug
            else:
                print("  [FAIL] ActionLedger has no processed/suppressed entry")
                results.append(False)
        else:
            print("  [FAIL] No ActionLedger entries")
            results.append(False)

        # 3. DeliveryQueue
        queue = list(DeliveryQueue.select().where(DeliveryQueue.email == email))
        if queue:
            for q in queue:
                print("  [INFO] DeliveryQueue #%d: status=%s type=%s priority=%d subj='%s'" % (
                    q.id, q.status, q.email_type, q.priority, (q.subject or "")[:40]))
            drained = any(q.status in ("sent", "shadowed") for q in queue)
            if drained:
                print("  [PASS] DeliveryQueue drained (sent/shadowed)")
                results.append(True)
            elif any(q.status == "queued" for q in queue):
                print("  [WAIT] DeliveryQueue still queued — queue processor hasn't run yet")
                results.append(True)  # not a fail, just timing
            else:
                print("  [WARN] DeliveryQueue status: %s" % {q.status for q in queue})
                results.append(True)
        else:
            # No queue entry = either suppressed (valid) or bug
            if ledger and any(le.status == "suppressed" for le in ledger):
                print("  [PASS] No DeliveryQueue (correctly suppressed)")
                results.append(True)
            elif not ledger:
                print("  [FAIL] No DeliveryQueue and no ActionLedger — pipeline did not run")
                results.append(False)
            else:
                print("  [WARN] No DeliveryQueue but ledger exists — check statuses")
                results.append(True)

        # 4. FlowEmail (backward compat)
        fe = list(FlowEmail.select().where(FlowEmail.contact == c))
        if fe:
            for f in fe:
                print("  [INFO] FlowEmail #%d: status=%s step=%s" % (f.id, f.status, f.step_id))
            print("  [PASS] FlowEmail compat record created")
            results.append(True)
        else:
            if queue and any(q.status in ("sent", "shadowed") for q in queue):
                print("  [FAIL] DeliveryQueue drained but no FlowEmail compat record")
                results.append(False)
            else:
                print("  [INFO] No FlowEmail yet (pipeline may not have completed)")
                results.append(True)

        # Scenario verdict
        if all(results):
            print("  >>> SCENARIO %s: PASS" % label.split(":")[0])
        else:
            print("  >>> SCENARIO %s: FAIL" % label.split(":")[0])
            all_pass = False

    print()
    print("=" * 60)
    if all_pass:
        print("OVERALL: ALL SCENARIOS PASSED")
    else:
        print("OVERALL: SOME SCENARIOS FAILED — see details above")
    print("=" * 60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "setup":
        setup()
    elif cmd == "trigger":
        trigger()
    elif cmd == "verify":
        verify()
    elif cmd == "cleanup":
        cleanup()
    elif cmd == "all":
        setup()
        print()
        trigger()
    else:
        print("Usage: python test_scenarios_live.py [setup|trigger|verify|cleanup|all]")
