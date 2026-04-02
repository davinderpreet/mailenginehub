"""
test_delivery_guards.py — Regression tests for delivery guard logic & buyer-state.

Covers:
  1. Guard 2 purchase-after-enrollment blocks recovery sends
  2. Guard 2 fail-closed on exception
  3. Guard 2 non-recovery flows pass through
  4. Guard 2 multi-signal: checkout_completed activity without ShopifyOrder
  5. Guard 2 multi-signal: recovered abandoned checkout without ShopifyOrder
  6. Guard 2 avoids false positives (old stale evidence)
  7. Time-correlation regression (mixed timestamps)
  8. apply_minimal_buyer_state creates/updates CustomerProfile
  9. apply_minimal_buyer_state corrects lifecycle from prospect
  10. schedule_profile_refresh creates profile when missing
  11. repair_post_purchase_contact full lifecycle
  12. Real-case regression modeled after suchavirk@gmail.com

Run:  python -m pytest tests/test_delivery_guards.py -v
"""

import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock, call

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_queue_item(email="test@example.com", email_type="flow", enrollment_id=1,
                     status="queued", ledger_id=0, contact=None, contact_id=None):
    item = MagicMock()
    item.email = email
    item.email_type = email_type
    item.enrollment_id = enrollment_id
    item.status = status
    item.ledger_id = ledger_id
    item.contact = contact
    item.contact_id = contact_id
    item.from_email = "news@news.ldaselectronics.com"
    item.from_name = "LDAS"
    item.subject = "Test"
    item.html = "<p>Test</p>"
    item.unsubscribe_url = ""
    item.campaign_id = None
    item.template_id = None
    item.auto_email_id = None
    item.source_id = None
    item.step_id = None
    item.id = 999
    item.error_msg = ""
    item.sent_at = None
    return item


def _make_enrollment(flow_id=1, status="active", enrolled_at=None):
    e = MagicMock()
    e.id = 1
    e.flow_id = flow_id
    e.status = status
    e.enrolled_at = enrolled_at or datetime.now() - timedelta(hours=24)
    return e


def _make_flow(trigger_type="checkout_abandoned"):
    f = MagicMock()
    f.id = 1
    f.trigger_type = trigger_type
    return f


def _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                         has_order=False, has_checkout_completed=False,
                         has_recovered_checkout=False):
    """Set up all Guard 2 signal mocks with proper Peewee field comparison support."""
    # ShopifyOrder
    mock_order.ordered_at = MagicMock()
    mock_order.ordered_at.__ge__ = MagicMock(return_value=MagicMock())
    mock_order.financial_status = MagicMock()
    mock_order.email = MagicMock()
    mock_order.email.__eq__ = MagicMock(return_value=MagicMock())
    mock_oq = MagicMock()
    mock_oq.exists.return_value = has_order
    mock_oq.where.return_value = mock_oq
    mock_order.select.return_value = mock_oq

    # CustomerActivity
    mock_activity.email = MagicMock()
    mock_activity.email.__eq__ = MagicMock(return_value=MagicMock())
    mock_activity.event_type = MagicMock()
    mock_activity.event_type.__eq__ = MagicMock(return_value=MagicMock())
    mock_activity.occurred_at = MagicMock()
    mock_activity.occurred_at.__ge__ = MagicMock(return_value=MagicMock())
    mock_aq = MagicMock()
    mock_aq.exists.return_value = has_checkout_completed
    mock_aq.where.return_value = mock_aq
    mock_activity.select.return_value = mock_aq

    # AbandonedCheckout
    mock_checkout.email = MagicMock()
    mock_checkout.email.__eq__ = MagicMock(return_value=MagicMock())
    mock_checkout.recovered = MagicMock()
    mock_checkout.recovered.__eq__ = MagicMock(return_value=MagicMock())
    mock_checkout.recovered_at = MagicMock()
    mock_checkout.recovered_at.__ge__ = MagicMock(return_value=MagicMock())
    mock_cq = MagicMock()
    mock_cq.exists.return_value = has_recovered_checkout
    mock_cq.where.return_value = mock_cq
    mock_checkout.select.return_value = mock_cq


def _mock_bounce_none(mock_bounce):
    """Set up BounceLog mock to return no bounces."""
    mock_bounce.email = MagicMock()
    mock_bounce.email.__eq__ = MagicMock(return_value=MagicMock())
    mock_bounce.event_type = MagicMock()
    mock_bounce.timestamp = MagicMock()
    mock_bounce.timestamp.__ge__ = MagicMock(return_value=MagicMock())
    mock_q = MagicMock()
    mock_q.exists.return_value = False
    mock_q.where.return_value = mock_q
    mock_bounce.select.return_value = mock_q


# ─────────────────────────────────────────────────────────────────────────────
# 1. Guard 2: ShopifyOrder blocks recovery sends
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2PurchaseBlock:
    """Guard 2 should cancel recovery emails when a purchase exists after enrollment."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_guard2_blocks_when_order_exists(self, mock_bounce, mock_contact,
                                              mock_order, mock_activity, mock_checkout,
                                              mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """Recovery email cancelled when ShopifyOrder exists after enrollment."""
        from delivery_engine import _send_one

        enrolled_at = datetime.now() - timedelta(hours=48)
        enrollment = _make_enrollment(enrolled_at=enrolled_at)
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=True, has_checkout_completed=False, has_recovered_checkout=False)

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        result = _send_one(item, MagicMock())
        assert result == 0
        assert item.status == "cancelled"
        assert item.error_msg == "purchased_after_enrollment"

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_guard2_allows_when_no_evidence(self, mock_bounce, mock_contact,
                                             mock_order, mock_activity, mock_checkout,
                                             mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """Recovery email proceeds when no purchase evidence exists."""
        from delivery_engine import _send_one

        enrollment = _make_enrollment()
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=False, has_checkout_completed=False, has_recovered_checkout=False)

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        send_fn = MagicMock(return_value=(True, None, "msg-123"))
        result = _send_one(item, send_fn)
        assert result == 1
        send_fn.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Guard 2: Fail-closed on exception
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2FailClosed:
    """Guard 2 should block sends when it encounters an error (fail-closed)."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    def test_guard2_blocks_on_db_error(self, mock_contact, mock_order, mock_activity,
                                        mock_checkout, mock_enrollment_cls,
                                        mock_flow_cls, mock_ledger):
        """When Guard 2 signal check throws exception, email is cancelled (fail-closed)."""
        from delivery_engine import _send_one

        enrollment = _make_enrollment()
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        # Simulate database error in signal helper
        mock_order.select.side_effect = Exception("DB connection lost")
        mock_activity.select.side_effect = Exception("DB connection lost")
        mock_checkout.select.side_effect = Exception("DB connection lost")

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)

        result = _send_one(item, MagicMock())
        assert result == 0
        assert item.status == "cancelled"
        assert "guard2_error" in item.error_msg


# ─────────────────────────────────────────────────────────────────────────────
# 3. Guard 2: Non-recovery flows pass through
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2NonRecoveryFlows:
    """Guard 2 only applies to recovery flows — order_placed flows pass through."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_order_placed_flow_skips_guard2(self, mock_bounce, mock_contact,
                                             mock_order, mock_activity, mock_checkout,
                                             mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """order_placed flows should never be blocked by Guard 2."""
        from delivery_engine import _send_one

        enrollment = _make_enrollment()
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("order_placed")
        mock_flow_cls.get_or_none.return_value = flow

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        send_fn = MagicMock(return_value=(True, None, "msg-456"))
        result = _send_one(item, send_fn)
        assert result == 1
        send_fn.assert_called_once()
        mock_order.select.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Guard 2 multi-signal: checkout_completed without ShopifyOrder
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2CheckoutCompletedSignal:
    """Guard 2 blocks recovery when checkout_completed exists even without ShopifyOrder."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_checkout_completed_blocks_without_order(self, mock_bounce, mock_contact,
                                                      mock_order, mock_activity, mock_checkout,
                                                      mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """No ShopifyOrder row yet, but checkout_completed activity exists → block."""
        from delivery_engine import _send_one

        enrolled_at = datetime.now() - timedelta(hours=2)
        enrollment = _make_enrollment(enrolled_at=enrolled_at)
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        # No order, but checkout_completed activity exists
        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=False, has_checkout_completed=True, has_recovered_checkout=False)

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)

        result = _send_one(item, MagicMock())
        assert result == 0
        assert item.status == "cancelled"
        assert item.error_msg == "purchased_after_enrollment"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Guard 2 multi-signal: recovered abandoned checkout without ShopifyOrder
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2RecoveredCheckoutSignal:
    """Guard 2 blocks recovery when abandoned checkout is marked recovered."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_recovered_checkout_blocks_without_order(self, mock_bounce, mock_contact,
                                                      mock_order, mock_activity, mock_checkout,
                                                      mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """No ShopifyOrder row yet, but recovered abandoned checkout exists → block."""
        from delivery_engine import _send_one

        enrolled_at = datetime.now() - timedelta(hours=2)
        enrollment = _make_enrollment(enrolled_at=enrolled_at)
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("cart_abandonment")
        mock_flow_cls.get_or_none.return_value = flow

        # No order, no activity, but recovered checkout exists
        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=False, has_checkout_completed=False, has_recovered_checkout=True)

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)

        result = _send_one(item, MagicMock())
        assert result == 0
        assert item.status == "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Guard 2 avoids false positives (old stale evidence)
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2NoFalsePositives:
    """Guard 2 should not false-positive on old/stale evidence unrelated to this enrollment."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_no_evidence_in_window_allows_send(self, mock_bounce, mock_contact,
                                                mock_order, mock_activity, mock_checkout,
                                                mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """All signals return false (evidence too old or wrong email) → allow send."""
        from delivery_engine import _send_one

        enrollment = _make_enrollment()
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("browse_abandonment")
        mock_flow_cls.get_or_none.return_value = flow

        # All signals negative — the WHERE >= window_start filtered out old data
        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=False, has_checkout_completed=False, has_recovered_checkout=False)

        item = _make_queue_item()
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        send_fn = MagicMock(return_value=(True, None, "msg-789"))
        result = _send_one(item, send_fn)
        assert result == 1
        send_fn.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Time-correlation regression
# ─────────────────────────────────────────────────────────────────────────────

class TestGuard2TimestampCorrelation:
    """The 1-hour buffer in the correlation window handles edge cases."""

    def test_signal_helper_uses_buffer(self):
        """_has_post_enrollment_purchase_signal uses PRE_ENROLLMENT_BUFFER."""
        from delivery_engine import _has_post_enrollment_purchase_signal, PRE_ENROLLMENT_BUFFER
        assert PRE_ENROLLMENT_BUFFER == timedelta(hours=1)

    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    def test_buffer_catches_order_30min_before_enrollment(self, mock_order, mock_activity, mock_checkout):
        """Order placed 30 min before enrollment is inside the 1-hour buffer window."""
        from delivery_engine import _has_post_enrollment_purchase_signal

        enrolled_at = datetime(2026, 3, 28, 15, 0, 0)
        enrollment = MagicMock()
        enrollment.enrolled_at = enrolled_at

        # Set up mocks — order exists (30 min before enrollment is inside window)
        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=True, has_checkout_completed=False, has_recovered_checkout=False)

        has_signal, evidence = _has_post_enrollment_purchase_signal(
            "test@example.com", enrollment, "checkout_abandoned")

        assert has_signal is True
        assert "ShopifyOrder" in evidence

        # Verify buffer: ordered_at.__ge__ called with enrolled_at - 1 hour
        expected_window_start = datetime(2026, 3, 28, 14, 0, 0)
        ge_call = mock_order.ordered_at.__ge__
        ge_call.assert_called_once()
        actual_ts = ge_call.call_args[0][0]
        assert actual_ts == expected_window_start

    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    def test_non_recovery_trigger_returns_false(self, mock_order, mock_activity, mock_checkout):
        """Signal helper returns false for non-recovery triggers."""
        from delivery_engine import _has_post_enrollment_purchase_signal

        enrollment = MagicMock()
        enrollment.enrolled_at = datetime.now()

        has_signal, _ = _has_post_enrollment_purchase_signal(
            "test@example.com", enrollment, "order_placed")
        assert has_signal is False


# ─────────────────────────────────────────────────────────────────────────────
# 8. apply_minimal_buyer_state creates/updates CustomerProfile
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyMinimalBuyerState:
    """apply_minimal_buyer_state persists minimum buyer fields immediately."""

    @patch("database.CustomerProfile")
    def test_creates_profile_when_missing(self, mock_profile_cls):
        """Creates a CustomerProfile row if one doesn't exist."""
        import importlib
        import customer_intelligence
        importlib.reload(customer_intelligence)

        contact = MagicMock()
        contact.id = 42
        contact.email = "buyer@test.com"
        contact.total_orders = 0
        contact.total_spent = 0.0

        # No existing profile
        mock_profile_cls.get_or_none.return_value = None
        mock_profile_cls.contact_id = MagicMock()

        # Mock the create to return a mutable profile object
        new_profile = MagicMock()
        new_profile.total_orders = 0
        new_profile.total_spent = 0.0
        new_profile.last_order_at = None
        new_profile.first_order_at = None
        new_profile.lifecycle_stage = "unknown"
        new_profile.customer_type = "unknown"
        new_profile.days_since_last_order = 0
        new_profile.last_active_at = None
        mock_profile_cls.create.return_value = new_profile

        # Pass all params explicitly to avoid ShopifyOrder queries
        order_time = datetime(2026, 3, 28, 20, 14, 59)
        result = customer_intelligence.apply_minimal_buyer_state(
            contact, order_count=1, total_spent=96.62,
            last_order_at=order_time, first_order_at=order_time)

        assert "error" not in result
        mock_profile_cls.create.assert_called_once()
        assert new_profile.save.called

    @patch("database.CustomerProfile")
    def test_updates_lifecycle_from_prospect(self, mock_profile_cls):
        """Prospect lifecycle → new_customer after first confirmed purchase."""
        import importlib
        import customer_intelligence
        importlib.reload(customer_intelligence)

        contact = MagicMock()
        contact.id = 42
        contact.email = "buyer@test.com"
        contact.total_orders = 0
        contact.total_spent = 0.0

        # Existing profile with stale data
        profile = MagicMock()
        profile.total_orders = 0
        profile.total_spent = 0.0
        profile.last_order_at = None
        profile.first_order_at = None
        profile.lifecycle_stage = "prospect"
        profile.customer_type = "browser"
        profile.days_since_last_order = 0
        profile.last_active_at = None
        mock_profile_cls.get_or_none.return_value = profile
        mock_profile_cls.contact_id = MagicMock()

        order_time = datetime(2026, 3, 28, 20, 14, 59)
        result = customer_intelligence.apply_minimal_buyer_state(
            contact, order_count=1, total_spent=96.62,
            last_order_at=order_time, first_order_at=order_time)

        assert "error" not in result
        assert profile.lifecycle_stage == "new_customer"
        assert profile.customer_type == "one_time"
        assert profile.total_orders == 1
        assert profile.save.called


# ─────────────────────────────────────────────────────────────────────────────
# 9. apply_minimal_buyer_state handles repeat buyers
# ─────────────────────────────────────────────────────────────────────────────

class TestBuyerStateRepeatBuyer:
    """Repeat buyer gets active_buyer lifecycle and repeat customer_type."""

    @patch("database.CustomerProfile")
    def test_repeat_buyer_lifecycle(self, mock_profile_cls):
        """2+ orders → active_buyer + repeat."""
        import importlib
        import customer_intelligence
        importlib.reload(customer_intelligence)

        contact = MagicMock()
        contact.id = 42
        contact.email = "repeat@test.com"
        contact.total_orders = 0
        contact.total_spent = 0.0

        profile = MagicMock()
        profile.total_orders = 0
        profile.total_spent = 0.0
        profile.last_order_at = None
        profile.first_order_at = datetime(2026, 1, 15)
        profile.lifecycle_stage = "prospect"
        profile.customer_type = "browser"
        profile.days_since_last_order = 0
        profile.last_active_at = None
        mock_profile_cls.get_or_none.return_value = profile
        mock_profile_cls.contact_id = MagicMock()

        order_time = datetime(2026, 3, 28)
        result = customer_intelligence.apply_minimal_buyer_state(
            contact, order_count=3, total_spent=250.0,
            last_order_at=order_time, first_order_at=datetime(2026, 1, 15))

        assert profile.lifecycle_stage == "active_buyer"
        assert profile.customer_type == "repeat"


# ─────────────────────────────────────────────────────────────────────────────
# 10. schedule_profile_refresh creates profile when missing
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduleProfileRefreshCreatesProfile:
    """schedule_profile_refresh should create CustomerProfile if it doesn't exist."""

    @patch("database.CustomerProfile")
    @patch("database.Contact")
    def test_creates_profile_when_missing(self, mock_contact_cls, mock_profile_cls):
        """When UPDATE affects 0 rows, a new profile is created."""
        import importlib
        import customer_intelligence
        importlib.reload(customer_intelligence)

        mock_profile_cls.update.return_value.where.return_value.execute.return_value = 0
        mock_contact = MagicMock()
        mock_contact.id = 42
        mock_contact.email = "test@example.com"
        mock_contact_cls.get_or_none.return_value = mock_contact

        customer_intelligence.schedule_profile_refresh(42, "placed_order")

        mock_profile_cls.create.assert_called_once()
        create_kwargs = mock_profile_cls.create.call_args[1]
        assert create_kwargs["contact"] == mock_contact
        assert create_kwargs["lifecycle_stage"] == "unknown"

    @patch("database.CustomerProfile")
    def test_skips_create_when_profile_exists(self, mock_profile_cls):
        """When UPDATE affects 1 row, no new profile is created."""
        import importlib
        import customer_intelligence
        importlib.reload(customer_intelligence)

        mock_profile_cls.update.return_value.where.return_value.execute.return_value = 1
        customer_intelligence.schedule_profile_refresh(42, "placed_order")
        mock_profile_cls.create.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 11. repair_post_purchase_contact full lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestRepairPostPurchaseContact:
    """Repair utility fixes contacts who purchased but weren't transitioned."""

    @patch("database.CustomerProfile")
    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.DeliveryQueue")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    def test_dry_run_reports_all_fixes(self, mock_contact_cls, mock_order_cls,
                                       mock_activity_cls, mock_checkout_cls,
                                       mock_queue_cls, mock_enrollment_cls,
                                       mock_flow_cls, mock_ledger, mock_cp):
        """Dry run identifies all required fixes without applying them."""
        from delivery_engine import repair_post_purchase_contact

        contact = MagicMock()
        contact.id = 42
        contact.email = "buyer@example.com"
        contact.total_orders = 0
        mock_contact_cls.get_or_none.return_value = contact

        # Set up ShopifyOrder count
        mock_order_cls.email = MagicMock()
        mock_order_cls.email.__eq__ = MagicMock(return_value=MagicMock())
        mock_order_cls.financial_status = MagicMock()
        mock_oq = MagicMock()
        mock_oq.count.return_value = 1
        mock_oq.where.return_value = mock_oq
        mock_oq.exists.return_value = False
        mock_order_cls.select.return_value = mock_oq

        # CustomerActivity — no evidence needed for order_count > 0 path
        mock_activity_cls.email = MagicMock()
        mock_activity_cls.email.__eq__ = MagicMock(return_value=MagicMock())
        mock_activity_cls.event_type = MagicMock()
        mock_activity_cls.event_type.__eq__ = MagicMock(return_value=MagicMock())
        mock_aq = MagicMock()
        mock_aq.exists.return_value = False
        mock_aq.where.return_value = mock_aq
        mock_activity_cls.select.return_value = mock_aq

        # AbandonedCheckout
        mock_checkout_cls.email = MagicMock()
        mock_checkout_cls.email.__eq__ = MagicMock(return_value=MagicMock())
        mock_checkout_cls.recovered = MagicMock()
        mock_checkout_cls.recovered.__eq__ = MagicMock(return_value=MagicMock())
        mock_cq = MagicMock()
        mock_cq.exists.return_value = False
        mock_cq.where.return_value = mock_cq
        mock_checkout_cls.select.return_value = mock_cq

        # CustomerProfile (stale)
        stale_profile = MagicMock()
        stale_profile.total_orders = 0
        stale_profile.lifecycle_stage = "prospect"
        stale_profile.last_order_at = None
        mock_cp.get_or_none.return_value = stale_profile
        mock_cp.contact_id = MagicMock()

        # Recovery flows
        mock_flow = MagicMock()
        mock_flow.id = 10
        mock_flow.name = "Checkout Recovery"
        mock_flow_cls.select.return_value.where.return_value = [mock_flow]

        # Active recovery enrollment
        active_enrollment = MagicMock()
        active_enrollment.id = 100
        active_enrollment.flow_id = 10
        active_enrollment.status = "active"
        mock_fe_q = MagicMock()
        mock_fe_q.__iter__ = MagicMock(return_value=iter([active_enrollment]))
        mock_fe_q.where.return_value = mock_fe_q
        mock_enrollment_cls.select.return_value = mock_fe_q
        mock_enrollment_cls.contact = MagicMock()
        mock_enrollment_cls.flow_id = MagicMock()
        mock_enrollment_cls.status = MagicMock()

        # No pending queue items
        mock_dq_q = MagicMock()
        mock_dq_q.__iter__ = MagicMock(return_value=iter([]))
        mock_dq_q.where.return_value = mock_dq_q
        mock_queue_cls.select.return_value = mock_dq_q
        mock_queue_cls.email = MagicMock()
        mock_queue_cls.status = MagicMock()
        mock_queue_cls.email_type = MagicMock()

        result = repair_post_purchase_contact("buyer@example.com", dry_run=True)

        assert result["dry_run"] is True
        fixes_str = " ".join(result["fixes"])
        assert "total_orders" in fixes_str or "cancel enrollment" in fixes_str

    @patch("delivery_engine.Contact")
    def test_returns_error_for_missing_contact(self, mock_contact_cls):
        """Repair returns error dict when contact not found."""
        from delivery_engine import repair_post_purchase_contact

        mock_contact_cls.get_or_none.return_value = None
        mock_contact_cls.email = MagicMock()

        result = repair_post_purchase_contact("nobody@example.com", dry_run=True)
        assert "error" in result
        assert result["error"] == "Contact not found"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Real-case regression: suchavirk@gmail.com scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestSuchaVirkRegression:
    """
    Reproduce the production bug: customer purchased (order #2975, $96.62,
    2026-03-28) but system left them as prospect with active recovery flows.
    Guard 2 should block the checkout recovery send 3 days later.
    """

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_recovery_blocked_even_if_only_checkout_completed_exists(
            self, mock_bounce, mock_contact, mock_order, mock_activity,
            mock_checkout, mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """
        Scenario: ShopifyOrder delayed, but checkout_completed activity recorded.
        The checkout recovery send 3 days later should be blocked.
        """
        from delivery_engine import _send_one

        # Enrollment was March 28 at 8pm
        enrolled_at = datetime(2026, 3, 28, 20, 0, 0)
        enrollment = _make_enrollment(enrolled_at=enrolled_at)
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        # No ShopifyOrder yet (delayed sync), but checkout_completed exists
        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=False, has_checkout_completed=True, has_recovered_checkout=True)

        item = _make_queue_item(email="suchavirk@gmail.com")
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)

        result = _send_one(item, MagicMock())

        assert result == 0
        assert item.status == "cancelled"
        assert item.error_msg == "purchased_after_enrollment"

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.AbandonedCheckout")
    @patch("delivery_engine.CustomerActivity")
    @patch("delivery_engine.ShopifyOrder")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_all_three_signals_agree_blocks_send(
            self, mock_bounce, mock_contact, mock_order, mock_activity,
            mock_checkout, mock_enrollment_cls, mock_flow_cls, mock_ledger):
        """
        When all three signals confirm purchase (order + activity + recovered
        checkout), the send is definitely blocked.
        """
        from delivery_engine import _send_one

        enrolled_at = datetime(2026, 3, 28, 20, 0, 0)
        enrollment = _make_enrollment(enrolled_at=enrolled_at)
        mock_enrollment_cls.get_or_none.return_value = enrollment

        flow = _make_flow("checkout_abandoned")
        mock_flow_cls.get_or_none.return_value = flow

        _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                            has_order=True, has_checkout_completed=True, has_recovered_checkout=True)

        item = _make_queue_item(email="suchavirk@gmail.com")
        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)

        result = _send_one(item, MagicMock())
        assert result == 0
        assert item.status == "cancelled"

    def test_signal_helper_with_suchavirk_timeline(self):
        """
        Reproduce exact timeline: enrolled 2026-03-28 20:00,
        checkout_completed 2026-03-29 00:15, recovered 2026-03-29 00:15,
        order 2026-03-28 20:14. All inside the correlation window.
        """
        from delivery_engine import _has_post_enrollment_purchase_signal

        enrolled_at = datetime(2026, 3, 28, 20, 0, 0)
        enrollment = MagicMock()
        enrollment.enrolled_at = enrolled_at

        # All signals mock-true (the DB query would find them in the window)
        with patch("delivery_engine.ShopifyOrder") as mock_order, \
             patch("delivery_engine.CustomerActivity") as mock_activity, \
             patch("delivery_engine.AbandonedCheckout") as mock_checkout:

            _setup_guard2_mocks(mock_order, mock_activity, mock_checkout,
                                has_order=True, has_checkout_completed=True,
                                has_recovered_checkout=True)

            has_signal, evidence = _has_post_enrollment_purchase_signal(
                "suchavirk@gmail.com", enrollment, "checkout_abandoned")

            assert has_signal is True
            # Should mention at least ShopifyOrder and checkout_completed
            assert "ShopifyOrder" in evidence
            assert "checkout_completed" in evidence


# ═══════════════════════════════════════════════════════════════
# Test Inbox Redirect
# ═══════════════════════════════════════════════════════════════


class TestTestRedirect:
    """Test inbox redirect: when enabled, all sends go to a test address."""

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_redirect_changes_recipient_and_subject(
            self, mock_bounce, mock_contact, mock_flow, mock_enrollment,
            mock_ledger, in_memory_db):
        """When redirect is enabled, send_fn receives the test inbox address."""
        from delivery_engine import _send_one
        from database import LearningConfig

        LearningConfig.set_val("test_redirect_enabled", "true")
        LearningConfig.set_val("test_redirect_to", "testbox@example.com")

        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        item = _make_queue_item(email="real-customer@shop.com",
                                email_type="auto", enrollment_id=0)
        item.subject = "Your gear picks"
        mock_send = MagicMock(return_value=(True, None, "msg-123"))

        _send_one(item, mock_send)

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["to_email"] == "testbox@example.com"
        assert "intended:real-customer@shop.com" in call_kwargs["subject"]
        assert "Your gear picks" in call_kwargs["subject"]

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_redirect_disabled_sends_to_real_recipient(
            self, mock_bounce, mock_contact, mock_flow, mock_enrollment,
            mock_ledger, in_memory_db):
        """When redirect is disabled, send_fn receives the real recipient."""
        from delivery_engine import _send_one
        from database import LearningConfig

        LearningConfig.set_val("test_redirect_enabled", "false")
        LearningConfig.set_val("test_redirect_to", "testbox@example.com")

        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        item = _make_queue_item(email="real-customer@shop.com",
                                email_type="auto", enrollment_id=0)
        item.subject = "Your gear picks"
        mock_send = MagicMock(return_value=(True, None, "msg-456"))

        _send_one(item, mock_send)

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["to_email"] == "real-customer@shop.com"
        assert call_kwargs["subject"] == "Your gear picks"

    @patch("delivery_engine.update_ledger_status")
    @patch("delivery_engine.FlowEnrollment")
    @patch("delivery_engine.Flow")
    @patch("delivery_engine.Contact")
    @patch("delivery_engine.BounceLog")
    def test_redirect_preserves_original_email_in_queue(
            self, mock_bounce, mock_contact, mock_flow, mock_enrollment,
            mock_ledger, in_memory_db):
        """Redirect does NOT modify item.email — original is preserved in DB."""
        from delivery_engine import _send_one
        from database import LearningConfig

        LearningConfig.set_val("test_redirect_enabled", "true")
        LearningConfig.set_val("test_redirect_to", "testbox@example.com")

        mock_contact.get_or_none.return_value = MagicMock(subscribed=True)
        _mock_bounce_none(mock_bounce)

        item = _make_queue_item(email="real-customer@shop.com",
                                email_type="auto", enrollment_id=0)
        mock_send = MagicMock(return_value=(True, None, "msg-789"))

        _send_one(item, mock_send)

        # item.email in DB is still the original
        assert item.email == "real-customer@shop.com"
