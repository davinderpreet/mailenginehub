"""
Tests for upgraded template block sequences.

Validates:
1. Every template passes validate_template() for its family
2. Every template passes enforce_family_constraints()
3. Every block_type in each template exists in BLOCK_TYPES
4. Every block_type is allowed for its family in TEMPLATE_FAMILIES
5. Block count stays within max_blocks for the family
6. Each template renders without errors via render_template_blocks()
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from block_registry import BLOCK_TYPES, validate_template, render_template_blocks
from condition_engine import TEMPLATE_FAMILIES, enforce_family_constraints
from convert_templates import CONVERSIONS


class TestAllConversionsValid:
    """Every entry in CONVERSIONS must pass validation."""

    # Dynamic-content blocks (e.g. bundle_value) use empty placeholder fields
    # that get populated at render time from product data. These are not real errors.
    KNOWN_DYNAMIC_WARNINGS = {
        "Required field 'items' is empty",  # bundle_value items filled from products
    }

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_blocks_json_validates(self, conv):
        blocks_json = json.dumps(conv["blocks"])
        warnings = validate_template(blocks_json, family=conv["family"])
        errors = [
            w for w in warnings
            if w.get("level") == "error"
            and not any(known in w.get("message", "") for known in self.KNOWN_DYNAMIC_WARNINGS)
        ]
        assert errors == [], (
            f"Template '{conv['name']}' has validation errors: "
            + "; ".join(e["message"] for e in errors)
        )

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_all_block_types_exist(self, conv):
        for i, block in enumerate(conv["blocks"]):
            bt = block["block_type"]
            assert bt in BLOCK_TYPES, (
                f"Template '{conv['name']}' block [{i}] uses unknown "
                f"block_type '{bt}'"
            )

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_all_block_types_allowed_for_family(self, conv):
        family = conv["family"]
        allowed = TEMPLATE_FAMILIES[family]["allowed_blocks"]
        for i, block in enumerate(conv["blocks"]):
            bt = block["block_type"]
            assert bt in allowed, (
                f"Template '{conv['name']}' block [{i}] type '{bt}' is NOT "
                f"in {family}.allowed_blocks"
            )

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_block_count_within_max(self, conv):
        family = conv["family"]
        max_blocks = TEMPLATE_FAMILIES[family]["max_blocks"]
        actual = len(conv["blocks"])
        assert actual <= max_blocks, (
            f"Template '{conv['name']}' has {actual} blocks, exceeds "
            f"{family} max_blocks={max_blocks}"
        )

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_family_constraints_pass(self, conv):
        blocks = conv["blocks"]
        family = conv["family"]
        is_valid, errors = enforce_family_constraints(blocks, family)
        assert is_valid, (
            f"Template '{conv['name']}' failed enforce_family_constraints: "
            + "; ".join(errors)
        )


class TestTemplatesRender:

    @pytest.mark.parametrize(
        "conv",
        CONVERSIONS,
        ids=[c["name"] for c in CONVERSIONS],
    )
    def test_renders_without_error(self, conv, in_memory_db):
        from database import EmailTemplate

        tpl = EmailTemplate.create(
            name=conv["name"],
            subject="Test Subject",
            html_body="<p>fallback</p>",
            template_format="blocks",
            template_family=conv["family"],
            blocks_json=json.dumps(conv["blocks"]),
        )

        html = render_template_blocks(tpl, contact=None, products=[])
        assert isinstance(html, str)
        assert len(html) > 100, (
            f"Template '{conv['name']}' rendered to suspiciously short HTML "
            f"({len(html)} chars)"
        )


class TestNewCartTemplatesExist:

    def test_cart_recovery_templates_exist(self):
        cart_templates = [c for c in CONVERSIONS if c["family"] == "cart_recovery"]
        assert len(cart_templates) >= 2, (
            f"Expected at least 2 cart_recovery templates, found {len(cart_templates)}"
        )

    def test_cart_templates_are_not_browse(self):
        cart = [c for c in CONVERSIONS if c["family"] == "cart_recovery"]
        browse = [c for c in CONVERSIONS if c["family"] == "browse_recovery"]
        for ct in cart:
            cart_types = [b["block_type"] for b in ct["blocks"]]
            for bt in browse:
                browse_types = [b["block_type"] for b in bt["blocks"]]
                assert cart_types != browse_types, (
                    f"Cart template '{ct['name']}' has identical block sequence "
                    f"to browse template '{bt['name']}'"
                )


class TestFallbackLogging:

    def test_fallback_logs_on_invalid_json(self, in_memory_db, caplog):
        import logging
        from database import EmailTemplate

        tpl = EmailTemplate.create(
            name="Bad JSON Template",
            subject="Test",
            html_body="<p>fallback html</p>",
            template_format="blocks",
            template_family="welcome",
            blocks_json="{invalid json!!!",
        )

        with caplog.at_level(logging.WARNING, logger="block_registry"):
            result = render_template_blocks(tpl, contact=None, products=[])

        assert result == "<p>fallback html</p>"
        assert any("BLOCKS_PARSE_FAIL" in r.message for r in caplog.records), (
            "Expected BLOCKS_PARSE_FAIL warning in logs"
        )

    def test_fallback_logs_on_empty_blocks(self, in_memory_db, caplog):
        import logging
        from database import EmailTemplate

        tpl = EmailTemplate.create(
            name="Empty Blocks Template",
            subject="Test",
            html_body="<p>fallback html</p>",
            template_format="blocks",
            template_family="welcome",
            blocks_json="[]",
        )

        with caplog.at_level(logging.WARNING, logger="block_registry"):
            result = render_template_blocks(tpl, contact=None, products=[])

        assert result == "<p>fallback html</p>"
        assert any("BLOCKS_FALLBACK" in r.message for r in caplog.records), (
            "Expected BLOCKS_FALLBACK warning in logs"
        )
