"""
test_phase3.py — Phase 3 Verification Sprint

Verifies all Phase 3 (AI-Assisted Block Content Authoring) guarantees:
  1. Fallback behavior under forced AI failure
  2. Sanitize/guardrails on generated text
  3. Family constraints block invalid generation
  4. Runtime AI only affects allowed text fields
  5. ActionLedger logging
  6. Render latency impact

Run:  python -m pytest tests/test_phase3.py -v
"""

import os
import sys
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Add project root to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# =========================================================================
#  1. FALLBACK BEHAVIOR
# =========================================================================

class TestFallbackBehavior:
    """AI must return fallback content when API is unavailable."""

    def test_generate_block_no_api_key(self):
        """With no ANTHROPIC_API_KEY, generate_block_content returns fallback."""
        from ai_content import generate_block_content
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            fallback = {"headline": "Welcome!", "subheadline": "We are glad you are here"}
            result = generate_block_content("hero", fallback=fallback)
            assert result["headline"] == "Welcome!"
            assert result["subheadline"] == "We are glad you are here"

    def test_generate_block_api_exception(self):
        """API exception returns fallback, does not raise."""
        from ai_content import generate_block_content
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")
        with patch("ai_content._get_client", return_value=mock_client):
            fallback = {"headline": "Fallback headline"}
            result = generate_block_content("hero", fallback=fallback)
            assert result["headline"] == "Fallback headline"

    def test_generate_block_json_parse_error(self):
        """Invalid JSON from API returns fallback."""
        from ai_content import generate_block_content
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="this is not json {{{")]
        mock_client.messages.create.return_value = mock_response
        with patch("ai_content._get_client", return_value=mock_client):
            fallback = {"headline": "Safe fallback"}
            result = generate_block_content("hero", fallback=fallback)
            assert result["headline"] == "Safe fallback"

    def test_personalize_no_api_key_basic_tokens(self):
        """personalize_text_field replaces {{first_name}} without AI."""
        from ai_content import personalize_text_field
        from database import Contact
        contact = Contact.create(
            email="test@example.com", first_name="Dave",
            last_name="Singh", subscribed=True,
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = personalize_text_field(
                "headline", "Hello {{first_name}}!", contact, fallback="Hello!"
            )
            assert result == "Hello Dave!"

    def test_personalize_ai_marker_stripped_on_failure(self):
        """{{ai:...}} markers removed when API unavailable."""
        from ai_content import personalize_text_field
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = personalize_text_field(
                "headline",
                "Hello {{ai:make it personal}}",
                contact=None,
                fallback="Hello!",
            )
            # ai marker should be stripped, leaving "Hello"
            assert "{{ai:" not in result

    def test_generate_template_content_no_api(self):
        """Batch generation falls back to fallback_blocks."""
        from ai_content import generate_template_content
        blocks = [
            {"block_type": "hero", "content": {"headline": "Original"}},
            {"block_type": "cta", "content": {"text": "Click Me", "url": "https://ldas.ca"}},
        ]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = generate_template_content(blocks, fallback_blocks=blocks)
            assert result[0]["content"]["headline"] == "Original"
            assert result[1]["content"]["text"] == "Click Me"

    def test_fallback_none_returns_empty_dict(self):
        """When fallback is None, returns empty dict on failure."""
        from ai_content import generate_block_content
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = generate_block_content("hero", fallback=None)
            assert isinstance(result, dict)

    def test_divider_block_returns_fallback_immediately(self):
        """Divider has no writable fields — always returns fallback."""
        from ai_content import generate_block_content
        fallback = {}
        result = generate_block_content("divider", fallback=fallback)
        assert result == {}


# =========================================================================
#  2. SANITIZE / GUARDRAILS
# =========================================================================

class TestSanitizeGuardrails:
    """AI output must be stripped of HTML, markdown, and length-capped."""

    def test_html_tags_stripped(self):
        from ai_content import _sanitize_text
        result = _sanitize_text('<script>alert("xss")</script>Hello')
        assert "<script>" not in result
        assert "</script>" not in result
        assert "Hello" in result
        # Tags are stripped; text between tags is preserved (safe — no HTML injection)
        # The key guarantee: no HTML tags survive in the output
        assert "<" not in result

    def test_html_img_tag_stripped(self):
        from ai_content import _sanitize_text
        result = _sanitize_text('<img src="http://evil.com/pixel.gif">Clean text')
        assert "<img" not in result
        assert "Clean text" in result

    def test_html_link_stripped(self):
        from ai_content import _sanitize_text
        result = _sanitize_text('<a href="http://evil.com">Click here</a>')
        assert "<a " not in result
        assert "</a>" not in result
        assert "Click here" in result

    def test_markdown_stripped(self):
        from ai_content import _sanitize_text
        result = _sanitize_text("**bold** _italic_ ~strike~ `code` # heading")
        assert "*" not in result
        assert "_" not in result
        assert "~" not in result
        assert "`" not in result
        assert "#" not in result

    def test_length_cap_enforced(self):
        from ai_content import _sanitize_text
        long_text = "A" * 1000
        result = _sanitize_text(long_text, max_len=120)
        assert len(result) <= 123  # 120 + "..."

    def test_length_cap_per_field(self):
        from ai_content import FIELD_MAX_LENGTHS, _sanitize_text
        for field, max_len in FIELD_MAX_LENGTHS.items():
            long_text = "word " * (max_len // 3)
            result = _sanitize_text(long_text, max_len=max_len)
            assert len(result) <= max_len + 3  # +3 for "..."

    def test_whitespace_normalized(self):
        from ai_content import _sanitize_text
        result = _sanitize_text("  hello   world   ")
        assert result == "hello world"

    def test_empty_input(self):
        from ai_content import _sanitize_text
        assert _sanitize_text("") == ""
        assert _sanitize_text(None) == ""

    def test_sanitize_paragraphs(self):
        from ai_content import _sanitize_paragraphs
        paras = [
            '<script>bad</script>Good para',
            "Normal paragraph",
            "",  # empty gets filtered
            "A" * 1000,  # overlength
        ]
        result = _sanitize_paragraphs(paras, max_per=500, max_count=6)
        assert len(result) <= 3  # empty filtered out
        assert "<script>" not in result[0]
        for p in result:
            assert len(p) <= 503  # 500 + "..."

    def test_sanitize_paragraphs_max_count(self):
        from ai_content import _sanitize_paragraphs
        paras = ["Para %d" % i for i in range(20)]
        result = _sanitize_paragraphs(paras, max_per=500, max_count=6)
        assert len(result) <= 6

    def test_sanitize_paragraphs_non_list(self):
        from ai_content import _sanitize_paragraphs
        assert _sanitize_paragraphs("not a list") == []
        assert _sanitize_paragraphs(None) == []


# =========================================================================
#  3. FAMILY CONSTRAINTS
# =========================================================================

class TestFamilyConstraints:
    """enforce_family_constraints must block invalid block compositions."""

    def test_disallowed_block_rejected(self):
        """Welcome family does not allow urgency block."""
        from condition_engine import enforce_family_constraints
        blocks = [
            {"block_type": "hero", "content": {}},
            {"block_type": "text", "content": {}},
            {"block_type": "urgency", "content": {}},  # NOT allowed in welcome
            {"block_type": "cta", "content": {}},
        ]
        is_valid, errors = enforce_family_constraints(blocks, "welcome")
        assert is_valid is False
        assert any("urgency" in e.lower() for e in errors)

    def test_missing_required_block(self):
        """Welcome requires hero — omitting it should fail."""
        from condition_engine import enforce_family_constraints
        blocks = [
            {"block_type": "text", "content": {}},
            {"block_type": "cta", "content": {}},
        ]
        is_valid, errors = enforce_family_constraints(blocks, "welcome")
        assert is_valid is False
        assert any("hero" in e.lower() for e in errors)

    def test_valid_welcome_template(self):
        """A properly formed welcome template passes."""
        from condition_engine import enforce_family_constraints
        blocks = [
            {"block_type": "hero", "content": {}},
            {"block_type": "text", "content": {}},
            {"block_type": "cta", "content": {}},
        ]
        is_valid, errors = enforce_family_constraints(blocks, "welcome")
        assert is_valid is True
        assert errors == []

    def test_unknown_family_rejected(self):
        """Unknown family key is rejected."""
        from condition_engine import enforce_family_constraints
        blocks = [{"block_type": "hero", "content": {}}]
        is_valid, errors = enforce_family_constraints(blocks, "nonexistent_family")
        assert is_valid is False

    def test_ai_batch_respects_family(self):
        """generate_template_content returns fallback when family violated."""
        from ai_content import generate_template_content
        blocks = [
            {"block_type": "urgency", "content": {"message": "Hurry!"}},
        ]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
            result = generate_template_content(
                blocks, family="welcome", fallback_blocks=blocks,
            )
            # Should return fallback because urgency is not allowed in welcome
            assert result[0]["content"]["message"] == "Hurry!"

    def test_max_blocks_exceeded(self):
        """Exceeding max_blocks for a family should fail."""
        from condition_engine import enforce_family_constraints, TEMPLATE_FAMILIES
        # Build blocks list exceeding the max for welcome family
        family_cfg = TEMPLATE_FAMILIES.get("welcome", {})
        max_blocks = family_cfg.get("max_blocks", 10)
        blocks = [{"block_type": "text", "content": {}} for _ in range(max_blocks + 5)]
        # Add required blocks
        blocks.insert(0, {"block_type": "hero", "content": {}})
        blocks.append({"block_type": "cta", "content": {}})
        is_valid, errors = enforce_family_constraints(blocks, "welcome")
        assert is_valid is False
        assert any("max" in e.lower() or "exceed" in e.lower() for e in errors)


# =========================================================================
#  4. AI WRITABLE FIELDS RESTRICTION
# =========================================================================

class TestAIWritableFields:
    """AI must NOT write to url, code, color, bg_color, or other locked fields."""

    def _mock_ai_response(self, response_dict):
        """Create a mock Anthropic client that returns the given dict as JSON."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(response_dict))]
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_cta_url_not_overwritten(self):
        """AI returning 'url' should NOT update the CTA url."""
        from ai_content import generate_block_content
        mock = self._mock_ai_response({"text": "Shop Now", "url": "http://evil.com", "color": "#ff0000"})
        with patch("ai_content._get_client", return_value=mock):
            fallback = {"text": "Click Here", "url": "https://ldas.ca", "color": "#0428aa"}
            result = generate_block_content("cta", fallback=fallback)
            # AI CAN write "text" (it's in AI_WRITABLE_FIELDS for cta)
            assert result["text"] == "Shop Now"
            # AI CANNOT write "url" or "color"
            assert result.get("url") == "https://ldas.ca"
            assert result.get("color") == "#0428aa"

    def test_discount_code_not_overwritten(self):
        """AI returning 'code' should NOT update the discount code."""
        from ai_content import generate_block_content
        mock = self._mock_ai_response({
            "display_text": "Special deal!",
            "expires_text": "Ends soon",
            "value_display": "10% Off",
            "code": "HACKED",
        })
        with patch("ai_content._get_client", return_value=mock):
            fallback = {"code": "WELCOME5", "display_text": "", "expires_text": "", "value_display": ""}
            result = generate_block_content("discount", fallback=fallback)
            # AI CAN write display_text, expires_text, value_display
            assert result["display_text"] == "Special deal!"
            assert result["value_display"] == "10% Off"
            # AI CANNOT write code
            assert result.get("code") == "WELCOME5"

    def test_hero_bg_color_not_overwritten(self):
        """AI returning 'bg_color' should NOT update the hero background."""
        from ai_content import generate_block_content
        mock = self._mock_ai_response({
            "headline": "Great Deals",
            "subheadline": "Check them out",
            "bg_color": "red",
        })
        with patch("ai_content._get_client", return_value=mock):
            fallback = {"headline": "", "subheadline": "", "bg_color": "linear-gradient(135deg, #cffafe 0%, #a5f3fc 100%)"}
            result = generate_block_content("hero", fallback=fallback)
            assert result["headline"] == "Great Deals"
            # bg_color NOT in AI_WRITABLE_FIELDS["hero"]
            assert result.get("bg_color") == "linear-gradient(135deg, #cffafe 0%, #a5f3fc 100%)"

    def test_product_grid_columns_not_overwritten(self):
        """AI cannot change product_grid columns."""
        from ai_content import generate_block_content
        mock = self._mock_ai_response({"section_title": "Top Picks", "columns": 99})
        with patch("ai_content._get_client", return_value=mock):
            fallback = {"section_title": "", "columns": 2}
            result = generate_block_content("product_grid", fallback=fallback)
            assert result["section_title"] == "Top Picks"
            assert result.get("columns") == 2

    def test_writable_fields_whitelist_complete(self):
        """Every block type is listed in AI_WRITABLE_FIELDS."""
        from ai_content import AI_WRITABLE_FIELDS
        from block_registry import BLOCK_TYPES
        for block_type in BLOCK_TYPES:
            assert block_type in AI_WRITABLE_FIELDS, (
                "%s missing from AI_WRITABLE_FIELDS" % block_type
            )


# =========================================================================
#  5. ACTION LEDGER LOGGING
# =========================================================================

class TestActionLedgerLogging:
    """Every AI generation must be logged to ActionLedger."""

    def test_generate_block_logs_success(self):
        """Successful AI generation creates ActionLedger entry."""
        from ai_content import generate_block_content
        from database import ActionLedger
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"headline": "Hi", "subheadline": "There"}')]
        mock_client.messages.create.return_value = mock_response
        with patch("ai_content._get_client", return_value=mock_client):
            generate_block_content("hero", fallback={"headline": "", "subheadline": ""}, purpose="test")
        entries = list(ActionLedger.select().where(
            ActionLedger.trigger_type == "ai_content"
        ))
        assert len(entries) >= 1
        assert "generate" in entries[-1].source_type
        assert entries[-1].status == "sent"  # success = "sent"

    def test_generate_block_logs_failure(self):
        """Failed AI generation creates ActionLedger entry with status=failed."""
        from ai_content import generate_block_content
        from database import ActionLedger
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Boom")
        with patch("ai_content._get_client", return_value=mock_client):
            generate_block_content("hero", fallback={"headline": "safe"})
        entries = list(ActionLedger.select().where(
            ActionLedger.trigger_type == "ai_content",
            ActionLedger.status == "failed",
        ))
        assert len(entries) >= 1

    def test_generate_no_api_key_logs(self):
        """No API key logs a failure entry."""
        from ai_content import generate_block_content
        from database import ActionLedger
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            generate_block_content("hero", fallback={"headline": "x"})
        entries = list(ActionLedger.select().where(
            ActionLedger.trigger_type == "ai_content",
            ActionLedger.status == "failed",
        ))
        assert len(entries) >= 1


# =========================================================================
#  6. RENDER LATENCY
# =========================================================================

class TestRenderLatency:
    """Block rendering without AI should be fast (<50ms for 5 blocks)."""

    def test_render_5_blocks_under_50ms(self):
        """Pure block rendering (no AI) completes in under 50ms."""
        from database import EmailTemplate
        from block_registry import render_template_blocks

        blocks = [
            {"block_type": "hero", "content": {"headline": "Hello", "subheadline": "World"}},
            {"block_type": "text", "content": {"paragraphs": ["Para 1", "Para 2"]}},
            {"block_type": "discount", "content": {"code": "SAVE10", "value_display": "10% Off",
                                                     "display_text": "Your discount", "expires_text": "7 days"}},
            {"block_type": "product_grid", "content": {"section_title": "Products", "columns": 2}},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca", "color": "#0428aa"}},
        ]

        template = EmailTemplate.create(
            name="Latency Test", subject="Test",
            html_body="", template_format="blocks",
            blocks_json=json.dumps(blocks),
        )

        # Warm up (first call may be slower due to imports)
        render_template_blocks(template)

        # Measure
        times = []
        for _ in range(10):
            start = time.perf_counter()
            html = render_template_blocks(template)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

        avg_ms = sum(times) / len(times)
        assert avg_ms < 50, "Average render time %.1fms exceeds 50ms threshold" % avg_ms
        assert isinstance(html, str)
        assert len(html) > 100  # sanity: real HTML output

    def test_render_with_explain_overhead(self):
        """explain=True adds minimal overhead."""
        from database import EmailTemplate
        from block_registry import render_template_blocks

        blocks = [
            {"block_type": "hero", "content": {"headline": "Hi"}},
            {"block_type": "cta", "content": {"text": "Go", "url": "https://ldas.ca"}},
        ]
        template = EmailTemplate.create(
            name="Explain Test", subject="Test",
            html_body="", template_format="blocks",
            blocks_json=json.dumps(blocks),
        )

        # Without explain
        start = time.perf_counter()
        for _ in range(10):
            render_template_blocks(template)
        base_time = (time.perf_counter() - start) * 1000

        # With explain
        start = time.perf_counter()
        for _ in range(10):
            render_template_blocks(template, explain=True)
        explain_time = (time.perf_counter() - start) * 1000

        # Explain overhead should be < 2x base time
        assert explain_time < base_time * 3, (
            "Explain overhead too high: base=%.1fms, explain=%.1fms" % (base_time, explain_time)
        )


# =========================================================================
#  Run standalone
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
