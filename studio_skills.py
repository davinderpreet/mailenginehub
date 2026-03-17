"""
studio_skills.py — 6 Composable AI Skills for Email Studio

Each skill is a function that takes a context dict and an AIProvider,
calls the AI (or performs validation), and returns the updated context.

Skills:
    1. select_block_sequence  — pick which blocks to include
    2. compose_hero           — write hero block content
    3. compose_text           — write text block content
    4. compose_generic_block  — write content for any block type
    5. compose_subject_line   — write subject + preview text
    6. validate_and_fix       — pure-Python validation pass (no AI)

Helpers:
    _parse_json_response     — strip markdown fences, parse JSON
    _build_knowledge_summary — format knowledge entries for prompts
"""

import json
from ai_provider import AIProvider
from block_registry import BLOCK_TYPES
from condition_engine import TEMPLATE_FAMILIES


# =========================================================================
#  System prompt — shared across all AI-facing skills
# =========================================================================

_SYSTEM_PROMPT = (
    "You are an expert email designer and copywriter for LDAS Electronics, "
    "a Canadian electronics brand (https://ldas.ca) specializing in dash cams, "
    "headsets, and driver accessories. Brand color: #063cff (blue). "
    "CRITICAL RULES:\n"
    "1. Be EXTREMELY concise. Emails are scanned, not read. Short punchy copy ONLY.\n"
    "2. Every sentence must be under 15 words. No filler. No fluff.\n"
    "3. Paragraphs: 1-2 sentences MAX. Never write walls of text.\n"
    "4. Features: 5-8 words each, not full sentences.\n"
    "5. Think billboard copy, not blog post.\n"
    "You ALWAYS respond with valid JSON only — no markdown, no explanation, "
    "no extra text outside the JSON object."
)


# =========================================================================
#  Helpers
# =========================================================================

def _parse_json_response(text: str):
    """
    Extract and parse JSON from an AI response.

    Handles:
    - Pure JSON responses
    - Markdown fenced JSON (```json ... ```)
    - Reasoning-model responses where JSON is embedded in thinking text

    Returns:
        dict or list

    Raises:
        ValueError on parse failure
    """
    if text is None:
        raise ValueError("AI returned empty/null response — model may not support this prompt format")
    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()
            cleaned = cleaned[:-3].rstrip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Reasoning models: try to find JSON embedded in text
    # Look for last JSON object or array in the response
    import re

    # Try to find ```json ... ``` blocks anywhere in text
    fence_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', cleaned)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find the last JSON object {...} or array [...] in the text
    # Search from end backwards for the most complete JSON
    for pattern in [
        r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})',  # nested objects
        r'(\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\])',  # nested arrays
    ]:
        matches = list(re.finditer(pattern, cleaned))
        for m in reversed(matches):
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

    raise ValueError(
        "Failed to parse AI response as JSON.\nResponse was: %s"
        % text[:500]
    )


def _build_knowledge_summary(knowledge: list, block_type: str = "") -> str:
    """
    Format knowledge entries into a prompt-ready string.

    Filters by relevance to block_type when provided:
      - testimonial blocks  → testimonial entries
      - product blocks      → product entries
      - faq blocks          → faq entries
      - otherwise           → all entries

    Truncates to ~2000 chars to fit in prompts.
    """
    if not knowledge:
        return "(No knowledge context available.)"

    # Map block types to relevant knowledge categories
    _PRODUCT_BLOCKS = {
        "product_grid", "product_hero", "bundle_value",
        "comparison", "comparison_block", "spec_table",
    }
    _TESTIMONIAL_BLOCKS = {"driver_testimonial"}
    _FAQ_BLOCKS = {"faq"}

    filtered = knowledge
    if block_type:
        if block_type in _PRODUCT_BLOCKS:
            filtered = [
                k for k in knowledge
                if k.get("type", "").lower() in ("product_catalog", "brand_copy")
            ] or knowledge
        elif block_type in _TESTIMONIAL_BLOCKS:
            filtered = [
                k for k in knowledge
                if k.get("type", "").lower() in ("testimonial", "brand_copy")
            ] or knowledge
        elif block_type in _FAQ_BLOCKS:
            filtered = [
                k for k in knowledge
                if k.get("type", "").lower() in ("faq", "brand_copy")
            ] or knowledge

    lines = []
    total_chars = 0
    for entry in filtered:
        title = entry.get("title", entry.get("key", ""))
        body = entry.get("content", entry.get("value", ""))
        line = "- %s: %s" % (title, body) if title else "- %s" % body
        if total_chars + len(line) > 3000:
            lines.append("- ... (truncated)")
            break
        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines) if lines else "(No relevant knowledge found.)"


# =========================================================================
#  Skill 1: select_block_sequence
# =========================================================================

def select_block_sequence(context: dict, provider: AIProvider) -> dict:
    """
    Ask the AI to choose which blocks to include and in what order.

    Reads family config from TEMPLATE_FAMILIES, prompts the AI, validates
    the response against the family's allowed/required blocks.
    """
    family_key = context.get("family", "welcome")
    family = TEMPLATE_FAMILIES.get(family_key)
    if not family:
        raise ValueError("Unknown template family: %s" % family_key)

    allowed = family["allowed_blocks"]
    recommended = family["recommended_order"]
    required = family["required_blocks"]
    max_blocks = family["max_blocks"]

    knowledge_summary = _build_knowledge_summary(
        context.get("knowledge", [])
    )

    user_prompt = json.dumps({
        "task": "select_block_sequence",
        "journey_type": family_key,
        "journey_label": family.get("label", family_key),
        "available_blocks": allowed,
        "recommended_order": recommended,
        "required_blocks": required,
        "max_blocks": max_blocks,
        "product_focus": context.get("product_focus", ""),
        "tone": context.get("tone", ""),
        "knowledge_context": knowledge_summary,
        "instructions": (
            "Select 4-6 blocks in order for this email. LESS IS MORE. "
            "A great email has 4-5 blocks, not 8. Keep it scannable. "
            "Every block must be from the available_blocks list. "
            "All required_blocks must be included. "
            "Do not exceed max_blocks. "
            "Respond with a JSON array of block type strings, e.g. "
            '[\"hero\", \"text\", \"cta\"]'
        ),
    }, indent=2)

    raw = provider.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=512)
    sequence = _parse_json_response(raw)

    # Validate
    if not isinstance(sequence, list):
        raise ValueError("AI returned non-list for block sequence: %s" % type(sequence))

    allowed_set = set(allowed)
    errors = []
    for bt in sequence:
        if bt not in allowed_set:
            errors.append("Block '%s' is not allowed in %s family" % (bt, family_key))
    for req in required:
        if req not in sequence:
            errors.append("Required block '%s' is missing" % req)
    if len(sequence) > max_blocks:
        errors.append("Sequence has %d blocks, max is %d" % (len(sequence), max_blocks))

    # Retry once with error feedback
    if errors:
        retry_prompt = json.dumps({
            "task": "select_block_sequence",
            "retry": True,
            "previous_errors": errors,
            "journey_type": family_key,
            "available_blocks": allowed,
            "required_blocks": required,
            "max_blocks": max_blocks,
            "instructions": (
                "Your previous selection had errors. Fix them and respond "
                "with a corrected JSON array of block type strings."
            ),
        }, indent=2)

        raw = provider.complete(_SYSTEM_PROMPT, retry_prompt, max_tokens=512)
        sequence = _parse_json_response(raw)

        if not isinstance(sequence, list):
            raise ValueError("AI retry returned non-list for block sequence")

        # Final validation — raise on failure
        final_errors = []
        for bt in sequence:
            if bt not in allowed_set:
                final_errors.append("Block '%s' is not allowed" % bt)
        for req in required:
            if req not in sequence:
                final_errors.append("Required block '%s' is missing" % req)
        if len(sequence) > max_blocks:
            final_errors.append("Exceeds max_blocks (%d > %d)" % (len(sequence), max_blocks))
        if final_errors:
            raise ValueError(
                "Block sequence still invalid after retry: %s"
                % "; ".join(final_errors)
            )

    context["block_sequence"] = sequence
    context["reasoning"] = context.get("reasoning", "") + (
        "\n[select_block_sequence] Chose %d blocks for %s: %s"
        % (len(sequence), family_key, ", ".join(sequence))
    )
    return context


# =========================================================================
#  Skill 2: compose_hero
# =========================================================================

def compose_hero(context: dict, provider: AIProvider) -> dict:
    """
    Ask the AI to write hero block content (headline + subheadline).
    """
    family_key = context.get("family", "welcome")
    tone = context.get("tone", "confident")
    knowledge_summary = _build_knowledge_summary(
        context.get("knowledge", []), block_type="hero"
    )

    user_prompt = json.dumps({
        "task": "compose_hero",
        "journey_type": family_key,
        "tone": tone,
        "product_focus": context.get("product_focus", ""),
        "brand": "LDAS Electronics — Canadian electronics for drivers (dash cams, headsets, accessories). https://ldas.ca",
        "knowledge": knowledge_summary,
        "instructions": (
            "Write a hero block for a %s email. "
            "Headline: 3-6 words ONLY. Punchy, bold, direct. Like a billboard. "
            "Subheadline: max 10 words. One clear benefit. "
            "Tone: %s. "
            'Respond with JSON: {"headline": "...", "subheadline": "..."}'
            % (family_key, tone)
        ),
    }, indent=2)

    raw = provider.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=256)
    data = _parse_json_response(raw)

    if not isinstance(data, dict) or "headline" not in data:
        raise ValueError("AI hero response missing 'headline': %s" % raw[:300])

    block = {
        "block_type": "hero",
        "content": {
            "headline": str(data["headline"]),
            "subheadline": str(data.get("subheadline", "")),
        },
    }
    context.setdefault("blocks", []).append(block)
    return context


# =========================================================================
#  Skill 3: compose_text
# =========================================================================

def compose_text(context: dict, provider: AIProvider) -> dict:
    """
    Ask the AI to write a text block (1-2 paragraphs).
    """
    family_key = context.get("family", "welcome")
    tone = context.get("tone", "confident")
    knowledge_summary = _build_knowledge_summary(
        context.get("knowledge", []), block_type="text"
    )

    user_prompt = json.dumps({
        "task": "compose_text",
        "journey_type": family_key,
        "tone": tone,
        "product_focus": context.get("product_focus", ""),
        "brand": "LDAS Electronics — Canadian electronics for drivers. https://ldas.ca",
        "knowledge": knowledge_summary,
        "existing_blocks": [b["block_type"] for b in context.get("blocks", [])],
        "instructions": (
            "Write a text block for a %s email. "
            "CRITICAL: Keep it SHORT. 1-2 sentences MAX per paragraph. "
            "Max 2 paragraphs. Each sentence under 15 words. "
            "No filler, no fluff, no long intros. Get to the point fast. "
            "Think billboard, not blog. Tone: %s. "
            'Respond with JSON: {"paragraphs": ["...", "..."]}'
            % (family_key, tone)
        ),
    }, indent=2)

    raw = provider.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=256)
    data = _parse_json_response(raw)

    if not isinstance(data, dict) or "paragraphs" not in data:
        raise ValueError("AI text response missing 'paragraphs': %s" % raw[:300])

    content = {"paragraphs": data["paragraphs"]}
    if data.get("section_header"):
        content["section_header"] = str(data["section_header"])

    block = {"block_type": "text", "content": content}
    context.setdefault("blocks", []).append(block)
    return context


# =========================================================================
#  Skill 4: compose_generic_block
# =========================================================================

def compose_generic_block(block_type: str, context: dict, provider: AIProvider) -> dict:
    """
    Ask the AI to generate content for any block type using its registry definition.
    """
    type_def = BLOCK_TYPES.get(block_type)
    if not type_def:
        raise ValueError("Unknown block type: %s" % block_type)

    required = type_def["required"]
    optional = type_def["optional"]
    defaults = type_def["defaults"]

    # Choose relevant knowledge based on block type
    _PRODUCT_BLOCKS = {
        "product_grid", "product_hero", "bundle_value",
        "comparison", "comparison_block", "spec_table",
    }
    _TESTIMONIAL_BLOCKS = {"driver_testimonial"}
    _FAQ_BLOCKS = {"faq"}

    knowledge_summary = _build_knowledge_summary(
        context.get("knowledge", []), block_type=block_type
    )

    # Build format-specific instructions to match renderer expectations
    extra_hints = ""
    format_hint = ""

    if block_type == "product_grid":
        # product_grid renders from real product data at send time — just need section title
        format_hint = (
            ' CRITICAL: product_grid only needs "section_title" (short label) and "columns" (2). '
            'Products are injected at send time. DO NOT generate product objects. '
            'Example: {"section_title": "Featured Headsets", "columns": 2}. '
            'Response must be under 50 characters total.'
        )
    elif block_type == "features_benefits":
        format_hint = (
            ' CRITICAL FORMAT: "items" must be a list of SHORT strings (5-8 words each). '
            'NOT objects/dicts. Example: {"section_title": "Why LDAS", "items": ["Crystal-clear noise-cancelling audio", "96-hour battery life", "Built for Canadian roads"]}. '
            'Max 5 items. Each item is a short phrase, NOT a sentence.'
        )
    elif block_type == "product_hero":
        product = context.get("product_focus", "")
        # Try to find real image URL and product URL from knowledge base
        real_image_url = "https://ldas.ca/placeholder.jpg"
        real_product_url = "https://ldas.ca"
        for k in context.get("knowledge", []):
            if k.get("type") == "product_catalog" and product and product.lower() in k.get("title", "").lower():
                meta = k.get("metadata", {})
                imgs = meta.get("image_urls", [])
                if imgs and imgs[0]:
                    img = imgs[0]
                    # Fix protocol-relative URLs
                    if img.startswith("//"):
                        img = "https:" + img
                    real_image_url = img
                src_url = meta.get("source_url", "")
                if src_url and "ldas.ca" in src_url:
                    real_product_url = src_url
                break
        format_hint = (
            ' CRITICAL FORMAT: Must include "title" (product name), "image_url" (use "%s"), '
            '"price" (realistic price from knowledge base), "product_url" (use "%s"), '
            '"short_description" (1 sentence, max 12 words). '
            'Example: {"title": "LDAS G10 Bluetooth Headset", "image_url": "%s", '
            '"price": "65.99", "product_url": "%s", "short_description": "40-hour battery, dual-mic noise cancellation for drivers."}'
        ) % (real_image_url, real_product_url, real_image_url, real_product_url)
        if product:
            extra_hints = " Focus on product: %s." % product
    elif block_type == "trust_reassurance":
        format_hint = (
            ' CRITICAL FORMAT: "items" must be a list of objects with "icon" and "text" fields. '
            'Icons: "package", "shield", "star", "maple", "clock", "truck". '
            'Text: 4-6 words each. Max 4 items. '
            'Example: {"items": [{"icon": "package", "text": "Free Shipping on $50+"}, {"icon": "shield", "text": "30-Day Easy Returns"}]}'
        )
    elif block_type == "driver_testimonial":
        format_hint = (
            ' CRITICAL FORMAT: Need "quote" (1-2 short sentences from a driver), '
            '"name" (realistic name), "role" (e.g. "Long-haul trucker, Ontario"). '
            'Keep the quote punchy and real-sounding, under 20 words.'
        )
        extra_hints = " Write a realistic customer testimonial for a driver electronics product."
    elif block_type in _FAQ_BLOCKS:
        format_hint = (
            ' CRITICAL FORMAT: "items" must be list of objects with "question" and "answer" fields. '
            'Max 3 items. Answers: 1 sentence each, under 15 words.'
        )
        extra_hints = " Common customer questions about driver electronics."
    elif block_type in _PRODUCT_BLOCKS:
        product = context.get("product_focus", "")
        if product:
            extra_hints = " Focus on product: %s." % product

    user_prompt = json.dumps({
        "task": "compose_block",
        "block_type": block_type,
        "block_label": type_def["label"],
        "journey_type": context.get("family", "welcome"),
        "tone": context.get("tone", "confident"),
        "product_focus": context.get("product_focus", ""),
        "required_fields": required,
        "optional_fields": optional,
        "default_values": defaults,
        "knowledge": knowledge_summary,
        "instructions": (
            "Generate content for a %s block (%s). "
            "KEEP ALL COPY SHORT. No filler. No fluff. "
            "Required fields: %s. Optional fields: %s.%s%s "
            "Respond with a JSON object containing the block content fields."
            % (
                block_type,
                type_def["label"],
                json.dumps(required),
                json.dumps(optional),
                format_hint,
                extra_hints,
            )
        ),
    }, indent=2)

    raw = provider.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=512)
    data = _parse_json_response(raw)

    if not isinstance(data, dict):
        raise ValueError("AI returned non-dict for %s block: %s" % (block_type, raw[:300]))

    # Validate required fields are present
    for field in required:
        if field not in data or data[field] is None or data[field] == "" or data[field] == []:
            # Fill from defaults if available
            if field in defaults:
                data[field] = defaults[field]
            else:
                raise ValueError(
                    "AI response for %s block missing required field '%s'"
                    % (block_type, field)
                )

    # Fill missing or empty optional fields from defaults
    for field in optional:
        val = data.get(field)
        if (val is None or val == "" or val == []) and field in defaults:
            data[field] = defaults[field]

    block = {"block_type": block_type, "content": data}
    context.setdefault("blocks", []).append(block)
    return context


# =========================================================================
#  Skill 5: compose_subject_line
# =========================================================================

def compose_subject_line(context: dict, provider: AIProvider) -> dict:
    """
    Ask the AI to write the email subject line and preview text.
    """
    family_key = context.get("family", "welcome")
    tone = context.get("tone", "confident")
    block_types = [b["block_type"] for b in context.get("blocks", [])]

    user_prompt = json.dumps({
        "task": "compose_subject_line",
        "journey_type": family_key,
        "tone": tone,
        "product_focus": context.get("product_focus", ""),
        "blocks_included": block_types,
        "brand": "LDAS Electronics — Canadian electronics for drivers. https://ldas.ca",
        "instructions": (
            "Write a subject line (max 50 characters) and preview text "
            "(max 90 characters) for a %s email. "
            "The email contains these blocks: %s. "
            "Tone: %s. "
            "Make the subject line attention-grabbing but not spammy. "
            'Respond with JSON: {"subject": "...", "preview_text": "..."}'
            % (family_key, ", ".join(block_types), tone)
        ),
    }, indent=2)

    raw = provider.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=256)
    data = _parse_json_response(raw)

    if not isinstance(data, dict):
        raise ValueError("AI subject line response is not a dict: %s" % raw[:300])

    subject = str(data.get("subject", ""))
    preview = str(data.get("preview_text", ""))

    if not subject:
        raise ValueError("AI returned empty subject line")

    context["subject"] = subject
    context["preview_text"] = preview
    return context


# =========================================================================
#  Skill 6: validate_and_fix
# =========================================================================

def validate_and_fix(context: dict, provider: AIProvider) -> dict:
    """
    Pure-Python validation — does NOT call the AI provider.

    Runs block_registry.validate_template and condition_engine.enforce_family_constraints,
    then attempts auto-fix for common issues before raising on hard failures.
    """
    from block_registry import validate_template
    from condition_engine import enforce_family_constraints

    family_key = context.get("family", "welcome")
    blocks = context.get("blocks", [])

    family = TEMPLATE_FAMILIES.get(family_key)
    if not family:
        raise ValueError("Unknown template family: %s" % family_key)

    allowed_set = set(family["allowed_blocks"])
    required_set = set(family["required_blocks"])
    max_blocks = family["max_blocks"]

    # --- Run validators ---
    validation_warnings = validate_template(json.dumps(blocks), family_key)
    is_valid, constraint_errors = enforce_family_constraints(blocks, family_key)

    has_errors = (
        any(w["level"] == "error" for w in validation_warnings)
        or not is_valid
    )

    if has_errors:
        # --- Auto-fix pass ---
        fixed_blocks = list(blocks)

        # 1. Remove blocks not in allowed_blocks
        fixed_blocks = [
            b for b in fixed_blocks
            if b.get("block_type", "") in allowed_set
        ]

        # 2. Add missing required blocks with defaults
        present_types = {b.get("block_type", "") for b in fixed_blocks}
        for req in required_set:
            if req not in present_types:
                type_def = BLOCK_TYPES.get(req)
                if type_def:
                    fixed_blocks.append({
                        "block_type": req,
                        "content": dict(type_def.get("defaults", {})),
                    })

        # 3. Trim to max_blocks (keep required blocks, trim from end)
        if len(fixed_blocks) > max_blocks:
            # Partition into required and non-required
            required_blocks = [
                b for b in fixed_blocks
                if b.get("block_type", "") in required_set
            ]
            non_required = [
                b for b in fixed_blocks
                if b.get("block_type", "") not in required_set
            ]
            # Keep all required, trim non-required
            remaining = max_blocks - len(required_blocks)
            if remaining < 0:
                remaining = 0
            fixed_blocks = required_blocks + non_required[:remaining]

        # Re-validate after fix
        is_valid_now, final_errors = enforce_family_constraints(fixed_blocks, family_key)
        if not is_valid_now:
            raise ValueError(
                "Template still invalid after auto-fix for %s family: %s"
                % (family_key, "; ".join(final_errors))
            )

        context["blocks"] = fixed_blocks

    # --- Build validation report ---
    error_count = sum(1 for w in validation_warnings if w["level"] == "error")
    warning_count = sum(1 for w in validation_warnings if w["level"] == "warning")
    report = (
        "\n[validate_and_fix] Family: %s | %d errors, %d warnings."
        % (family_key, error_count, warning_count)
    )
    if has_errors:
        report += " Auto-fix applied."
    if constraint_errors:
        report += " Original constraint issues: %s" % "; ".join(constraint_errors)
    report += " Final block count: %d." % len(context["blocks"])

    context["reasoning"] = context.get("reasoning", "") + report
    return context
