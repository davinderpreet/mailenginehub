"""
Tests for the Template Engine (Phase 2).

True unit tests — no dependency on peewee, condition_engine, or database.
Block rendering is mocked via _mock_render_template_blocks so these tests
verify template_engine logic (contract, validation, result assembly) in
isolation.

Tests:
1.  Block template renders through shared engine → subject/preview/html/report
2.  Legacy HTML template renders through shared engine
3.  10% offer consistency passes when subject/body/discount block all match
4.  Mismatched offer values fail validation in send mode
5.  Missing unsubscribe link fails validation
6.  Invalid CTA link (javascript:) fails validation
7.  Unresolved placeholders fail in send mode
8.  Product block with no concrete resolved product fails/warns
9.  Studio/candidate preview uses same render engine behavior
10. Preflight can consume the shared validation result
11. Expired offer fails in send mode
12. Category-label-only product fails product consistency
"""

import sys
import os
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── Mock for block_registry.render_template_blocks ──────────────
# Produces valid HTML from blocks + email shell without needing
# peewee, condition_engine, or database.

def _mock_render_template_blocks(template, contact=None, products=None,
                                  discount=None, explain=False):
    """Lightweight stand-in for block_registry.render_template_blocks.

    Generates simple HTML from block content, enriches preview_text with
    discount context (matching real block_registry logic), wraps in
    email_shell, and returns (html, explain_list) when explain=True.
    """
    from email_shell import wrap_email

    blocks_json = getattr(template, 'blocks_json', '[]')
    blocks = json.loads(blocks_json) if isinstance(blocks_json, str) else (blocks_json or [])
    preview_text = getattr(template, 'preview_text', '') or ''

    # Build body HTML with block content values embedded
    body_parts = []
    for block in blocks:
        bt = block.get('block_type', '')
        ct = block.get('content', {})
        if bt == 'hero':
            body_parts.append(
                '<tr><td style="padding:40px 30px;text-align:center;">'
                '<h1>%s</h1><p>%s</p></td></tr>'
                % (ct.get('headline', ''), ct.get('subheadline', '')))
        elif bt == 'text':
            for p in ct.get('paragraphs', []):
                body_parts.append(
                    '<tr><td style="padding:20px 30px;"><p>%s</p></td></tr>' % p)
        elif bt == 'cta':
            body_parts.append(
                '<tr><td style="padding:20px 30px;text-align:center;">'
                '<a href="%s">%s</a>'
                % (ct.get('url', '#'), ct.get('text', '')))
            if ct.get('subtext'):
                body_parts[-1] += '<p>%s</p>' % ct['subtext']
            body_parts[-1] += '</td></tr>'
        elif bt == 'discount':
            body_parts.append(
                '<tr><td style="padding:20px 30px;text-align:center;">'
                '<div>%s</div><div>%s</div><div>%s</div></td></tr>'
                % (ct.get('value_display', ''), ct.get('code', ''),
                   ct.get('display_text', '')))
        elif bt in ('product_grid', 'product_hero'):
            body_parts.append(
                '<tr><td>%s: %s</td></tr>'
                % (bt, ct.get('section_title', '')))
        else:
            body_parts.append('<tr><td>%s block</td></tr>' % bt)

    body_html = '\n'.join(body_parts)

    # Enrich preview_text with discount (matches block_registry lines 2062-2069)
    if discount and discount.get('code'):
        _val = discount.get('value_display', '')
        _code = discount['code']
        if preview_text:
            preview_text = '%s - Code: %s - %s' % (_val, _code, preview_text)
        else:
            _exp = discount.get('expires_text', '')
            if _exp:
                preview_text = '%s - Use code %s before %s' % (_val, _code, _exp)
            else:
                preview_text = '%s - Code: %s' % (_val, _code)

    html = wrap_email(body_html, preview_text=preview_text,
                      unsubscribe_url='{{unsubscribe_url}}')

    if explain:
        explain_list = [
            {'block_index': i, 'block_type': b.get('block_type', ''),
             'summary': 'no variants'}
            for i, b in enumerate(blocks)
        ]
        return html, explain_list
    return html


# Patch target: block_registry.render_template_blocks is imported inside
# template_engine._render_blocks via "from block_registry import render_template_blocks".
_BLOCK_MOCK = patch('block_registry.render_template_blocks',
                     side_effect=_mock_render_template_blocks)


class FakeTemplate:
    """Minimal EmailTemplate stand-in for testing."""
    def __init__(self, blocks=None, html_body="", subject="Test Subject",
                 preview_text="Test preview", family="welcome",
                 template_format="blocks", template_id=1, name="Test Template"):
        self.id = template_id
        self.name = name
        self.subject = subject
        self.preview_text = preview_text
        self.html_body = html_body
        self.template_format = template_format
        self.template_family = family
        self.blocks_json = json.dumps(blocks) if blocks else ""
        self.ai_enabled = False
        self.block_ai_overrides = ""


def _make_blocks_template(blocks=None, subject="Test Subject",
                          preview_text="Test preview", family="welcome"):
    """Create a minimal blocks template with hero + text + CTA."""
    if blocks is None:
        blocks = [
            {"block_type": "hero", "content": {
                "headline": "Welcome to LDAS",
                "subheadline": "Your trucking electronics experts",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["We have the best products for your truck."],
            }},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
            }},
        ]
    return FakeTemplate(blocks=blocks, subject=subject,
                        preview_text=preview_text, family=family)


class TestTemplateEngineRender(unittest.TestCase):
    """Test core rendering through the template engine."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_block_template_renders_with_result_structure(self):
        """Test 1: Block template renders through shared engine with full result."""
        from template_engine import render_email, make_render_contract

        template = _make_blocks_template()
        contract = make_render_contract(template=template, mode="preview")
        result = render_email(contract)

        # Must have all required keys
        self.assertIn("schema_version", result)
        self.assertIn("subject", result)
        self.assertIn("preview_text", result)
        self.assertIn("html", result)
        self.assertIn("validation_report", result)
        self.assertIn("resolved_products", result)
        self.assertIn("resolved_offer", result)
        self.assertIn("warnings", result)
        self.assertIn("errors", result)
        self.assertIn("is_valid", result)

        # Subject and preview must be populated
        self.assertEqual(result["subject"], "Test Subject")
        self.assertIn("Test preview", result["preview_text"])

        # HTML must contain shell markers
        self.assertIn("<!DOCTYPE", result["html"])
        self.assertIn("LDAS", result["html"])
        self.assertIn("unsubscribe", result["html"].lower())

        # Validation report is a list
        self.assertIsInstance(result["validation_report"], list)

    def test_legacy_html_template_renders(self):
        """Test 2: Legacy HTML template renders through shared engine."""
        from template_engine import render_email, make_render_contract

        template = FakeTemplate(
            template_format="html",
            html_body="<tr><td>Hello {{first_name}}, welcome to LDAS!</td></tr>",
            subject="Welcome!",
            preview_text="Welcome to our store",
        )
        contract = make_render_contract(template=template, mode="preview")
        result = render_email(contract)

        self.assertIn("html", result)
        self.assertTrue(len(result["html"]) > 100)
        # Legacy gets shell-wrapped
        self.assertIn("<!DOCTYPE", result["html"])
        # Personalization applied (no contact → demo values)
        self.assertIn("John", result["html"])
        self.assertEqual(result["subject"], "Welcome!")

    def test_render_with_explain(self):
        """Preview with explain=True returns explain data."""
        from template_engine import render_email, make_render_contract

        template = _make_blocks_template()
        contract = make_render_contract(template=template, mode="preview", explain=True)
        result = render_email(contract)

        self.assertIsNotNone(result["explain"])
        self.assertIsInstance(result["explain"], list)


class TestOfferConsistency(unittest.TestCase):
    """Test offer consistency enforcement (most important requirement)."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_offer_consistency_passes_when_matching(self):
        """Test 3: 10% offer in subject + discount block + offer_context all match."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {
                "headline": "10% OFF Everything",
                "subheadline": "Limited time offer",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Save 10% on your entire order."],
            }},
            {"block_type": "discount", "content": {
                "code": "SAVE10",
                "value_display": "10% OFF",
                "display_text": "10% off your order",
                "expires_text": "Expires in 5 days",
            }},
            {"block_type": "cta", "content": {
                "text": "Claim 10% Off",
                "url": "https://ldas.ca",
            }},
        ]
        template = FakeTemplate(
            blocks=blocks,
            subject="10% OFF — Don't miss out!",
            preview_text="Save 10% today",
            family="promo",
        )
        offer = {
            "code": "SAVE10",
            "value_display": "10% OFF",
            "display_text": "10% off your order",
            "expires_text": "Expires in 5 days",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(
            template=template, offer_context=offer, mode="send",
        )
        result = render_email(contract)

        # No offer_consistency errors
        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertEqual(offer_errors, [], "Expected no offer consistency errors, got: %s" % offer_errors)

    def test_offer_mismatch_fails_send_mode(self):
        """Test 4: Subject says 15% but offer is 10% → error in send mode."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Deals!", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["Check it out."]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10", "value_display": "10% OFF",
                "display_text": "10% off", "expires_text": "5 days",
            }},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(
            blocks=blocks,
            subject="Get 15% off today!",  # MISMATCH
            preview_text="Shop now",
            family="promo",
        )
        offer = {
            "code": "SAVE10",
            "value_display": "10% OFF",
            "display_text": "10% off",
            "expires_text": "5 days",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(
            template=template, offer_context=offer, mode="send",
        )
        result = render_email(contract)

        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_errors), 0, "Expected offer consistency error for mismatched discount")
        self.assertFalse(result["is_valid"])

    def test_discount_block_code_mismatch(self):
        """Discount block has different code than offer_context."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Sale", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["x"]}},
            {"block_type": "discount", "content": {
                "code": "WRONG_CODE",
                "value_display": "10% OFF",
                "display_text": "10% off",
                "expires_text": "5 days",
            }},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(blocks=blocks, subject="Sale", family="promo")
        offer = {
            "code": "SAVE10", "value_display": "10% OFF",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="send")
        result = render_email(contract)

        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_errors), 0, "Expected error for code mismatch")

    def test_expired_offer_fails_send_mode(self):
        """Test 11: Expired offer should fail in send mode."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Sale", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["x"]}},
            {"block_type": "discount", "content": {
                "code": "EXPIRED", "value_display": "10% OFF",
            }},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(blocks=blocks, subject="Sale", family="promo")
        offer = {
            "code": "EXPIRED", "value_display": "10% OFF",
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),  # EXPIRED
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="send")
        result = render_email(contract)

        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_errors), 0, "Expected error for expired offer")

    def test_discount_block_no_offer_warns(self):
        """Discount block present but no offer_context → error in send mode."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Sale", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["x"]}},
            {"block_type": "discount", "content": {"code": "SAVE5", "value_display": "5% OFF"}},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(blocks=blocks, subject="Sale", family="promo")
        contract = make_render_contract(
            template=template, offer_context=None, mode="send",
        )
        result = render_email(contract)

        offer_issues = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_issues), 0, "Expected offer consistency issue when no offer provided")


class TestStructuralValidation(unittest.TestCase):
    """Test structural and placeholder validation."""

    def test_missing_unsubscribe_fails(self):
        """Test 5: Missing unsubscribe link should always fail."""
        from template_engine import validate_rendered_email

        # HTML without unsubscribe
        html = "<!DOCTYPE html><html><body><p>Hello world</p></body></html>"
        issues = validate_rendered_email(
            html=html, subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        structural_errors = [e for e in issues if e["check"] == "structural" and "unsubscribe" in e["message"].lower()]
        self.assertGreater(len(structural_errors), 0, "Expected unsubscribe error")

    def test_javascript_link_fails(self):
        """Test 6: javascript: URLs should fail validation."""
        from template_engine import validate_rendered_email

        html = '<!DOCTYPE html><html><body><a href="javascript:alert(1)">Click</a><a href="#" style="">Unsubscribe</a></body></html>'
        issues = validate_rendered_email(
            html=html, subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        js_errors = [e for e in issues if e["check"] == "structural" and "javascript" in e["message"].lower()]
        self.assertGreater(len(js_errors), 0, "Expected javascript link error")

    def test_unresolved_placeholders_fail_send_mode(self):
        """Test 7: Unresolved {{custom_token}} should fail in send mode."""
        from template_engine import validate_rendered_email

        html = '<!DOCTYPE html><html><body><p>Hello {{custom_token}}</p><a href="#">Unsubscribe</a></body></html>'
        issues = validate_rendered_email(
            html=html, subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        placeholder_errors = [e for e in issues if e["check"] == "placeholder_check" and e["level"] == "error"]
        self.assertGreater(len(placeholder_errors), 0, "Expected placeholder error in send mode")

    def test_allowed_tokens_pass_send_mode(self):
        """{{unsubscribe_url}} should be allowed in send mode."""
        from template_engine import validate_rendered_email

        html = '<!DOCTYPE html><html><body><p>Hello</p><a href="{{unsubscribe_url}}">Unsubscribe</a></body></html>'
        issues = validate_rendered_email(
            html=html, subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        placeholder_errors = [e for e in issues if e["check"] == "placeholder_check" and e["level"] == "error"]
        self.assertEqual(placeholder_errors, [], "{{unsubscribe_url}} should be allowed in send mode")

    def test_empty_html_fails(self):
        """Empty rendered HTML should fail."""
        from template_engine import validate_rendered_email

        issues = validate_rendered_email(
            html="", subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        structural_errors = [e for e in issues if e["check"] == "structural" and "empty" in e["message"].lower()]
        self.assertGreater(len(structural_errors), 0)

    def test_empty_subject_fails_send_mode(self):
        """Empty subject should fail in send mode."""
        from template_engine import validate_rendered_email

        html = '<!DOCTYPE html><html><body><a href="#">Unsubscribe</a></body></html>'
        issues = validate_rendered_email(
            html=html, subject="", preview_text="Test",
            offer_context=None, products=[], mode="send",
        )
        subject_errors = [e for e in issues if "subject" in e["message"].lower() and e["level"] == "error"]
        self.assertGreater(len(subject_errors), 0, "Expected error for empty subject in send mode")


class TestProductConsistency(unittest.TestCase):
    """Test product consistency enforcement."""

    def test_product_block_no_products_fails(self):
        """Test 8: Product blocks with empty products → error in send mode."""
        from template_engine import validate_rendered_email

        blocks_json = json.dumps([
            {"block_type": "hero", "content": {"headline": "Sale"}},
            {"block_type": "product_grid", "content": {"section_title": "Featured", "columns": 2}},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ])
        issues = validate_rendered_email(
            html='<!DOCTYPE html><html><body><a href="#">Unsubscribe</a></body></html>',
            subject="Test", preview_text="Test",
            offer_context=None, products=[],
            mode="send", blocks_json=blocks_json,
        )
        prod_errors = [e for e in issues if e["check"] == "product_consistency"]
        self.assertGreater(len(prod_errors), 0, "Expected product consistency error")

    def test_category_label_product_fails(self):
        """Test 12: Product with category label as title should fail."""
        from template_engine import validate_rendered_email

        blocks_json = json.dumps([
            {"block_type": "product_hero", "content": {"section_title": "Featured"}},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ])
        products = [{"title": "Bluetooth Headsets", "product_url": ""}]  # Category label, not product
        issues = validate_rendered_email(
            html='<!DOCTYPE html><html><body><a href="#">Unsubscribe</a></body></html>',
            subject="Test", preview_text="Test",
            offer_context=None, products=products,
            mode="send", blocks_json=blocks_json,
        )
        prod_errors = [e for e in issues if e["check"] == "product_consistency"]
        self.assertGreater(len(prod_errors), 0, "Expected error for category-label product")

    def test_concrete_product_passes(self):
        """Concrete products with title + product_url should pass."""
        from template_engine import validate_rendered_email

        blocks_json = json.dumps([
            {"block_type": "product_grid", "content": {"section_title": "Featured", "columns": 2}},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ])
        products = [
            {"title": "BlueParrott G7 Headset", "product_url": "https://ldas.ca/products/g7", "price": "79.99"},
            {"title": "A20 Dash Cam", "product_url": "https://ldas.ca/products/a20", "price": "89.99"},
        ]
        issues = validate_rendered_email(
            html='<!DOCTYPE html><html><body><a href="#">Unsubscribe</a></body></html>',
            subject="Test", preview_text="Test",
            offer_context=None, products=products,
            mode="send", blocks_json=blocks_json,
        )
        prod_errors = [e for e in issues if e["check"] == "product_consistency" and e["level"] == "error"]
        self.assertEqual(prod_errors, [], "Concrete products should pass: %s" % prod_errors)


class TestPreviewEngine(unittest.TestCase):
    """Test preview behavior and studio alignment."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_preview_uses_same_engine(self):
        """Test 9: preview_email returns same structure as render_email."""
        from template_engine import preview_email

        template = _make_blocks_template()
        result = preview_email(template)

        # Same result structure
        self.assertIn("schema_version", result)
        self.assertIn("subject", result)
        self.assertIn("preview_text", result)
        self.assertIn("html", result)
        self.assertIn("validation_report", result)
        self.assertIn("is_valid", result)

        # Explain should be populated in preview mode
        self.assertIsNotNone(result["explain"])
        self.assertIsInstance(result["explain"], list)

    def test_preview_mode_lenient_on_placeholders(self):
        """Preview mode should warn (not error) on unresolved tokens."""
        from template_engine import validate_rendered_email

        html = '<!DOCTYPE html><html><body><p>{{first_name}}</p><a href="#">Unsubscribe</a></body></html>'
        issues = validate_rendered_email(
            html=html, subject="Test", preview_text="Test",
            offer_context=None, products=[], mode="preview",
        )
        placeholder_errors = [e for e in issues if e["check"] == "placeholder_check" and e["level"] == "error"]
        self.assertEqual(placeholder_errors, [], "Preview mode should not have placeholder errors")

    def test_substitute_preview_tokens(self):
        """substitute_preview_tokens replaces tokens for browser display."""
        from template_engine import substitute_preview_tokens

        html = '<a href="{{unsubscribe_url}}">Unsub</a> Code: {{discount_code}}'
        result = substitute_preview_tokens(html)
        self.assertIn('href="#"', result)
        self.assertIn("PREVIEW5", result)
        self.assertNotIn("{{unsubscribe_url}}", result)


class TestPreflightIntegration(unittest.TestCase):
    """Test that preflight can consume template engine validation."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_preflight_consumes_validation(self):
        """Test 10: Template engine validation can feed preflight checks."""
        from template_engine import render_email, make_render_contract

        # Valid template → should pass
        template = _make_blocks_template()
        contract = make_render_contract(template=template, mode="send")
        result = render_email(contract)

        # Preflight would check: any errors → BLOCK, any warnings → WARN, else PASS
        errors = result["errors"]
        warnings = result["warnings"]

        # A valid minimal template in send mode — check we can classify
        # (may have content_completeness warnings about preview text, that's fine)
        self.assertIsInstance(errors, list)
        self.assertIsInstance(warnings, list)

        # Filter to only critical checks that preflight cares about
        critical_checks = {"structural", "offer_consistency", "placeholder_check"}
        critical_errors = [e for e in errors if e["check"] in critical_checks]
        # A well-formed template should have no critical errors
        self.assertEqual(critical_errors, [],
                         "Valid template should have no critical errors: %s" % critical_errors)

    def test_invalid_template_produces_actionable_errors(self):
        """Preflight gets specific, actionable error messages."""
        from template_engine import render_email, make_render_contract

        # Template with javascript link
        blocks = [
            {"block_type": "hero", "content": {"headline": "Bad", "subheadline": "x"}},
            {"block_type": "cta", "content": {
                "text": "Click me",
                "url": "javascript:alert(1)",  # BAD
            }},
        ]
        template = FakeTemplate(blocks=blocks, subject="Test", family="welcome")
        contract = make_render_contract(template=template, mode="send")
        result = render_email(contract)

        self.assertFalse(result["is_valid"])
        # Errors should have check + message for preflight display
        for error in result["errors"]:
            self.assertIn("check", error)
            self.assertIn("message", error)
            self.assertIn("level", error)


class TestRenderContract(unittest.TestCase):
    """Test render contract construction and validation."""

    def test_make_render_contract_defaults(self):
        """Contract has correct defaults."""
        from template_engine import make_render_contract, SCHEMA_VERSION

        contract = make_render_contract(template=1)
        self.assertEqual(contract["schema_version"], SCHEMA_VERSION)
        self.assertEqual(contract["mode"], "preview")
        self.assertEqual(contract["source_system"], "test")
        self.assertIsNone(contract["contact_id"])
        self.assertIsNone(contract["product_context"])
        self.assertIsNone(contract["offer_context"])
        self.assertFalse(contract["explain"])

    def test_make_render_contract_explicit_values(self):
        """Contract preserves explicitly provided values."""
        from template_engine import make_render_contract

        offer = {"code": "X", "value_display": "10% OFF"}
        products = [{"title": "G7", "product_url": "https://ldas.ca/g7"}]
        contract = make_render_contract(
            template=42,
            source_system="flow",
            objective="cart_recovery",
            contact_id=99,
            product_context=products,
            offer_context=offer,
            mode="send",
            explain=True,
        )
        self.assertEqual(contract["template"], 42)
        self.assertEqual(contract["source_system"], "flow")
        self.assertEqual(contract["objective"], "cart_recovery")
        self.assertEqual(contract["contact_id"], 99)
        self.assertEqual(contract["product_context"], products)
        self.assertEqual(contract["offer_context"], offer)
        self.assertEqual(contract["mode"], "send")
        self.assertTrue(contract["explain"])

    def test_template_not_found_returns_error(self):
        """Passing invalid template ID returns error result."""
        from template_engine import render_email, make_render_contract

        contract = make_render_contract(template=None)
        result = render_email(contract)
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["errors"][0]["check"], "fatal")


class TestRenderedContentOfferMismatch(unittest.TestCase):
    """Test that offer mismatches in rendered hero/body/CTA copy are caught."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_hero_headline_offer_mismatch(self):
        """Hero headline says '15% off' but offer is 10% → error in send mode."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {
                "headline": "Get 15% off Everything!",  # MISMATCH
                "subheadline": "Limited time",
            }},
            {"block_type": "text", "content": {"paragraphs": ["Shop now."]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10", "value_display": "10% OFF",
                "display_text": "10% off", "expires_text": "5 days",
            }},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(blocks=blocks, subject="Sale", family="promo")
        offer = {
            "code": "SAVE10", "value_display": "10% OFF",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="send")
        result = render_email(contract)

        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_errors), 0,
                           "Expected offer consistency error for hero '15%%' vs offer '10%%'. Got: %s" % result["errors"])

    def test_cta_text_offer_mismatch(self):
        """CTA button text says 'Save 20%' but offer is 10% → error in send mode."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Sale", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["Great deals."]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10", "value_display": "10% OFF",
            }},
            {"block_type": "cta", "content": {
                "text": "Save 20% off Now",  # MISMATCH in button text
                "url": "https://ldas.ca",
            }},
        ]
        template = FakeTemplate(blocks=blocks, subject="Sale", family="promo")
        offer = {
            "code": "SAVE10", "value_display": "10% OFF",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="send")
        result = render_email(contract)

        offer_errors = [e for e in result["errors"] if e["check"] == "offer_consistency"]
        self.assertGreater(len(offer_errors), 0,
                           "Expected offer consistency error for CTA '20%%' vs offer '10%%'")


class TestExplainFlag(unittest.TestCase):
    """Test that explain=False works correctly."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_explain_false_returns_none(self):
        """explain=False → result['explain'] is None, render still succeeds."""
        from template_engine import render_email, make_render_contract

        template = _make_blocks_template()
        contract = make_render_contract(template=template, mode="preview", explain=False)
        result = render_email(contract)

        self.assertIsNone(result["explain"])
        self.assertIn("<!DOCTYPE", result["html"])
        self.assertTrue(len(result["html"]) > 100)

    def test_explain_false_no_explain_data(self):
        """Render with explain=False doesn't produce explain data."""
        from template_engine import render_email, make_render_contract

        template = _make_blocks_template()
        contract = make_render_contract(template=template, mode="send", explain=False)
        result = render_email(contract)

        self.assertIsNone(result["explain"])
        # Still renders correctly
        self.assertIn("LDAS", result["html"])


class TestIntelligenceProductResolution(unittest.TestCase):
    """Test that _intelligence_to_products uses real repo models."""

    def test_intelligence_schema_key_extraction(self):
        """Verify correct keys are extracted from intelligence schema."""
        from template_engine import _intelligence_to_products

        intel = {
            "schema_version": 1,
            "replacements": [{"product_key": "Replacement Ear Cushion", "category": "Ear Cushions & Parts", "due_in_days": 10}],
            "upgrades": [{"to_product": "BlueParrott G10", "category": "Bluetooth Headsets", "from_tier": "Mid"}],
            "cross_sells": [{"target_key": "Dash Cams", "strength": 60, "is_product": False}],
            "accessories": [{"target_key": "USB-C Cable Pro", "strength": 80, "is_product": True}],
            "reorders": [{"product_key": "Screen Protector", "category": "Accessories", "days_since": 200}],
            "top_pick": {"action": "replacement", "product_key": "Replacement Ear Cushion", "reason": "Due soon"},
        }
        # This will return [] because no DB tables exist in unit test context,
        # but it should NOT crash with ImportError for ShopifyProduct
        result = _intelligence_to_products(intel)
        self.assertIsInstance(result, list)
        # Should not raise any ImportError — uses ProductImageCache/ProductCommercial

    def test_intelligence_empty_input(self):
        """Empty intelligence result returns empty list."""
        from template_engine import _intelligence_to_products

        self.assertEqual(_intelligence_to_products(None), [])
        self.assertEqual(_intelligence_to_products({}), [])
        self.assertEqual(_intelligence_to_products({
            "replacements": [], "upgrades": [], "cross_sells": [],
            "accessories": [], "reorders": [], "top_pick": None,
        }), [])

    def test_intelligence_upgrade_fallback_to_category(self):
        """Upgrades without to_product fall back to category-level lookup."""
        from template_engine import _intelligence_to_products

        intel = {
            "replacements": [],
            "upgrades": [{"category": "Bluetooth Headsets", "from_tier": "Budget"}],  # No to_product
            "cross_sells": [],
            "accessories": [],
            "reorders": [],
            "top_pick": None,
        }
        # Should not crash — processes category as fallback
        result = _intelligence_to_products(intel)
        self.assertIsInstance(result, list)


class TestPreviewTextConsistency(unittest.TestCase):
    """Test that preview_text in RenderResult matches HTML preheader."""

    def setUp(self):
        self._mock = _BLOCK_MOCK.start()

    def tearDown(self):
        _BLOCK_MOCK.stop()

    def test_preview_text_enriched_with_offer(self):
        """preview_text should include offer enrichment."""
        from template_engine import render_email, make_render_contract

        blocks = [
            {"block_type": "hero", "content": {"headline": "Sale", "subheadline": "x"}},
            {"block_type": "text", "content": {"paragraphs": ["x"]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10", "value_display": "10% OFF",
            }},
            {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
        ]
        template = FakeTemplate(
            blocks=blocks, subject="Sale",
            preview_text="Check out our deals", family="promo",
        )
        offer = {
            "code": "SAVE10", "value_display": "10% OFF",
            "display_text": "10% off", "expires_text": "5 days",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=5),
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="preview")
        result = render_email(contract)

        # RenderResult preview_text should be enriched with offer
        self.assertIn("SAVE10", result["preview_text"])
        self.assertIn("10% OFF", result["preview_text"])
        self.assertIn("Check out our deals", result["preview_text"])

    def test_preview_text_personalization_and_offer_enrichment_match(self):
        """Personalized + offer-enriched preview_text is identical between
        the RenderResult and what the engine intends in the shell preheader."""
        from template_engine import render_email, make_render_contract
        import html as html_mod

        template = FakeTemplate(
            blocks=[
                {"block_type": "hero", "content": {"headline": "Hi", "subheadline": "there"}},
                {"block_type": "text", "content": {"paragraphs": ["Welcome."]}},
                {"block_type": "discount", "content": {
                    "code": "WELCOME5", "value_display": "5% OFF",
                }},
                {"block_type": "cta", "content": {"text": "Shop", "url": "https://ldas.ca"}},
            ],
            subject="Hello {{first_name}}",
            preview_text="Welcome {{first_name}} to LDAS",
            family="welcome",
        )
        offer = {
            "code": "WELCOME5", "value_display": "5% OFF",
            "display_text": "5% off", "expires_text": "7 days",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="preview")
        result = render_email(contract)

        # The canonical preview_text in the result
        canonical = result["preview_text"]
        self.assertIn("John", canonical)  # personalized (no contact = demo)
        self.assertIn("WELCOME5", canonical)  # offer-enriched
        self.assertIn("5% OFF", canonical)

        # The preheader in the HTML should contain the same text (HTML-escaped)
        safe_canonical = html_mod.escape(canonical)
        self.assertIn(safe_canonical, result["html"],
                       "Preheader in HTML should contain the canonical preview_text")

    def test_legacy_preview_text_enriched(self):
        """Legacy HTML templates also get enriched preview_text in RenderResult."""
        from template_engine import render_email, make_render_contract

        template = FakeTemplate(
            template_format="html",
            html_body="<tr><td>Welcome</td></tr>",
            subject="Sale!",
            preview_text="Big savings today",
        )
        offer = {
            "code": "BIGSALE", "value_display": "15% OFF",
            "expires_text": "3 days",
        }
        contract = make_render_contract(template=template, offer_context=offer, mode="preview")
        result = render_email(contract)

        self.assertIn("BIGSALE", result["preview_text"])
        self.assertIn("15% OFF", result["preview_text"])
        self.assertIn("Big savings today", result["preview_text"])


if __name__ == "__main__":
    unittest.main()
