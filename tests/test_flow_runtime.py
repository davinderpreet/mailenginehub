"""
test_flow_runtime.py — Unit tests for flow_runtime.py (Phase 3: Flows pillar)

Tests every major function in flow_runtime:
  1. TestResolveObjective      — _resolve_objective (trigger maps + family overrides)
  2. TestSoftTimingGate        — _check_soft_timing_gate (urgency levels)
  3. TestResolveProducts       — _resolve_products (priority chain)
  4. TestResolveOffer          — _resolve_offer (discount policy gating)
  5. TestLegacyTokenContext    — _build_legacy_token_context (all 17 tokens)
  6. TestBuildFlowSendPackage  — build_flow_send_package (end-to-end pipeline)

Run:  python -m pytest tests/test_flow_runtime.py -v
"""

import os
import sys
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, call

# Add project root to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_flow(trigger_type="checkout_abandoned"):
    flow = MagicMock()
    flow.trigger_type = trigger_type
    return flow


def _make_mock_template(family="", template_format="html"):
    tmpl = MagicMock()
    tmpl.template_family = family
    tmpl.template_format = template_format
    tmpl.subject = "Hello {{first_name}}"
    tmpl.preview_text = ""
    tmpl.html_body = "<p>Hello {{first_name}}</p>"
    tmpl.shell_version = 1
    tmpl.blocks_json = "[]"
    return tmpl


def _make_mock_step(subject_override="", template=None):
    step = MagicMock()
    step.subject_override = subject_override
    step.template = template or _make_mock_template()
    return step


def _make_mock_enrollment():
    return MagicMock()


# ─────────────────────────────────────────────────────────────────────────────
# 1. TestResolveObjective
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveObjective:
    """_resolve_objective maps triggers and template families correctly."""

    def test_checkout_abandoned_maps_correctly(self):
        """checkout_abandoned → checkout_recovery objective, urgent, cart_abandonment purpose."""
        from flow_runtime import _resolve_objective
        flow = _make_mock_flow("checkout_abandoned")
        step = _make_mock_step()
        tmpl = _make_mock_template(family="")
        objective, urgency, discount_purpose = _resolve_objective(flow, step, tmpl)
        assert objective == "checkout_recovery"
        assert urgency == "urgent"
        assert discount_purpose == "cart_abandonment"

    def test_cart_abandonment_alias_matches_checkout(self):
        """cart_abandonment is aliased to checkout_recovery like checkout_abandoned."""
        from flow_runtime import _resolve_objective
        flow = _make_mock_flow("cart_abandonment")
        step = _make_mock_step()
        tmpl = _make_mock_template(family="")
        objective, urgency, discount_purpose = _resolve_objective(flow, step, tmpl)
        assert objective == "checkout_recovery"
        assert urgency == "urgent"
        assert discount_purpose == "cart_abandonment"

    def test_template_family_overrides_trigger(self):
        """template_family='winback' overrides checkout_abandoned trigger's objective."""
        from flow_runtime import _resolve_objective
        flow = _make_mock_flow("checkout_abandoned")
        step = _make_mock_step()
        tmpl = _make_mock_template(family="winback")
        objective, urgency, discount_purpose = _resolve_objective(flow, step, tmpl)
        # Family overrides objective and discount_purpose but NOT urgency
        assert objective == "winback"
        assert discount_purpose == "winback"
        # Urgency still comes from trigger (checkout_abandoned → urgent)
        assert urgency == "urgent"

    def test_no_purchase_days_is_lower_urgency(self):
        """no_purchase_days trigger has lower urgency."""
        from flow_runtime import _resolve_objective
        flow = _make_mock_flow("no_purchase_days")
        step = _make_mock_step()
        tmpl = _make_mock_template(family="")
        objective, urgency, discount_purpose = _resolve_objective(flow, step, tmpl)
        assert urgency == "lower"
        assert objective == "winback"
        assert discount_purpose == "winback"


# ─────────────────────────────────────────────────────────────────────────────
# 2. TestSoftTimingGate
# ─────────────────────────────────────────────────────────────────────────────

class TestSoftTimingGate:
    """_check_soft_timing_gate respects urgency levels correctly."""

    def test_urgent_bypasses_should_contact_now(self):
        """urgent urgency skips the gate entirely — should_contact_now not called."""
        from flow_runtime import _check_soft_timing_gate
        contact = MagicMock()
        contact.id = 1
        with patch("flow_runtime.intelligence_layer") as mock_il:
            status, next_at = _check_soft_timing_gate(contact, "urgent")
        assert status == "ok"
        assert next_at is None
        mock_il.should_contact_now.assert_not_called()

    def test_medium_ignores_too_soon(self):
        """medium urgency proceeds even when reason is 'too_soon'."""
        from flow_runtime import _check_soft_timing_gate
        contact = MagicMock()
        contact.id = 1
        with patch("flow_runtime.intelligence_layer") as mock_il:
            mock_il.should_contact_now.return_value = {
                "can_send": False,
                "reason": "too_soon (sent 6h ago, need 48h gap)",
                "next_available_at": datetime.now() + timedelta(hours=42),
            }
            status, next_at = _check_soft_timing_gate(contact, "medium")
        assert status == "ok"
        assert next_at is None

    def test_medium_respects_weekly_cap(self):
        """medium urgency defers when reason starts with 'weekly_cap'."""
        from flow_runtime import _check_soft_timing_gate
        contact = MagicMock()
        contact.id = 1
        future = datetime.now() + timedelta(days=2)
        with patch("flow_runtime.intelligence_layer") as mock_il:
            mock_il.should_contact_now.return_value = {
                "can_send": False,
                "reason": "weekly_cap (4 emails in last 7 days)",
                "next_available_at": future,
            }
            status, next_at = _check_soft_timing_gate(contact, "medium")
        assert status == "deferred"
        assert next_at == future

    def test_lower_defers_on_too_soon(self):
        """lower urgency fully defers when too_soon."""
        from flow_runtime import _check_soft_timing_gate
        contact = MagicMock()
        contact.id = 1
        future = datetime.now() + timedelta(hours=40)
        with patch("flow_runtime.intelligence_layer") as mock_il:
            mock_il.should_contact_now.return_value = {
                "can_send": False,
                "reason": "too_soon (sent 8h ago, need 48h gap)",
                "next_available_at": future,
            }
            status, next_at = _check_soft_timing_gate(contact, "lower")
        assert status == "deferred"
        assert next_at == future

    def test_lower_ok_when_can_send(self):
        """lower urgency returns ok when can_send is True."""
        from flow_runtime import _check_soft_timing_gate
        contact = MagicMock()
        contact.id = 1
        with patch("flow_runtime.intelligence_layer") as mock_il:
            mock_il.should_contact_now.return_value = {
                "can_send": True,
                "reason": "ok",
                "next_available_at": None,
            }
            status, next_at = _check_soft_timing_gate(contact, "lower")
        assert status == "ok"
        assert next_at is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. TestResolveProducts
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveProducts:
    """_resolve_products uses the correct priority chain."""

    def test_checkout_uses_trigger_context(self, make_contact, in_memory_db):
        """checkout_abandoned: trigger_context cart_items takes priority."""
        from flow_runtime import _resolve_products
        contact = make_contact()
        flow = _make_mock_flow("checkout_abandoned")
        enrollment = _make_mock_enrollment()
        trigger_context = {
            "cart_items": [
                {"title": "Bluetooth Headset Pro", "quantity": 1, "price": "89.99"},
            ]
        }
        with patch("flow_runtime.intelligence_layer"):
            products = _resolve_products(contact, flow, enrollment, trigger_context)
        assert len(products) >= 1
        assert products[0]["product_title"] == "Bluetooth Headset Pro"

    def test_browse_uses_trigger_context(self, make_contact, in_memory_db):
        """browse_abandonment: trigger_context viewed_products takes priority."""
        from flow_runtime import _resolve_products
        contact = make_contact()
        flow = _make_mock_flow("browse_abandonment")
        enrollment = _make_mock_enrollment()
        trigger_context = {
            "viewed_products": [
                {"title": "Wireless Charger", "price": "29.99"},
                {"title": "USB-C Hub", "price": "49.99"},
            ]
        }
        with patch("flow_runtime.intelligence_layer"):
            products = _resolve_products(contact, flow, enrollment, trigger_context)
        assert len(products) >= 2
        assert products[0]["product_title"] == "Wireless Charger"

    def test_welcome_uses_intelligence(self, make_contact, in_memory_db):
        """contact_created (welcome): falls through to intelligence_layer."""
        from flow_runtime import _resolve_products
        contact = make_contact()
        flow = _make_mock_flow("contact_created")
        enrollment = _make_mock_enrollment()

        intel_products = [
            {"product_title": "Smart Speaker", "product_url": "https://ldas.ca/p/1",
             "image_url": "", "price": "99.99"}
        ]

        with patch("flow_runtime.intelligence_layer") as mock_il:
            mock_il.get_next_products.return_value = {
                "schema_version": 1,
                "top_pick": intel_products[0],
                "cross_sells": [],
                "reorders": [],
                "upgrades": [],
                "replacements": [],
                "accessories": [],
            }
            products = _resolve_products(contact, flow, enrollment, None)

        assert len(products) >= 1
        assert products[0]["product_title"] == "Smart Speaker"

    def test_checkout_no_context_queries_abandoned_checkout(self, make_contact, in_memory_db):
        """checkout_abandoned with no trigger_context queries AbandonedCheckout DB."""
        from flow_runtime import _resolve_products
        from database import AbandonedCheckout
        contact = make_contact()

        # Create a DB checkout record
        AbandonedCheckout.create(
            shopify_checkout_id="test-checkout-001",
            email=contact.email,
            contact=contact.id,
            checkout_url="https://ldas.ca/checkout/abc",
            line_items_json=json.dumps([
                {"title": "Gaming Headset", "quantity": 1, "price": "129.99"}
            ]),
            recovered=False,
        )

        flow = _make_mock_flow("checkout_abandoned")
        enrollment = _make_mock_enrollment()

        with patch("flow_runtime.intelligence_layer"):
            products = _resolve_products(contact, flow, enrollment, None)

        assert len(products) >= 1
        assert products[0]["product_title"] == "Gaming Headset"


# ─────────────────────────────────────────────────────────────────────────────
# 4. TestResolveOffer
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveOffer:
    """_resolve_offer respects discount policy decisions."""

    def test_no_offer_when_policy_says_no(self, make_contact, in_memory_db):
        """offer_discount=False from policy → returns None."""
        from flow_runtime import _resolve_offer
        contact = make_contact()
        with patch("flow_runtime.intelligence_layer") as mock_il, \
             patch("flow_runtime.discount_engine") as mock_de:
            mock_il.get_discount_policy.return_value = {
                "offer_discount": False,
                "reason": "vip_no_discount",
            }
            result = _resolve_offer(contact, "cart_abandonment", [])
        assert result is None
        mock_de.get_or_create_discount.assert_not_called()

    def test_offer_when_policy_approves(self, make_contact, in_memory_db):
        """offer_discount=True → calls discount_engine and returns display dict."""
        from flow_runtime import _resolve_offer
        contact = make_contact()
        discount_info = {
            "code": "CART5XYZABC",
            "value": "5",
            "discount_type": "percentage",
            "expires_at": datetime.now() + timedelta(hours=48),
        }
        display_info = {
            "code": "CART5XYZABC",
            "display_text": "5% off your entire order",
            "value_display": "5% OFF",
            "expires_text": "Expires in 2 days",
        }
        with patch("flow_runtime.intelligence_layer") as mock_il, \
             patch("flow_runtime.discount_engine") as mock_de:
            mock_il.get_discount_policy.return_value = {
                "offer_discount": True,
                "reason": "high_value_target",
            }
            mock_de.get_or_create_discount.return_value = discount_info
            mock_de.get_discount_display.return_value = display_info
            result = _resolve_offer(contact, "cart_abandonment", [])
        assert result is not None
        assert result["code"] == "CART5XYZABC"
        assert "5%" in result["display_text"]

    def test_no_offer_when_purpose_is_none(self, make_contact, in_memory_db):
        """purpose=None short-circuits immediately → returns None."""
        from flow_runtime import _resolve_offer
        contact = make_contact()
        with patch("flow_runtime.intelligence_layer") as mock_il:
            result = _resolve_offer(contact, None, [])
        assert result is None
        mock_il.get_discount_policy.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 5. TestLegacyTokenContext
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyTokenContext:
    """_build_legacy_token_context resolves all 17 tokens."""

    def test_all_17_tokens_resolve(self, make_contact, in_memory_db):
        """All 17 expected token keys are present in the returned context."""
        from flow_runtime import _build_legacy_token_context
        from database import CustomerProfile, ContactScore

        contact = make_contact(
            email="test_tokens@example.com",
            first_name="Jane",
            last_name="Doe",
        )

        # Create a CustomerProfile with real data
        CustomerProfile.create(
            contact=contact.id,
            email=contact.email,
            total_orders=5,
            total_spent=499.95,
            lifecycle_stage="active_buyer",
            customer_type="repeat",
            intent_score=75,
            last_viewed_product="Noise-Cancelling Headphones",
            top_products=json.dumps(["Headset A", "Charger B"]),
            category_affinity_json=json.dumps({"Audio": 90, "Accessories": 60}),
            last_order_at=datetime.now() - timedelta(days=30),
        )

        ContactScore.create(
            contact=contact.id,
            rfm_segment="loyal",
            engagement_score=80,
            recency_days=30,
            frequency_rate=0.5,
            optimal_gap_hours=48.0,
        )

        flow = _make_mock_flow("checkout_abandoned")
        trigger_context = {
            "cart_items": [{"title": "Gaming Mouse", "quantity": 2, "price": "59.99"}],
            "checkout_url": "https://ldas.ca/checkout/test123",
        }

        token_ctx = _build_legacy_token_context(
            contact, flow, trigger_context, [], None
        )

        expected_tokens = [
            "{{first_name}}", "{{last_name}}", "{{email}}",
            "{{discount_code}}", "{{cart_items}}", "{{checkout_url}}",
            "{{last_viewed_product}}", "{{recently_browsed_html}}", "{{top_products_html}}",
            "{{total_orders}}", "{{total_spent}}",
            "{{rfm_segment}}", "{{lifecycle_stage}}", "{{customer_type}}",
            "{{top_category}}", "{{days_since_purchase}}", "{{intent_level}}",
            "{{unsubscribe_url}}",
        ]

        for token in expected_tokens:
            assert token in token_ctx, "Missing token: %s" % token

        # Spot-check values
        assert token_ctx["{{first_name}}"] == "Jane"
        assert token_ctx["{{last_name}}"] == "Doe"
        assert token_ctx["{{email}}"] == "test_tokens@example.com"
        assert token_ctx["{{discount_code}}"] == ""   # no offer_context
        assert "Gaming Mouse" in token_ctx["{{cart_items}}"]
        assert token_ctx["{{checkout_url}}"] == "https://ldas.ca/checkout/test123"
        assert token_ctx["{{last_viewed_product}}"] == "Noise-Cancelling Headphones"
        assert token_ctx["{{total_orders}}"] == "5"
        assert token_ctx["{{rfm_segment}}"] == "loyal"
        assert token_ctx["{{lifecycle_stage}}"] == "active_buyer"
        assert token_ctx["{{customer_type}}"] == "repeat"
        assert token_ctx["{{top_category}}"] == "Audio"
        assert token_ctx["{{intent_level}}"] == "high"  # intent_score=75 >= 70
        assert token_ctx["{{unsubscribe_url}}"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. TestBuildFlowSendPackage
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFlowSendPackage:
    """build_flow_send_package — end-to-end pipeline tests."""

    def test_ready_package_for_blocks_template(self, make_contact, make_flow, in_memory_db):
        """blocks template with valid render → status=ready, html and subject populated."""
        from flow_runtime import build_flow_send_package
        from database import FlowStep

        contact = make_contact(first_name="Alice")
        flow = make_flow("checkout_abandoned")
        step = FlowStep.select().where(FlowStep.flow == flow).first()
        step.template.template_format = "blocks"
        step.template.save()
        enrollment = _make_mock_enrollment()

        mock_render = {
            "is_valid": True,
            "html": "<html>blocks email</html>",
            "subject": "Alice, you left something behind",
            "errors": [],
            "warnings": [],
            "validation_report": [],
            "resolved_products": [],
            "resolved_offer": None,
        }

        with patch("flow_runtime.intelligence_layer") as mock_il, \
             patch("flow_runtime.discount_engine") as mock_de, \
             patch("flow_runtime.te") as mock_te:

            mock_il.should_contact_now.return_value = {"can_send": True, "reason": "ok", "next_available_at": None}
            mock_il.get_next_products.return_value = {
                "schema_version": 1, "top_pick": None, "cross_sells": [],
                "reorders": [], "upgrades": [], "replacements": [], "accessories": [],
            }
            mock_il.get_discount_policy.return_value = {"offer_discount": False}
            mock_te.make_render_contract.return_value = {"contract": True}
            mock_te.render_email.return_value = mock_render

            package = build_flow_send_package(
                enrollment, step, contact, flow,
                template=step.template, trigger_context=None
            )

        assert package["status"] == "ready"
        assert package["html"] == "<html>blocks email</html>"
        assert package["subject"] is not None
        assert package["objective"] == "checkout_recovery"
        assert package["urgency"] == "urgent"

    def test_invalid_render_returns_invalid_status(self, make_contact, make_flow, in_memory_db):
        """When template_engine reports errors, package status is 'invalid'."""
        from flow_runtime import build_flow_send_package
        from database import FlowStep

        contact = make_contact(first_name="Bob")
        flow = make_flow("contact_created")
        step = FlowStep.select().where(FlowStep.flow == flow).first()
        step.template.template_format = "blocks"
        step.template.save()
        enrollment = _make_mock_enrollment()

        mock_render = {
            "is_valid": False,
            "html": "",
            "subject": "",
            "errors": [{"level": "error", "check": "structural", "message": "No unsubscribe link"}],
            "warnings": [],
            "validation_report": [],
        }

        with patch("flow_runtime.intelligence_layer") as mock_il, \
             patch("flow_runtime.discount_engine"), \
             patch("flow_runtime.te") as mock_te:

            mock_il.should_contact_now.return_value = {"can_send": True, "reason": "ok", "next_available_at": None}
            mock_il.get_next_products.return_value = {
                "schema_version": 1, "top_pick": None, "cross_sells": [],
                "reorders": [], "upgrades": [], "replacements": [], "accessories": [],
            }
            mock_il.get_discount_policy.return_value = {"offer_discount": False}
            mock_te.make_render_contract.return_value = {"contract": True}
            mock_te.render_email.return_value = mock_render

            package = build_flow_send_package(
                enrollment, step, contact, flow,
                template=step.template, trigger_context=None
            )

        assert package["status"] == "invalid"
        assert package["reason"] == "render_failed"

    def test_deferred_for_lower_urgency_timing(self, make_contact, make_flow, in_memory_db):
        """no_purchase_days (lower urgency) defers when timing gate blocks."""
        from flow_runtime import build_flow_send_package
        from database import FlowStep

        contact = make_contact()
        flow = make_flow("no_purchase_days")
        step = FlowStep.select().where(FlowStep.flow == flow).first()
        enrollment = _make_mock_enrollment()
        future = datetime.now() + timedelta(hours=36)

        with patch("flow_runtime.intelligence_layer") as mock_il, \
             patch("flow_runtime.discount_engine"), \
             patch("flow_runtime.te"):

            mock_il.should_contact_now.return_value = {
                "can_send": False,
                "reason": "too_soon (sent 12h ago, need 48h gap)",
                "next_available_at": future,
            }

            package = build_flow_send_package(
                enrollment, step, contact, flow,
                template=step.template, trigger_context=None
            )

        assert package["status"] == "deferred"
        assert package["reason"] == "timing_gate"
        assert package["metadata"]["next_available_at"] == future
