"""
email_templates.py — Premium, conversion-oriented HTML email templates.

Architecture: Claude generates structured JSON content. These template functions
merge that content with product images + discount codes to produce beautiful,
brand-consistent email HTML.

All templates use:
- Table-based layout (email client compatible)
- Inline styles only (except @media queries in <head>)
- 600px container, stacks on mobile
- Dark mode @media overrides
- LDAS Electronics brand identity
"""

import html as html_mod
from datetime import datetime
from email_shell import wrap_email

# ── Brand Constants ──────────────────────────────────────────
BRAND_NAME = "LDAS Electronics"
BRAND_URL = "https://ldas-electronics.com"
BRAND_COLOR = "#063cff"
BRAND_COLOR_DARK = "#0532d4"
BRAND_COLOR_LIGHT = "#e8f0ff"
ACCENT_COLOR = "#0428aa"
ACCENT_DARK = "#031e80"
TEXT_DARK = "#1a1a2e"
TEXT_MID = "#4a5568"
TEXT_LIGHT = "#718096"
BG_OUTER = "#f0f0f5"
BG_BODY = "#ffffff"
LOGO_URL = "https://ldas.ca/cdn/shop/files/Untitled_design_Logo.png?v=1758142321&width=300"


# ── Helper: Responsive Email Base ────────────────────────────

def _email_base(preheader, body_content, unsubscribe_url="{{unsubscribe_url}}"):
    """
    Wrap body content in the universal LDAS Electronics email shell.
    Delegates to email_shell.wrap_email() for consistent header + footer.
    """
    return wrap_email(body_content, preview_text=preheader, unsubscribe_url=unsubscribe_url)


# ── Helper: Product Card ─────────────────────────────────────

def _product_card(product, width="48%"):
    """
    Single product card with image, title, price, and CTA.
    product = {title, image_url, price, product_url, compare_price}
    """
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

    img_html = ""
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


def _product_grid(products, columns=2):
    """Render products as a responsive grid (table rows)."""
    if not products:
        return ""

    rows_html = ""
    for i in range(0, len(products), columns):
        row_products = products[i:i + columns]
        cells = ""
        w = "%d%%" % (100 // columns - 2)
        for p in row_products:
            cells += _product_card(p, width=w)
        # Pad if odd number
        while len(row_products) < columns:
            cells += '<td class="stack-col" style="width:%s;padding:6px;"></td>' % w
            row_products.append(None)

        rows_html += '<tr>%s</tr>' % cells

    return '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
  %s
</table>''' % rows_html


# ── Helper: Discount Code Block ──────────────────────────────

def _discount_block(discount):
    """
    Prominent discount code display.
    discount = {code, value_display, display_text, expires_text} from get_discount_display()
    """
    if not discount:
        return ""

    code = html_mod.escape(discount.get("code", ""))
    value_display = html_mod.escape(discount.get("value_display", ""))
    display_text = html_mod.escape(discount.get("display_text", ""))
    expires_text = html_mod.escape(discount.get("expires_text", ""))

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


# ── Helper: Urgency Bar ──────────────────────────────────────

def _urgency_bar(message):
    """Amber urgency message bar."""
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


# ── Helper: CTA Button ──────────────────────────────────────

def _cta_button(text, url, color=None):
    """Large, mobile-friendly call-to-action button."""
    btn_color = color or BRAND_COLOR
    safe_text = html_mod.escape(text)
    return '''<tr><td style="padding:8px 30px 24px;text-align:center;" class="mobile-pad">
  <a href="%s" style="display:inline-block;background:%s;color:#ffffff;text-decoration:none;padding:16px 40px;border-radius:10px;font-weight:700;font-size:16px;letter-spacing:0.3px;min-width:200px;text-align:center;">
    %s
  </a>
</td></tr>''' % (url, btn_color, safe_text)


# ── Helper: Hero Section ─────────────────────────────────────

def _hero_section(headline, subheadline="", bg_color=None):
    """Large hero headline section."""
    bg = bg_color or "linear-gradient(135deg, %s 0%%, %s 100%%)" % (BRAND_COLOR_LIGHT, "#f0f4ff")
    safe_h = html_mod.escape(headline)
    sub_html = ""
    if subheadline:
        sub_html = '<p style="margin:10px 0 0;font-size:16px;color:%s;font-weight:400;">%s</p>' % (
            TEXT_MID, html_mod.escape(subheadline)
        )
    return '''<tr><td style="background:%s;padding:36px 30px;text-align:center;" class="mobile-pad">
      <h1 style="margin:0;font-size:26px;font-weight:800;color:%s;line-height:1.3;">%s</h1>
      %s
    </td></tr>''' % (bg, TEXT_DARK, safe_h, sub_html)


# ── Helper: Body Paragraphs ──────────────────────────────────

def _body_paragraphs(paragraphs):
    """Render body text paragraphs."""
    if not paragraphs:
        return ""
    paras = ""
    for p in paragraphs:
        safe = html_mod.escape(p)
        paras += '<p style="margin:0 0 14px;font-size:15px;color:%s;line-height:1.7;">%s</p>' % (TEXT_MID, safe)
    return '''<tr><td style="padding:24px 30px 8px;" class="mobile-pad">%s</td></tr>''' % paras


# ── Helper: Section Header ───────────────────────────────────

def _section_header(title, icon_color=None):
    """Small section divider with title."""
    color = icon_color or BRAND_COLOR
    safe = html_mod.escape(title)
    return '''<tr><td style="padding:20px 30px 8px;" class="mobile-pad">
      <p style="margin:0;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;color:%s;">%s</p>
    </td></tr>''' % (color, safe)


# ═══════════════════════════════════════════════════════════════
#  PURPOSE-SPECIFIC TEMPLATES
# ═══════════════════════════════════════════════════════════════

def render_browse_abandonment_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "Still thinking about X?" — Product image + free shipping + urgency.
    """
    hero = _hero_section(
        content.get("hero_headline", "Still thinking about it?"),
        content.get("hero_subheadline", "The gear you were eyeing is still available"),
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    products_section = ""
    if products:
        products_section = _section_header("Products You Viewed", ACCENT_COLOR)
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    discount_section = _discount_block(discount) if discount else ""
    urgency = _urgency_bar(content.get("urgency_message", ""))
    cta = _cta_button(
        content.get("cta_text", "Continue Shopping"),
        content.get("cta_url", BRAND_URL),
    )

    body_html = hero + body + products_section + discount_section + urgency + cta
    return _email_base(
        content.get("preheader", "The gear you were looking at is still available"),
        body_html, unsubscribe_url
    )


def render_cart_abandonment_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "You left items behind" — Cart products + 5% discount + 48hr urgency.
    """
    hero = _hero_section(
        content.get("hero_headline", "You left something behind!"),
        content.get("hero_subheadline", "Your cart is waiting for you"),
        bg_color="linear-gradient(135deg, #fef3c7 0%, #fde68a 100%)",
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    products_section = ""
    if products:
        products_section = _section_header("Your Cart Items", "#d97706")
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    discount_section = _discount_block(discount) if discount else ""
    urgency = _urgency_bar(content.get("urgency_message", "Complete your order before these items sell out"))
    cta = _cta_button(
        content.get("cta_text", "Complete Your Order"),
        content.get("cta_url", BRAND_URL + "/cart"),
        color="#d97706",
    )

    body_html = hero + body + products_section + discount_section + urgency + cta
    return _email_base(
        content.get("preheader", "You left items in your cart — complete your order"),
        body_html, unsubscribe_url
    )


def render_winback_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "We miss you" — 10% discount centerpiece + previously bought + recommendations.
    """
    hero = _hero_section(
        content.get("hero_headline", "We miss you!"),
        content.get("hero_subheadline", "It's been a while — here's something special"),
        bg_color="linear-gradient(135deg, %s 0%%, #ddd6fe 100%%)" % BRAND_COLOR_LIGHT,
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    discount_section = _discount_block(discount) if discount else ""
    products_section = ""
    if products:
        products_section = _section_header("Recommended For You", BRAND_COLOR)
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    urgency = _urgency_bar(content.get("urgency_message", ""))
    cta = _cta_button(
        content.get("cta_text", "Come Back & Save"),
        content.get("cta_url", BRAND_URL),
    )

    body_html = hero + body + discount_section + products_section + urgency + cta
    return _email_base(
        content.get("preheader", "We miss you — here's a special offer just for you"),
        body_html, unsubscribe_url
    )


def render_welcome_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "Welcome to LDAS" — Brand intro + 5% first order code + popular products.
    """
    hero = _hero_section(
        content.get("hero_headline", "Welcome to LDAS Electronics!"),
        content.get("hero_subheadline", "Your go-to store for trucking electronics"),
        bg_color="linear-gradient(135deg, #cffafe 0%, #a5f3fc 100%)",
    )
    body = _body_paragraphs(content.get("body_paragraphs", [
        "Thanks for joining the LDAS family! We specialize in electronics built for professional drivers — from Bluetooth headsets to dash cams.",
        "Whether you're on the highway or at the office, we've got the gear to keep you connected."
    ]))
    discount_section = _discount_block(discount) if discount else ""
    products_section = ""
    if products:
        products_section = _section_header("Popular Products", ACCENT_COLOR)
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    cta = _cta_button(
        content.get("cta_text", "Start Shopping"),
        content.get("cta_url", BRAND_URL),
        color=ACCENT_COLOR,
    )

    body_html = hero + body + discount_section + products_section + cta
    return _email_base(
        content.get("preheader", "Welcome! Here's a little something to get started"),
        body_html, unsubscribe_url
    )


def render_upsell_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "Perfect pairings for your X" — Complementary product grid + 5% nudge.
    """
    hero = _hero_section(
        content.get("hero_headline", "Great choice! Here's what pairs perfectly"),
        content.get("hero_subheadline", "Products other truckers love alongside yours"),
        bg_color="linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%)",
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    products_section = ""
    if products:
        products_section = _section_header("Recommended For You", "#059669")
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    discount_section = _discount_block(discount) if discount else ""
    cta = _cta_button(
        content.get("cta_text", "Explore More"),
        content.get("cta_url", BRAND_URL),
        color="#059669",
    )

    body_html = hero + body + products_section + discount_section + cta
    return _email_base(
        content.get("preheader", "Products that go perfectly with your recent purchase"),
        body_html, unsubscribe_url
    )


def render_loyalty_reward_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "You're a VIP" — Customer stats + exclusive 10% code.
    """
    hero = _hero_section(
        content.get("hero_headline", "Thank you — you're a VIP!"),
        content.get("hero_subheadline", "We appreciate your loyalty to LDAS Electronics"),
        bg_color="linear-gradient(135deg, #fef3c7 0%, #fde68a 100%)",
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    discount_section = _discount_block(discount) if discount else ""
    products_section = ""
    if products:
        products_section = _section_header("Exclusive Picks For You", "#d97706")
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    cta = _cta_button(
        content.get("cta_text", "Shop Your Exclusive Deal"),
        content.get("cta_url", BRAND_URL),
        color="#d97706",
    )

    body_html = hero + body + discount_section + products_section + cta
    return _email_base(
        content.get("preheader", "You're a VIP — here's an exclusive reward just for you"),
        body_html, unsubscribe_url
    )


def render_re_engagement_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "What's new since you left" — New products + 5% code.
    """
    hero = _hero_section(
        content.get("hero_headline", "It's been a while!"),
        content.get("hero_subheadline", "Here's what's new at LDAS Electronics"),
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    discount_section = _discount_block(discount) if discount else ""
    products_section = ""
    if products:
        products_section = _section_header("New Arrivals", BRAND_COLOR)
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    cta = _cta_button(
        content.get("cta_text", "See What's New"),
        content.get("cta_url", BRAND_URL),
    )

    body_html = hero + body + discount_section + products_section + cta
    return _email_base(
        content.get("preheader", "We've got new gear since you last visited"),
        body_html, unsubscribe_url
    )


def render_high_intent_email(content, products, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    "Ready to decide?" — Viewed products + free shipping + social proof.
    """
    hero = _hero_section(
        content.get("hero_headline", "Ready to make your move?"),
        content.get("hero_subheadline", "You've been doing your research — here's a nudge"),
        bg_color="linear-gradient(135deg, #cffafe 0%, #e0f2fe 100%)",
    )
    body = _body_paragraphs(content.get("body_paragraphs", []))
    products_section = ""
    if products:
        products_section = _section_header("Products You've Been Eyeing", ACCENT_COLOR)
        products_section += '<tr><td style="padding:0 24px;" class="mobile-pad">' + _product_grid(products) + '</td></tr>'

    discount_section = _discount_block(discount) if discount else ""
    urgency = _urgency_bar(content.get("urgency_message", ""))
    cta = _cta_button(
        content.get("cta_text", "Get Yours Today"),
        content.get("cta_url", BRAND_URL),
        color=ACCENT_COLOR,
    )

    body_html = hero + body + products_section + discount_section + urgency + cta
    return _email_base(
        content.get("preheader", "You've been browsing — here's a little push"),
        body_html, unsubscribe_url
    )


# ── Template Registry ────────────────────────────────────────

TEMPLATE_RENDERERS = {
    "browse_abandonment": render_browse_abandonment_email,
    "cart_abandonment":   render_cart_abandonment_email,
    "winback":            render_winback_email,
    "welcome":            render_welcome_email,
    "upsell":             render_upsell_email,
    "loyalty_reward":     render_loyalty_reward_email,
    "re_engagement":      render_re_engagement_email,
    "high_intent":        render_high_intent_email,
}


def render_email(purpose, content, products=None, discount=None, unsubscribe_url="{{unsubscribe_url}}"):
    """
    Main entry point: render a complete email HTML for any purpose.

    Args:
        purpose: one of TEMPLATE_RENDERERS keys
        content: dict from Claude's structured JSON output
        products: list of product dicts from get_products_for_email()
        discount: dict from get_discount_display()
        unsubscribe_url: unsubscribe link

    Returns:
        str: complete HTML email
    """
    renderer = TEMPLATE_RENDERERS.get(purpose, render_winback_email)
    return renderer(content, products or [], discount, unsubscribe_url)
