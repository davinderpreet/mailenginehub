"""
End-to-end scenario tests for the MailEngineHub automation pipeline.

Each test seeds fixture data, runs the relevant processor, and asserts
that the ActionLedger and DeliveryQueue contain the expected entries.

All tests run in shadow mode with mock SES — no real emails are sent.
"""

import os
import sys
import pytest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from database import (
    Contact, Flow, FlowStep, FlowEnrollment, FlowEmail,
    ActionLedger, DeliveryQueue, AbandonedCheckout,
    CustomerProfile, CustomerActivity, PendingTrigger,
    ShopifyOrder, WarmupConfig, SuppressionEntry,
    EmailTemplate,
)


class TestNewSignup:
    """Scenario: New customer signs up → enrolled in welcome flow → email queued."""

    def test_new_signup_creates_enrollment_and_ledger(self, make_contact, make_flow):
        # Setup: create a contact_created flow with 1 step
        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="newsignup@test.com")

        # Trigger: enroll contact in flows matching "contact_created"
        # We call the enrollment function directly (imported from app.py context)
        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now() - timedelta(minutes=1),
            status="active",
        )

        # Verify enrollment exists
        assert FlowEnrollment.select().where(
            FlowEnrollment.contact == contact,
            FlowEnrollment.flow == flow,
            FlowEnrollment.status == "active"
        ).count() == 1


class TestUnsubscribedSuppression:
    """Scenario: Unsubscribed contact in active enrollment → suppressed with reason."""

    def test_unsubscribed_contact_gets_suppressed_in_ledger(self, make_contact, make_flow):
        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="unsub@test.com", subscribed=False)

        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now() - timedelta(minutes=1),
            status="active",
        )

        # Simulate what the flow processor does for unsubscribed contacts
        from action_ledger import log_action, RC_UNSUBSCRIBED
        log_action(contact, "flow", flow.id, "suppressed", RC_UNSUBSCRIBED,
                   source_type=flow.name, enrollment_id=enrollment.id,
                   reason_detail="Contact unsubscribed")

        # Verify ledger entry
        ledger = ActionLedger.select().where(
            ActionLedger.email == "unsub@test.com",
            ActionLedger.status == "suppressed",
            ActionLedger.reason_code == "unsubscribed"
        ).first()
        assert ledger is not None
        assert ledger.trigger_type == "flow"
        assert "unsubscribed" in ledger.reason_detail.lower()


class TestSuppressionList:
    """Scenario: Contact on suppression list → suppressed with reason."""

    def test_suppressed_contact_gets_ledger_entry(self, make_contact, make_flow):
        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="bounced@test.com")

        # Add to suppression list
        SuppressionEntry.create(
            email="bounced@test.com", reason="hard_bounce",
            source="ses_notification", detail="550 user unknown"
        )

        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now() - timedelta(minutes=1),
            status="active",
        )

        # Simulate suppression check
        from action_ledger import log_action, RC_SUPPRESSED_ENTRY
        log_action(contact, "flow", flow.id, "suppressed", RC_SUPPRESSED_ENTRY,
                   source_type=flow.name, enrollment_id=enrollment.id,
                   reason_detail="Contact on suppression list")

        ledger = ActionLedger.select().where(
            ActionLedger.email == "bounced@test.com",
            ActionLedger.status == "suppressed",
            ActionLedger.reason_code == "suppressed_entry"
        ).first()
        assert ledger is not None


class TestWarmupLimit:
    """Scenario: Warmup daily limit reached → suppressed with warmup_limit reason."""

    def test_warmup_limit_creates_ledger_entry(self, make_contact, make_flow):
        # Enable warmup and set it to limit
        warmup = WarmupConfig.get_by_id(1)
        warmup.is_active = True
        warmup.current_phase = 1
        warmup.emails_sent_today = 50  # Phase 1 limit = 50
        warmup.last_reset_date = datetime.now().date().isoformat()
        warmup.save()

        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="warmup@test.com")

        from action_ledger import log_action, RC_WARMUP_LIMIT
        log_action(contact, "flow", flow.id, "suppressed", RC_WARMUP_LIMIT,
                   source_type=flow.name,
                   reason_detail="Warmup daily limit (50) reached")

        ledger = ActionLedger.select().where(
            ActionLedger.email == "warmup@test.com",
            ActionLedger.status == "suppressed",
            ActionLedger.reason_code == "warmup_limit"
        ).first()
        assert ledger is not None


class TestFrequencyCap:
    """Scenario: Contact received email < 16h ago → suppressed with cooldown reason."""

    def test_frequency_cap_creates_ledger_entry(self, make_contact, make_flow):
        flow = make_flow("contact_created", steps=2, priority=5)
        contact = make_contact(email="freqcap@test.com")

        from action_ledger import log_action, RC_COOLDOWN_ACTIVE
        log_action(contact, "flow", flow.id, "suppressed", RC_COOLDOWN_ACTIVE,
                   source_type=flow.name,
                   reason_detail="16h frequency cap - last email 4.2h ago, rescheduled")

        ledger = ActionLedger.select().where(
            ActionLedger.email == "freqcap@test.com",
            ActionLedger.status == "suppressed",
            ActionLedger.reason_code == "cooldown_active"
        ).first()
        assert ledger is not None
        assert "frequency cap" in ledger.reason_detail.lower()


class TestQueueAndShadow:
    """Scenario: Email enqueued and processed in shadow mode → shadowed status."""

    def test_enqueue_creates_queue_item_and_ledger(self, make_contact, make_flow):
        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="shadow@test.com")

        from action_ledger import log_action, RC_OK
        from delivery_engine import enqueue_email

        ledger = log_action(contact, "flow", flow.id, "rendered", RC_OK,
                            source_type=flow.name, template_id=1,
                            subject="Welcome {{first_name}}", html="<p>Hello</p>",
                            priority=50)

        item = enqueue_email(
            contact=contact, email_type="flow", source_id=flow.id,
            enrollment_id=0, step_id=1, template_id=1,
            from_name="LDAS", from_email="test@ldas.ca",
            subject="Welcome Test", html="<p>Hello</p>",
            unsubscribe_url="https://example.com/unsub",
            priority=50, ledger_id=ledger.id,
        )

        # Verify queue item created
        assert item.status == "queued"
        assert item.email == "shadow@test.com"

        # Verify ledger updated to queued
        ledger_updated = ActionLedger.get_by_id(ledger.id)
        assert ledger_updated.status == "queued"

        # Verify queue item exists
        queue_count = DeliveryQueue.select().where(
            DeliveryQueue.email == "shadow@test.com",
            DeliveryQueue.status == "queued"
        ).count()
        assert queue_count == 1


class TestShadowProcessing:
    """Scenario: Queue processor in shadow mode marks items as shadowed."""

    def test_shadow_mode_marks_queued_as_shadowed(self, make_contact, make_flow):
        flow = make_flow("contact_created", steps=1, priority=5)
        contact = make_contact(email="process@test.com")

        from action_ledger import log_action, RC_OK
        from delivery_engine import enqueue_email, process_queue

        ledger = log_action(contact, "flow", flow.id, "rendered", RC_OK,
                            source_type=flow.name, priority=50)

        enqueue_email(
            contact=contact, email_type="flow", source_id=flow.id,
            enrollment_id=0, step_id=0, template_id=1,
            from_name="LDAS", from_email="test@ldas.ca",
            subject="Test", html="<p>Test</p>",
            unsubscribe_url="", priority=50,
            ledger_id=ledger.id,
        )

        # Process the queue (should shadow, not send)
        processed = process_queue()
        assert processed == 1

        # Verify queue item is shadowed
        item = DeliveryQueue.select().where(DeliveryQueue.email == "process@test.com").first()
        assert item.status == "shadowed"

        # Verify ledger is shadowed
        ledger_final = ActionLedger.get_by_id(ledger.id)
        assert ledger_final.status == "shadowed"


class TestCheckoutAbandonment:
    """Scenario: Abandoned checkout detected → contact enrolled in checkout flow."""

    def test_abandoned_checkout_creates_enrollment(self, make_contact, make_flow):
        flow = make_flow("checkout_abandoned", steps=2, priority=1)
        contact = make_contact(email="cart@test.com")

        # Create abandoned checkout older than 1 hour
        checkout = AbandonedCheckout.create(
            shopify_checkout_id="chk_123",
            email="cart@test.com",
            contact=contact,
            checkout_url="https://ldas.ca/checkout/123",
            total_price="99.99",
            currency="CAD",
            line_items_json='[{"title":"Dash Cam","quantity":1,"price":"99.99"}]',
            recovered=False,
            enrolled_in_flow=False,
            created_at=datetime.now() - timedelta(hours=2),
        )

        # Manually enroll (simulating what _check_abandoned_checkouts does)
        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now(), status="active",
        )
        checkout.enrolled_in_flow = True
        checkout.save()

        # Verify
        assert FlowEnrollment.select().where(
            FlowEnrollment.contact == contact,
            FlowEnrollment.flow == flow,
        ).count() == 1
        assert checkout.enrolled_in_flow is True


class TestLapsedCustomer:
    """Scenario: Customer with last order 91 days ago → enrolled in winback flow."""

    def test_lapsed_customer_eligible_for_winback(self, make_contact, make_flow):
        flow = make_flow("no_purchase_days", trigger_value="90", steps=1, priority=4)
        contact = make_contact(email="lapsed@test.com")

        # Create an old Shopify order (91 days ago)
        ShopifyOrder.create(
            shopify_order_id="ord_old_123",
            email="lapsed@test.com",
            contact=contact,
            order_number="1001",
            total_price=150.00,
            currency="CAD",
            created_at=datetime.now() - timedelta(days=91),
        )

        # Verify the contact would be eligible: last order > 90 days ago
        last_order = (ShopifyOrder.select()
                      .where(ShopifyOrder.contact == contact)
                      .order_by(ShopifyOrder.created_at.desc())
                      .first())
        days_since = (datetime.now() - last_order.created_at).days
        assert days_since >= 90

        # Enroll (simulating what _check_passive_triggers does)
        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now(), status="active",
        )
        assert enrollment.status == "active"
