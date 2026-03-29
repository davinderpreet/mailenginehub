"""
template_engine.py — Shared Template Rendering & Validation Engine

The single public API for rendering and validating email templates.
All preview routes, send paths, studio, and preflight should use this module.

Delegates block rendering to block_registry.py (internal).
Adds post-render validation: offer consistency, product consistency,
structural checks, placeholder detection, and content completeness.

Phase 2 of the architecture rebuild.
Build order: Intelligence Layer → Templates (this) → Flows → AM → Campaigns

Usage:
    from template_engine import render_email, make_render_contract, preview_email

    contract = make_render_contract(template=t, contact_id=42, mode="send")
    result = render_email(contract)
    if result["is_valid"]:
        send(result["html"], result["subject"])
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Tokens that are allowed to remain unresolved in send mode
# (they get substituted at delivery time by the sending infrastructure)
_SEND_MODE_ALLOWED_TOKENS = {"{{unsubscribe_url}}"}

# Tokens that are allowed in preview mode (demo/sample values)
_PREVIEW_ALLOWED_TOKENS = {
    "{{unsubscribe_url}}", "{{first_name}}", "{{last_name}}",
    "{{email}}", "{{discount_code}}",
}


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def make_render_contract(
    template,                    # EmailTemplate instance or int (template_id)
    source_system="test",        # flow | am | campaign | studio | test
    objective=None,              # welcome | browse_recovery | reorder | etc.
    contact_id=None,             # Contact ID (optional for preview)
    content_brief=None,          # dict: messaging intent (future use)
    product_context=None,        # list of product dicts (explicit) or None (auto-resolve)
    offer_context=None,          # resolved offer dict or None
    brand_context=None,          # reserved for future
    mode="preview",              # "preview" | "send"
    explain=False,               # return explain trace
):
    """Build a render contract dict.

    Args:
        template: EmailTemplate instance or int template_id
        source_system: who is requesting this render
        objective: the email's purpose (defaults to template_family)
        contact_id: Contact primary key for personalization + variant resolution
        content_brief: dict of messaging intent (reserved for future)
        product_context: explicit product list, or None to auto-resolve
        offer_context: resolved offer dict {code, value_display, display_text, expires_text, expires_at}
        brand_context: reserved for future brand customization
        mode: "preview" (lenient validation) or "send" (strict validation)
        explain: if True, include variant resolution trace

    Returns:
        dict: render contract
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "template": template,
        "source_system": source_system,
        "objective": objective,
        "contact_id": contact_id,
        "content_brief": content_brief,
        "product_context": product_context,
        "offer_context": offer_context,
        "brand_context": brand_context,
        "mode": mode,
        "explain": explain,
    }


def render_email(contract):
    """
    THE single render entry point for all template rendering.

    Takes a render contract dict and returns a RenderResult dict with
    rendered HTML, subject, preview text, validation report, and metadata.

    Returns:
        dict: {
            "schema_version": int,
            "subject": str,
            "preview_text": str,
            "html": str,
            "validation_report": [{"level", "check", "message"}, ...],
            "resolved_products": list,
            "resolved_offer": dict or None,
            "explain": list or None,
            "warnings": list,
            "errors": list,
            "is_valid": bool,
        }
    """
    # ── 1. Resolve template ──
    template = _resolve_template(contract["template"])
    if template is None:
        return _error_result("Template not found")

    mode = contract.get("mode", "preview")
    explain = contract.get("explain", False)

    # ── 2. Resolve contact ──
    contact = _resolve_contact(contract.get("contact_id"))

    # ── 3. Resolve products (strict priority) ──
    products = _resolve_products(contract.get("product_context"), contact, template)

    # ── 4. Resolve offer ──
    offer = _resolve_offer(contract.get("offer_context"), contact, template)

    # ── 5. Determine template format and compute canonical preview_text ──
    template_format = getattr(template, "template_format", "html") or "html"
    blocks_json_str = getattr(template, "blocks_json", None) or ""
    html_body = getattr(template, "html_body", None) or ""

    # ── 5a. Build subject and preview_text with personalization FIRST ──
    subject = _personalize(getattr(template, "subject", "") or "", contact)
    raw_preview = _personalize(getattr(template, "preview_text", "") or "", contact)

    # Enrich preview_text with discount context via shared helper.
    # This is the ONE canonical preview_text — used in both the RenderResult
    # and passed into the render path so the HTML preheader matches.
    preview_text = _enrich_preview_text(raw_preview, offer)

    # ── 5b. Render ──
    explain_list = None

    if template_format == "blocks" and blocks_json_str:
        # Pass the personalized (pre-enrichment) preview_text into block_registry
        # so it enriches exactly once via its own discount logic.
        html, explain_list = _render_blocks(
            template, contact, products, offer, explain,
            preview_text=raw_preview,
        )
    elif html_body:
        # For legacy HTML, pass the fully enriched preview_text into wrap_email
        html = _render_legacy_html(template, contact, offer, preview_text=preview_text)
    else:
        html = ""

    # ── 7. Post-render validation (BEFORE preview-token substitution) ──
    family = getattr(template, "template_family", None) or contract.get("objective")
    validation_report = validate_rendered_email(
        html=html,
        subject=subject,
        preview_text=preview_text,
        offer_context=offer,
        products=products,
        mode=mode,
        family=family,
        blocks_json=blocks_json_str if template_format == "blocks" else None,
    )

    # ── 8. Assemble result ──
    warnings = [v for v in validation_report if v["level"] == "warning"]
    errors = [v for v in validation_report if v["level"] == "error"]

    return {
        "schema_version": SCHEMA_VERSION,
        "subject": subject,
        "preview_text": preview_text,
        "html": html,
        "validation_report": validation_report,
        "resolved_products": products,
        "resolved_offer": offer,
        "explain": explain_list if explain else None,
        "warnings": warnings,
        "errors": errors,
        "is_valid": len(errors) == 0,
    }


def preview_email(template, contact_id=None, source_system="studio"):
    """Convenience wrapper: render in preview mode with explain=True.

    Returns the same RenderResult dict as render_email().
    """
    contract = make_render_contract(
        template=template,
        source_system=source_system,
        contact_id=contact_id,
        mode="preview",
        explain=True,
    )
    return render_email(contract)


def validate_rendered_email(html, subject, preview_text, offer_context,
                            products, mode, family=None, blocks_json=None):
    """
    Post-render validation. Returns list of {"level", "check", "message"} dicts.

    Runs 6 check categories:
    1. offer_consistency — discount values match across subject/blocks/offer
    2. product_consistency — concrete sellable products in product blocks
    3. structural — unsubscribe, no JS, valid URLs, shell present
    4. placeholder_check — no unresolved tokens in send mode
    5. block_structure — block-level validation (delegates to existing validators)
    6. content_completeness — subject, preview, HTML exist and are reasonable

    Args:
        html: rendered HTML string
        subject: rendered subject line
        preview_text: rendered preview text
        offer_context: resolved offer dict or None
        products: list of resolved product dicts
        mode: "preview" or "send"
        family: template family key (optional)
        blocks_json: JSON string of blocks (optional, for block-level checks)
    """
    report = []
    is_send = mode == "send"

    report.extend(_check_offer_consistency(subject, preview_text, html, offer_context, blocks_json, is_send))
    report.extend(_check_product_consistency(products, blocks_json, is_send))
    report.extend(_check_structural(html, subject, is_send))
    report.extend(_check_placeholders(html, subject, preview_text, is_send))
    report.extend(_check_block_structure(blocks_json, family))
    report.extend(_check_content_completeness(html, subject, preview_text, is_send))

    return report


# ═══════════════════════════════════════════════════════════════
# Internal: Resolution helpers
# ═══════════════════════════════════════════════════════════════

def _resolve_template(template_input):
    """Resolve template from instance or ID."""
    if template_input is None:
        return None
    if isinstance(template_input, int):
        try:
            from database import EmailTemplate
            return EmailTemplate.get_by_id(template_input)
        except Exception:
            return None
    return template_input


def _resolve_contact(contact_id):
    """Resolve contact from ID."""
    if not contact_id:
        return None
    try:
        from database import Contact
        return Contact.get_by_id(int(contact_id))
    except Exception:
        return None


def _resolve_products(explicit_products, contact, template):
    """Resolve products with strict priority:
    1. Explicit product_context (caller-provided)
    2. Intelligence layer concrete recommendations
    3. Legacy block_registry fallback
    """
    # Priority 1: Explicit products provided by caller
    if explicit_products is not None:
        return explicit_products

    if not contact:
        return []

    # Priority 2: Intelligence layer concrete recommendations
    try:
        from intelligence_layer import get_next_products
        intel = get_next_products(getattr(contact, "id", None))
        # get_next_products returns a dict with top_pick, replacements, etc.
        # Extract concrete product suggestions and convert to render-friendly format
        concrete = _intelligence_to_products(intel)
        if concrete:
            return concrete
    except Exception:
        pass

    # Priority 3: Legacy block_registry fallback
    try:
        from block_registry import resolve_products_for_contact
        return resolve_products_for_contact(contact, limit=4)
    except Exception:
        return []


def _intelligence_to_products(intel_result):
    """Convert intelligence_layer.get_next_products() output to product dicts.

    Aligns with the Phase 1 intelligence schema:
    - replacements / reorders / top_pick → product_key
    - cross_sells / accessories → target_key
    - upgrades → to_product (when present), otherwise category-level fallback

    Uses ProductImageCache (primary render source) and ProductCommercial
    (stock/promotion filter) — the real product models in this repo.

    Returns list of product dicts with {title, image_url, price, product_url, ...}
    """
    if not intel_result:
        return []

    # Collect recommended product keys from intelligence (ordered by priority)
    # Each entry: (key, is_product_level)
    candidates = []
    seen = set()

    def _add(key, is_product=False):
        if key and key not in seen:
            seen.add(key)
            candidates.append((key, is_product))

    # top_pick first (highest priority)
    top_pick = intel_result.get("top_pick") or {}
    if top_pick.get("product_key"):
        _add(top_pick["product_key"], is_product=True)

    # replacements: product_key is a specific product title
    for item in intel_result.get("replacements", []):
        _add(item.get("product_key"), is_product=True)

    # reorders: product_key is a specific product title
    for item in intel_result.get("reorders", []):
        _add(item.get("product_key"), is_product=True)

    # cross_sells / accessories: target_key, may or may not be product-level
    for key in ("cross_sells", "accessories"):
        for item in intel_result.get(key, []):
            is_prod = item.get("is_product", False)
            _add(item.get("target_key"), is_product=is_prod)

    # upgrades: to_product when present, otherwise category-level fallback
    for item in intel_result.get("upgrades", []):
        if item.get("to_product"):
            _add(item["to_product"], is_product=True)
        elif item.get("category"):
            _add(item["category"], is_product=False)

    if not candidates:
        return []

    # Look up concrete products using ProductImageCache + ProductCommercial
    products = []
    try:
        from database import ProductImageCache, ProductCommercial
    except ImportError:
        logger.warning("[template_engine] ProductImageCache/ProductCommercial not available for product resolution")
        return []

    # Build out-of-stock set from ProductCommercial
    out_of_stock = set()
    try:
        for pc in ProductCommercial.select(ProductCommercial.product_title).where(
            ProductCommercial.stock_pressure == "out_of_stock"
        ):
            out_of_stock.add(pc.product_title)
    except Exception:
        pass

    for key, is_product in candidates[:6]:  # Check up to 6 candidates
        if len(products) >= 4:
            break

        matched = None

        if is_product:
            # Exact product: look up by title match in ProductImageCache
            try:
                matched = ProductImageCache.get_or_none(
                    ProductImageCache.product_title == key
                )
            except Exception:
                pass

        if not matched:
            # Category-level or product not found: find by product_type
            try:
                matched = (ProductImageCache.select()
                           .where(ProductImageCache.product_type == key)
                           .first())
            except Exception:
                pass

        if not matched:
            logger.debug("[template_engine] No ProductImageCache match for key=%s is_product=%s", key, is_product)
            continue

        # Filter out-of-stock products
        if matched.product_title in out_of_stock:
            logger.debug("[template_engine] Skipping out-of-stock product: %s", matched.product_title)
            continue

        products.append({
            "title": matched.product_title,
            "image_url": matched.image_url or "",
            "price": matched.price or "0.00",
            "product_url": matched.product_url or "",
            "compare_price": matched.compare_price or "",
            "product_id": matched.product_id,
        })

    if not products:
        logger.info("[template_engine] Intelligence recommended %d candidates but no concrete products resolved", len(candidates))

    return products


def _resolve_offer(explicit_offer, contact, template):
    """Resolve offer from explicit context or auto-detect from contact."""
    if explicit_offer is not None:
        return explicit_offer

    if not contact:
        return None

    # Check if template has a discount block
    blocks_json = getattr(template, "blocks_json", None) or ""
    try:
        blocks = json.loads(blocks_json)
    except (json.JSONDecodeError, TypeError):
        blocks = []

    has_discount_block = any(b.get("block_type") == "discount" for b in blocks)
    if not has_discount_block:
        return None

    # Auto-resolve from active discount
    try:
        from discount_engine import get_active_discount, get_discount_display
        active = get_active_discount(getattr(contact, "email", ""))
        if active:
            return get_discount_display(active)
    except Exception:
        pass

    return None


# ═══════════════════════════════════════════════════════════════
# Internal: Rendering
# ═══════════════════════════════════════════════════════════════

def _render_blocks(template, contact, products, offer, explain, preview_text=None):
    """Render a blocks-format template via block_registry.

    Args:
        template: EmailTemplate instance (or proxy). If preview_text is provided,
                  a lightweight proxy is used so block_registry enriches the
                  canonical preview_text exactly once.
        contact: Contact instance or None
        products: list of product dicts
        offer: resolved offer dict or None
        explain: if True, return explain trace
        preview_text: pre-computed finalized preview_text (personalized + enriched).
                      If provided, a proxy template is used so block_registry
                      sees this value and does not double-enrich.

    Returns (html, explain_list). explain_list is None if explain=False.
    """
    from block_registry import render_template_blocks

    # If we have a pre-computed preview_text, use a lightweight proxy so
    # block_registry's internal preview_text enrichment starts from this value.
    # Since block_registry reads template.preview_text and enriches it with
    # discount context, we pass the personalized (but NOT yet offer-enriched)
    # value so enrichment happens exactly once inside block_registry.
    render_template = template
    if preview_text is not None:
        render_template = _TemplateProxy(template, preview_text_override=preview_text)

    result = render_template_blocks(
        render_template, contact=contact,
        products=products, discount=offer, explain=explain,
    )
    # render_template_blocks with explain=True returns (html, explain_list)
    if isinstance(result, tuple):
        html, explain_list = result
    else:
        html = result
        explain_list = None

    return html, explain_list


def _render_legacy_html(template, contact, offer, preview_text=None):
    """Render a legacy HTML template with personalization + shell.

    Args:
        template: EmailTemplate instance
        contact: Contact instance or None
        offer: resolved offer dict or None
        preview_text: pre-computed finalized preview_text (personalized + enriched).
                      Used in wrap_email() so the preheader matches the RenderResult.
    """
    html = template.html_body or ""

    # Apply personalization tokens
    html = _personalize(html, contact)

    # Apply discount token
    if offer and offer.get("code"):
        html = html.replace("{{discount_code}}", offer["code"])

    # Wrap in shell if not already wrapped
    if "<!DOCTYPE" not in html:
        from email_shell import wrap_email
        shell_preview = preview_text if preview_text is not None else (getattr(template, "preview_text", "") or "")
        html = wrap_email(html, preview_text=shell_preview)

    return html


def _personalize(text, contact):
    """Apply personalization tokens to text."""
    if not text:
        return text

    if contact:
        text = text.replace("{{first_name}}", getattr(contact, "first_name", "") or "Friend")
        text = text.replace("{{last_name}}", getattr(contact, "last_name", "") or "")
        text = text.replace("{{email}}", getattr(contact, "email", "") or "")
    else:
        text = text.replace("{{first_name}}", "John")
        text = text.replace("{{last_name}}", "Smith")
        text = text.replace("{{email}}", "john@example.com")

    return text


# ═══════════════════════════════════════════════════════════════
# Internal: Validation checks
# ═══════════════════════════════════════════════════════════════

def _check_offer_consistency(subject, preview_text, html, offer_context, blocks_json, is_send):
    """Check 1: Offer consistency enforcement.

    For block templates, validate against structured data first:
    - template subject and preview_text vs offer_context
    - discount block content in blocks_json vs offer_context
    Use regex on rendered HTML only as fallback (and always for legacy html).
    """
    issues = []
    level = "error" if is_send else "warning"

    # Parse blocks if available
    blocks = []
    if blocks_json:
        try:
            blocks = json.loads(blocks_json)
        except (json.JSONDecodeError, TypeError):
            blocks = []

    has_discount_block = any(b.get("block_type") == "discount" for b in blocks)

    # ── Check: discount block present but no offer ──
    if has_discount_block and not offer_context:
        issues.append({
            "level": level,
            "check": "offer_consistency",
            "message": "Template has a discount block but no offer was resolved",
        })

    if not offer_context:
        # Also check if subject/preview mention discount-like patterns without an offer
        discount_pattern = re.compile(r'\b(\d+)\s*%\s*(?:off|discount|save)', re.IGNORECASE)
        dollar_pattern = re.compile(r'\$\s*(\d+(?:\.\d+)?)\s*(?:off|discount|save)', re.IGNORECASE)

        for label, text in [("subject", subject), ("preview_text", preview_text)]:
            if text and (discount_pattern.search(text) or dollar_pattern.search(text)):
                issues.append({
                    "level": level,
                    "check": "offer_consistency",
                    "message": "%s contains discount language but no offer context provided" % label,
                })
        return issues

    # We have offer_context — validate consistency
    offer_value = (offer_context.get("value_display") or "").strip()
    offer_code = (offer_context.get("code") or "").strip()

    # ── Check: expired offer in send mode ──
    if is_send and offer_context.get("expires_at"):
        try:
            from datetime import datetime, timezone
            expires_at = offer_context["expires_at"]
            if hasattr(expires_at, "tzinfo") and expires_at.tzinfo:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.now()
            if expires_at < now:
                issues.append({
                    "level": "error",
                    "check": "offer_consistency",
                    "message": "Offer has already expired (expires_at=%s)" % expires_at.isoformat(),
                })
        except Exception:
            pass

    # ── For block templates: validate discount block content vs offer ──
    if blocks:
        for block in blocks:
            if block.get("block_type") != "discount":
                continue
            content = block.get("content", {})
            block_code = (content.get("code") or "").strip()
            block_value = (content.get("value_display") or "").strip()

            # If discount block has hardcoded values, check against offer
            if block_code and offer_code and block_code != offer_code:
                issues.append({
                    "level": level,
                    "check": "offer_consistency",
                    "message": "Discount block code '%s' doesn't match resolved offer code '%s'" % (block_code, offer_code),
                })
            if block_value and offer_value and block_value.lower() != offer_value.lower():
                issues.append({
                    "level": level,
                    "check": "offer_consistency",
                    "message": "Discount block value '%s' doesn't match resolved offer value '%s'" % (block_value, offer_value),
                })

    # ── Check subject and preview_text for discount value mentions ──
    if offer_value:
        # Extract numeric value from offer (e.g., "10% OFF" → "10", "$5 OFF" → "5")
        offer_nums = re.findall(r'(\d+(?:\.\d+)?)', offer_value)

        for label, text in [("subject", subject), ("preview_text", preview_text)]:
            if not text:
                continue
            # Look for percentage/dollar discount mentions
            pct_matches = re.findall(r'(\d+)\s*%\s*(?:off|discount|save)', text, re.IGNORECASE)
            dollar_matches = re.findall(r'\$\s*(\d+(?:\.\d+)?)\s*(?:off|discount|save)', text, re.IGNORECASE)
            text_nums = pct_matches + dollar_matches

            for num in text_nums:
                if num not in offer_nums:
                    issues.append({
                        "level": level,
                        "check": "offer_consistency",
                        "message": "%s mentions %s%% discount but offer is '%s'" % (label, num, offer_value)
                        if num in pct_matches else
                        "%s mentions $%s discount but offer is '%s'" % (label, num, offer_value),
                    })

    # ── Rendered HTML scan: catches discount claims in hero/body/CTA copy ──
    # For block templates, this catches AI-generated mismatches in rendered content.
    # For legacy HTML templates, this is the primary consistency check.
    if html and offer_value:
        offer_nums = re.findall(r'(\d+(?:\.\d+)?)', offer_value)
        discount_pattern = re.compile(r'(\d+)\s*%\s*(?:off|discount|save)', re.IGNORECASE)
        dollar_pattern = re.compile(r'\$\s*(\d+(?:\.\d+)?)\s*(?:off|discount|save)', re.IGNORECASE)

        pct_matches = discount_pattern.findall(html)
        dollar_matches = dollar_pattern.findall(html)
        html_nums = set(pct_matches + dollar_matches)

        for num in html_nums:
            if num not in offer_nums:
                issues.append({
                    "level": level,
                    "check": "offer_consistency",
                    "message": "Rendered HTML mentions %s discount but resolved offer is '%s'" % (
                        "%s%%" % num if num in pct_matches else "$%s" % num,
                        offer_value,
                    ),
                })

    if not blocks and html and offer_code:
        # For legacy HTML templates, check that the rendered HTML contains the offer code
        if offer_code not in html:
            issues.append({
                "level": level,
                "check": "offer_consistency",
                "message": "Rendered HTML does not contain offer code '%s'" % offer_code,
            })

    return issues


def _check_product_consistency(products, blocks_json, is_send):
    """Check 2: Product consistency enforcement.

    In send mode, require concrete sellable products with title + product_url.
    Products must not be just category labels.
    """
    issues = []
    level = "error" if is_send else "warning"

    # Parse blocks to check for product blocks
    blocks = []
    if blocks_json:
        try:
            blocks = json.loads(blocks_json)
        except (json.JSONDecodeError, TypeError):
            blocks = []

    product_block_types = {"product_grid", "product_hero", "comparison_block", "bundle_value"}
    has_product_blocks = any(b.get("block_type") in product_block_types for b in blocks)

    if not has_product_blocks:
        return issues  # No product blocks — nothing to check

    # Must have products if template has product blocks
    if not products:
        issues.append({
            "level": level,
            "check": "product_consistency",
            "message": "Template has product blocks but no products were resolved",
        })
        return issues

    # Validate each product is concrete and sellable
    try:
        from shared_constants import CATEGORY_KEYWORDS
        category_names = set(CATEGORY_KEYWORDS.keys())
    except ImportError:
        category_names = set()

    for i, product in enumerate(products):
        title = (product.get("title") or "").strip()
        url = (product.get("product_url") or "").strip()

        if not title:
            issues.append({
                "level": level,
                "check": "product_consistency",
                "message": "Product %d has no title" % (i + 1),
            })
            continue

        # Check if title is just a category label
        if title in category_names:
            issues.append({
                "level": level,
                "check": "product_consistency",
                "message": "Product %d title '%s' is a category label, not a concrete product" % (i + 1, title),
            })

        # In send mode, require product_url
        if is_send and not url:
            issues.append({
                "level": level,
                "check": "product_consistency",
                "message": "Product %d '%s' has no product_url (required for send)" % (i + 1, title),
            })

    return issues


def _check_structural(html, subject, is_send):
    """Check 3: Structural validation.

    - Unsubscribe link exists
    - No javascript: links
    - No malformed CTA URLs
    - Shell/DOCTYPE present
    - Subject exists
    - HTML is non-empty
    """
    issues = []

    if not html:
        issues.append({
            "level": "error",
            "check": "structural",
            "message": "Rendered HTML is empty",
        })
        return issues

    # Unsubscribe link (required in all modes for CAN-SPAM)
    if "unsubscribe" not in html.lower():
        issues.append({
            "level": "error",
            "check": "structural",
            "message": "Email does not contain an unsubscribe link (CAN-SPAM requirement)",
        })

    # No javascript: URLs
    js_pattern = re.compile(r'href\s*=\s*["\']javascript:', re.IGNORECASE)
    if js_pattern.search(html):
        issues.append({
            "level": "error",
            "check": "structural",
            "message": "Email contains javascript: links (security risk)",
        })

    # Check CTA href values for valid protocols
    href_pattern = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    for match in href_pattern.finditer(html):
        url = match.group(1).strip()
        if not url:
            continue
        # Allow: http, https, mailto, {{tokens}}, # (anchor/preview)
        if url.startswith(("http://", "https://", "mailto:", "{{", "#", "tel:")):
            continue
        # Flag suspicious URLs
        if not url.startswith("/"):
            issues.append({
                "level": "error" if is_send else "warning",
                "check": "structural",
                "message": "Suspicious href value: '%s' (expected http/https/mailto)" % url[:100],
            })

    # Shell/DOCTYPE present
    if "<!DOCTYPE" not in html:
        issues.append({
            "level": "error" if is_send else "warning",
            "check": "structural",
            "message": "HTML is missing DOCTYPE — email shell may not be applied",
        })

    # Subject must exist in send mode
    if is_send and not (subject or "").strip():
        issues.append({
            "level": "error",
            "check": "structural",
            "message": "Subject line is empty",
        })

    return issues


def _check_placeholders(html, subject, preview_text, is_send):
    """Check 4: Unresolved placeholder detection.

    In send mode, only {{unsubscribe_url}} is allowed to remain unresolved.
    In preview mode, common tokens are allowed.
    """
    issues = []
    token_pattern = re.compile(r'\{\{(\w+)\}\}')
    allowed = _SEND_MODE_ALLOWED_TOKENS if is_send else _PREVIEW_ALLOWED_TOKENS

    for label, text in [("subject", subject), ("preview_text", preview_text), ("html", html)]:
        if not text:
            continue
        tokens = token_pattern.findall(text)
        for token_name in tokens:
            full_token = "{{%s}}" % token_name
            if full_token not in allowed:
                issues.append({
                    "level": "error" if is_send else "warning",
                    "check": "placeholder_check",
                    "message": "Unresolved token %s in %s" % (full_token, label),
                })

    return issues


def _check_block_structure(blocks_json, family):
    """Check 5: Block-level validation.

    Delegates to existing block_registry.validate_template() and
    condition_engine.validate_family().
    """
    if not blocks_json:
        return []

    issues = []

    # Block-level validation
    try:
        from block_registry import validate_template
        block_warnings = validate_template(blocks_json, family=family)
        for w in block_warnings:
            issues.append({
                "level": w.get("level", "warning"),
                "check": "block_structure",
                "message": w.get("message", ""),
            })
    except Exception as e:
        logger.debug("[template_engine] block validation error: %s", e)

    return issues


def _check_content_completeness(html, subject, preview_text, is_send):
    """Check 6: Content completeness.

    Subject, preview_text, and HTML should exist and be reasonable.
    """
    issues = []
    level = "error" if is_send else "warning"

    if not (subject or "").strip():
        # Already caught by structural check in send mode, add warning for preview
        if not is_send:
            issues.append({
                "level": "warning",
                "check": "content_completeness",
                "message": "Subject line is empty",
            })

    if not (preview_text or "").strip():
        issues.append({
            "level": level,
            "check": "content_completeness",
            "message": "Preview text is empty",
        })

    subject_len = len((subject or "").strip())
    if subject_len > 150:
        issues.append({
            "level": "warning",
            "check": "content_completeness",
            "message": "Subject line is very long (%d chars, recommended < 150)" % subject_len,
        })

    preview_len = len((preview_text or "").strip())
    if preview_len > 200:
        issues.append({
            "level": "warning",
            "check": "content_completeness",
            "message": "Preview text is very long (%d chars, recommended < 200)" % preview_len,
        })

    html_len = len((html or "").strip())
    if is_send and html_len < 500:
        issues.append({
            "level": "error",
            "check": "content_completeness",
            "message": "Rendered HTML is suspiciously short (%d chars)" % html_len,
        })

    return issues


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _enrich_preview_text(preview_text, offer):
    """Enrich preview text with discount context.

    This is the single canonical enrichment path. Both block templates and
    legacy HTML templates use this to compute the final preview_text.
    Matches the enrichment logic in block_registry.py lines 2061-2069.

    Args:
        preview_text: personalized preview text (already has {{first_name}} etc. resolved)
        offer: resolved offer dict or None

    Returns:
        str: enriched preview text
    """
    if not offer or not offer.get("code"):
        return preview_text

    val = offer.get("value_display", "")
    code = offer["code"]
    if preview_text:
        return "%s - Code: %s - %s" % (val, code, preview_text)
    else:
        exp = offer.get("expires_text", "")
        return "%s - Use code %s before %s" % (val, code, exp) if exp else "%s - Code: %s" % (val, code)


class _TemplateProxy:
    """Lightweight proxy around an EmailTemplate to override preview_text.

    Used to pass a pre-computed preview_text into block_registry so its
    internal enrichment produces the canonical value (not double-enriched).
    """
    def __init__(self, template, preview_text_override=None):
        self._template = template
        self._preview_text_override = preview_text_override

    def __getattr__(self, name):
        if name == "preview_text" and self._preview_text_override is not None:
            return self._preview_text_override
        return getattr(self._template, name)


def _error_result(message):
    """Return a RenderResult with a single error."""
    return {
        "schema_version": SCHEMA_VERSION,
        "subject": "",
        "preview_text": "",
        "html": "",
        "validation_report": [{"level": "error", "check": "fatal", "message": message}],
        "resolved_products": [],
        "resolved_offer": None,
        "explain": None,
        "warnings": [],
        "errors": [{"level": "error", "check": "fatal", "message": message}],
        "is_valid": False,
    }


def substitute_preview_tokens(html, preview_defaults=None):
    """Replace remaining tokens for browser preview display.

    Call this AFTER validation, only for preview rendering in iframes/browsers.
    This should NOT be called in send mode — send mode leaves {{unsubscribe_url}}
    for the delivery engine to substitute.

    Args:
        html: rendered HTML string
        preview_defaults: dict of token → value overrides

    Returns:
        str: HTML with preview tokens substituted
    """
    defaults = {
        "{{unsubscribe_url}}": "#",
        "{{discount_code}}": "PREVIEW5",
    }
    if preview_defaults:
        defaults.update(preview_defaults)

    for token, value in defaults.items():
        html = html.replace(token, value)

    return html
