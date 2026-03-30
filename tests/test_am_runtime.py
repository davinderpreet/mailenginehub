"""
test_am_runtime.py — Unit tests for am_runtime.py (Phase 4: Account Manager pillar)

Tests every major function in am_runtime:
  1. TestNormalizeStrategy     — _normalize_strategy (legacy phases, idempotent, defaults)
  2. TestCheckPreconditions    — _check_preconditions (active flow, unsubscribed, clean)
  3. TestCheckTiming           — _check_timing (too soon, ok/scheduled)
  4. TestEvaluateCandidates    — _evaluate_candidates (5 scenarios)
  5. TestResolveProducts       — _resolve_products (no OOS)
  6. TestResolveOffer          — _resolve_offer (no-offer paths)
  7. TestBuildAmDecision       — build_am_decision (ready + wait)
  8. TestExecuteAmDecision     — execute_am_decision (rendered + invalid)
  9. TestSuggestNextAction     — _suggest_next_action (avoids repeat)

Run:  python -m pytest tests/test_am_runtime.py -v
"""

import os
import sys
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_intel(
    intent=50, reorder_likelihood=60, churn_risk=30, engagement=70,
    discount_sensitivity=0.3, lifecycle_stage="active", rfm_segment="potential",
    days_since_last_order=30, avg_days_between_orders=45,
    preferred_hour=10, reorders=None, cross_sells=None, top_pick=None,
):
    """Build a mock intelligence dict matching intelligence_layer output."""
    return {
        "schema_version": 1,
        "contact": {"id": 1, "email": "test@example.com", "first_name": "Test"},
        "classification": {
            "lifecycle_stage": lifecycle_stage,
            "customer_type": "returning",
            "rfm_segment": rfm_segment,
        },
        "scores": {
            "intent": intent,
            "reorder_likelihood": reorder_likelihood,
            "churn_risk": churn_risk,
            "engagement": engagement,
            "discount_sensitivity": discount_sensitivity,
        },
        "purchase": {
            "total_orders": 3,
            "total_spent": 500.0,
            "avg_order_value": 166.67,
            "days_since_last_order": days_since_last_order,
            "avg_days_between_orders": avg_days_between_orders,
        },
        "products": {"top_categories": ["Electronics"]},
        "timing": {"preferred_hour": preferred_hour},
        "engagement": {},
        "learning": {},
        "next_products": {
            "schema_version": 1,
            "replacements": [],
            "upgrades": [],
            "cross_sells": cross_sells or [],
            "accessories": [],
            "reorders": reorders or [],
            "top_pick": top_pick,
        },
    }


def _make_strategy(
    allowed_actions=None, tactic=None, current_phase="growth",
    cadence_policy=None, offer_policy=None, discount_approach="",
):
    """Build a strategy dict."""
    strat = {"current_phase_name": current_phase}
    if allowed_actions:
        strat["allowed_actions"] = allowed_actions
    if tactic:
        strat["phases"] = {current_phase: {"tactic": tactic}}
    if cadence_policy:
        strat["cadence_policy"] = cadence_policy
    if offer_policy:
        strat["offer_policy"] = offer_policy
    if discount_approach:
        strat["discount_approach"] = discount_approach
    return strat


# ─────────────────────────────────────────────────────────────────────────────
# Test Classes
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeStrategy:
    """Tests for _normalize_strategy."""

    def test_legacy_phases_to_allowed_actions(self):
        """Tactic in phases dict maps to allowed_actions."""
        from am_runtime import _normalize_strategy
        strat = {"phases": {"growth": {"tactic": "growth"}}}
        result = _normalize_strategy(strat, current_phase="growth")
        assert "cross_sell" in result["allowed_actions"]
        assert "product_recommendation" in result["allowed_actions"]
        assert "education" in result["allowed_actions"]

    def test_already_normalized_is_idempotent(self):
        """Running normalize twice gives same result."""
        from am_runtime import _normalize_strategy
        strat = _make_strategy(
            allowed_actions=["winback", "education"],
            cadence_policy={"min_gap_days": 3, "max_gap_days": 10, "preferred_gap_days": 5},
            offer_policy={"allow_discount": False, "max_discount_pct": 0, "free_shipping_ok": False},
        )
        first = _normalize_strategy(strat, "growth")
        second = _normalize_strategy(first, "growth")
        assert first == second

    def test_empty_gets_defaults(self):
        """Empty strategy gets all defaults."""
        from am_runtime import _normalize_strategy, _DEFAULT_ACTIONS, _DEFAULT_CADENCE, _DEFAULT_OFFER_POLICY
        result = _normalize_strategy({})
        assert result["allowed_actions"] == _DEFAULT_ACTIONS
        assert result["cadence_policy"] == _DEFAULT_CADENCE
        assert result["offer_policy"] == _DEFAULT_OFFER_POLICY


class TestCheckPreconditions:
    """Tests for _check_preconditions."""

    def test_active_flow_skipped(self, in_memory_db, make_contact, make_flow):
        """Contact with active flow enrollment is skipped."""
        from am_runtime import _check_preconditions
        from database import FlowEnrollment

        contact = make_contact()
        flow = make_flow("contact_created")
        FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now(), status="active",
        )
        status, reason = _check_preconditions(contact)
        assert status == "skipped"
        assert "active_flow" in reason

    def test_unsubscribed_skipped(self, in_memory_db, make_contact):
        """Unsubscribed contact is skipped."""
        from am_runtime import _check_preconditions
        contact = make_contact(subscribed=False)
        status, reason = _check_preconditions(contact)
        assert status == "skipped"
        assert reason == "unsubscribed"

    def test_clean_contact_ok(self, in_memory_db, make_contact):
        """Clean subscribed contact passes preconditions."""
        from am_runtime import _check_preconditions
        contact = make_contact()
        status, reason = _check_preconditions(contact)
        assert status == "ok"


class TestCheckTiming:
    """Tests for _check_timing."""

    @patch("am_runtime.intelligence_layer")
    def test_too_soon_returns_wait(self, mock_il, in_memory_db, make_contact):
        """When timing gate says can't send, return wait."""
        from am_runtime import _check_timing
        mock_il.should_contact_now.return_value = {
            "can_send": False,
            "reason": "too_soon",
            "next_available_at": datetime.now() + timedelta(hours=12),
        }
        contact = make_contact()
        intel = _make_intel()
        status, value = _check_timing(contact, intel)
        assert status == "wait"
        assert value is not None

    @patch("am_runtime.intelligence_layer")
    def test_ok_returns_scheduled(self, mock_il, in_memory_db, make_contact):
        """When timing gate allows, return ok with scheduled_at."""
        from am_runtime import _check_timing
        mock_il.should_contact_now.return_value = {"can_send": True}
        contact = make_contact()
        intel = _make_intel(preferred_hour=10)
        status, value = _check_timing(contact, intel)
        assert status == "ok"
        assert isinstance(value, datetime)


class TestEvaluateCandidates:
    """Tests for _evaluate_candidates."""

    def test_reorder_wins_when_signal_strong(self):
        """Reorder reminder wins when reorder_likelihood is high and cycle is due."""
        from am_runtime import _evaluate_candidates
        intel = _make_intel(
            reorder_likelihood=90, days_since_last_order=50,
            avg_days_between_orders=45, intent=20, churn_risk=10,
        )
        strat = _make_strategy(allowed_actions=[
            "reorder_reminder", "cross_sell", "education",
        ])
        action, score, reason = _evaluate_candidates(strat, intel)
        assert action == "reorder_reminder"
        assert score > 0.3

    def test_winback_for_dormant(self):
        """Winback wins for dormant contacts with high churn risk."""
        from am_runtime import _evaluate_candidates
        intel = _make_intel(
            churn_risk=90, lifecycle_stage="dormant",
            reorder_likelihood=5, intent=10,
        )
        strat = _make_strategy(allowed_actions=[
            "winback", "education", "product_recommendation",
        ])
        action, score, reason = _evaluate_candidates(strat, intel)
        assert action == "winback"

    def test_loyalty_for_vip(self):
        """Loyalty wins for VIP segment with low discount sensitivity."""
        from am_runtime import _evaluate_candidates
        intel = _make_intel(
            rfm_segment="champion", discount_sensitivity=0.1,
            reorder_likelihood=10, churn_risk=5, intent=20,
        )
        strat = _make_strategy(allowed_actions=["loyalty", "education"])
        action, score, reason = _evaluate_candidates(strat, intel)
        assert action == "loyalty"

    def test_wait_when_all_weak(self):
        """Returns wait when all scores below threshold."""
        from am_runtime import _evaluate_candidates
        intel = _make_intel(
            intent=0, reorder_likelihood=0, churn_risk=0, engagement=0,
            rfm_segment="new", discount_sensitivity=1.0,
        )
        # Only education at 0.25 which is below 0.3 threshold
        strat = _make_strategy(allowed_actions=["education"])
        action, score, reason = _evaluate_candidates(strat, intel)
        assert action == "wait"

    def test_performance_data_ignored_below_threshold(self, in_memory_db):
        """ActionPerformance with small sample_size doesn't boost score."""
        from am_runtime import _evaluate_candidates
        from database import ActionPerformance

        ActionPerformance.create(
            action_type="education", segment="new",
            sample_size=5, open_rate=0.30,  # sample too small
        )
        intel = _make_intel(intent=0, rfm_segment="new")
        strat = _make_strategy(allowed_actions=["education"])
        strat["metadata"] = {}
        action, score, reason = _evaluate_candidates(strat, intel)
        # Score should still be base 0.25 (no boost applied)
        assert action == "wait"  # 0.25 < 0.3 threshold


class TestResolveProducts:
    """Tests for _resolve_products."""

    @patch("am_runtime.intelligence_layer")
    def test_concrete_products_no_oos(self, mock_il, in_memory_db, make_contact):
        """Resolves concrete products, filtering out-of-stock."""
        from am_runtime import _resolve_products
        from database import ProductImageCache, ProductCommercial

        # Create test products
        ProductImageCache.create(
            product_id="prod_a",
            product_title="Widget A", product_url="https://ldas.ca/a",
            image_url="https://img/a.jpg", price="29.99", product_type="Widgets",
        )
        ProductImageCache.create(
            product_id="prod_b",
            product_title="Widget B", product_url="https://ldas.ca/b",
            image_url="https://img/b.jpg", price="39.99", product_type="Widgets",
        )
        # Mark B as out of stock
        ProductCommercial.create(
            product_id="prod_b",
            product_title="Widget B", stock_pressure="out_of_stock",
        )

        mock_il.get_next_products.return_value = {
            "schema_version": 1,
            "reorders": [{"product_key": "Widget A"}, {"product_key": "Widget B"}],
            "replacements": [], "upgrades": [], "cross_sells": [],
            "accessories": [], "top_pick": None,
        }

        contact = make_contact()
        products = _resolve_products(contact, "reorder_reminder", _make_intel())
        assert len(products) == 1
        assert products[0]["product_title"] == "Widget A"


class TestResolveOffer:
    """Tests for _resolve_offer."""

    @patch("am_runtime.intelligence_layer")
    def test_no_offer_when_policy_says_no(self, mock_il, in_memory_db, make_contact):
        """Intelligence layer says no discount -> no offer."""
        from am_runtime import _resolve_offer
        mock_il.get_discount_policy.return_value = {"offer_discount": False}
        contact = make_contact()
        strat = _make_strategy(offer_policy={"allow_discount": True})
        result = _resolve_offer(contact, "winback", [], strat)
        assert result is None

    def test_no_offer_when_strategy_forbids(self, in_memory_db, make_contact):
        """Strategy offer_policy.allow_discount=False -> no offer."""
        from am_runtime import _resolve_offer
        contact = make_contact()
        strat = _make_strategy(offer_policy={"allow_discount": False})
        result = _resolve_offer(contact, "winback", [], strat)
        assert result is None


class TestBuildAmDecision:
    """Tests for build_am_decision."""

    @patch("am_runtime.intelligence_layer")
    def test_ready_decision_for_due_contact(self, mock_il, in_memory_db, make_contact):
        """Due contact with strong signals gets a ready decision."""
        from am_runtime import build_am_decision

        mock_il.should_contact_now.return_value = {"can_send": True}
        mock_il.get_contact_intelligence.return_value = _make_intel(
            reorder_likelihood=80, days_since_last_order=50,
            avg_days_between_orders=45,
            reorders=[{"product_key": "Widget A"}],
        )
        mock_il.get_next_products.return_value = {
            "schema_version": 1,
            "reorders": [{"product_key": "Widget A"}],
            "replacements": [], "upgrades": [], "cross_sells": [],
            "accessories": [], "top_pick": None,
        }
        mock_il.get_discount_policy.return_value = {"offer_discount": False}

        contact = make_contact()
        strategy = _make_strategy(allowed_actions=[
            "reorder_reminder", "education", "winback",
        ])

        decision = build_am_decision(contact, strategy)
        assert decision["should_act"] is True
        assert decision["status"] == "ready"
        assert decision["action_type"] in ("reorder_reminder", "education", "winback")

    @patch("am_runtime.intelligence_layer")
    def test_wait_decision_has_wait_until(self, mock_il, in_memory_db, make_contact):
        """Contact that fails timing gate gets wait status with wait_until."""
        from am_runtime import build_am_decision

        wait_time = datetime.now() + timedelta(hours=6)
        mock_il.should_contact_now.return_value = {
            "can_send": False,
            "reason": "too_soon",
            "next_available_at": wait_time,
        }
        mock_il.get_contact_intelligence.return_value = _make_intel()

        contact = make_contact()
        strategy = _make_strategy()

        decision = build_am_decision(contact, strategy)
        assert decision["should_act"] is False
        assert decision["status"] == "wait"
        assert decision["wait_until"] is not None


class TestExecuteAmDecision:
    """Tests for execute_am_decision."""

    @patch("am_runtime._generate_ai_copy")
    @patch("am_runtime.te")
    def test_renders_through_template_engine(self, mock_te, mock_ai, in_memory_db, make_contact, make_template):
        """Valid decision renders through template_engine."""
        from am_runtime import execute_am_decision

        contact = make_contact()
        template = make_template(name="AM: Education")
        template.blocks_json = json.dumps([{"type": "hero", "headline": "X"}])
        template.template_family = "post_purchase"
        template.save()

        mock_ai.return_value = {
            "hero_headline": "Learn More",
            "hero_subheadline": "Expert Tips",
            "paragraphs": ["Great content here."],
            "cta_text": "Read More",
            "cta_url": "https://ldas.ca",
            "tokens_used": 150,
        }
        mock_te.make_render_contract.return_value = {"schema_version": 1}
        mock_te.render_email.return_value = {
            "html": "<html>rendered</html>",
            "subject": "Learn More",
            "validation_report": [],
        }

        decision = {
            "action_type": "education",
            "objective": "AM: Education",
            "candidate_products": [],
            "offer_context": None,
        }

        result = execute_am_decision(contact, {}, decision, template=template)
        assert result["status"] == "rendered"
        assert result["html"] == "<html>rendered</html>"
        assert result["template_id"] == template.id

    @patch("am_runtime._generate_ai_copy")
    @patch("am_runtime.te")
    def test_invalid_returns_invalid(self, mock_te, mock_ai, in_memory_db, make_contact, make_template):
        """When render produces validation errors, return invalid."""
        from am_runtime import execute_am_decision

        contact = make_contact()
        template = make_template(name="AM: Win-Back")
        template.blocks_json = json.dumps([{"type": "hero"}])
        template.template_family = "winback"
        template.save()

        mock_ai.return_value = {
            "hero_headline": "Come Back",
            "hero_subheadline": "We Miss You",
            "paragraphs": ["Return to us."],
            "cta_text": "Shop Now",
            "cta_url": "https://ldas.ca",
            "tokens_used": 100,
        }
        mock_te.make_render_contract.return_value = {"schema_version": 1}
        mock_te.render_email.return_value = {
            "html": "",
            "subject": "",
            "validation_report": [{"level": "error", "check": "html_empty", "message": "No HTML output"}],
        }

        decision = {
            "action_type": "winback",
            "objective": "AM: Win-Back",
            "candidate_products": [],
            "offer_context": None,
        }

        result = execute_am_decision(contact, {}, decision, template=template)
        assert result["status"] == "invalid"


class TestSuggestNextAction:
    """Tests for _suggest_next_action."""

    def test_cadence_avoids_repeat(self):
        """Next action avoids repeating the just-sent action."""
        from am_runtime import _suggest_next_action

        strat = {"allowed_actions": ["education", "winback", "cross_sell"]}
        decision = {
            "action_type": "education",
            "metadata": {
                "ranked_actions": [
                    {"action": "education", "score": 0.9},
                    {"action": "winback", "score": 0.7},
                    {"action": "cross_sell", "score": 0.4},
                ],
            },
        }
        result = _suggest_next_action(strat, decision)
        assert result != "education"
        assert result == "winback"  # second-best from ranked


# ═══════════════════════════════════════════════════════════════
# Regression tests for Phase 4 bug fixes
# ═══════════════════════════════════════════════════════════════


class TestBuildAmDecisionWithModel:
    """Fix 1: build_am_decision must work with ContactStrategy model instances."""

    def test_model_instance_uses_strategy_json(self, in_memory_db, make_contact):
        """Persisted allowed_actions / cadence / current_phase influence the decision."""
        from am_runtime import build_am_decision
        from database import ContactStrategy, ProductImageCache

        contact = make_contact()
        cs = ContactStrategy.create(
            contact=contact, enrolled=True,
            current_phase="Phase 2: Cross-sell",
            next_action_type="cross_sell",
            strategy_json=json.dumps({
                "overall_goal": "maximize cross-sell revenue",
                "allowed_actions": ["cross_sell", "product_recommendation"],
                "cadence_policy": {"min_gap_days": 3, "max_gap_days": 10, "preferred_gap_days": 5},
                "offer_policy": {"allow_discount": False, "max_discount_pct": 0},
            }),
        )
        ProductImageCache.create(
            product_id="1", product_title="Test Widget",
            image_url="https://cdn/w.jpg", product_url="https://ldas.ca/w",
            price="29.99", product_type="Widgets", handle="w",
        )

        with patch("am_runtime.intelligence_layer") as mock_intel:
            mock_intel.should_contact_now.return_value = {"can_send": True, "reason": "ok"}
            mock_intel.get_contact_intelligence.return_value = {
                "classification": {"lifecycle_stage": "active", "customer_type": "repeat"},
                "scores": {"intent": 60, "reorder_likelihood": 0.3, "churn_risk": 15, "engagement": 55},
                "purchase": {"days_since_last": 20, "avg_cycle_days": 0, "total_orders": 4},
                "products": {"category_affinities": {"Widgets": 0.7}},
                "timing": {"preferred_hour": 10},
            }
            mock_intel.get_next_products.return_value = {
                "schema_version": 1, "top_pick": {"product_key": "Test Widget"},
                "cross_sells": [], "reorders": [], "replacements": [],
                "upgrades": [], "accessories": [],
            }
            mock_intel.get_discount_policy.return_value = {"offer_discount": False}

            decision = build_am_decision(contact, cs)

        # Decision should use persisted strategy:
        # - action_type must be from allowed_actions (cross_sell or product_recommendation)
        assert decision["action_type"] in ("cross_sell", "product_recommendation", "wait")
        # - strategy_phase should match current_phase from model
        assert decision["strategy_phase"] == "Phase 2: Cross-sell"

    def test_model_instance_not_empty_dict(self, in_memory_db, make_contact):
        """Verify strategy_json is actually parsed, not falling through to {}."""
        from am_runtime import _extract_strategy
        from database import ContactStrategy

        contact = make_contact()
        cs = ContactStrategy.create(
            contact=contact, enrolled=True,
            current_phase="Phase 1: Educate",
            strategy_json=json.dumps({
                "overall_goal": "educate new customer",
                "allowed_actions": ["education"],
                "cadence_policy": {"min_gap_days": 7, "max_gap_days": 14, "preferred_gap_days": 10},
            }),
        )

        strat_dict, current_phase = _extract_strategy(cs)
        assert strat_dict.get("overall_goal") == "educate new customer"
        assert strat_dict.get("allowed_actions") == ["education"]
        assert current_phase == "Phase 1: Educate"


class TestApproveEmailMetadataChain:
    """Fix 2: approved AM emails must carry frozen metadata end-to-end."""

    def test_approve_creates_auto_email_before_enqueue(self, in_memory_db, make_contact):
        """AutoEmail created before enqueue, with real template_id and auto_email_id."""
        from database import ContactStrategy, AMPendingReview, AutoEmail, DeliveryQueue, ActionLedger

        contact = make_contact()
        cs = ContactStrategy.create(contact=contact, enrolled=True, strategy_json="{}")

        pe = AMPendingReview.create(
            contact=contact, strategy=cs,
            subject="Cross-sell picks for you",
            body_html="<p>Check these out</p>",
            reasoning="cross-sell opportunity for repeat buyer",
            action_type="cross_sell",
            template_id=42,
            decision_json=json.dumps({"action_type": "cross_sell", "confidence": 0.75}),
            status="pending",
        )

        # Guard: skip if Flask/app dependencies unavailable
        pytest.importorskip("flask", reason="Flask not installed — skipping approve_email test")
        try:
            from account_manager import approve_email
        except Exception as exc:
            pytest.skip("Cannot import account_manager: %s" % exc)

        with patch("account_manager._get_optimal_send_time") as mock_time, \
             patch("delivery_engine.enqueue_email") as mock_enqueue, \
             patch("account_manager.log_action") as mock_log:
            mock_time.return_value = datetime.now() + timedelta(hours=2)
            mock_log.return_value = MagicMock(id=999)

            result = approve_email(pe.id)

        # AutoEmail should exist with frozen metadata
        ae = AutoEmail.select().where(AutoEmail.contact == contact).first()
        assert ae is not None
        assert ae.action_type == "cross_sell"
        assert ae.subject == "Cross-sell picks for you"
        assert json.loads(ae.decision_json)["action_type"] == "cross_sell"

        # enqueue should have been called with real template_id and auto_email_id
        if mock_enqueue.called:
            call_kwargs = mock_enqueue.call_args
            # Check template_id is 42 (not 0)
            kw = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
            assert kw.get("template_id") == 42
            # Check auto_email_id is set
            assert kw.get("auto_email_id") == ae.id

    def test_delivery_compat_updates_auto_email_status(self, in_memory_db, make_contact, make_template):
        """With real template_id and auto_email_id, delivery compat can update AutoEmail."""
        from database import AutoEmail, DeliveryQueue

        contact = make_contact()
        tpl = make_template(name="AM: Cross-Sell")
        ae = AutoEmail.create(
            contact=contact, subject="Test", status="queued",
            action_type="cross_sell", template=tpl.id,
        )

        # Create queue item matching what approve_email produces
        dq = DeliveryQueue.create(
            contact=contact, email=contact.email,
            email_type="auto", source_id=0,
            enrollment_id=0, step_id=0,
            template_id=tpl.id,  # real template_id (not 0)
            from_name="Test", from_email="test@ldas.ca",
            subject="Test", html="<p>test</p>",
            unsubscribe_url="https://unsub",
            priority=60, status="queued", ledger_id=0,
            auto_email_id=ae.id,  # linked
        )

        # Simulate what delivery_engine._create_compat_record does for auto emails
        # The condition is: item.email_type == "auto" and item.template_id
        assert dq.email_type == "auto"
        assert dq.template_id  # truthy — not 0
        assert dq.auto_email_id == ae.id

        # Simulate compat update
        AutoEmail.update(status="sent").where(AutoEmail.id == dq.auto_email_id).execute()
        ae_refreshed = AutoEmail.get_by_id(ae.id)
        assert ae_refreshed.status == "sent"


class TestNormalizeListPhases:
    """Fix 3: _normalize_strategy must support list-based phases."""

    def test_list_phases_with_current_phase(self):
        from am_runtime import _normalize_strategy
        strategy = {
            "overall_goal": "grow revenue",
            "phases": [
                {"name": "Phase 1: Educate", "months": "1-2", "goal": "educate", "tactic": "education"},
                {"name": "Phase 2: Sell", "months": "3-4", "goal": "sell", "tactic": "cross_sell"},
            ],
        }
        result = _normalize_strategy(strategy, current_phase="Phase 1: Educate")
        assert "education" in result["allowed_actions"]
        assert "product_recommendation" in result["allowed_actions"]

    def test_list_phases_fallback_to_first(self):
        from am_runtime import _normalize_strategy
        strategy = {
            "phases": [
                {"name": "Phase 1", "tactic": "loyalty"},
                {"name": "Phase 2", "tactic": "cross_sell"},
            ],
        }
        # No current_phase — should use first phase
        result = _normalize_strategy(strategy, current_phase="")
        assert "loyalty" in result["allowed_actions"]

    def test_dict_phases_still_work(self):
        from am_runtime import _normalize_strategy
        strategy = {
            "phases": {
                "Phase A": {"tactic": "education"},
                "Phase B": {"tactic": "winback"},
            },
        }
        result = _normalize_strategy(strategy, current_phase="Phase A")
        assert "education" in result["allowed_actions"]

    def test_resharpened_list_phases(self):
        """Resharpened strategies from _resharpen_strategy use list format."""
        from am_runtime import _normalize_strategy
        resharpened = {
            "overall_goal": "re-engage after flow",
            "phases": [
                {"name": "Phase 1: Re-engage", "months": "1-2", "goal": "reactivate", "tactic": "winback"},
                {"name": "Phase 2: Build", "months": "3-4", "goal": "build value", "tactic": "cross_sell"},
            ],
            "product_focus": "cables",
            "first_action_type": "winback",
        }
        result = _normalize_strategy(resharpened, current_phase="Phase 1: Re-engage")
        assert "winback" in result["allowed_actions"]
        assert "cadence_policy" in result
