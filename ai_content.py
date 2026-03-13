"""
ai_content.py — Phase 3: AI-Assisted Block Content Authoring

LIMITED SCOPE (by design):
  - AI generates text for SMALL FIELDS ONLY: headlines, subheadlines,
    paragraphs, CTA text, urgency messages, discount display text.
  - AI CANNOT generate HTML, block structures, or modify block types.
  - AI CANNOT bypass template-family constraints.
  - Every AI generation has a MANDATORY fallback_content dict.
  - Every AI call is logged to ActionLedger for full audit.
  - AI output is sanitized: no HTML tags, no URLs, length-capped per field.

Usage:
    from ai_content import generate_block_content, personalize_text_field

    # Full block content generation (authoring time)
    result = generate_block_content("hero", contact, family="welcome",
                                     fallback={"headline": "Welcome!"})

    # Single field personalization (send time, lightweight)
    text = personalize_text_field("headline", "Welcome {{first_name}}!",
                                  contact, fallback="Welcome!")
"""

import os
import json
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# ── AI model config ──────────────────────────────────────────────────
AI_MODEL = "claude-haiku-4-5-20251001"
AI_MAX_TOKENS = 400  # Small — we only generate short text fields

# ── Field length caps (hard limits, AI output truncated beyond these) ──
FIELD_MAX_LENGTHS = {
    "headline":      120,
    "subheadline":   200,
    "paragraph":     500,   # per paragraph
    "cta_text":      50,
    "urgency":       120,
    "display_text":  80,    # discount display text
    "expires_text":  60,
    "value_display": 30,
    "section_title": 60,
}

# ── Brand context for AI (matches ai_engine.py) ──────────────────────
BRAND_CONTEXT = """You are the email copywriter for LDAS Electronics (ldas.ca),
a Canadian electronics store specializing in trucking electronics, dash cams, headsets,
CB radios, and accessories for professional drivers and fleet operators.
Tone: friendly, knowledgeable, helpful — like a fellow trucker who knows their tech.
Never pushy or corporate. Keep it short and natural."""

# ── Allowed AI-writable fields per block type ────────────────────────
# AI can ONLY write to these fields. Everything else (URLs, colors, columns,
# codes, bg_color) is human-authored or system-set.
AI_WRITABLE_FIELDS = {
    "hero":         ["headline", "subheadline"],
    "text":         ["paragraphs"],
    "cta":          ["text"],           # NOT url, NOT color
    "urgency":      ["message"],
    "discount":     ["display_text", "expires_text", "value_display"],  # NOT code
    "product_grid": ["section_title"],
    "divider":      [],                 # nothing to write
}


def _get_client():
    """Get Anthropic client. Returns None if API key not configured."""
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return None
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None


def _sanitize_text(text, max_len=500):
    """
    Sanitize AI-generated text. Strip HTML, limit length, clean whitespace.
    This is a SECURITY gate — AI cannot inject HTML into emails.
    """
    if not text or not isinstance(text, str):
        return ""
    # Strip any HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Strip markdown formatting
    text = re.sub(r'[*_~`#]', '', text)
    # Normalize whitespace
    text = ' '.join(text.split())
    # Length cap
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0] + '...'
    return text.strip()


def _sanitize_paragraphs(paragraphs, max_per=500, max_count=6):
    """Sanitize a list of AI-generated paragraphs."""
    if not isinstance(paragraphs, list):
        return []
    result = []
    for p in paragraphs[:max_count]:
        clean = _sanitize_text(str(p), max_len=max_per)
        if clean:
            result.append(clean)
    return result


def _log_ai_content(action, block_type, contact=None, input_summary="",
                     output_summary="", success=True, error_msg=""):
    """Log AI content generation to ActionLedger for audit."""
    try:
        from database import ActionLedger, db
        db.connect(reuse_if_open=True)
        ActionLedger.create(
            contact=contact,
            email=getattr(contact, "email", "") if contact else "",
            trigger_type="ai_content",
            source_type="ai_content_%s" % action,
            status="sent" if success else "failed",
            reason_code="ai_%s_%s" % (action, block_type),
            reason_detail=json.dumps({
                "action": action,
                "block_type": block_type,
                "input": input_summary[:500],
                "output": output_summary[:500],
                "error": error_msg[:300],
                "model": AI_MODEL,
                "timestamp": datetime.now().isoformat(),
            }),
            created_at=datetime.now(),
        )
    except Exception as e:
        logger.warning("Failed to log AI content action: %s" % e)


def _log_ai_render(template_id=0, contact_id=None, block_index=0,
                   field_name="", generated_text="", fallback_used=False,
                   render_ms=0, model_name=AI_MODEL, error_summary=""):
    """Log to AIRenderLog table for telemetry and debugging."""
    try:
        from database import AIRenderLog, db
        db.connect(reuse_if_open=True)
        AIRenderLog.create(
            template_id=template_id,
            contact_id=contact_id,
            block_index=block_index,
            field_name=field_name,
            generated_text=str(generated_text)[:2000],
            fallback_used=fallback_used,
            render_ms=render_ms,
            model_name=model_name,
            error_summary=str(error_summary)[:500] if error_summary else "",
        )
    except Exception as e:
        logger.warning("Failed to log AIRenderLog: %s" % e)


# =========================================================================
#  AUTHORING-TIME: Generate block content with AI
# =========================================================================

def generate_block_content(block_type, contact=None, family=None,
                            fallback=None, purpose="", extra_context=""):
    """
    Generate AI content for a specific block type's writable fields.

    CONSTRAINTS:
      - Only writes to fields listed in AI_WRITABLE_FIELDS[block_type]
      - Output is sanitized (no HTML, length-capped)
      - Falls back to fallback dict if AI fails or is unavailable
      - Family constraints are checked before returning
      - Logged to ActionLedger

    Args:
        block_type:    one of BLOCK_TYPES keys (hero, text, cta, etc.)
        contact:       optional Contact for personalization context
        family:        template family key (welcome, cart_recovery, etc.)
        fallback:      dict of fallback content (REQUIRED — used if AI fails)
        purpose:       email purpose context (e.g. "welcome new subscriber")
        extra_context: additional context for the AI prompt

    Returns:
        dict: content fields for the block (only AI-writable fields)
    """
    if fallback is None:
        fallback = {}

    writable = AI_WRITABLE_FIELDS.get(block_type, [])
    if not writable:
        return dict(fallback)

    # Extract IDs for AIRenderLog
    _template_id = extra_context if isinstance(extra_context, int) else 0
    _contact_id = getattr(contact, "id", None) if contact else None
    _block_index = 0  # caller can set via extra_context or kwarg

    client = _get_client()
    if not client:
        _log_ai_content("generate", block_type, contact,
                         input_summary="no API key", success=False,
                         error_msg="ANTHROPIC_API_KEY not configured")
        # Log fallback to AIRenderLog for each writable field
        for field in writable:
            _log_ai_render(template_id=_template_id, contact_id=_contact_id,
                           block_index=_block_index, field_name=field,
                           generated_text=str(fallback.get(field, "")),
                           fallback_used=True, render_ms=0,
                           model_name=AI_MODEL, error_summary="no_api_key")
        return dict(fallback)

    # Build prompt
    customer_ctx = ""
    if contact:
        try:
            from condition_engine import get_contact_context
            ctx = get_contact_context(contact)
            customer_ctx = "Customer: %s, %s, %d orders, $%.0f spent, %s stage" % (
                getattr(contact, "first_name", "") or "Friend",
                ctx.get("source", "unknown"),
                ctx.get("total_orders", 0),
                ctx.get("total_spent", 0.0),
                ctx.get("lifecycle_stage", "unknown"),
            )
        except Exception:
            customer_ctx = "Customer: %s" % (getattr(contact, "first_name", "") or "subscriber")

    fields_desc = ", ".join(writable)
    prompt = "%s\n\nGenerate email content for a '%s' block.\n" % (BRAND_CONTEXT, block_type)
    if purpose:
        prompt += "Email purpose: %s\n" % purpose
    if family:
        prompt += "Template family: %s\n" % family
    if customer_ctx:
        prompt += "%s\n" % customer_ctx
    if extra_context and isinstance(extra_context, str):
        prompt += "Additional context: %s\n" % extra_context

    prompt += "\nReturn ONLY a JSON object with these fields: %s\n" % fields_desc
    prompt += "Rules:\n"
    prompt += "- Keep text SHORT and natural (no corporate jargon)\n"
    prompt += "- No HTML tags, no markdown, no URLs\n"
    prompt += "- Headlines under 120 chars, paragraphs under 500 chars each\n"
    prompt += "- If 'paragraphs' field: return a JSON array of 1-4 short strings\n"
    prompt += "- Return ONLY the JSON object, no other text\n"

    start_time = time.time()
    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        render_ms = int((time.time() - start_time) * 1000)
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        ai_output = json.loads(raw)

        # Build result: only AI-writable fields, sanitized
        result = dict(fallback)
        for field in writable:
            if field in ai_output:
                if field == "paragraphs":
                    result["paragraphs"] = _sanitize_paragraphs(
                        ai_output["paragraphs"],
                        max_per=FIELD_MAX_LENGTHS.get("paragraph", 500),
                    )
                else:
                    max_len = FIELD_MAX_LENGTHS.get(field, 200)
                    result[field] = _sanitize_text(ai_output[field], max_len=max_len)
                # Log each AI-generated field
                _log_ai_render(template_id=_template_id, contact_id=_contact_id,
                               block_index=_block_index, field_name=field,
                               generated_text=str(result.get(field, "")),
                               fallback_used=False, render_ms=render_ms,
                               model_name=AI_MODEL)

        _log_ai_content("generate", block_type, contact,
                         input_summary=purpose[:200],
                         output_summary=json.dumps(result)[:500])

        return result

    except json.JSONDecodeError as e:
        render_ms = int((time.time() - start_time) * 1000)
        _log_ai_content("generate", block_type, contact,
                         input_summary=purpose[:200], success=False,
                         error_msg="JSON parse error: %s" % str(e)[:200])
        for field in writable:
            _log_ai_render(template_id=_template_id, contact_id=_contact_id,
                           block_index=_block_index, field_name=field,
                           generated_text=str(fallback.get(field, "")),
                           fallback_used=True, render_ms=render_ms,
                           model_name=AI_MODEL, error_summary="json_parse_error")
        return dict(fallback)

    except Exception as e:
        render_ms = int((time.time() - start_time) * 1000)
        _log_ai_content("generate", block_type, contact,
                         input_summary=purpose[:200], success=False,
                         error_msg=str(e)[:300])
        for field in writable:
            _log_ai_render(template_id=_template_id, contact_id=_contact_id,
                           block_index=_block_index, field_name=field,
                           generated_text=str(fallback.get(field, "")),
                           fallback_used=True, render_ms=render_ms,
                           model_name=AI_MODEL, error_summary=str(e)[:200])
        return dict(fallback)


# =========================================================================
#  SEND-TIME: Lightweight field personalization
# =========================================================================

def personalize_text_field(field_name, template_text, contact=None,
                            fallback="", max_retries=1):
    """
    Lightweight send-time AI personalization for a single text field.
    Used for small tweaks like making a headline more personal.

    CONSTRAINTS:
      - Only processes plain text (no HTML)
      - Output is sanitized and length-capped
      - Falls back to fallback on any error
      - FAST: uses Haiku with low max_tokens
      - Logged to ActionLedger

    Args:
        field_name:    field being personalized (for length cap lookup)
        template_text: the template text (may contain {{first_name}} etc.)
        contact:       Contact instance for personalization
        fallback:      string to use if AI fails
        max_retries:   number of retries on failure (default 1)

    Returns:
        str: personalized text (sanitized)
    """
    if not template_text:
        return fallback or ""

    # First do basic token replacement (no AI needed for simple tokens)
    text = template_text
    if contact:
        text = text.replace("{{first_name}}", getattr(contact, "first_name", "") or "Friend")
        text = text.replace("{{last_name}}", getattr(contact, "last_name", "") or "")
        text = text.replace("{{email}}", getattr(contact, "email", "") or "")

    # If no AI markers remain, return the token-replaced text
    if "{{ai:" not in text:
        max_len = FIELD_MAX_LENGTHS.get(field_name, 500)
        return _sanitize_text(text, max_len=max_len)

    # AI personalization requested via {{ai:instruction}} markers
    client = _get_client()
    if not client:
        # Remove AI markers and return what we have
        cleaned = re.sub(r'\{\{ai:[^}]*\}\}', '', text).strip()
        return _sanitize_text(cleaned or fallback,
                              max_len=FIELD_MAX_LENGTHS.get(field_name, 500))

    # Extract AI instruction
    ai_match = re.search(r'\{\{ai:([^}]*)\}\}', text)
    if not ai_match:
        return _sanitize_text(text, max_len=FIELD_MAX_LENGTHS.get(field_name, 500))

    instruction = ai_match.group(1)
    customer_name = getattr(contact, "first_name", "") or "Friend" if contact else "Friend"

    prompt = "%s\n\n" % BRAND_CONTEXT
    prompt += "Rewrite this text for the customer named '%s': %s\n" % (customer_name, text)
    prompt += "AI instruction: %s\n" % instruction
    prompt += "Return ONLY the final text, no quotes, no explanation. Keep it short.\n"

    _contact_id = getattr(contact, "id", None) if contact else None

    for attempt in range(max_retries + 1):
        start_time = time.time()
        try:
            response = client.messages.create(
                model=AI_MODEL,
                max_tokens=150,  # Very short — single field
                messages=[{"role": "user", "content": prompt}]
            )
            render_ms = int((time.time() - start_time) * 1000)
            raw = response.content[0].text.strip()
            max_len = FIELD_MAX_LENGTHS.get(field_name, 500)
            result = _sanitize_text(raw, max_len=max_len)

            _log_ai_content("personalize", field_name, contact,
                             input_summary=template_text[:200],
                             output_summary=result[:200])
            _log_ai_render(contact_id=_contact_id, field_name=field_name,
                           generated_text=result, fallback_used=False,
                           render_ms=render_ms, model_name=AI_MODEL)
            return result

        except Exception as e:
            if attempt < max_retries:
                continue
            render_ms = int((time.time() - start_time) * 1000)
            _log_ai_content("personalize", field_name, contact,
                             input_summary=template_text[:200], success=False,
                             error_msg=str(e)[:300])
            # Fall back: remove AI markers
            cleaned = re.sub(r'\{\{ai:[^}]*\}\}', '', text).strip()
            fallback_result = _sanitize_text(cleaned or fallback,
                                             max_len=FIELD_MAX_LENGTHS.get(field_name, 500))
            _log_ai_render(contact_id=_contact_id, field_name=field_name,
                           generated_text=fallback_result, fallback_used=True,
                           render_ms=render_ms, model_name=AI_MODEL,
                           error_summary=str(e)[:200])
            return fallback_result


# =========================================================================
#  BATCH: Generate content for all blocks in a template
# =========================================================================

def generate_template_content(blocks, family=None, contact=None,
                                purpose="", fallback_blocks=None,
                                ai_enabled=True, block_ai_overrides=None):
    """
    Generate AI content for all AI-writable fields across a full template.
    Respects family constraints via enforce_family_constraints().

    CONSTRAINTS:
      - Does NOT modify block_type or block structure
      - Only fills AI-writable fields
      - Falls back to fallback_blocks on per-block failures
      - Returns the blocks list with content populated
      - Respects ai_enabled and block_ai_overrides rollout controls

    Args:
        blocks:              list of block dicts (parsed, not JSON)
        family:              template family key
        contact:             optional Contact
        purpose:             email purpose for AI context
        fallback_blocks:     list of block dicts with fallback content (same length)
        ai_enabled:          template-level AI on/off (default True for backward compat)
        block_ai_overrides:  dict of per-block AI overrides {"0": false, "2": true}

    Returns:
        list of block dicts with AI-generated content merged in
    """
    if fallback_blocks is None:
        fallback_blocks = blocks
    if block_ai_overrides is None:
        block_ai_overrides = {}

    # If AI is disabled at template level, return fallback immediately
    if not ai_enabled:
        return list(fallback_blocks)

    # Enforce family constraints BEFORE any AI generation
    if family:
        try:
            from condition_engine import enforce_family_constraints
            is_valid, errors = enforce_family_constraints(blocks, family)
            if not is_valid:
                _log_ai_content("batch_generate", "template", contact,
                                 input_summary="family=%s" % family, success=False,
                                 error_msg="Family constraint violation: %s" % "; ".join(errors))
                return list(fallback_blocks)
        except ImportError:
            pass

    result_blocks = []
    for i, block in enumerate(blocks):
        block_type = block.get("block_type", "")
        current_content = block.get("content", {})
        fb_content = fallback_blocks[i].get("content", {}) if i < len(fallback_blocks) else current_content

        writable = AI_WRITABLE_FIELDS.get(block_type, [])
        if not writable:
            result_blocks.append(dict(block))
            continue

        # Check per-block AI override (string keys from JSON)
        block_override = block_ai_overrides.get(str(i))
        if block_override is False:
            # AI explicitly disabled for this block
            result_blocks.append(dict(block))
            continue

        # Generate AI content for this block
        ai_content = generate_block_content(
            block_type=block_type,
            contact=contact,
            family=family,
            fallback=fb_content,
            purpose=purpose,
        )

        # Merge: keep non-writable fields from original, use AI for writable
        merged = dict(current_content)
        for field in writable:
            if field in ai_content and ai_content[field]:
                merged[field] = ai_content[field]

        new_block = dict(block)
        new_block["content"] = merged
        result_blocks.append(new_block)

    return result_blocks
