"""
block_registry.py -- Block-Based Email Template Rendering Engine

Phase 1 of the unified template system. Provides 7 reusable email block
types with tested HTML renderers. Templates store an ordered list of blocks
as JSON; at send time blocks are rendered and wrapped in the email shell.

Block renderers are extracted from email_templates.py helpers to produce
identical, proven HTML output.

Usage:
    from block_registry import render_template_blocks, validate_template

    html = render_template_blocks(template, contact, products=[], discount=None)
    warnings = validate_template(blocks_json_string)
"""

import json
import html as html_mod
from email_shell import (
    wrap_email,
    BRAND_NAME, BRAND_URL, BRAND_COLOR, BRAND_COLOR_DARK,
    TEXT_DARK, TEXT_MID, TEXT_LIGHT,
)

# -- Brand Constants (imported from email_shell.py — single source of truth) --

BRAND_COLOR_LIGHT = "#e8f0ff"
ACCENT_COLOR      = "#0428aa"


# =========================================================================
#  BLOCK TYPE REGISTRY
# =========================================================================

BLOCK_TYPES = {
    "hero": {
        "label": "Hero Section",
        "required": ["headline"],
        "optional": ["subheadline", "bg_color"],
        "defaults": {
            "headline": "Your Headline Here",
            "subheadline": "",
            "bg_color": "linear-gradient(135deg, %s 0%%, #f0f4ff 100%%)" % BRAND_COLOR_LIGHT,
        },
    },
    "text": {
        "label": "Text Block",
        "required": ["paragraphs"],
        "optional": [],
        "defaults": {
            "paragraphs": ["Your text content goes here."],
        },
    },
    "product_grid": {
        "label": "Product Grid",
        "required": [],
        "optional": ["section_title", "columns"],
        "defaults": {
            "section_title": "Products",
            "columns": 2,
        },
    },
    "discount": {
        "label": "Discount Block",
        "required": ["code", "value_display"],
        "optional": ["display_text", "expires_text"],
        "defaults": {
            "code": "CODE",
            "value_display": "Save",
            "display_text": "",
            "expires_text": "",
        },
    },
    "cta": {
        "label": "CTA Button",
        "required": ["text", "url"],
        "optional": ["color"],
        "defaults": {
            "text": "Shop Now",
            "url": BRAND_URL,
            "color": BRAND_COLOR,
        },
    },
    "urgency": {
        "label": "Urgency Bar",
        "required": ["message"],
        "optional": [],
        "defaults": {
            "message": "Limited time offer",
        },
    },
    "divider": {
        "label": "Divider",
        "required": [],
        "optional": [],
        "defaults": {},
    },
}


# =========================================================================
#  BLOCK RENDER FUNCTIONS
#  Extracted from email_templates.py helpers -- produce identical HTML
# =========================================================================

def render_hero(content):
    """Hero headline section with gradient background."""
    headline = html_mod.escape(content.get("headline", ""))
    subheadline = content.get("subheadline", "")
    bg = content.get("bg_color",
                     "linear-gradient(135deg, %s 0%%, #f0f4ff 100%%)" % BRAND_COLOR_LIGHT)

    sub_html = ""
    if subheadline:
        sub_html = '<p style="margin:10px 0 0;font-size:16px;color:%s;font-weight:400;">%s</p>' % (
            TEXT_MID, html_mod.escape(subheadline)
        )

    return '''<tr><td style="background:%s;padding:36px 30px;text-align:center;" class="mobile-pad">
  <h1 style="margin:0;font-size:26px;font-weight:800;color:%s;line-height:1.3;">%s</h1>
  %s
</td></tr>''' % (bg, TEXT_DARK, headline, sub_html)


def render_text(content):
    """Body text paragraphs."""
    paragraphs = content.get("paragraphs", [])
    if not paragraphs:
        return ""

    paras_html = ""
    for p in paragraphs:
        safe = html_mod.escape(p)
        # Preserve basic HTML formatting from content (bold, line breaks)
        safe = safe.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
        safe = safe.replace("&lt;br/&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
        safe = safe.replace("&amp;bull;", "&bull;")
        paras_html += '<p style="margin:0 0 14px;font-size:15px;color:%s;line-height:1.7;">%s</p>' % (
            TEXT_MID, safe
        )

    return '<tr><td style="padding:24px 30px 8px;" class="mobile-pad">%s</td></tr>' % paras_html


def render_product_grid(content, products=None):
    """Product card grid with optional section header."""
    if not products:
        return ""

    section_title = content.get("section_title", "Products")
    columns = int(content.get("columns", 2))

    # Section header
    header_html = '''<tr><td style="padding:20px 30px 8px;" class="mobile-pad">
  <p style="margin:0;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;color:%s;">%s</p>
</td></tr>''' % (BRAND_COLOR, html_mod.escape(section_title))

    # Product cards
    rows_html = ""
    for i in range(0, len(products), columns):
        row_products = products[i:i + columns]
        cells = ""
        w = "%d%%" % (100 // columns - 2)
        for p in row_products:
            cells += _render_product_card(p, width=w)
        # Pad if fewer products than columns
        while len(row_products) < columns:
            cells += '<td class="stack-col" style="width:%s;padding:6px;"></td>' % w
            row_products.append(None)
        rows_html += '<tr>%s</tr>' % cells

    grid_html = '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
  %s
</table>''' % rows_html

    return header_html + '<tr><td style="padding:0 24px;" class="mobile-pad">%s</td></tr>' % grid_html


def _render_product_card(product, width="48%"):
    """Single product card -- extracted from email_templates._product_card()."""
    title = html_mod.escape(product.get("title", "")[:60])
    image_url = product.get("image_url", "")
    price = product.get("price", "0.00")
    product_url = product.get("product_url", BRAND_URL)
    compare_price = product.get("compare_price", "")

    price_html = '<span style="font-size:18px;font-weight:800;color:%s;">$%s</span>' % (BRAND_COLOR, price)
    if compare_price and compare_price != price:
        price_html = (
            '<span style="font-size:14px;color:#a0aec0;text-decoration:line-through;margin-right:6px;">$%s</span>' % compare_price
            + price_html
        )

    if image_url:
        img_html = '''<a href="%s" style="text-decoration:none;">
          <img src="%s" alt="%s" width="100%%" style="display:block;border-radius:12px 12px 0 0;max-width:100%%;" />
        </a>''' % (product_url, image_url, title)
    else:
        img_html = '''<div style="background:#f0f0f5;height:160px;border-radius:12px 12px 0 0;display:flex;align-items:center;justify-content:center;">
          <span style="color:#a0aec0;font-size:14px;">No image</span>
        </div>'''

    return '''<td class="stack-col" style="width:%s;padding:6px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e8e8f0;border-radius:12px;overflow:hidden;background:#ffffff;">
    <tr><td style="padding:0;">%s</td></tr>
    <tr><td style="padding:14px 14px 16px;">
      <p style="margin:0 0 6px;font-size:13px;font-weight:600;color:%s;line-height:1.4;">%s</p>
      <p style="margin:0 0 12px;">%s</p>
      <a href="%s" style="display:inline-block;background:%s;color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;font-size:12px;text-align:center;">
        View Product
      </a>
    </td></tr>
  </table>
</td>''' % (width, img_html, TEXT_DARK, title, price_html, product_url, BRAND_COLOR)


def render_discount(content, discount_data=None):
    """Prominent discount code display block."""
    # Use discount_data dict if provided, otherwise fall back to block content
    code = html_mod.escape(content.get("code", ""))
    value_display = html_mod.escape(content.get("value_display", ""))
    display_text = html_mod.escape(content.get("display_text", ""))
    expires_text = html_mod.escape(content.get("expires_text", ""))

    if discount_data:
        code = html_mod.escape(discount_data.get("code", code))
        value_display = html_mod.escape(discount_data.get("value_display", value_display))
        display_text = html_mod.escape(discount_data.get("display_text", display_text))
        expires_text = html_mod.escape(discount_data.get("expires_text", expires_text))

    if not code:
        return ""

    return '''<tr><td style="padding:0 30px;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
    <tr><td style="background:linear-gradient(135deg, %s 0%%, %s 100%%);border-radius:14px;padding:28px 24px;text-align:center;">
      <p style="margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:2px;color:rgba(255,255,255,0.75);">Your Exclusive Code</p>
      <p style="margin:0 0 6px;font-size:30px;font-weight:800;color:#ffffff;letter-spacing:4px;font-family:'Courier New',monospace;">%s</p>
      <p style="margin:0 0 4px;font-size:18px;font-weight:700;color:#ffffff;">%s</p>
      <p style="margin:0;font-size:16px;color:rgba(255,255,255,0.85);">%s</p>
      <p style="margin:10px 0 0;font-size:12px;color:rgba(255,255,255,0.6);">%s &bull; Single use only</p>
    </td></tr>
  </table>
</td></tr>''' % (BRAND_COLOR, BRAND_COLOR_DARK, code, value_display, display_text, expires_text)


def render_cta(content):
    """Large call-to-action button."""
    text = html_mod.escape(content.get("text", "Shop Now"))
    url = content.get("url", BRAND_URL)
    color = content.get("color", BRAND_COLOR)

    return '''<tr><td style="padding:8px 30px 24px;text-align:center;" class="mobile-pad">
  <a href="%s" style="display:inline-block;background:%s;color:#ffffff;text-decoration:none;padding:16px 40px;border-radius:10px;font-weight:700;font-size:16px;letter-spacing:0.3px;min-width:200px;text-align:center;">
    %s
  </a>
</td></tr>''' % (url, color, text)


def render_urgency(content):
    """Amber urgency message bar."""
    message = content.get("message", "")
    if not message:
        return ""
    safe = html_mod.escape(message)
    return '''<tr><td style="padding:0 30px 16px;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:10px;padding:12px 16px;text-align:center;">
      <span style="font-size:13px;font-weight:600;color:#d97706;">&#9203; %s</span>
    </td></tr>
  </table>
</td></tr>''' % safe


def render_divider(content):
    """Simple horizontal divider."""
    return '''<tr><td style="padding:8px 30px;" class="mobile-pad">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="border-top:1px solid #eeeef2;"></td></tr>
  </table>
</td></tr>'''


# =========================================================================
#  BLOCK RENDERER DISPATCH
# =========================================================================

_BLOCK_RENDERERS = {
    "hero":         lambda content, **kw: render_hero(content),
    "text":         lambda content, **kw: render_text(content),
    "product_grid": lambda content, **kw: render_product_grid(content, products=kw.get("products")),
    "discount":     lambda content, **kw: render_discount(content, discount_data=kw.get("discount")),
    "cta":          lambda content, **kw: render_cta(content),
    "urgency":      lambda content, **kw: render_urgency(content),
    "divider":      lambda content, **kw: render_divider(content),
}


# =========================================================================
#  MASTER RENDER FUNCTION
# =========================================================================

def render_template_blocks(template, contact=None, products=None, discount=None):
    """
    Render a blocks-format EmailTemplate to complete HTML email.

    Args:
        template: EmailTemplate instance with template_format="blocks"
        contact:  Contact instance (for personalization tokens)
        products: list of product dicts [{title, image_url, price, product_url, compare_price}]
        discount: discount dict {code, value_display, display_text, expires_text}

    Returns:
        str: Complete HTML email document ready to send
    """
    try:
        blocks = json.loads(template.blocks_json or "[]")
    except (json.JSONDecodeError, TypeError):
        blocks = []

    if not blocks:
        # Empty blocks -- fall back to html_body if available
        if template.html_body:
            return template.html_body
        return ""

    # Render each block
    body_parts = []
    for block in blocks:
        block_type = block.get("block_type", "")
        content = block.get("content", {})

        renderer = _BLOCK_RENDERERS.get(block_type)
        if not renderer:
            continue  # Skip unknown block types

        html_fragment = renderer(content, products=products or [], discount=discount)
        if html_fragment:
            body_parts.append(html_fragment)

    body_html = "\n".join(body_parts)

    # Apply personalization tokens
    if contact:
        body_html = body_html.replace("{{first_name}}", getattr(contact, "first_name", "") or "Friend")
        body_html = body_html.replace("{{last_name}}", getattr(contact, "last_name", "") or "")
        body_html = body_html.replace("{{email}}", getattr(contact, "email", "") or "")
    else:
        # Preview mode -- use sample data
        body_html = body_html.replace("{{first_name}}", "John")
        body_html = body_html.replace("{{last_name}}", "Smith")
        body_html = body_html.replace("{{email}}", "john@example.com")

    body_html = body_html.replace("{{unsubscribe_url}}", "{{unsubscribe_url}}")

    # Wrap in email shell
    preview_text = getattr(template, "preview_text", "") or ""
    full_html = wrap_email(body_html, preview_text=preview_text, unsubscribe_url="{{unsubscribe_url}}")

    return full_html


# =========================================================================
#  TEMPLATE VALIDATION
# =========================================================================

def validate_template(blocks_json_str):
    """
    Validate a blocks-format template and return warnings.

    Args:
        blocks_json_str: JSON string of block definitions

    Returns:
        list of dicts: [{"level": "error"|"warning", "message": "..."}]
    """
    warnings = []

    try:
        blocks = json.loads(blocks_json_str or "[]")
    except (json.JSONDecodeError, TypeError):
        return [{"level": "error", "message": "Invalid JSON in blocks definition"}]

    if not blocks:
        return [{"level": "error", "message": "Template has no blocks defined"}]

    # Check for required block types
    block_types_present = [b.get("block_type", "") for b in blocks]

    if "cta" not in block_types_present:
        warnings.append({"level": "error", "message": "Template has no CTA button block -- emails should have a clear call to action"})

    if "text" not in block_types_present:
        warnings.append({"level": "warning", "message": "Template has no text block -- consider adding body copy"})

    # Validate each block
    for i, block in enumerate(blocks):
        block_type = block.get("block_type", "")
        content = block.get("content", {})
        block_num = i + 1

        # Check for unknown block type
        if block_type not in BLOCK_TYPES:
            warnings.append({
                "level": "error",
                "message": "Block %d: Unknown block type '%s'" % (block_num, block_type)
            })
            continue

        # Check required fields
        type_def = BLOCK_TYPES[block_type]
        for field in type_def["required"]:
            val = content.get(field)
            if val is None or val == "" or val == []:
                warnings.append({
                    "level": "error",
                    "message": "Block %d (%s): Required field '%s' is empty" % (block_num, type_def["label"], field)
                })

        # ── Block-specific validation contracts ──

        if block_type == "cta":
            url = content.get("url", "")
            text = content.get("text", "")
            if url and not url.startswith(("http://", "https://", "{{", "mailto:")):
                warnings.append({
                    "level": "error",
                    "message": "Block %d (CTA): URL '%s' is not a valid link (must start with http(s)://, mailto:, or a template variable)" % (block_num, url[:60])
                })
            if url and "javascript:" in url.lower():
                warnings.append({
                    "level": "error",
                    "message": "Block %d (CTA): URL contains javascript: which is unsafe and will be blocked by email clients" % block_num
                })
            if text and len(text) > 50:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (CTA): Button text is %d chars -- keep under 50 for readability" % (block_num, len(text))
                })
            color = content.get("color", "")
            if color and not color.startswith("#") and color not in ("", BRAND_COLOR):
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (CTA): Color '%s' should be a hex value (#rrggbb)" % (block_num, color)
                })

        if block_type == "product_grid":
            section_title = content.get("section_title", "")
            if not section_title:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Product Grid): No section title -- products will render without a header" % block_num
                })
            columns = content.get("columns", 2)
            try:
                columns = int(columns)
            except (ValueError, TypeError):
                columns = 0
            if columns not in (1, 2, 3):
                warnings.append({
                    "level": "error",
                    "message": "Block %d (Product Grid): columns must be 1, 2, or 3 (got %s)" % (block_num, content.get("columns", ""))
                })

        if block_type == "hero":
            headline = content.get("headline", "")
            if headline and len(headline) > 120:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Hero): Headline is %d chars -- keep under 120 for mobile readability" % (block_num, len(headline))
                })
            subheadline = content.get("subheadline", "")
            if subheadline and len(subheadline) > 200:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Hero): Subheadline is %d chars -- keep under 200" % (block_num, len(subheadline))
                })

        if block_type == "text":
            paragraphs = content.get("paragraphs", [])
            if not isinstance(paragraphs, list):
                warnings.append({
                    "level": "error",
                    "message": "Block %d (Text): 'paragraphs' must be a list of strings" % block_num
                })
            elif len(paragraphs) > 8:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Text): %d paragraphs is long -- consider splitting into multiple text blocks" % (block_num, len(paragraphs))
                })

        if block_type == "discount":
            code = content.get("code", "")
            if code and len(code) > 30:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Discount): Code '%s' is %d chars -- long codes are hard to type manually" % (block_num, code[:30], len(code))
                })
            if code and " " in code:
                warnings.append({
                    "level": "error",
                    "message": "Block %d (Discount): Code '%s' contains spaces -- discount codes should not have spaces" % (block_num, code)
                })

        if block_type == "urgency":
            message = content.get("message", "")
            if message and len(message) > 120:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d (Urgency): Message is %d chars -- keep under 120 for impact" % (block_num, len(message))
                })

    return warnings


# =========================================================================
#  UTILITY: Create Example Blocks JSON
# =========================================================================

def make_example_blocks():
    """
    Return a sample blocks JSON string for testing/seeding.
    Produces a Welcome-style email with hero, text, discount, product grid, and CTA.
    """
    blocks = [
        {
            "block_type": "hero",
            "content": {
                "headline": "Welcome to LDAS Electronics, {{first_name}}!",
                "subheadline": "Your go-to store for trucking electronics",
                "bg_color": "linear-gradient(135deg, #cffafe 0%, #a5f3fc 100%)",
            },
        },
        {
            "block_type": "text",
            "content": {
                "paragraphs": [
                    "Thanks for joining the LDAS family! We specialize in electronics built for professional drivers -- from Bluetooth headsets to dash cams.",
                    "Whether you're on the highway or at the office, we've got the gear to keep you connected.",
                ],
            },
        },
        {
            "block_type": "discount",
            "content": {
                "code": "WELCOME5",
                "value_display": "5% Off",
                "display_text": "Your first order",
                "expires_text": "Valid for 30 days",
            },
        },
        {
            "block_type": "product_grid",
            "content": {
                "section_title": "Popular Products",
                "columns": 2,
            },
        },
        {
            "block_type": "cta",
            "content": {
                "text": "Start Shopping",
                "url": BRAND_URL,
                "color": "#0428aa",
            },
        },
    ]
    return json.dumps(blocks, indent=2)
