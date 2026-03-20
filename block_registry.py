"""
block_registry.py -- Block-Based Email Template Rendering Engine

Provides 11 reusable email block types with tested HTML renderers.
Templates store an ordered list of blocks as JSON; at send time blocks
are rendered and wrapped in the email shell.

Usage:
    from block_registry import render_template_blocks, validate_template

    html = render_template_blocks(template, contact, products=[], discount=None)
    warnings = validate_template(blocks_json_string)
"""

import json
import logging
import html as html_mod

logger = logging.getLogger(__name__)
from email_shell import (
    wrap_email,
    BRAND_NAME, BRAND_URL, BRAND_COLOR, BRAND_COLOR_DARK,
)

# =========================================================================
#  DESIGN SYSTEM -- Dark Email Tokens (single source of truth)
# =========================================================================

DESIGN = {
    # Dark backgrounds — unified deep navy for cohesive look
    "body_bg":           "#0d1020",
    "surface":           "#141828",
    "surface_border":    "#1e2440",
    "divider_color":     "#1a1e35",

    # Text on dark
    "text_primary":      "#ffffff",
    "text_secondary":    "#b0b0b0",
    "text_tertiary":     "#707070",

    # Brand
    "brand":             "#063cff",
    "brand_light":       "#3366ff",
    "brand_glow":        "rgba(6,60,255,0.15)",

    # Buttons (brand blue on dark)
    "btn_primary_bg":    "#063cff",
    "btn_primary_text":  "#ffffff",
    "btn_card_bg":       "#063cff",
    "btn_card_text":     "#ffffff",

    # Prices
    "price_color":       "#ffffff",
    "price_strike":      "#666666",

    # Accents
    "savings_green":     "#34d399",
    "star_gold":         "#fbbf24",
    "badge_bg":          "rgba(255,255,255,0.1)",
    "badge_text":        "#ffffff",
    "urgency_bg":        "rgba(251,191,36,0.1)",
    "urgency_border":    "rgba(251,191,36,0.25)",
    "urgency_text":      "#fbbf24",

    # Product spotlight glow
    "spotlight":         "radial-gradient(ellipse at center, rgba(255,255,255,0.06) 0%, transparent 70%)",

    # Placeholder
    "placeholder_bg":    "#1a1a1a",
    "placeholder_text":  "#707070",

    # Spacing
    "section_pad":       "28px 30px",
    "section_pad_top":   "24px 30px 10px",
    "section_pad_tight": "8px 30px",
    "grid_pad":          "4px 24px 8px",

    # Cards (dark)
    "card_border":       "1px solid #2a2a2a",
    "card_radius":       "14px",
    "card_shadow":       "none",
    "card_shadow_lg":    "none",
    "card_bg":           "#1a1a1a",
    "card_img_radius":   "14px 14px 0 0",
    "card_inner_pad":    "18px 18px 20px",

    # Typography
    "h1":                "margin:0;font-size:32px;font-weight:800;line-height:1.2",
    "h2":                "margin:0;font-size:22px;font-weight:700;line-height:1.3",
    "body":              "font-size:15px;line-height:1.7",
    "label":             "font-size:11px;text-transform:uppercase;letter-spacing:2px;font-weight:700",
    "caption":           "font-size:12px",

    # Buttons (style strings — color applied separately)
    "btn_primary":       "display:inline-block;text-decoration:none;padding:18px 48px;border-radius:10px;font-weight:700;font-size:16px;letter-spacing:0.3px;min-width:200px;text-align:center",
    "btn_card":          "display:inline-block;text-decoration:none;padding:14px 28px;border-radius:8px;font-weight:600;font-size:14px;text-align:center",
}


# =========================================================================
#  BLOCK TYPE REGISTRY
# =========================================================================

BLOCK_TYPES = {
    "hero": {
        "label": "Hero Section",
        "required": ["headline"],
        "optional": ["subheadline", "bg_color", "hero_image_url", "cta_text", "cta_url"],
        "defaults": {
            "headline": "Your Headline Here",
            "subheadline": "",
            "bg_color": "linear-gradient(135deg, %s 0%%, %s 100%%)" % (BRAND_COLOR, BRAND_COLOR_DARK),
        },
    },
    "text": {
        "label": "Text Block",
        "required": ["paragraphs"],
        "optional": ["section_header"],
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
    "product_hero": {
        "label": "Product Hero",
        "required": [],
        "optional": ["section_title", "cta_text"],
        "defaults": {
            "section_title": "Featured Product",
            "cta_text": "Shop Now",
        },
    },
    "comparison_block": {
        "label": "Product Comparison",
        "required": [],
        "optional": ["section_title", "columns"],
        "defaults": {
            "section_title": "Compare Products",
            "columns": 2,
        },
    },
    "trust_reassurance": {
        "label": "Trust & Reassurance",
        "required": [],
        "optional": ["items"],
        "defaults": {
            "items": [
                {"icon": "package", "text": "Free Shipping on $50+"},
                {"icon": "shield", "text": "30-Day Easy Returns"},
                {"icon": "star", "text": "4.8/5 Customer Rating"},
                {"icon": "maple", "text": "Canadian-Owned Business"},
            ],
        },
    },
    "features_benefits": {
        "label": "Features & Benefits",
        "required": [],
        "optional": ["section_title", "items"],
        "defaults": {
            "section_title": "Why Choose Us",
            "items": [
                "Premium quality electronics built for the road",
                "Fast, free shipping across Canada",
                "Dedicated customer support team",
            ],
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
        "optional": ["color", "secondary_text", "secondary_url"],
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
    # ── New Persuasion Modules ──────────────────────────────
    "driver_testimonial": {
        "label": "Customer Testimonial",
        "required": ["quote", "author_name"],
        "optional": ["author_role", "rating", "product_name", "section_title"],
        "defaults": {
            "quote": "Great product, works perfectly.",
            "author_name": "Customer",
            "author_role": "",
            "rating": 5,
            "product_name": "",
            "section_title": "What Customers Say",
        },
    },
    "comparison": {
        "label": "Product Comparison",
        "required": [],
        "optional": ["section_title", "columns", "highlight_index", "cta_text"],
        "defaults": {
            "section_title": "Compare Your Options",
            "columns": 2,
            "highlight_index": -1,
            "cta_text": "View Details",
        },
    },
    "why_choose_this": {
        "label": "Why Choose This",
        "required": ["items"],
        "optional": ["section_title", "product_name", "icon_style"],
        "defaults": {
            "items": ["Benefit one", "Benefit two", "Benefit three"],
            "section_title": "Why You'll Love It",
            "product_name": "",
            "icon_style": "check",
        },
    },
    "objection_handling": {
        "label": "Objection Handling",
        "required": ["items"],
        "optional": ["section_title", "style"],
        "defaults": {
            "items": [
                {"objection": "What if it doesn't work for me?", "answer": "30-day hassle-free returns."},
                {"objection": "Is shipping fast?", "answer": "Most orders arrive in 3-5 business days."},
            ],
            "section_title": "Quick Answers",
            "style": "qa",
        },
    },
    "bundle_value": {
        "label": "Bundle Value",
        "required": ["items", "bundle_price"],
        "optional": ["section_title", "savings_text", "cta_text", "cta_url"],
        "defaults": {
            "items": [],
            "bundle_price": "0.00",
            "section_title": "Better Together",
            "savings_text": "",
            "cta_text": "Shop the Bundle",
            "cta_url": BRAND_URL,
        },
    },
    "best_seller_proof": {
        "label": "Best Seller Proof",
        "required": [],
        "optional": ["section_title", "proof_line", "badge_text", "show_rating"],
        "defaults": {
            "section_title": "Customer Favourites",
            "proof_line": "",
            "badge_text": "",
            "show_rating": True,
        },
    },
    "feature_highlights": {
        "label": "Feature Highlights",
        "required": ["items"],
        "optional": ["section_title", "icon_type", "columns"],
        "defaults": {
            "items": ["Feature one", "Feature two", "Feature three"],
            "section_title": "Why LDAS",
            "icon_type": "check",
            "columns": 1,
        },
    },
    # ── Content-rich modules ──
    "competitor_comparison": {
        "label": "Competitor Comparison",
        "required": ["competitors", "rows"],
        "optional": ["section_title", "ldas_label"],
        "defaults": {
            "section_title": "How We Compare",
            "ldas_label": "LDAS",
        },
    },
    "spec_table": {
        "label": "Spec Table",
        "required": ["rows"],
        "optional": ["products", "product_name", "section_title", "highlight_index"],
        "defaults": {
            "products": [],
            "product_name": "",
            "section_title": "Specifications",
            "highlight_index": -1,
        },
    },
    "stat_callout": {
        "label": "Stat Callout",
        "required": ["stats"],
        "optional": ["section_title", "accent_color"],
        "defaults": {
            "section_title": "",
            "accent_color": "",
        },
    },
    "whats_included": {
        "label": "What's Included",
        "required": ["items"],
        "optional": ["section_title", "product_name", "image_url"],
        "defaults": {
            "section_title": "What's Included",
            "product_name": "",
            "image_url": "",
        },
    },
    "faq": {
        "label": "FAQ",
        "required": ["items"],
        "optional": ["section_title"],
        "defaults": {
            "section_title": "Common Questions",
        },
    },
    "use_case_match": {
        "label": "Use Case Match",
        "required": ["cases"],
        "optional": ["section_title", "cta_text"],
        "defaults": {
            "section_title": "Find Your Perfect Match",
            "cta_text": "Shop Now",
        },
    },
    "brand_story": {
        "label": "Brand Story",
        "required": ["headline", "body"],
        "optional": ["section_title", "variant", "badges", "cta_text", "cta_url"],
        "defaults": {
            "section_title": "",
            "variant": "mission",
            "badges": [],
            "cta_text": "",
            "cta_url": "",
        },
    },
}


# =========================================================================
#  BRAND STORY BADGE DEFAULTS (by variant)
# =========================================================================

_BRAND_STORY_BADGES = {
    "mission": [
        {"icon": "&#127911;", "text": "Premium Audio"},
        {"icon": "&#127809;", "text": "Canadian Brand"},
        {"icon": "&#128172;", "text": "24/7 Support"},
        {"icon": "&#11088;", "text": "4.8/5 Rated"},
    ],
    "sustainability": [
        {"icon": "&#9851;", "text": "95% Recyclable Packaging"},
        {"icon": "&#127793;", "text": "Carbon-Conscious Shipping"},
        {"icon": "&#127464;&#127462;", "text": "Ships from Ontario"},
        {"icon": "&#128230;", "text": "Minimal Waste Design"},
    ],
    "heritage": [
        {"icon": "&#127809;", "text": "Proudly Canadian"},
        {"icon": "&#128205;", "text": "Brampton, Ontario"},
        {"icon": "&#128737;", "text": "ISED Approved"},
        {"icon": "&#129309;", "text": "Family-Owned"},
    ],
}


# =========================================================================
#  ICON MAP for trust_reassurance (HTML entities -- email-safe)
# =========================================================================

_TRUST_ICONS = {
    "package":  "&#x1F4E6;",
    "truck":    "&#x1F69A;",
    "shield":   "&#x1F6E1;",
    "star":     "&#x2B50;",
    "maple":    "&#x1F341;",
    "lock":     "&#x1F512;",
    "heart":    "&#x2764;",
    "check":    "&#x2705;",
    "clock":    "&#x23F0;",
    "gift":     "&#x1F381;",
}


# =========================================================================
#  BLOCK RENDER FUNCTIONS
# =========================================================================

def render_hero(content):
    """Hero headline section — flows seamlessly into next block via gradient fade."""
    headline = html_mod.escape(content.get("headline", ""))
    subheadline = content.get("subheadline", "")

    text_color = DESIGN["text_primary"]
    sub_color = DESIGN["text_secondary"]

    # Optional hero image
    hero_image_url = content.get("hero_image_url", "")
    img_html = ""
    if hero_image_url:
        img_html = '<img src="%s" alt="" width="100%%" style="display:block;max-width:100%%;" />' % html_mod.escape(hero_image_url)
        img_html = '<tr><td style="padding:0;background:%s;">%s</td></tr>' % (DESIGN["body_bg"], img_html)

    # Thin blue accent line — visual thread that ties email together
    accent_line = '<div style="width:48px;height:3px;background:linear-gradient(90deg,%s,%s);margin:0 auto 20px;border-radius:2px;"></div>' % (
        DESIGN["brand"], DESIGN["brand_light"]
    )

    sub_html = ""
    if subheadline:
        sub_html = '<p style="margin:14px 0 0;font-size:17px;color:%s;font-weight:400;letter-spacing:0.2px;line-height:1.5;">%s</p>' % (
            sub_color, html_mod.escape(subheadline)
        )

    # Optional inline CTA
    cta_text = content.get("cta_text", "")
    cta_url = content.get("cta_url", "")
    cta_html = ""
    if cta_text and cta_url:
        cta_html = '<p style="margin:22px 0 0;"><a href="%s" style="%s;background:%s;color:%s;box-shadow:0 0 24px rgba(6,60,255,0.35);">%s</a></p>' % (
            cta_url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], html_mod.escape(cta_text)
        )

    # Hero uses generous top padding but tight bottom — flows INTO the next block
    return '''%s<tr><td style="padding:48px 30px 16px;text-align:center;" class="mobile-pad">
  %s
  <h1 style="%s;color:%s;font-size:34px;letter-spacing:-0.5px;">%s</h1>
  %s%s
</td></tr>''' % (img_html, accent_line, DESIGN["h1"], text_color, headline, sub_html, cta_html)


def render_text(content):
    """Body text — center-aligned to maintain visual flow from hero."""
    paragraphs = content.get("paragraphs", [])
    if not paragraphs:
        return ""

    # Optional section header
    section_header = content.get("section_header", "")
    header_html = ""
    if section_header:
        header_html = '<p style="margin:0 0 14px;%s;color:%s;">%s</p>' % (
            DESIGN["label"], DESIGN["text_tertiary"], html_mod.escape(section_header)
        )

    paras_html = ""
    for p in paragraphs:
        safe = html_mod.escape(p)
        safe = safe.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
        safe = safe.replace("&lt;br/&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
        safe = safe.replace("&amp;bull;", "&bull;")
        paras_html += '<p style="margin:0 0 12px;%s;color:%s;">%s</p>' % (
            DESIGN["body"], DESIGN["text_secondary"], safe
        )

    # Center-aligned text, tighter padding to flow from hero/previous block
    return '<tr><td style="padding:12px 36px 20px;background:%s;text-align:center;" class="mobile-pad">%s%s</td></tr>' % (
        DESIGN["body_bg"], header_html, paras_html
    )


def render_product_grid(content, products=None):
    """Product card grid with section header."""
    if not products:
        return ""

    section_title = content.get("section_title", "Products")
    columns = int(content.get("columns", 2))

    header_html = '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <p style="margin:0;%s;color:%s;">%s</p>
</td></tr>''' % (DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title))

    rows_html = ""
    for i in range(0, len(products), columns):
        row_products = products[i:i + columns]
        cells = ""
        w = "%d%%" % (100 // columns - 2)
        for p in row_products:
            cells += _render_product_card(p, width=w)
        while len(row_products) < columns:
            cells += '<td class="stack-col" style="width:%s;padding:6px;background:%s;"></td>' % (w, DESIGN["body_bg"])
            row_products.append(None)
        rows_html += '<tr>%s</tr>' % cells

    grid_html = '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
  %s
</table>''' % rows_html

    return header_html + '<tr><td style="padding:%s;background:%s;" class="mobile-pad">%s</td></tr>' % (
        DESIGN["grid_pad"], DESIGN["body_bg"], grid_html
    )


def _render_product_card(product, width="48%"):
    """Single product card — dark theme with spotlight glow."""
    title = html_mod.escape(product.get("title", "")[:60])
    image_url = product.get("image_url", "")
    price = product.get("price", "0.00")
    product_url = product.get("product_url", BRAND_URL)
    compare_price = product.get("compare_price", "")
    description = product.get("short_description", "")

    price_html = '<span style="font-size:20px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
    if compare_price and compare_price != price:
        price_html = (
            '<span style="font-size:14px;color:%s;text-decoration:line-through;margin-right:6px;">$%s</span>' % (DESIGN["price_strike"], compare_price)
            + price_html
        )

    if image_url:
        img_html = '''<a href="%s" style="text-decoration:none;display:block;">
          <img src="%s" alt="%s" width="100%%" style="display:block;border-radius:%s;max-width:100%%;" />
        </a>''' % (product_url, image_url, title, DESIGN["card_img_radius"])
    else:
        img_html = '''<div style="background:%s;height:180px;border-radius:%s;display:flex;align-items:center;justify-content:center;">
          <span style="color:%s;font-size:14px;">No image</span>
        </div>''' % (DESIGN["placeholder_bg"], DESIGN["card_img_radius"], DESIGN["placeholder_text"])

    # Description line
    desc_html = ""
    if description:
        desc_html = '<p style="margin:0 0 10px;font-size:13px;color:%s;line-height:1.5;">%s</p>' % (
            DESIGN["text_secondary"], html_mod.escape(description[:100])
        )

    return '''<td class="stack-col" style="width:%s;padding:8px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s;border-radius:%s;overflow:hidden;background:%s;box-shadow:%s;">
    <tr><td style="padding:0;background:%s;">%s</td></tr>
    <tr><td style="padding:%s;">
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="margin:0 0 8px;font-size:15px;font-weight:700;color:%s;line-height:1.4;">%s</p></a>
      %s
      <p style="margin:0 0 14px;">%s</p>
      <p style="margin:0;text-align:center;"><a href="%s" style="%s;background:%s;color:%s;width:100%%;box-sizing:border-box;">Shop Now</a></p>
    </td></tr>
  </table>
</td>''' % (width, DESIGN["card_border"], DESIGN["card_radius"], DESIGN["card_bg"], DESIGN["card_shadow"],
            DESIGN["spotlight"], img_html, DESIGN["card_inner_pad"],
            product_url, DESIGN["text_primary"], title,
            desc_html, price_html,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"])


def render_product_hero(content, products=None):
    """Large single-product feature block — dark elevated card."""
    # Support both: products kwarg (from render pipeline) and content dict fields (from test/showcase)
    if products:
        product = products[0]
    elif content.get("title") and content.get("image_url"):
        product = content
    else:
        return ""

    title = html_mod.escape(product.get("title", "")[:80])
    image_url = product.get("image_url", "")
    # Guardrail: reject placeholder/broken URLs — fall back to no-image card
    if image_url and ("placeholder" in image_url or not image_url.startswith("http")):
        image_url = ""
    price = product.get("price", "0.00")
    product_url = product.get("product_url", BRAND_URL)
    compare_price = product.get("compare_price", "")
    description = product.get("short_description", "")

    section_title = content.get("section_title", "Featured Product")
    cta_text = html_mod.escape(content.get("cta_text", "Shop Now"))

    # Section label
    label_html = '<tr><td style="padding:%s;background:%s;" class="mobile-pad"><p style="margin:0;%s;color:%s;">%s</p></td></tr>' % (
        DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
    )

    # Product image with spotlight glow
    if image_url:
        img_cell = '''<a href="%s" style="text-decoration:none;display:block;">
      <img src="%s" alt="%s" width="100%%" style="display:block;border-radius:%s;max-width:100%%;" />
    </a>''' % (product_url, image_url, title, DESIGN["card_img_radius"])
    else:
        img_cell = '''<div style="background:%s;height:240px;border-radius:%s;display:flex;align-items:center;justify-content:center;">
      <span style="color:%s;font-size:16px;">Product Image</span>
    </div>''' % (DESIGN["placeholder_bg"], DESIGN["card_img_radius"], DESIGN["placeholder_text"])

    # Price with SALE badge
    price_html = '<span style="font-size:24px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
    if compare_price and compare_price != price:
        price_html = (
            '<span style="font-size:15px;color:%s;text-decoration:line-through;margin-right:8px;">$%s</span>' % (DESIGN["price_strike"], compare_price)
            + price_html
            + ' <span style="display:inline-block;background:%s;color:#ffffff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;vertical-align:middle;margin-left:6px;">SALE</span>' % DESIGN["savings_green"]
        )

    # Description
    desc_html = ""
    if description:
        desc_html = '<p style="margin:0 0 20px;%s;color:%s;">%s</p>' % (
            DESIGN["body"], DESIGN["text_secondary"], html_mod.escape(description[:200])
        )

    # Elevated dark card (no CTA button — the dedicated cta block handles that)
    card_html = '''<tr><td style="padding:10px 30px 8px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s;border-radius:%s;overflow:hidden;background:%s;box-shadow:%s;">
    <tr><td style="padding:0;background:%s;">%s</td></tr>
    <tr><td style="padding:22px 24px 26px;">
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="%s;color:%s;margin-bottom:4px;">%s</p></a>
      <p style="margin:10px 0 16px;">%s</p>
      %s
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["card_border"], DESIGN["card_radius"], DESIGN["card_bg"], DESIGN["card_shadow_lg"],
                 DESIGN["spotlight"], img_cell,
                 product_url, DESIGN["h2"], DESIGN["text_primary"], title,
                 price_html, desc_html)

    return label_html + card_html


def render_comparison(content, products=None):
    """Side-by-side product comparison block — dark cards."""
    if not products or len(products) < 2:
        return ""

    section_title = content.get("section_title", "Compare Products")
    columns = min(int(content.get("columns", 2)), len(products), 3)
    compare_products = products[:columns]

    header_html = '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <p style="margin:0;%s;color:%s;">%s</p>
</td></tr>''' % (DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title))

    w = "%d%%" % (100 // columns - 2)
    cells = ""
    for p in compare_products:
        title = html_mod.escape(p.get("title", "")[:50])
        image_url = p.get("image_url", "")
        price = p.get("price", "0.00")
        product_url = p.get("product_url", BRAND_URL)
        compare_price = p.get("compare_price", "")

        price_html = '<span style="font-size:18px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
        if compare_price and compare_price != price:
            price_html = '<span style="font-size:13px;color:%s;text-decoration:line-through;margin-right:6px;">$%s</span>%s' % (
                DESIGN["price_strike"], compare_price, price_html
            )

        if image_url:
            img = '<a href="%s" style="text-decoration:none;"><img src="%s" alt="%s" width="100%%" style="display:block;border-radius:8px;max-width:100%%;" /></a>' % (
                product_url, image_url, title
            )
        else:
            img = '<div style="background:%s;height:140px;border-radius:8px;"></div>' % DESIGN["placeholder_bg"]

        cells += '''<td class="stack-col" style="width:%s;padding:6px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:%s;%s;overflow:hidden;">
    <tr><td style="padding:12px 12px 0;background:%s;">%s</td></tr>
    <tr><td style="padding:12px;">
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="margin:0 0 6px;font-size:13px;font-weight:600;color:%s;line-height:1.3;">%s</p></a>
      <p style="margin:0 0 10px;">%s</p>
      <a href="%s" style="%s;background:%s;color:%s;width:100%%;box-sizing:border-box;">Shop Now</a>
    </td></tr>
  </table>
</td>''' % (w, DESIGN["surface"], DESIGN["card_radius"], DESIGN["card_border"],
            DESIGN["spotlight"], img, product_url, DESIGN["text_primary"], title, price_html,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"])

    return header_html + '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>%s</tr></table>
</td></tr>''' % (DESIGN["grid_pad"], DESIGN["body_bg"], cells)


def render_trust(content):
    """Trust badges — lightweight inline row, no heavy card. Flows as a natural pause."""
    items = content.get("items", BLOCK_TYPES["trust_reassurance"]["defaults"]["items"])
    if not items:
        return ""

    _ICON_CHARS = {
        "package":  "&#x25A1;",
        "truck":    "&#x27A4;",
        "shield":   "&#x2714;",
        "star":     "&#x2605;",
        "maple":    "&#x2665;",
        "lock":     "&#x25CF;",
        "heart":    "&#x2665;",
        "check":    "&#x2714;",
        "clock":    "&#x25D4;",
        "gift":     "&#x2714;",
    }

    badges_html = ""
    for idx, item in enumerate(items[:4]):
        icon_key = item.get("icon", "check") if isinstance(item, dict) else "check"
        text = item.get("text", str(item)) if isinstance(item, dict) else str(item)
        icon_char = _ICON_CHARS.get(icon_key, "&#x2714;")

        # Dot separator between badges (not divider lines)
        sep = ""
        if idx > 0:
            sep = '<td style="width:12px;text-align:center;font-size:0;color:#333;vertical-align:middle;">&bull;</td>'

        badges_html += '''%s<td style="padding:6px 8px;text-align:center;vertical-align:middle;">
  <span style="display:inline-block;width:28px;height:28px;background:linear-gradient(135deg,%s,%s);border-radius:50%%;text-align:center;font-size:12px;color:#fff;line-height:28px;font-weight:700;vertical-align:middle;">%s</span>
  <span style="font-size:11px;font-weight:600;color:%s;vertical-align:middle;margin-left:4px;">%s</span>
</td>''' % (sep, DESIGN["brand"], DESIGN["brand_light"], icon_char, DESIGN["text_secondary"], html_mod.escape(text))

    # No card background — just a clean row with subtle top/bottom borders for rhythm
    return '''<tr><td style="padding:16px 24px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid %s;border-bottom:1px solid %s;">
    <tr>
      <td style="padding:14px 0;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
          <tr>%s</tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["surface_border"], DESIGN["surface_border"], badges_html)


def render_features(content):
    """Features & benefits — center-aligned with inline checkmarks for flow."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "")

    header_html = ""
    if section_title:
        header_html = '<p style="margin:0 0 18px;%s;color:%s;text-align:center;">%s</p>' % (
            DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
        )

    rows = ""
    capped = items[:6]
    for item in capped:
        text = html_mod.escape(str(item))
        rows += '''<tr>
  <td style="width:32px;vertical-align:middle;padding:7px 0;">
    <span style="display:inline-block;width:22px;height:22px;background:linear-gradient(135deg,%s,%s);border-radius:50%%;text-align:center;font-size:11px;color:#fff;line-height:22px;font-weight:700;">&#x2713;</span>
  </td>
  <td style="font-size:14px;line-height:1.6;color:%s;padding:7px 0;">%s</td>
</tr>''' % (DESIGN["brand"], DESIGN["brand_light"], DESIGN["text_primary"], text)

    # Center the feature list within a narrower container for visual focus
    return '''<tr><td style="padding:20px 30px;background:%s;text-align:center;" class="mobile-pad">
  %s
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="text-align:left;">
    %s
  </table>
</td></tr>''' % (DESIGN["body_bg"], header_html, rows)


def render_discount(content, discount_data=None):
    """Prominent discount code display block."""
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

    return '''<tr><td style="padding:0 30px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
    <tr><td style="background:linear-gradient(135deg, %s 0%%, %s 100%%);border-radius:14px;padding:28px 24px;text-align:center;">
      <p style="margin:0 0 6px;%s;letter-spacing:2px;color:rgba(255,255,255,0.75);">Your Exclusive Code</p>
      <p style="margin:0 0 6px;font-size:30px;font-weight:800;color:#ffffff;letter-spacing:4px;font-family:'Courier New',monospace;">%s</p>
      <p style="margin:0 0 4px;font-size:18px;font-weight:700;color:#ffffff;">%s</p>
      <p style="margin:0;font-size:16px;color:rgba(255,255,255,0.85);">%s</p>
      <p style="margin:10px 0 0;%s;color:rgba(255,255,255,0.6);">%s &bull; Single use only</p>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], BRAND_COLOR, BRAND_COLOR_DARK, DESIGN["label"], code, value_display, display_text, DESIGN["caption"], expires_text)


def render_cta(content):
    """CTA button — generous breathing room, blue glow draws the eye down."""
    text = html_mod.escape(content.get("text", "Shop Now"))
    url = content.get("url", BRAND_URL)

    secondary_text = content.get("secondary_text", "")
    secondary_url = content.get("secondary_url", "")

    secondary_html = ""
    if secondary_text and secondary_url:
        secondary_html = '<p style="margin:14px 0 0;font-size:13px;"><a href="%s" style="color:%s;text-decoration:underline;">%s</a></p>' % (
            secondary_url, DESIGN["text_secondary"], html_mod.escape(secondary_text)
        )

    return '''<tr><td style="padding:24px 30px 32px;text-align:center;" class="mobile-pad">
  <a href="%s" style="%s;background:%s;color:%s;box-shadow:0 0 28px rgba(6,60,255,0.35),0 4px 14px rgba(6,60,255,0.2);font-size:17px;padding:18px 52px;">%s</a>
  %s
</td></tr>''' % (url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], text, secondary_html)


def render_urgency(content):
    """Urgency bar — centered, lightweight, acts as a visual beat before CTA."""
    message = content.get("message", "")
    if not message:
        return ""
    safe = html_mod.escape(message)
    return '''<tr><td style="padding:8px 30px 14px;background:%s;text-align:center;" class="mobile-pad">
  <span style="display:inline-block;font-size:13px;font-weight:600;color:%s;letter-spacing:0.3px;padding:10px 24px;background:%s;border:1px solid %s;border-radius:24px;">&#9203; %s</span>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["urgency_text"], DESIGN["urgency_bg"], DESIGN["urgency_border"], safe)


def render_driver_testimonial(content):
    """Customer testimonial — center-aligned, minimal styling, flows in the narrative."""
    quote = html_mod.escape(content.get("quote", ""))
    author_name = html_mod.escape(content.get("author_name", ""))
    author_role = html_mod.escape(content.get("author_role", ""))
    product_name = html_mod.escape(content.get("product_name", ""))
    section_title = html_mod.escape(content.get("section_title", "What Customers Say"))
    rating = int(content.get("rating", 5))

    if not quote or not author_name:
        return ""

    # Star rating
    stars_html = ""
    for i in range(5):
        if i < min(rating, 5):
            stars_html += '<span style="color:%s;">&#9733;</span>' % DESIGN["star_gold"]
        else:
            stars_html += '<span style="color:#333;">&#9733;</span>'

    # Attribution
    attr_parts = [author_name]
    if author_role:
        attr_parts.append(author_role)
    if product_name:
        attr_parts.append(product_name)

    return '''<tr><td style="padding:20px 36px;background:%s;text-align:center;" class="mobile-pad">
  <p style="margin:0 0 12px;%s;color:%s;">%s</p>
  <p style="margin:0 0 10px;font-size:18px;letter-spacing:1px;">%s</p>
  <p style="margin:0 0 14px;font-size:16px;font-style:italic;color:%s;line-height:1.6;">&ldquo;%s&rdquo;</p>
  <p style="margin:0;font-size:13px;font-weight:600;color:%s;">%s</p>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], section_title,
                 stars_html, DESIGN["text_primary"], quote,
                 DESIGN["text_secondary"], " &bull; ".join(attr_parts))


def render_comparison_module(content, products=None):
    """Side-by-side product comparison — dark elevated cards with accent highlights."""
    if not products or len(products) < 2:
        return ""

    section_title = content.get("section_title", "Compare Your Options")
    columns = min(int(content.get("columns", 2)), len(products), 3)
    highlight_index = int(content.get("highlight_index", -1))
    cta_text = html_mod.escape(content.get("cta_text", "View Details"))
    compare_products = products[:columns]

    header_html = '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <p style="margin:0;%s;color:%s;">%s</p>
</td></tr>''' % (DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title))

    w = "%d%%" % (100 // columns - 2)
    cells = ""
    for idx, p in enumerate(compare_products):
        title = html_mod.escape(p.get("title", "")[:50])
        image_url = p.get("image_url", "")
        price = p.get("price", "0.00")
        product_url = p.get("product_url", BRAND_URL)
        compare_price = p.get("compare_price", "")
        description = p.get("short_description", "")

        is_highlighted = idx == highlight_index

        # Card styling: highlighted gets brand border, others get subtle border
        if is_highlighted:
            card_style = "background:%s;border-radius:%s;border:2px solid %s;overflow:hidden;" % (
                DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["brand"])
        else:
            card_style = "background:%s;border-radius:%s;%s;overflow:hidden;" % (
                DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["card_border"])

        # Top accent bar for highlighted card
        accent_bar = ""
        if is_highlighted:
            accent_bar = '<tr><td style="background:%s;height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>' % DESIGN["brand"]

        # Badge
        badge_html = ""
        if is_highlighted:
            badge_html = '<p style="margin:0 0 8px;text-align:center;"><span style="display:inline-block;background:%s;color:%s;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;padding:4px 14px;border-radius:20px;">Recommended</span></p>' % (DESIGN["badge_bg"], DESIGN["badge_text"])

        price_html = '<span style="font-size:20px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
        if compare_price and compare_price != price:
            price_html = '<span style="font-size:13px;color:%s;text-decoration:line-through;margin-right:6px;">$%s</span>%s' % (
                DESIGN["price_strike"], compare_price, price_html
            )

        if image_url:
            img = '<a href="%s" style="text-decoration:none;display:block;"><img src="%s" alt="%s" width="100%%" style="display:block;max-width:100%%;" /></a>' % (
                product_url, image_url, title
            )
        else:
            img = '<div style="background:%s;height:160px;"></div>' % DESIGN["placeholder_bg"]

        desc_html = ""
        if description:
            desc_html = '<p style="margin:0 0 10px;font-size:13px;color:%s;line-height:1.5;">%s</p>' % (
                DESIGN["text_secondary"], html_mod.escape(description[:100])
            )

        cells += '''<td class="stack-col" style="width:%s;padding:8px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s">
    %s
    <tr><td style="padding:0;background:%s;">%s</td></tr>
    <tr><td style="padding:16px 16px 18px;">
      %s
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="margin:0 0 8px;font-size:15px;font-weight:700;color:%s;line-height:1.3;">%s</p></a>
      %s
      <p style="margin:0 0 14px;">%s</p>
      <p style="margin:0;text-align:center;"><a href="%s" style="%s;background:%s;color:%s;width:100%%;box-sizing:border-box;">%s</a></p>
    </td></tr>
  </table>
</td>''' % (w, card_style,
            accent_bar, DESIGN["spotlight"], img, badge_html, product_url, DESIGN["text_primary"], title,
            desc_html, price_html,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"], cta_text)

    return header_html + '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>%s</tr></table>
</td></tr>''' % (DESIGN["grid_pad"], DESIGN["body_bg"], cells)


def render_why_choose_this(content):
    """Product-specific benefit statements — flat on dark with icon circles."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "Why You'll Love It")
    product_name = content.get("product_name", "")
    icon_style = content.get("icon_style", "check")

    if product_name:
        section_title = "Why %s" % html_mod.escape(product_name)

    header_html = '<p style="margin:0 0 16px;%s;color:%s;">%s</p>' % (
        DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
    )

    rows = ""
    for idx, item in enumerate(items[:6]):
        text = html_mod.escape(str(item))
        if icon_style == "number":
            icon = '<span style="display:inline-block;width:28px;height:28px;background:%s;color:#ffffff;border-radius:50%%;text-align:center;font-size:14px;font-weight:700;line-height:28px;">%d</span>' % (DESIGN["brand"], idx + 1)
        elif icon_style == "bullet":
            icon = '<span style="display:inline-block;width:28px;height:28px;background:%s;border-radius:50%%;text-align:center;font-size:16px;color:%s;line-height:28px;">&bull;</span>' % (DESIGN["brand_glow"], DESIGN["brand"])
        else:
            icon = '<span style="display:inline-block;width:28px;height:28px;background:%s;border-radius:50%%;text-align:center;font-size:14px;color:%s;line-height:28px;">&#x2713;</span>' % (DESIGN["brand_glow"], DESIGN["brand"])

        # Divider between items (not after last)
        divider = ""
        if idx < len(items[:6]) - 1:
            divider = '<tr><td colspan="2" style="padding:0;"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr><td style="border-bottom:1px solid %s;font-size:0;line-height:0;height:1px;">&nbsp;</td></tr></table></td></tr>' % DESIGN["divider_color"]

        rows += '''<tr>
  <td style="width:40px;vertical-align:middle;padding:10px 0;">%s</td>
  <td style="%s;color:%s;padding:10px 0;">%s</td>
</tr>%s''' % (icon, DESIGN["body"], DESIGN["text_primary"], text, divider)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="padding:0;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
        %s
      </table>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, rows)


def render_objection_handling(content):
    """Q&A or statement-style objection handling — dark card styling."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = html_mod.escape(content.get("section_title", "Quick Answers"))
    style = content.get("style", "qa")

    header_html = '<p style="margin:0 0 16px;%s;color:%s;">%s</p>' % (
        DESIGN["label"], DESIGN["brand"], section_title
    )

    valid_items = [item for item in items[:4] if isinstance(item, dict) and item.get("objection") and item.get("answer")]
    items_html = ""
    for idx, item in enumerate(valid_items):
        objection = html_mod.escape(item.get("objection", ""))
        answer = html_mod.escape(item.get("answer", ""))

        # Divider between items (not before first)
        divider = ""
        if idx > 0:
            divider = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr><td style="border-top:1px solid %s;font-size:0;line-height:0;height:1px;padding:0;">&nbsp;</td></tr></table>' % DESIGN["divider_color"]

        if style == "statement":
            items_html += '''%s<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="padding:12px 0;">
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:2px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:12px;color:#ffffff;line-height:24px;">&#x2717;</span>
    </td>
    <td style="padding:0 0 6px;font-size:14px;color:%s;text-decoration:line-through;line-height:1.5;">&ldquo;%s&rdquo;</td>
  </tr>
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:2px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:12px;color:#ffffff;line-height:24px;">&#x2713;</span>
    </td>
    <td style="padding:0;font-size:15px;color:%s;font-weight:600;line-height:1.5;">%s</td>
  </tr>
</table>''' % (divider, DESIGN["price_strike"], DESIGN["text_tertiary"], objection,
               DESIGN["savings_green"], DESIGN["text_primary"], answer)
        else:
            items_html += '''%s<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="padding:12px 0;">
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:3px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:11px;font-weight:800;color:#ffffff;line-height:24px;">Q</span>
    </td>
    <td style="font-size:15px;font-weight:600;color:%s;line-height:1.5;padding-bottom:6px;">%s</td>
  </tr>
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:3px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:11px;font-weight:800;color:#ffffff;line-height:24px;">A</span>
    </td>
    <td style="%s;color:%s;">%s</td>
  </tr>
</table>''' % (divider, DESIGN["brand"], DESIGN["text_primary"], objection,
               DESIGN["savings_green"], DESIGN["body"], DESIGN["text_secondary"], answer)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:14px;%s;">
    <tr><td style="padding:16px 24px;">%s</td></tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, DESIGN["card_bg"], DESIGN["card_border"], items_html)


def render_bundle_value(content, products=None):
    """Bundle value block with premium product cards and savings callout."""
    items = content.get("items", [])
    bundle_price = content.get("bundle_price", "0.00")
    section_title = html_mod.escape(content.get("section_title", "Better Together"))
    savings_text = content.get("savings_text", "")
    cta_text = html_mod.escape(content.get("cta_text", "Shop the Bundle"))
    cta_url = content.get("cta_url", BRAND_URL)

    # Use explicit items if provided, otherwise fall back to resolved products
    bundle_items = items if items else (products or [])
    if len(bundle_items) < 2:
        return ""

    bundle_items = bundle_items[:3]

    # Calculate total value and savings
    total_value = 0.0
    for item in bundle_items:
        try:
            total_value += float(item.get("price", 0))
        except (ValueError, TypeError):
            pass

    try:
        bundle_price_f = float(bundle_price)
    except (ValueError, TypeError):
        bundle_price_f = total_value

    if not savings_text and total_value > bundle_price_f:
        savings_text = "Save $%.2f" % (total_value - bundle_price_f)

    header_html = '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <p style="margin:0;%s;color:%s;">%s</p>
</td></tr>''' % (DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], section_title)

    # Product cells with styled + separator
    product_cells = ""
    w = "%d%%" % (90 // len(bundle_items))
    for idx, item in enumerate(bundle_items):
        title = html_mod.escape(str(item.get("title", ""))[:40])
        image_url = item.get("image_url", "")
        price = item.get("price", "0.00")
        product_url = item.get("product_url", BRAND_URL)

        if image_url:
            img = '<a href="%s" style="text-decoration:none;display:block;"><img src="%s" alt="%s" width="100%%" style="display:block;border-radius:10px;max-width:100%%;" /></a>' % (
                product_url, image_url, title
            )
        else:
            img = '<div style="background:%s;height:100px;border-radius:10px;"></div>' % DESIGN["placeholder_bg"]

        product_cells += '''<td class="stack-col" style="width:%s;padding:6px;vertical-align:top;text-align:center;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:10px;%s;">
    <tr><td style="padding:10px 10px 0;">%s</td></tr>
    <tr><td style="padding:10px;">
      <p style="margin:0 0 4px;font-size:13px;font-weight:600;color:%s;line-height:1.3;">
        <a href="%s" style="text-decoration:none;color:inherit;">%s</a>
      </p>
      <p style="margin:0;font-size:14px;font-weight:700;color:%s;">$%s</p>
    </td></tr>
  </table>
</td>''' % (w, DESIGN["card_bg"], DESIGN["card_border"], img, DESIGN["text_primary"], product_url, title, DESIGN["price_color"], price)

        # Add styled + separator between items
        if idx < len(bundle_items) - 1:
            product_cells += '''<td style="width:30px;text-align:center;vertical-align:middle;">
  <span style="display:inline-block;width:28px;height:28px;background:%s;border-radius:50%%;text-align:center;font-size:18px;font-weight:700;color:%s;line-height:28px;">+</span>
</td>''' % (DESIGN["brand_glow"], DESIGN["brand"])

    # Pricing summary with savings pill
    pricing_html = ""
    if total_value > bundle_price_f:
        pricing_html = '<p style="margin:0 0 6px;font-size:14px;color:%s;text-decoration:line-through;">Total Value: $%.2f</p>' % (DESIGN["price_strike"], total_value)
    pricing_html += '<p style="margin:0 0 6px;font-size:26px;font-weight:800;color:%s;">$%s</p>' % (DESIGN["price_color"], bundle_price)
    if savings_text:
        pricing_html += '<p style="margin:0 0 16px;"><span style="display:inline-block;background:%s;color:#ffffff;font-size:13px;font-weight:700;padding:6px 16px;border-radius:20px;">&#x2713; %s</span></p>' % (DESIGN["savings_green"], html_mod.escape(savings_text))

    return header_html + '''<tr><td style="padding:10px 24px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:14px;%s;padding:16px 10px;">
    <tr>%s</tr>
  </table>
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="text-align:center;padding:20px 0 8px;">
      %s
      <a href="%s" style="%s;background:%s;color:%s;">%s</a>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["surface"], DESIGN["card_border"], product_cells, pricing_html, cta_url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], cta_text)


def render_best_seller_proof(content, products=None):
    """Product cards with social proof badges — dark theme with spotlight glow."""
    if not products:
        return ""

    section_title = content.get("section_title", "Customer Favourites")
    proof_line = content.get("proof_line", "")
    badge_text = content.get("badge_text", "")
    show_rating = content.get("show_rating", True)

    header_html = '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <p style="margin:0;%s;color:%s;">%s</p>
</td></tr>''' % (DESIGN["section_pad_top"], DESIGN["body_bg"], DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title))

    columns = min(len(products), 2)
    w = "%d%%" % (100 // columns - 2)
    rows_html = ""
    for i in range(0, min(len(products), 4), columns):
        row_products = products[i:i + columns]
        cells = ""
        for p in row_products:
            title = html_mod.escape(p.get("title", "")[:50])
            image_url = p.get("image_url", "")
            price = p.get("price", "0.00")
            product_url = p.get("product_url", BRAND_URL)
            compare_price = p.get("compare_price", "")

            if image_url:
                img_html = '<a href="%s" style="text-decoration:none;display:block;"><img src="%s" alt="%s" width="100%%" style="display:block;border-radius:%s;max-width:100%%;" /></a>' % (
                    product_url, image_url, title, DESIGN["card_img_radius"]
                )
            else:
                img_html = '<div style="background:%s;height:180px;border-radius:%s;"></div>' % (
                    DESIGN["placeholder_bg"], DESIGN["card_img_radius"]
                )

            # Badge pill
            badge_html = ""
            if badge_text:
                badge_html = '<p style="margin:0 0 8px;"><span style="display:inline-block;background:%s;color:%s;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;padding:4px 12px;border-radius:20px;">%s</span></p>' % (DESIGN["badge_bg"], DESIGN["badge_text"], html_mod.escape(badge_text))

            # Star rating with numeric value
            rating_html = ""
            if show_rating:
                rating_html = '<p style="margin:0 0 6px;font-size:13px;color:%s;letter-spacing:1px;">&#9733;&#9733;&#9733;&#9733;&#9733; <span style="font-size:12px;color:%s;letter-spacing:0;">(4.8)</span></p>' % (DESIGN["star_gold"], DESIGN["text_tertiary"])

            # Proof line — social proof text
            proof_html = ""
            if proof_line:
                proof_html = '<p style="margin:0 0 6px;font-size:12px;font-weight:600;color:%s;">%s</p>' % (DESIGN["savings_green"], html_mod.escape(proof_line))

            # Price with optional compare
            price_html = '<span style="font-size:20px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
            if compare_price and compare_price != price:
                price_html = '<span style="font-size:13px;color:%s;text-decoration:line-through;margin-right:6px;">$%s</span>%s' % (
                    DESIGN["price_strike"], compare_price, price_html)

            cells += '''<td class="stack-col" style="width:%s;padding:8px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s;border-radius:%s;overflow:hidden;background:%s;">
    <tr><td style="padding:0;background:%s;">%s</td></tr>
    <tr><td style="padding:%s;">
      %s
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="margin:0 0 6px;font-size:15px;font-weight:700;color:%s;line-height:1.4;">%s</p></a>
      %s
      %s
      <p style="margin:0 0 14px;">%s</p>
      <p style="margin:0;text-align:center;"><a href="%s" style="%s;background:%s;color:%s;width:100%%;box-sizing:border-box;">Shop Now</a></p>
    </td></tr>
  </table>
</td>''' % (w, DESIGN["card_border"], DESIGN["card_radius"], DESIGN["card_bg"],
            DESIGN["spotlight"], img_html, DESIGN["card_inner_pad"],
            badge_html, product_url, DESIGN["text_primary"], title,
            rating_html, proof_html, price_html,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"])

        rows_html += '<tr>%s</tr>' % cells

    grid_html = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">%s</table>' % rows_html

    return header_html + '<tr><td style="padding:%s;background:%s;" class="mobile-pad">%s</td></tr>' % (DESIGN["grid_pad"], DESIGN["body_bg"], grid_html)


def render_feature_highlights(content):
    """Quick-scan feature list — dark theme with circle icons, optional 2-column."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "Why LDAS")
    icon_type = content.get("icon_type", "check")
    columns = int(content.get("columns", 1))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;">%s</p>' % (
        DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
    )

    # Choose icon character
    if icon_type == "arrow":
        icon_char = "&#x2192;"
    elif icon_type == "dot":
        icon_char = "&bull;"
    else:
        icon_char = "&#x2713;"

    # Build icon HTML with circle background
    def _icon_html():
        return '<span style="display:inline-block;width:26px;height:26px;background:%s;border-radius:50%%;text-align:center;font-size:13px;color:%s;line-height:26px;">%s</span>' % (
            DESIGN["brand_glow"], DESIGN["brand"], icon_char)

    if columns == 2 and len(items) >= 4:
        # 2-column grid layout
        rows = ""
        for i in range(0, min(len(items), 8), 2):
            row_items = items[i:i + 2]
            cells = ""
            for item in row_items:
                text = html_mod.escape(str(item))
                cells += '''<td style="width:50%%;vertical-align:top;padding:6px 4px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="width:34px;vertical-align:middle;padding-right:6px;">%s</td>
      <td style="font-size:14px;color:%s;line-height:1.5;">%s</td>
    </tr>
  </table>
</td>''' % (_icon_html(), DESIGN["text_primary"], text)
            if len(row_items) < 2:
                cells += '<td style="width:50%%;"></td>'
            rows += '<tr>%s</tr>' % cells

        inner = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">%s</table>' % rows
    else:
        # Single column
        rows = ""
        for item in items[:8]:
            text = html_mod.escape(str(item))
            rows += '''<tr>
  <td style="width:38px;vertical-align:middle;padding:7px 0;">%s</td>
  <td style="%s;color:%s;padding:7px 0;">%s</td>
</tr>''' % (_icon_html(), DESIGN["body"], DESIGN["text_primary"], text)

        inner = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">%s</table>' % rows

    # Flat on dark body — no card wrapper
    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  %s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, inner)


# =========================================================================
#  CONTENT-RICH MODULE RENDERERS
# =========================================================================

def render_competitor_comparison(content):
    """Competitor comparison grid — LDAS vs named competitors with check/X marks."""
    competitors = content.get("competitors", [])
    rows = content.get("rows", [])
    if not competitors or not rows:
        return ""

    section_title = html_mod.escape(content.get("section_title", "How We Compare"))
    ldas_label = html_mod.escape(content.get("ldas_label", "LDAS"))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], section_title
    )

    check = '<span style="color:%s;font-size:18px;font-weight:bold;">&#10003;</span>' % DESIGN["savings_green"]
    x_mark = '<span style="color:#ef4444;font-size:18px;font-weight:bold;">&#10007;</span>'

    # Header row
    comp_headers = ""
    for comp in competitors[:2]:
        comp_headers += '<td style="padding:10px 8px;text-align:center;%s;color:%s;background:%s;">%s</td>' % (
            DESIGN["label"], DESIGN["text_tertiary"], DESIGN["surface"], html_mod.escape(comp)
        )
    thead = '''<tr>
  <td style="padding:10px 8px;%s;color:%s;background:%s;">&nbsp;</td>
  <td style="padding:10px 8px;text-align:center;font-weight:700;color:%s;background:%s;">%s</td>
  %s
</tr>''' % (DESIGN["label"], DESIGN["text_tertiary"], DESIGN["surface"],
            DESIGN["brand"], DESIGN["brand_glow"], ldas_label, comp_headers)

    # Data rows
    tbody = ""
    for idx, row in enumerate(rows[:8]):
        if not isinstance(row, dict):
            continue
        feature = html_mod.escape(row.get("feature", ""))
        ldas_val = check if row.get("ldas", False) else x_mark
        bg = DESIGN["surface"] if idx % 2 == 0 else DESIGN["body_bg"]

        comp_cells = ""
        comp_vals = row.get("competitors", [])
        for ci, comp in enumerate(competitors[:2]):
            val = comp_vals[ci] if ci < len(comp_vals) else False
            comp_cells += '<td style="padding:10px 8px;text-align:center;background:%s;">%s</td>' % (
                bg, check if val else x_mark
            )

        tbody += '''<tr>
  <td style="padding:10px 8px;font-size:14px;color:%s;background:%s;">%s</td>
  <td style="padding:10px 8px;text-align:center;background:%s;">%s</td>
  %s
</tr>''' % (DESIGN["text_secondary"], bg, feature,
            DESIGN["brand_glow"] if idx % 2 == 0 else "rgba(6,60,255,0.08)", ldas_val, comp_cells)

    table = '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="border-radius:%s;overflow:hidden;%s;">
  %s%s
</table>''' % (DESIGN["card_radius"], DESIGN["card_border"], thead, tbody)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s%s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, table)


def render_spec_table(content):
    """Product spec table — single product (label/value rows) or multi-product comparison."""
    products = content.get("products", [])
    rows = content.get("rows", [])
    if not rows:
        return ""

    rows = rows[:12]
    section_title = html_mod.escape(content.get("section_title", "Compare Specs" if len(products) >= 2 else "Specifications"))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], section_title
    )

    # Single-product mode: rows have label + value directly
    if len(products) < 2:
        product_name = html_mod.escape(content.get("product_name", ""))
        thead = ""
        if product_name:
            thead = '<tr><td colspan="2" style="padding:10px 8px;text-align:center;font-weight:700;color:%s;background:%s;">%s</td></tr>' % (
                DESIGN["text_primary"], DESIGN["surface"], product_name
            )
        tbody = ""
        for ri, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            label = html_mod.escape(row.get("label", ""))
            value = html_mod.escape(str(row.get("value", "")))
            bg = DESIGN["surface"] if ri % 2 == 0 else DESIGN["body_bg"]
            tbody += '<tr><td style="padding:10px 12px;font-size:14px;color:%s;background:%s;width:40%%;">%s</td>' % (
                DESIGN["text_secondary"], bg, label
            )
            tbody += '<td style="padding:10px 12px;font-size:14px;font-weight:600;color:%s;background:%s;text-align:right;">%s</td></tr>' % (
                DESIGN["text_primary"], bg, value
            )
    else:
        # Multi-product comparison mode
        products = products[:3]
        highlight_index = int(content.get("highlight_index", -1))

        prod_headers = ""
        for pi, prod in enumerate(products):
            bg = DESIGN["brand_glow"] if pi == highlight_index else DESIGN["surface"]
            prod_headers += '<td style="padding:10px 8px;text-align:center;font-weight:700;color:%s;background:%s;">%s</td>' % (
                DESIGN["text_primary"], bg, html_mod.escape(prod.get("name", ""))
            )
        thead = '<tr><td style="padding:10px 8px;background:%s;">&nbsp;</td>%s</tr>' % (
            DESIGN["surface"], prod_headers
        )

        tbody = ""
        for ri, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            label = html_mod.escape(row.get("label", ""))
            key = row.get("key", "")
            bg = DESIGN["surface"] if ri % 2 == 0 else DESIGN["body_bg"]

            cells = ""
            for pi, prod in enumerate(products):
                val = html_mod.escape(str(prod.get("specs", {}).get(key, "\u2014")))
                cell_bg = DESIGN["brand_glow"] if pi == highlight_index else bg
                cells += '<td style="padding:10px 8px;text-align:center;font-size:14px;color:%s;background:%s;">%s</td>' % (
                    DESIGN["text_primary"], cell_bg, val
                )

            tbody += '<tr><td style="padding:10px 8px;font-size:14px;color:%s;background:%s;">%s</td>%s</tr>' % (
                DESIGN["text_secondary"], bg, label, cells
            )

    table = '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="border-radius:%s;overflow:hidden;%s;">
  %s%s
</table>''' % (DESIGN["card_radius"], DESIGN["card_border"], thead, tbody)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s%s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, table)


def render_stat_callout(content):
    """3 bold hero-style stat numbers — big value, small label."""
    stats = content.get("stats", [])
    if len(stats) != 3:
        return ""

    section_title = content.get("section_title", "")
    accent = content.get("accent_color", "") or DESIGN["brand"]

    header_html = ""
    if section_title:
        header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
            DESIGN["label"], DESIGN["text_tertiary"], html_mod.escape(section_title)
        )

    cells = ""
    for idx, stat in enumerate(stats[:3]):
        if not isinstance(stat, dict):
            continue
        value = html_mod.escape(str(stat.get("value", "")))[:10]
        label = html_mod.escape(str(stat.get("label", "")))[:20]

        border_left = "border-left:1px solid %s;" % DESIGN["surface_border"] if idx > 0 else ""
        cells += '''<td style="width:33.3%%;text-align:center;padding:16px 8px;%s">
  <p style="margin:0;font-size:28px;font-weight:800;color:%s;line-height:1.2;">%s</p>
  <p style="margin:6px 0 0;font-size:12px;color:%s;">%s</p>
</td>''' % (border_left, accent, value, DESIGN["text_secondary"], label)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr>%s</tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, cells)


def render_whats_included(content):
    """What's in the box list — checklist with optional product image."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "What's Included")
    product_name = content.get("product_name", "")
    image_url = content.get("image_url", "")

    if product_name:
        title_text = "What's in the %s Box" % html_mod.escape(product_name)
    else:
        title_text = html_mod.escape(section_title)

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], title_text
    )

    check_icon = '<span style="display:inline-block;width:26px;height:26px;background:%s;border-radius:50%%;text-align:center;font-size:13px;color:%s;line-height:26px;">&#x2713;</span>' % (
        DESIGN["brand_glow"], DESIGN["brand"]
    )

    list_rows = ""
    for item in items[:8]:
        # Handle both string items and dict items (e.g. {"name": "..."})
        if isinstance(item, dict):
            text = item.get("name", item.get("text", str(item)))
        else:
            text = str(item)
        text = html_mod.escape(text)[:40]
        list_rows += '''<tr>
  <td style="width:38px;vertical-align:middle;padding:6px 0;">%s</td>
  <td style="font-size:14px;color:%s;padding:6px 0;line-height:1.5;">%s</td>
</tr>''' % (check_icon, DESIGN["text_primary"], text)

    list_html = '<table role="presentation" cellpadding="0" cellspacing="0" border="0">%s</table>' % list_rows

    if image_url:
        inner = '''<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:%s;%s;">
  <tr>
    <td style="width:40%%;vertical-align:top;padding:0;">
      <img src="%s" alt="%s" style="width:100%%;display:block;border-radius:%s 0 0 %s;object-fit:cover;" />
    </td>
    <td style="width:60%%;vertical-align:top;padding:20px 24px;">%s</td>
  </tr>
</table>''' % (DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["card_border"],
               html_mod.escape(image_url), html_mod.escape(product_name or "Product"),
               DESIGN["card_radius"], DESIGN["card_radius"], list_html)
    else:
        inner = '<div style="max-width:360px;margin:0 auto;">%s</div>' % list_html

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s%s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, inner)


def render_faq(content):
    """Educational Q&A block — flat on dark body with circle badges."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = html_mod.escape(content.get("section_title", "Common Questions"))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], section_title
    )

    valid_items = [item for item in items[:4] if isinstance(item, dict) and item.get("question") and item.get("answer")]
    items_html = ""
    for idx, item in enumerate(valid_items):
        question = html_mod.escape(item.get("question", ""))
        answer = html_mod.escape(item.get("answer", ""))

        divider = ""
        if idx > 0:
            divider = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr><td style="border-top:1px solid %s;font-size:0;line-height:0;height:1px;padding:0;">&nbsp;</td></tr></table>' % DESIGN["divider_color"]

        items_html += '''%s<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="padding:12px 0;">
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:3px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:11px;font-weight:800;color:%s;line-height:24px;">Q</span>
    </td>
    <td style="font-size:15px;font-weight:600;color:%s;line-height:1.5;padding-bottom:6px;">%s</td>
  </tr>
  <tr>
    <td style="width:32px;vertical-align:top;padding-top:3px;">
      <span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:11px;font-weight:800;color:%s;line-height:24px;">A</span>
    </td>
    <td style="%s;color:%s;">%s</td>
  </tr>
</table>''' % (divider, DESIGN["brand_glow"], DESIGN["brand"], DESIGN["text_primary"], question,
               DESIGN["surface"], DESIGN["text_secondary"], DESIGN["body"], DESIGN["text_secondary"], answer)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s%s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, items_html)


def render_use_case_match(content):
    """Use-case segmentation — 2-3 persona cards with product recommendations."""
    cases = content.get("cases", [])
    if not cases:
        return ""

    cases = cases[:3]
    section_title = html_mod.escape(content.get("section_title", "Find Your Perfect Match"))
    cta_text = html_mod.escape(content.get("cta_text", "Shop Now"))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], section_title
    )

    width = "48%%" if len(cases) == 2 else "31%%"
    cards = ""
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        persona = html_mod.escape(case.get("persona", ""))
        description = html_mod.escape(case.get("description", ""))
        product_name = html_mod.escape(case.get("product_name", ""))
        product_url = html_mod.escape(case.get("product_url", BRAND_URL))

        cards += '''<td style="width:%s;vertical-align:top;padding:0 6px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:%s;%s;">
    <tr><td style="%s;text-align:center;">
      <p style="margin:0 0 8px;font-size:16px;font-weight:700;color:%s;">%s</p>
      <p style="margin:0 0 12px;font-size:13px;color:%s;line-height:1.5;">%s</p>
      <p style="margin:0 0 14px;font-size:14px;font-weight:700;color:%s;">%s</p>
      <a href="%s" style="%s;background:%s;color:%s;" target="_blank">%s</a>
    </td></tr>
  </table>
</td>''' % (width, DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["card_border"],
            DESIGN["card_inner_pad"], DESIGN["text_primary"], persona,
            DESIGN["text_secondary"], description,
            DESIGN["brand"], product_name,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"], cta_text)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr>%s</tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, cards)


def render_brand_story(content):
    """Brand narrative block — mission, sustainability, or heritage variant."""
    headline = content.get("headline", "")
    body = content.get("body", "")
    if not headline or not body:
        return ""

    section_title = content.get("section_title", "")
    variant = content.get("variant", "mission")
    badges = content.get("badges", [])
    cta_text = content.get("cta_text", "")
    cta_url = content.get("cta_url", "") or BRAND_URL

    if not badges:
        badges = _BRAND_STORY_BADGES.get(variant, _BRAND_STORY_BADGES["mission"])

    header_html = ""
    if section_title:
        header_html = '<p style="margin:0 0 10px;%s;color:%s;text-align:center;">%s</p>' % (
            DESIGN["label"], DESIGN["text_tertiary"], html_mod.escape(section_title)
        )

    headline_html = '<p style="%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["h2"], DESIGN["text_primary"], html_mod.escape(headline)[:50]
    )

    body_html = '<p style="margin:12px 0 0;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["body"], DESIGN["text_secondary"], html_mod.escape(body)[:200]
    )

    badge_parts = []
    for badge in badges[:4]:
        if isinstance(badge, dict):
            icon = badge.get("icon", "")
            text = html_mod.escape(badge.get("text", ""))
            badge_parts.append('%s %s' % (icon, text))
    badge_html = ""
    if badge_parts:
        sep = ' <span style="color:%s;">&middot;</span> ' % DESIGN["text_tertiary"]
        badge_html = '<p style="margin:20px 0 0;font-size:12px;color:%s;text-align:center;">%s</p>' % (
            DESIGN["text_tertiary"], sep.join(badge_parts)
        )

    cta_html = ""
    if cta_text:
        cta_html = '''<p style="margin:16px 0 0;text-align:center;">
  <a href="%s" style="%s;background:%s;color:%s;" target="_blank">%s</a>
</p>''' % (html_mod.escape(cta_url), DESIGN["btn_primary"], DESIGN["btn_primary_bg"],
           DESIGN["btn_primary_text"], html_mod.escape(cta_text))

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <div style="max-width:520px;margin:0 auto;">
    %s%s%s%s%s
  </div>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"],
                 header_html, headline_html, body_html, badge_html, cta_html)


def render_divider(content):
    """Simple horizontal divider — dark theme."""
    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="border-top:1px solid %s;"></td></tr>
  </table>
</td></tr>''' % (DESIGN["section_pad_tight"], DESIGN["body_bg"], DESIGN["divider_color"])


# =========================================================================
#  PRODUCT DATA RESOLUTION
# =========================================================================

def resolve_products_for_contact(contact, limit=4):
    """Resolve product data for a contact using profile-based fallback chain.

    Chain: recommendations -> top_products -> last_viewed -> popular products.
    Returns list of product dicts: [{title, image_url, price, product_url, compare_price}]
    """
    try:
        from shopify_products import get_products_for_email, get_popular_products
    except ImportError:
        return []

    if not contact:
        try:
            return get_popular_products(limit=limit)
        except Exception:
            return []

    results = []
    seen_titles = set()

    def _add_products(refs):
        if not refs:
            return
        found = get_products_for_email(refs, limit=limit)
        for p in found:
            if p["title"] not in seen_titles and len(results) < limit:
                seen_titles.add(p["title"])
                results.append(p)

    try:
        from database import CustomerProfile
        profile = CustomerProfile.get(CustomerProfile.contact == contact)

        # 1. Recommendations
        recs = json.loads(profile.product_recommendations or "[]")
        _add_products(recs)

        # 2. Top products
        if len(results) < limit:
            tops = json.loads(profile.top_products or "[]")
            _add_products(tops)

        # 3. Last viewed
        if len(results) < limit and profile.last_viewed_product:
            _add_products([profile.last_viewed_product])

    except Exception:
        pass

    # 4. Popular products fallback
    if len(results) < limit:
        try:
            popular = get_popular_products(limit=limit)
            for p in popular:
                if p["title"] not in seen_titles and len(results) < limit:
                    seen_titles.add(p["title"])
                    results.append(p)
        except Exception:
            pass

    return results[:limit]


# =========================================================================
#  BLOCK RENDERER DISPATCH
# =========================================================================

_BLOCK_RENDERERS = {
    "hero":              lambda content, **kw: render_hero(content),
    "text":              lambda content, **kw: render_text(content),
    "product_grid":      lambda content, **kw: render_product_grid(content, products=kw.get("products")),
    "product_hero":      lambda content, **kw: render_product_hero(content, products=kw.get("products")),
    "comparison_block":  lambda content, **kw: render_comparison(content, products=kw.get("products")),
    "trust_reassurance": lambda content, **kw: render_trust(content),
    "features_benefits": lambda content, **kw: render_features(content),
    "discount":          lambda content, **kw: render_discount(content, discount_data=kw.get("discount")),
    "cta":               lambda content, **kw: render_cta(content),
    "urgency":           lambda content, **kw: render_urgency(content),
    "divider":           lambda content, **kw: render_divider(content),
    # New persuasion modules
    "driver_testimonial":lambda content, **kw: render_driver_testimonial(content),
    "comparison":        lambda content, **kw: render_comparison_module(content, products=kw.get("products")),
    "why_choose_this":   lambda content, **kw: render_why_choose_this(content),
    "objection_handling":lambda content, **kw: render_objection_handling(content),
    "bundle_value":      lambda content, **kw: render_bundle_value(content, products=kw.get("products")),
    "best_seller_proof": lambda content, **kw: render_best_seller_proof(content, products=kw.get("products")),
    "feature_highlights":lambda content, **kw: render_feature_highlights(content),
    # Content-rich modules
    "competitor_comparison": lambda content, **kw: render_competitor_comparison(content),
    "spec_table":           lambda content, **kw: render_spec_table(content),
    "stat_callout":         lambda content, **kw: render_stat_callout(content),
    "whats_included":       lambda content, **kw: render_whats_included(content),
    "faq":                  lambda content, **kw: render_faq(content),
    "use_case_match":       lambda content, **kw: render_use_case_match(content),
    "brand_story":          lambda content, **kw: render_brand_story(content),
}


# =========================================================================
#  VISUAL FLOW — Inter-block spacing for cohesive design
# =========================================================================

# Block type categories for flow logic
_INTRO_BLOCKS = {"hero"}
_CONTENT_BLOCKS = {"text", "features_benefits", "feature_highlights", "why_choose_this"}
_PROOF_BLOCKS = {"trust_reassurance", "driver_testimonial", "best_seller_proof"}
_ACTION_BLOCKS = {"cta", "urgency", "discount"}
_PRODUCT_BLOCKS = {"product_grid", "product_hero", "comparison", "comparison_block", "bundle_value"}


def _get_flow_spacer(prev_type, next_type):
    """Return an inter-block HTML spacer row that creates visual flow.

    Rules:
    - Hero → anything: NO spacer (hero has tight bottom padding, flows directly)
    - Content → Content: tiny spacer (continuous reading flow)
    - Content → Proof/Trust: thin accent divider (section shift)
    - Anything → CTA: breathing room (let CTA be the focal point)
    - Anything → Product: section label spacer
    - Proof → Action: no spacer (trust flows right into CTA)
    """
    if prev_type is None:
        return None

    bg = DESIGN["body_bg"]

    # Hero flows directly into next block — no gap
    if prev_type in _INTRO_BLOCKS:
        return None

    # Urgency flows directly into CTA — no gap
    if prev_type == "urgency" and next_type == "cta":
        return None

    # CTA after discount — minimal gap
    if prev_type == "discount" and next_type == "cta":
        return '<tr><td style="height:6px;background:%s;font-size:0;line-height:0;">&nbsp;</td></tr>' % bg

    # Trust/proof flows into action — tight connection
    if prev_type in _PROOF_BLOCKS and next_type in _ACTION_BLOCKS:
        return '<tr><td style="height:8px;background:%s;font-size:0;line-height:0;">&nbsp;</td></tr>' % bg

    # Content → Content: minimal breathing
    if prev_type in _CONTENT_BLOCKS and next_type in _CONTENT_BLOCKS:
        return None  # text blocks have their own padding

    # Before product blocks: subtle section divider
    if next_type in _PRODUCT_BLOCKS:
        return '<tr><td style="padding:8px 60px;background:%s;"><div style="height:1px;background:linear-gradient(90deg,transparent,%s,transparent);"></div></td></tr>' % (
            bg, DESIGN["surface_border"]
        )

    # Content → Proof: subtle gradient divider (section transition)
    if prev_type in _CONTENT_BLOCKS and next_type in _PROOF_BLOCKS:
        return '<tr><td style="padding:6px 60px;background:%s;"><div style="height:1px;background:linear-gradient(90deg,transparent,%s,transparent);"></div></td></tr>' % (
            bg, DESIGN["surface_border"]
        )

    # Before any action block: breathing room
    if next_type in _ACTION_BLOCKS:
        return '<tr><td style="height:10px;background:%s;font-size:0;line-height:0;">&nbsp;</td></tr>' % bg

    # Default: small breathing spacer
    return '<tr><td style="height:6px;background:%s;font-size:0;line-height:0;">&nbsp;</td></tr>' % bg


# =========================================================================
#  MASTER RENDER FUNCTION
# =========================================================================

def render_template_blocks(template, contact=None, products=None, discount=None, explain=False):
    """
    Render a blocks-format EmailTemplate to complete HTML email.

    If blocks contain variants, resolves them against the contact's
    profile using condition_engine before rendering. First-match-wins.

    Auto-resolves product data from contact profile if products not provided.

    Args:
        template: EmailTemplate instance with template_format="blocks"
        contact:  Contact instance (for personalization tokens + variant resolution)
        products: list of product dicts [{title, image_url, price, product_url, compare_price}]
        discount: discount dict {code, value_display, display_text, expires_text}
        explain:  if True, return (html, explain_list) tuple for preview explainability

    Returns:
        str: Complete HTML email document ready to send
        -- OR if explain=True --
        tuple: (html_string, list_of_explain_dicts)
    """
    try:
        blocks = json.loads(template.blocks_json or "[]")
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(
            "BLOCKS_PARSE_FAIL template_id=%s name=%s error=%s",
            getattr(template, "id", "?"),
            getattr(template, "name", "?"),
            str(e),
        )
        blocks = []

    if not blocks:
        if template.html_body:
            logger.warning(
                "BLOCKS_FALLBACK template_id=%s name=%s reason=empty_or_invalid_blocks_json",
                getattr(template, "id", "?"),
                getattr(template, "name", "?"),
            )
        result = template.html_body if template.html_body else ""
        return (result, []) if explain else result

    # Auto-resolve products if none provided
    if not products:
        try:
            products = resolve_products_for_contact(contact, limit=4)
        except Exception:
            products = []

    # Auto-resolve discount from customer's existing codes if not provided
    if discount is None and contact:
        has_discount_block = any(b.get("block_type") == "discount" for b in blocks)
        if has_discount_block:
            try:
                from discount_engine import get_active_discount, get_discount_display
                _active = get_active_discount(getattr(contact, "email", ""))
                if _active:
                    discount = get_discount_display(_active)
            except Exception:
                pass

    # Build contact context for variant resolution
    contact_context = None
    if contact:
        try:
            from condition_engine import get_contact_context, resolve_block_variants
            contact_context = get_contact_context(contact)
        except ImportError:
            contact_context = None

    # Render each block (with variant resolution + visual flow)
    body_parts = []
    explain_list = []
    rendered_types = []  # track block types for flow logic
    product_offset = 0   # track products consumed by earlier blocks (e.g. product_hero)

    for i, block in enumerate(blocks):
        block_type = block.get("block_type", "")
        content = block.get("content", {})
        block_explain = None

        # Resolve variants if present and contact context available
        has_variants = bool(block.get("variants"))
        if has_variants and contact_context:
            from condition_engine import resolve_block_variants, _make_explain
            content, block_explain = resolve_block_variants(block, contact_context)
            if explain:
                block_explain["block_index"] = i
        elif has_variants and not contact_context:
            if explain:
                from condition_engine import _make_explain
                block_explain = _make_explain(
                    block_index=i, block_type=block_type,
                    summary="default (no contact context)",
                )
        else:
            if explain:
                from condition_engine import _make_explain
                block_explain = _make_explain(
                    block_index=i, block_type=block_type,
                    summary="no variants",
                )

        if explain and block_explain:
            explain_list.append(block_explain)

        renderer = _BLOCK_RENDERERS.get(block_type)
        if not renderer:
            continue

        # Pass remaining products (skip ones already consumed by earlier blocks)
        remaining_products = (products or [])[product_offset:]
        html_fragment = renderer(content, products=remaining_products, discount=discount)
        if block_type == "product_hero" and html_fragment and remaining_products:
            product_offset += 1  # product_hero consumes one product
        if html_fragment:
            # ── Visual flow: add connective spacing between blocks ──
            prev_type = rendered_types[-1] if rendered_types else None
            spacer = _get_flow_spacer(prev_type, block_type)
            if spacer:
                body_parts.append(spacer)
            body_parts.append(html_fragment)
            rendered_types.append(block_type)

    body_html = "\n".join(body_parts)

    # Apply personalization tokens
    if contact:
        body_html = body_html.replace("{{first_name}}", getattr(contact, "first_name", "") or "Friend")
        body_html = body_html.replace("{{last_name}}", getattr(contact, "last_name", "") or "")
        body_html = body_html.replace("{{email}}", getattr(contact, "email", "") or "")
    else:
        body_html = body_html.replace("{{first_name}}", "John")
        body_html = body_html.replace("{{last_name}}", "Smith")
        body_html = body_html.replace("{{email}}", "john@example.com")

    body_html = body_html.replace("{{unsubscribe_url}}", "{{unsubscribe_url}}")

    # Wrap in email shell
    preview_text = getattr(template, "preview_text", "") or ""
    full_html = wrap_email(body_html, preview_text=preview_text, unsubscribe_url="{{unsubscribe_url}}")

    if explain:
        return full_html, explain_list
    return full_html


# =========================================================================
#  TEMPLATE VALIDATION
# =========================================================================

def validate_template(blocks_json_str, family=None):
    """
    Validate a blocks-format template and return warnings.

    Args:
        blocks_json_str: JSON string of block definitions
        family: optional template family key (e.g. "welcome", "cart_recovery")

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

    block_types_present = [b.get("block_type", "") for b in blocks]

    if "cta" not in block_types_present:
        warnings.append({"level": "error", "message": "Template has no CTA button block -- emails should have a clear call to action"})

    if "text" not in block_types_present:
        warnings.append({"level": "warning", "message": "Template has no text block -- consider adding body copy"})

    for i, block in enumerate(blocks):
        block_type = block.get("block_type", "")
        content = block.get("content", {})
        block_num = i + 1

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

        # ---- Block-specific validation ----

        if block_type == "cta":
            url = content.get("url", "")
            text = content.get("text", "")
            if url and not url.startswith(("http://", "https://", "{{", "mailto:")):
                warnings.append({"level": "error", "message": "Block %d (CTA): URL '%s' is not a valid link" % (block_num, url[:60])})
            if url and "javascript:" in url.lower():
                warnings.append({"level": "error", "message": "Block %d (CTA): URL contains javascript: which is unsafe" % block_num})
            if text and len(text) > 50:
                warnings.append({"level": "warning", "message": "Block %d (CTA): Button text is %d chars -- keep under 50" % (block_num, len(text))})
            color = content.get("color", "")
            if color and not color.startswith("#") and color not in ("", BRAND_COLOR):
                warnings.append({"level": "warning", "message": "Block %d (CTA): Color '%s' should be a hex value" % (block_num, color)})

        if block_type == "product_grid":
            if not content.get("section_title", ""):
                warnings.append({"level": "warning", "message": "Block %d (Product Grid): No section title" % block_num})
            columns = content.get("columns", 2)
            try:
                columns = int(columns)
            except (ValueError, TypeError):
                columns = 0
            if columns not in (1, 2, 3):
                warnings.append({"level": "error", "message": "Block %d (Product Grid): columns must be 1, 2, or 3" % block_num})

        if block_type == "hero":
            headline = content.get("headline", "")
            if headline and len(headline) > 120:
                warnings.append({"level": "warning", "message": "Block %d (Hero): Headline is %d chars -- keep under 120" % (block_num, len(headline))})
            subheadline = content.get("subheadline", "")
            if subheadline and len(subheadline) > 200:
                warnings.append({"level": "warning", "message": "Block %d (Hero): Subheadline is %d chars -- keep under 200" % (block_num, len(subheadline))})

        if block_type == "text":
            paragraphs = content.get("paragraphs", [])
            if not isinstance(paragraphs, list):
                warnings.append({"level": "error", "message": "Block %d (Text): 'paragraphs' must be a list of strings" % block_num})
            elif len(paragraphs) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Text): %d paragraphs is long -- consider splitting" % (block_num, len(paragraphs))})

        if block_type == "discount":
            code = content.get("code", "")
            if code and len(code) > 30:
                warnings.append({"level": "warning", "message": "Block %d (Discount): Code is %d chars -- long codes are hard to type" % (block_num, len(code))})
            if code and " " in code:
                warnings.append({"level": "error", "message": "Block %d (Discount): Code '%s' contains spaces" % (block_num, code)})

        if block_type == "urgency":
            message = content.get("message", "")
            if message and len(message) > 120:
                warnings.append({"level": "warning", "message": "Block %d (Urgency): Message is %d chars -- keep under 120" % (block_num, len(message))})

        # New block type validations
        if block_type == "product_hero":
            if not content.get("section_title", ""):
                warnings.append({"level": "warning", "message": "Block %d (Product Hero): No section title" % block_num})

        if block_type == "comparison_block":
            columns = content.get("columns", 2)
            try:
                columns = int(columns)
            except (ValueError, TypeError):
                columns = 0
            if columns not in (2, 3):
                warnings.append({"level": "error", "message": "Block %d (Comparison): columns must be 2 or 3" % block_num})

        if block_type == "trust_reassurance":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (Trust): 'items' must be a list" % block_num})
            else:
                for ti, item in enumerate(items):
                    if not isinstance(item, dict) or "text" not in item:
                        warnings.append({"level": "error", "message": "Block %d (Trust): item %d must have 'icon' and 'text'" % (block_num, ti + 1)})

        if block_type == "features_benefits":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (Features): 'items' must be a list of strings" % block_num})
            elif len(items) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Features): %d items is long -- keep under 8" % (block_num, len(items))})

        # ── New persuasion module validations ──
        if block_type == "driver_testimonial":
            quote = content.get("quote", "")
            if quote and len(quote) > 200:
                warnings.append({"level": "warning", "message": "Block %d (Testimonial): Quote is %d chars -- keep under 200" % (block_num, len(quote))})
            rating = content.get("rating", 5)
            try:
                rating = int(rating)
                if rating < 1 or rating > 5:
                    warnings.append({"level": "error", "message": "Block %d (Testimonial): rating must be 1-5" % block_num})
            except (ValueError, TypeError):
                warnings.append({"level": "error", "message": "Block %d (Testimonial): rating must be an integer" % block_num})

        if block_type == "comparison":
            columns = content.get("columns", 2)
            try:
                columns = int(columns)
            except (ValueError, TypeError):
                columns = 0
            if columns not in (2, 3):
                warnings.append({"level": "error", "message": "Block %d (Comparison): columns must be 2 or 3" % block_num})

        if block_type == "why_choose_this":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (Why Choose): 'items' must be a list" % block_num})
            elif len(items) > 6:
                warnings.append({"level": "warning", "message": "Block %d (Why Choose): %d items -- keep under 6" % (block_num, len(items))})

        if block_type == "objection_handling":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (Objections): 'items' must be a list" % block_num})
            else:
                for oi, item in enumerate(items):
                    if not isinstance(item, dict) or "objection" not in item or "answer" not in item:
                        warnings.append({"level": "error", "message": "Block %d (Objections): item %d must have 'objection' and 'answer'" % (block_num, oi + 1)})
                if len(items) > 4:
                    warnings.append({"level": "warning", "message": "Block %d (Objections): %d items -- keep under 4" % (block_num, len(items))})

        if block_type == "bundle_value":
            items = content.get("items", [])
            if items and len(items) < 2:
                warnings.append({"level": "error", "message": "Block %d (Bundle): needs at least 2 items" % block_num})

        if block_type == "best_seller_proof":
            if not content.get("section_title", ""):
                warnings.append({"level": "warning", "message": "Block %d (Best Seller): No section title" % block_num})

        if block_type == "feature_highlights":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (Feature Highlights): 'items' must be a list" % block_num})
            elif len(items) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Feature Highlights): %d items -- keep under 8" % (block_num, len(items))})

        # ---- Content-rich module validation ----

        if block_type == "competitor_comparison":
            competitors = content.get("competitors", [])
            if not isinstance(competitors, list) or not competitors:
                warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): 'competitors' must be a non-empty list" % block_num})
            rows = content.get("rows", [])
            if not isinstance(rows, list):
                warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): 'rows' must be a list" % block_num})
            elif len(rows) < 2:
                warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): needs at least 2 rows" % block_num})
            elif len(rows) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Competitor Comparison): %d rows -- keep under 8" % (block_num, len(rows))})
            else:
                for ri, row in enumerate(rows):
                    if not isinstance(row, dict) or "feature" not in row:
                        warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): row %d must have 'feature'" % (block_num, ri + 1)})

        if block_type == "spec_table":
            rows = content.get("rows", [])
            if not isinstance(rows, list):
                warnings.append({"level": "error", "message": "Block %d (Spec Table): 'rows' must be a list" % block_num})
            elif len(rows) < 2:
                warnings.append({"level": "error", "message": "Block %d (Spec Table): needs at least 2 rows" % block_num})
            elif len(rows) > 12:
                warnings.append({"level": "warning", "message": "Block %d (Spec Table): %d rows -- keep under 12" % (block_num, len(rows))})
            else:
                for ri, row in enumerate(rows):
                    if not isinstance(row, dict) or "label" not in row:
                        warnings.append({"level": "error", "message": "Block %d (Spec Table): row %d must have 'label'" % (block_num, ri + 1)})

        if block_type == "stat_callout":
            stats = content.get("stats", [])
            if not isinstance(stats, list):
                warnings.append({"level": "error", "message": "Block %d (Stat Callout): 'stats' must be a list" % block_num})
            elif len(stats) != 3:
                warnings.append({"level": "error", "message": "Block %d (Stat Callout): must have exactly 3 stats" % block_num})
            else:
                for si, stat in enumerate(stats):
                    if not isinstance(stat, dict) or "value" not in stat or "label" not in stat:
                        warnings.append({"level": "error", "message": "Block %d (Stat Callout): stat %d must have 'value' and 'label'" % (block_num, si + 1)})

        if block_type == "whats_included":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (What's Included): 'items' must be a list" % block_num})
            elif len(items) < 2:
                warnings.append({"level": "error", "message": "Block %d (What's Included): needs at least 2 items" % block_num})
            elif len(items) > 8:
                warnings.append({"level": "warning", "message": "Block %d (What's Included): %d items -- keep under 8" % (block_num, len(items))})

        if block_type == "faq":
            items = content.get("items", [])
            if not isinstance(items, list):
                warnings.append({"level": "error", "message": "Block %d (FAQ): 'items' must be a list" % block_num})
            elif len(items) < 2:
                warnings.append({"level": "error", "message": "Block %d (FAQ): needs at least 2 items" % block_num})
            elif len(items) > 6:
                warnings.append({"level": "warning", "message": "Block %d (FAQ): %d items -- keep under 6" % (block_num, len(items))})
            else:
                for fi, item in enumerate(items):
                    if not isinstance(item, dict) or "question" not in item or "answer" not in item:
                        warnings.append({"level": "error", "message": "Block %d (FAQ): item %d must have 'question' and 'answer'" % (block_num, fi + 1)})

        if block_type == "use_case_match":
            cases = content.get("cases", [])
            if not isinstance(cases, list):
                warnings.append({"level": "error", "message": "Block %d (Use Case Match): 'cases' must be a list" % block_num})
            elif len(cases) < 2:
                warnings.append({"level": "error", "message": "Block %d (Use Case Match): needs at least 2 cases" % block_num})
            elif len(cases) > 3:
                warnings.append({"level": "warning", "message": "Block %d (Use Case Match): %d cases -- keep under 3" % (block_num, len(cases))})
            else:
                for ci, case in enumerate(cases):
                    if not isinstance(case, dict) or "persona" not in case or "description" not in case or "product_name" not in case:
                        warnings.append({"level": "error", "message": "Block %d (Use Case Match): case %d must have 'persona', 'description', 'product_name'" % (block_num, ci + 1)})

        if block_type == "brand_story":
            if not content.get("headline"):
                warnings.append({"level": "error", "message": "Block %d (Brand Story): 'headline' is required" % block_num})
            if not content.get("body"):
                warnings.append({"level": "error", "message": "Block %d (Brand Story): 'body' is required" % block_num})
            variant = content.get("variant", "mission")
            if variant not in ("mission", "sustainability", "heritage", "custom"):
                warnings.append({"level": "error", "message": "Block %d (Brand Story): variant must be mission/sustainability/heritage/custom" % block_num})

        # ---- Variant validation ----
        variants = block.get("variants", [])
        if variants:
            if not isinstance(variants, list):
                warnings.append({"level": "error", "message": "Block %d (%s): 'variants' must be a list" % (block_num, block_type)})
            else:
                try:
                    from condition_engine import validate_condition
                except ImportError:
                    validate_condition = None

                seen_unconditional = False
                for vi, variant in enumerate(variants):
                    conditions = variant.get("conditions", [])
                    if not isinstance(conditions, list):
                        warnings.append({"level": "error", "message": "Block %d variant %d: 'conditions' must be a list" % (block_num, vi + 1)})
                        continue
                    if not conditions:
                        if seen_unconditional:
                            warnings.append({"level": "warning", "message": "Block %d variant %d: Multiple unconditional variants" % (block_num, vi + 1)})
                        seen_unconditional = True
                        if vi < len(variants) - 1:
                            warnings.append({"level": "warning", "message": "Block %d variant %d: Unconditional variant is not last -- subsequent unreachable" % (block_num, vi + 1)})
                    if validate_condition:
                        for ci, cond in enumerate(conditions):
                            cond_warnings = validate_condition(cond, block_num=block_num, variant_num=vi + 1, cond_num=ci + 1)
                            warnings.extend(cond_warnings)

    # Duplicate CTA warning
    cta_count = block_types_present.count("cta")
    if cta_count > 2:
        warnings.append({"level": "warning", "message": "Template has %d CTA buttons -- more than 2 can reduce click-through rates" % cta_count})

    # Family validation
    if family:
        try:
            from condition_engine import validate_family
            family_warnings = validate_family(blocks_json_str, family)
            warnings.extend(family_warnings)
        except ImportError:
            pass

    return warnings


# =========================================================================
#  UTILITY: Create Example Blocks JSON
# =========================================================================

def make_example_blocks():
    """Return a sample blocks JSON string for testing/seeding."""
    blocks = [
        {
            "block_type": "hero",
            "content": {
                "headline": "Welcome to LDAS Electronics, {{first_name}}!",
                "subheadline": "Your go-to store for trucking electronics",
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
            "block_type": "trust_reassurance",
            "content": {
                "items": [
                    {"icon": "package", "text": "Free Shipping on $50+"},
                    {"icon": "shield", "text": "30-Day Easy Returns"},
                    {"icon": "star", "text": "4.8/5 Customer Rating"},
                    {"icon": "maple", "text": "Canadian-Owned Business"},
                ],
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
            },
        },
    ]
    return json.dumps(blocks, indent=2)
