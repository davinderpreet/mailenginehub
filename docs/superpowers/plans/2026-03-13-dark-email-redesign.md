# Dark Email Body Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform all 17 email block renderers from light white-card-on-grey to a premium dark `#111111` aesthetic with inverted CTAs, spotlight glow for products, and bold typography.

**Architecture:** Each block renderer wraps every `<td>` with `background:#111111` so the shell's white `BG_BODY` is never visible. The DESIGN dict becomes the single source of truth for all dark tokens. Shell (`email_shell.py`) is untouched.

**Tech Stack:** Python, inline HTML/CSS for email rendering, Peewee ORM (SQLite)

**Spec:** `docs/superpowers/specs/2026-03-13-dark-email-redesign-design.md`

---

## Chunk 1: DESIGN Dict + Simple Blocks

### Task 1: Replace DESIGN dict and module-level constants

**Files:**
- Modify: `block_registry.py:25-70`

- [ ] **Step 1: Remove old light-mode constants**

Replace lines 25-26:
```python
BRAND_COLOR_LIGHT = "#e8f0ff"
ACCENT_COLOR      = "#0428aa"
```

These are no longer used. The old `BRAND_COLOR_LIGHT` was referenced as `DESIGN["accent_light"]` and `ACCENT_COLOR` was never referenced in renderers.

- [ ] **Step 2: Replace DESIGN dict (lines 33-70)**

Replace the entire `DESIGN = { ... }` block with the new dark token dict from the spec. The new dict has these changes:
- Removes: `badge_amber`, `trust_bg`, `trust_border`, `trust_card_bg`, `accent_light`, `check_color`, `price_strike` (old value)
- Adds: `body_bg`, `surface`, `surface_border`, `text_primary`, `text_secondary`, `text_tertiary`, `brand`, `brand_light`, `brand_glow`, `btn_primary_bg`, `btn_primary_text`, `btn_card_bg`, `btn_card_text`, `price_color`, `badge_bg`, `badge_text`, `urgency_bg`, `urgency_border`, `urgency_text`, `spotlight`, `placeholder_bg` (new value), `placeholder_text` (new value), `card_bg` (new value)
- Changes values: `card_border`, `card_shadow` → `none`, `card_shadow_lg` → `none`, `divider_color`, `savings_green`, `star_gold`, `h1`, `h2`, `label`, `btn_primary` (removes color), `btn_card` (removes color), `section_pad`, spacing tokens adjusted

New DESIGN dict:

```python
DESIGN = {
    # Dark backgrounds
    "body_bg":           "#111111",
    "surface":           "#1a1a1a",
    "surface_border":    "#2a2a2a",
    "divider_color":     "#222222",

    # Text on dark
    "text_primary":      "#ffffff",
    "text_secondary":    "#b0b0b0",
    "text_tertiary":     "#707070",

    # Brand
    "brand":             "#063cff",
    "brand_light":       "#3366ff",
    "brand_glow":        "rgba(6,60,255,0.15)",

    # Buttons (inverted for dark)
    "btn_primary_bg":    "#ffffff",
    "btn_primary_text":  "#111111",
    "btn_card_bg":       "#ffffff",
    "btn_card_text":     "#111111",

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
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
git add block_registry.py
git commit -m "feat: replace DESIGN dict with dark email tokens"
```

---

### Task 2: Rewrite render_hero()

**Files:**
- Modify: `block_registry.py:293-332`

**Key changes:**
- Remove `is_branded` logic (lines 301-303) — text is ALWAYS white on dark
- All text uses `DESIGN["text_primary"]` and `DESIGN["text_secondary"]`
- CTA uses inverted white button: `DESIGN["btn_primary_bg"]` / `DESIGN["btn_primary_text"]`
- Default bg uses `DESIGN["body_bg"]` instead of brand gradient
- Every `<td>` gets `background:DESIGN["body_bg"]` (or custom bg)
- Hero image `<td>` also needs body_bg to prevent white bleed

- [ ] **Step 1: Replace render_hero() function**

```python
def render_hero(content):
    """Hero headline section with dark background."""
    headline = html_mod.escape(content.get("headline", ""))
    subheadline = content.get("subheadline", "")
    bg = content.get("bg_color", DESIGN["body_bg"])

    # On dark theme, text is ALWAYS white regardless of bg_color
    text_color = DESIGN["text_primary"]
    sub_color = DESIGN["text_secondary"]

    # Optional hero image
    hero_image_url = content.get("hero_image_url", "")
    img_html = ""
    if hero_image_url:
        img_html = '<img src="%s" alt="" width="100%%" style="display:block;max-width:100%%;" />' % html_mod.escape(hero_image_url)
        img_html = '<tr><td style="padding:0;background:%s;">%s</td></tr>' % (DESIGN["body_bg"], img_html)

    sub_html = ""
    if subheadline:
        sub_html = '<p style="margin:10px 0 0;font-size:17px;color:%s;font-weight:400;">%s</p>' % (
            sub_color, html_mod.escape(subheadline)
        )

    # Optional inline CTA — always inverted white on dark
    cta_text = content.get("cta_text", "")
    cta_url = content.get("cta_url", "")
    cta_html = ""
    if cta_text and cta_url:
        cta_html = '<p style="margin:18px 0 0;"><a href="%s" style="%s;background:%s;color:%s;">%s</a></p>' % (
            cta_url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], html_mod.escape(cta_text)
        )

    return '''%s<tr><td style="background:%s;padding:36px 30px;text-align:center;" class="mobile-pad">
  <h1 style="%s;color:%s;">%s</h1>
  %s%s
</td></tr>''' % (img_html, bg, DESIGN["h1"], text_color, headline, sub_html, cta_html)
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: dark render_hero — remove is_branded, always-white text"
```

---

### Task 3: Rewrite render_text()

**Files:**
- Modify: `block_registry.py:335-361`

**Key changes:**
- Body text: `DESIGN["text_secondary"]` instead of `TEXT_MID`
- Section header label: `DESIGN["text_tertiary"]` instead of `BRAND_COLOR`
- Every `<td>` gets `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_text() function**

```python
def render_text(content):
    """Body text paragraphs with optional section header."""
    paragraphs = content.get("paragraphs", [])
    if not paragraphs:
        return ""

    # Optional section header
    section_header = content.get("section_header", "")
    header_html = ""
    if section_header:
        header_html = '<p style="margin:0 0 12px;%s;color:%s;">%s</p>' % (
            DESIGN["label"], DESIGN["text_tertiary"], html_mod.escape(section_header)
        )

    paras_html = ""
    for p in paragraphs:
        safe = html_mod.escape(p)
        safe = safe.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
        safe = safe.replace("&lt;br/&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
        safe = safe.replace("&amp;bull;", "&bull;")
        paras_html += '<p style="margin:0 0 14px;%s;color:%s;">%s</p>' % (
            DESIGN["body"], DESIGN["text_secondary"], safe
        )

    return '<tr><td style="padding:%s;background:%s;" class="mobile-pad">%s%s</td></tr>' % (
        DESIGN["section_pad"], DESIGN["body_bg"], header_html, paras_html
    )
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_text — white text on dark bg"
```

---

### Task 4: Rewrite render_cta()

**Files:**
- Modify: `block_registry.py:659-677`

**Key changes:**
- Ignore template-level `color` param — all CTAs use inverted white
- Primary button: `DESIGN["btn_primary_bg"]` / `DESIGN["btn_primary_text"]`
- Secondary link: `DESIGN["text_secondary"]`
- `<td>` gets `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_cta() function**

```python
def render_cta(content):
    """Large call-to-action button with optional secondary link."""
    text = html_mod.escape(content.get("text", "Shop Now"))
    url = content.get("url", BRAND_URL)
    # color param ignored on dark theme — all CTAs use inverted white

    secondary_text = content.get("secondary_text", "")
    secondary_url = content.get("secondary_url", "")

    secondary_html = ""
    if secondary_text and secondary_url:
        secondary_html = '<p style="margin:12px 0 0;font-size:13px;"><a href="%s" style="color:%s;text-decoration:underline;">%s</a></p>' % (
            secondary_url, DESIGN["text_secondary"], html_mod.escape(secondary_text)
        )

    return '''<tr><td style="padding:8px 30px 24px;text-align:center;background:%s;" class="mobile-pad">
  <a href="%s" style="%s;background:%s;color:%s;">%s</a>
  %s
</td></tr>''' % (DESIGN["body_bg"], url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], text, secondary_html)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_cta — inverted white button, ignore color param"
```

---

### Task 5: Rewrite render_urgency()

**Files:**
- Modify: `block_registry.py:680-692`

**Key changes:**
- Use `DESIGN["urgency_bg"]`, `DESIGN["urgency_border"]`, `DESIGN["urgency_text"]`
- `<td>` gets `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_urgency() function**

```python
def render_urgency(content):
    """Urgency message bar — amber on dark."""
    message = content.get("message", "")
    if not message:
        return ""
    safe = html_mod.escape(message)
    return '''<tr><td style="padding:0 30px 16px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="background:%s;border:1px solid %s;border-radius:10px;padding:12px 16px;text-align:center;">
      <span style="font-size:13px;font-weight:600;color:%s;">&#9203; %s</span>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["urgency_bg"], DESIGN["urgency_border"], DESIGN["urgency_text"], safe)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_urgency — amber-400 on dark"
```

---

### Task 6: Rewrite render_divider()

**Files:**
- Modify: `block_registry.py:1187-1193`

**Key changes:**
- `background:DESIGN["body_bg"]` on `<td>`
- Divider color: `DESIGN["divider_color"]` (#222222)

- [ ] **Step 1: Replace render_divider() function**

```python
def render_divider(content):
    """Simple horizontal divider."""
    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="border-top:1px solid %s;"></td></tr>
  </table>
</td></tr>''' % (DESIGN["section_pad_tight"], DESIGN["body_bg"], DESIGN["divider_color"])
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_divider"
```

---

### Task 7: Rewrite render_discount()

**Files:**
- Modify: `block_registry.py:630-656`

**Key changes:**
- No visual changes to the discount block itself (spec says "no changes — already self-contained with brand gradient bg")
- BUT the outer `<td>` needs `background:DESIGN["body_bg"]` to prevent white bleed

- [ ] **Step 1: Add body_bg to render_discount() outer td**

Only change needed: add `background:%s;` to the outer `<td>` at line 646 and pass `DESIGN["body_bg"]`.

```python
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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_discount — add body_bg to outer td"
```

---

## Chunk 2: Product Blocks

### Task 8: Rewrite _render_product_card()

**Files:**
- Modify: `block_registry.py:397-443`

**Key changes:**
- Card bg: `DESIGN["card_bg"]` (#1a1a1a), border: `DESIGN["card_border"]`, shadow: `DESIGN["card_shadow"]` (none)
- Title: `DESIGN["text_primary"]` instead of `TEXT_DARK`
- Price: `DESIGN["price_color"]` instead of `BRAND_COLOR`
- Description: `DESIGN["text_secondary"]` instead of `TEXT_MID`
- Button: `DESIGN["btn_card"]` + `DESIGN["btn_card_bg"]` + `DESIGN["btn_card_text"]`
- Image area: spotlight gradient background
- Placeholder: dark values

- [ ] **Step 1: Replace _render_product_card() function**

```python
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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark _render_product_card — spotlight glow, inverted CTA"
```

---

### Task 9: Rewrite render_product_grid()

**Files:**
- Modify: `block_registry.py:364-394`

**Key changes:**
- Section label: `DESIGN["text_tertiary"]` instead of `BRAND_COLOR` (subtle on dark — spec says "#707070")
- Actually, spec section 5.3 says label stays branded. Let me check — the label pattern across all blocks uses `BRAND_COLOR` for the uppercase label. On dark, the spec says section header label is `#707070` for text blocks specifically, but product grid labels should stay brand-colored for contrast.
- Wait — re-reading spec 5.2: "Section header label: #707070 (tertiary, subtle on dark)" — this is for text block only. Product grid label stays brand.
- All `<td>` wrappers get `background:DESIGN["body_bg"]`
- Empty cells also need `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_product_grid() function**

```python
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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_product_grid — body_bg on all tds"
```

---

### Task 10: Rewrite render_product_hero()

**Files:**
- Modify: `block_registry.py:446-512`

**Key changes:**
- Card: dark bg, dark border, no shadow
- Title: `DESIGN["text_primary"]`
- Price: `DESIGN["price_color"]`
- Description: `DESIGN["text_secondary"]`
- Image: spotlight glow background
- CTA: inverted white
- SALE pill: `DESIGN["savings_green"]`
- All outer `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_product_hero() function**

```python
def render_product_hero(content, products=None):
    """Large single-product feature block — dark elevated card."""
    if not products:
        return ""

    product = products[0]
    title = html_mod.escape(product.get("title", "")[:80])
    image_url = product.get("image_url", "")
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

    # Elevated dark card
    card_html = '''<tr><td style="padding:10px 30px 8px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s;border-radius:%s;overflow:hidden;background:%s;box-shadow:%s;">
    <tr><td style="padding:0;background:%s;">%s</td></tr>
    <tr><td style="padding:22px 24px 26px;">
      <a href="%s" style="text-decoration:none;color:inherit;"><p style="%s;color:%s;margin-bottom:4px;">%s</p></a>
      <p style="margin:10px 0 16px;">%s</p>
      %s
      <p style="margin:0;text-align:center;">
        <a href="%s" style="%s;background:%s;color:%s;">%s</a>
      </p>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["card_border"], DESIGN["card_radius"], DESIGN["card_bg"], DESIGN["card_shadow_lg"],
                 DESIGN["spotlight"], img_cell,
                 product_url, DESIGN["h2"], DESIGN["text_primary"], title,
                 price_html, desc_html,
                 product_url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], cta_text)

    return label_html + card_html
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_product_hero — spotlight glow, inverted CTA"
```

---

### Task 11: Rewrite render_comparison() (old comparison_block)

**Files:**
- Modify: `block_registry.py:515-567`

**Key changes:**
- Cards: `DESIGN["surface"]` bg, `DESIGN["card_border"]`, no shadow
- Title: `DESIGN["text_primary"]`
- Price: `DESIGN["price_color"]`
- Button: inverted white
- All `<td>` get `background:DESIGN["body_bg"]`
- Placeholder: dark values

- [ ] **Step 1: Replace render_comparison() function**

```python
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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_comparison (old) — dark cards, inverted CTAs"
```

---

## Chunk 3: Trust, Features, Testimonial Blocks

### Task 12: Rewrite render_trust()

**Files:**
- Modify: `block_registry.py:570-597`

**Key changes per spec 5.6:**
- Single `DESIGN["surface"]` background row (not 2x2 white card grid)
- Horizontal inline badges: emoji + white text
- Separated by `DESIGN["surface_border"]` dividers
- All `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_trust() function**

```python
def render_trust(content):
    """Trust & reassurance badges — horizontal inline on dark surface."""
    items = content.get("items", BLOCK_TYPES["trust_reassurance"]["defaults"]["items"])
    if not items:
        return ""

    badges_html = ""
    for idx, item in enumerate(items[:4]):
        icon_key = item.get("icon", "check") if isinstance(item, dict) else "check"
        text = item.get("text", str(item)) if isinstance(item, dict) else str(item)
        icon_entity = _TRUST_ICONS.get(icon_key, "&#x2705;")

        # Divider between badges (not before first)
        divider = ""
        if idx > 0:
            divider = '<td style="width:1px;background:%s;font-size:0;">&nbsp;</td>' % DESIGN["surface_border"]

        badges_html += '''%s<td style="padding:16px 14px;text-align:center;vertical-align:middle;">
  <span style="font-size:20px;vertical-align:middle;">%s</span>
  <span style="font-size:13px;font-weight:600;color:%s;vertical-align:middle;margin-left:4px;">%s</span>
</td>''' % (divider, icon_entity, DESIGN["text_primary"], html_mod.escape(text))

    return '''<tr><td style="padding:20px 24px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:14px;%s;">
    <tr>%s</tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["surface"], DESIGN["card_border"], badges_html)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_trust — horizontal inline badges on dark surface"
```

---

### Task 13: Rewrite render_features()

**Files:**
- Modify: `block_registry.py:600-627`

**Key changes per spec 5.7:**
- Check icons: brand blue on dark (`DESIGN["brand_glow"]` bg circle)
- Text: `DESIGN["text_primary"]`
- No card wrapper — flat on `DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_features() function**

```python
def render_features(content):
    """Features & benefits checklist — flat on dark."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "")

    header_html = ""
    if section_title:
        header_html = '<p style="margin:0 0 14px;%s;color:%s;">%s</p>' % (
            DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
        )

    rows = ""
    for item in items[:8]:
        text = html_mod.escape(str(item))
        rows += '''<tr>
  <td style="width:28px;font-size:16px;vertical-align:top;padding:4px 0;"><span style="display:inline-block;width:24px;height:24px;background:%s;border-radius:50%%;text-align:center;font-size:13px;color:%s;line-height:24px;">&#x2713;</span></td>
  <td style="%s;color:%s;padding:4px 0;">%s</td>
</tr>''' % (DESIGN["brand_glow"], DESIGN["brand"], DESIGN["body"], DESIGN["text_primary"], text)

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    %s
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, rows)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_features — blue glow icons, white text, flat on dark"
```

---

### Task 14: Rewrite render_driver_testimonial()

**Files:**
- Modify: `block_registry.py:695-742`

**Key changes per spec 5.12:**
- Card: `DESIGN["card_bg"]` (#1a1a1a), `DESIGN["card_border"]`, no shadow
- Left accent bar: `DESIGN["brand"]` (stays)
- Quote: 17px italic, `DESIGN["text_primary"]`
- Stars: `DESIGN["star_gold"]`
- Attribution: `DESIGN["text_secondary"]`
- Author role: `DESIGN["text_tertiary"]`
- Product name: `DESIGN["text_secondary"]`
- All outer `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_driver_testimonial() function**

```python
def render_driver_testimonial(content):
    """Customer testimonial quote card — dark with left accent bar."""
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
            stars_html += '<span style="color:#444;">&#9733;</span>'

    # Attribution line
    attribution = "&mdash; %s" % author_name
    if author_role:
        attribution += '<br/><span style="font-size:12px;color:%s;font-weight:400;">%s</span>' % (DESIGN["text_tertiary"], author_role)
    if product_name:
        attribution += '<br/><span style="font-size:12px;color:%s;font-style:italic;">Re: %s</span>' % (DESIGN["text_secondary"], product_name)

    # Section header
    header_html = '<p style="margin:0 0 16px;%s;color:%s;">%s</p>' % (
        DESIGN["label"], DESIGN["brand"], section_title
    )

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:14px;%s;overflow:hidden;">
    <tr>
      <td style="width:5px;background:%s;font-size:0;">&nbsp;</td>
      <td style="padding:26px 28px 26px 24px;">
        <p style="margin:0 0 12px;font-size:20px;letter-spacing:2px;">%s</p>
        <p style="margin:0 0 18px;font-size:17px;font-style:italic;color:%s;line-height:1.65;">&ldquo;%s&rdquo;</p>
        <p style="margin:0;font-size:14px;font-weight:600;color:%s;line-height:1.5;">%s</p>
      </td>
    </tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html,
                 DESIGN["card_bg"], DESIGN["card_border"],
                 DESIGN["brand"],
                 stars_html, DESIGN["text_primary"], quote, DESIGN["text_secondary"], attribution)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_driver_testimonial — dark card, white quote"
```

---

## Chunk 4: Persuasion Module Blocks

### Task 15: Rewrite render_comparison_module() (new comparison)

**Files:**
- Modify: `block_registry.py:745-828`

**Key changes per spec 5.5:**
- Cards: `DESIGN["card_bg"]`, `DESIGN["card_border"]`
- Highlighted: `DESIGN["brand"]` 2px border + accent bar
- "Recommended" badge: `DESIGN["badge_bg"]`, `DESIGN["badge_text"]`
- Text: `DESIGN["text_primary"]` titles, `DESIGN["text_secondary"]` descriptions
- Price: `DESIGN["price_color"]`
- Button: inverted white
- All `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_comparison_module() function**

```python
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

        if is_highlighted:
            card_style = "background:%s;border-radius:%s;border:2px solid %s;overflow:hidden;" % (
                DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["brand"])
        else:
            card_style = "background:%s;border-radius:%s;%s;overflow:hidden;" % (
                DESIGN["card_bg"], DESIGN["card_radius"], DESIGN["card_border"])

        accent_bar = ""
        if is_highlighted:
            accent_bar = '<tr><td style="background:%s;height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>' % DESIGN["brand"]

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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_comparison_module — dark cards, badge_bg, inverted CTAs"
```

---

### Task 16: Rewrite render_why_choose_this()

**Files:**
- Modify: `block_registry.py:831-877`

**Key changes per spec 5.13:**
- Circle icons: `DESIGN["brand_glow"]` bg, `DESIGN["brand"]` icon color
- Text: `DESIGN["text_primary"]`
- Dividers: `DESIGN["divider_color"]`
- No card wrapper — flat on `DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_why_choose_this() function**

```python
def render_why_choose_this(content):
    """Product-specific benefit statements — flat on dark with circle icons."""
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
    %s
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, rows)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_why_choose_this — flat on dark, glow icons"
```

---

### Task 17: Rewrite render_objection_handling()

**Files:**
- Modify: `block_registry.py:880-942`

**Key changes per spec 5.14:**
- Q circle: `DESIGN["brand"]` bg, A circle: `DESIGN["savings_green"]` bg
- Statement: red X, green check (on dark)
- Card: `DESIGN["card_bg"]`, no shadow
- Questions: `DESIGN["text_primary"]`, Answers: `DESIGN["text_secondary"]`
- Dividers: `DESIGN["divider_color"]`
- All outer `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_objection_handling() function**

```python
def render_objection_handling(content):
    """Q&A or statement-style objection handling — dark card."""
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
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_objection_handling — dark card, brand Q circles"
```

---

### Task 18: Rewrite render_bundle_value()

**Files:**
- Modify: `block_registry.py:945-1033`

**Key changes per spec 5.15:**
- Mini-cards: `DESIGN["card_bg"]`, `DESIGN["card_border"]`
- Plus circles: `DESIGN["brand_glow"]` bg
- Savings pill: `DESIGN["savings_green"]`
- Pricing: `DESIGN["price_color"]` bold
- Title: `DESIGN["text_primary"]`, Price: `DESIGN["text_secondary"]`
- CTA: inverted white
- All `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_bundle_value() function**

```python
def render_bundle_value(content, products=None):
    """Bundle value block — dark product cards with savings callout."""
    items = content.get("items", [])
    bundle_price = content.get("bundle_price", "0.00")
    section_title = html_mod.escape(content.get("section_title", "Better Together"))
    savings_text = content.get("savings_text", "")
    cta_text = html_mod.escape(content.get("cta_text", "Shop the Bundle"))
    cta_url = content.get("cta_url", BRAND_URL)

    bundle_items = items if items else (products or [])
    if len(bundle_items) < 2:
        return ""

    bundle_items = bundle_items[:3]

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
</td>''' % (w, DESIGN["card_bg"], DESIGN["card_border"], img, DESIGN["text_primary"], product_url, title, DESIGN["text_secondary"], price)

        if idx < len(bundle_items) - 1:
            product_cells += '''<td style="width:30px;text-align:center;vertical-align:middle;">
  <span style="display:inline-block;width:28px;height:28px;background:%s;border-radius:50%%;text-align:center;font-size:18px;font-weight:700;color:%s;line-height:28px;">+</span>
</td>''' % (DESIGN["brand_glow"], DESIGN["brand"])

    pricing_html = ""
    if total_value > bundle_price_f:
        pricing_html = '<p style="margin:0 0 6px;font-size:14px;color:%s;text-decoration:line-through;">Total Value: $%.2f</p>' % (DESIGN["price_strike"], total_value)
    pricing_html += '<p style="margin:0 0 6px;font-size:26px;font-weight:800;color:%s;">$%s</p>' % (DESIGN["price_color"], bundle_price)
    if savings_text:
        pricing_html += '<p style="margin:0 0 16px;"><span style="display:inline-block;background:%s;color:#ffffff;font-size:13px;font-weight:700;padding:6px 16px;border-radius:20px;">&#x2713; %s</span></p>' % (DESIGN["savings_green"], html_mod.escape(savings_text))

    return header_html + '''<tr><td style="padding:10px 24px;background:%s;" class="mobile-pad">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:14px;padding:16px 10px;">
    <tr>%s</tr>
  </table>
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="text-align:center;padding:20px 0 8px;">
      %s
      <a href="%s" style="%s;background:%s;color:%s;">%s</a>
    </td></tr>
  </table>
</td></tr>''' % (DESIGN["body_bg"], DESIGN["surface"], product_cells, pricing_html, cta_url, DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"], cta_text)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_bundle_value — dark cards, glow plus icons"
```

---

## Chunk 5: Remaining Blocks + Smoke Test

### Task 19: Rewrite render_best_seller_proof()

**Files:**
- Modify: `block_registry.py:1036-1115`

**Key changes per spec 5.16:**
- Same as product grid — dark cards, spotlight glow, white text
- Badge pills: `DESIGN["badge_bg"]`, `DESIGN["badge_text"]`
- Stars: `DESIGN["star_gold"]`
- Proof line: `DESIGN["savings_green"]`
- All `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_best_seller_proof() function**

```python
def render_best_seller_proof(content, products=None):
    """Product cards with social proof — dark theme with badges and ratings."""
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

            badge_html = ""
            if badge_text:
                badge_html = '<p style="margin:0 0 8px;"><span style="display:inline-block;background:%s;color:%s;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;padding:4px 12px;border-radius:20px;">%s</span></p>' % (DESIGN["badge_bg"], DESIGN["badge_text"], html_mod.escape(badge_text))

            rating_html = ""
            if show_rating:
                rating_html = '<p style="margin:0 0 6px;font-size:13px;color:%s;letter-spacing:1px;">&#9733;&#9733;&#9733;&#9733;&#9733; <span style="font-size:12px;color:%s;letter-spacing:0;">(4.8)</span></p>' % (DESIGN["star_gold"], DESIGN["text_tertiary"])

            proof_html = ""
            if proof_line:
                proof_html = '<p style="margin:0 0 6px;font-size:12px;font-weight:600;color:%s;">%s</p>' % (DESIGN["savings_green"], html_mod.escape(proof_line))

            price_html = '<span style="font-size:20px;font-weight:800;color:%s;">$%s</span>' % (DESIGN["price_color"], price)
            if compare_price and compare_price != price:
                price_html = '<span style="font-size:13px;color:%s;text-decoration:line-through;margin-right:6px;">$%s</span>%s' % (
                    DESIGN["price_strike"], compare_price, price_html)

            cells += '''<td class="stack-col" style="width:%s;padding:8px;vertical-align:top;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="%s;border-radius:%s;overflow:hidden;background:%s;box-shadow:%s;">
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
</td>''' % (w, DESIGN["card_border"], DESIGN["card_radius"], DESIGN["card_bg"], DESIGN["card_shadow"],
            DESIGN["spotlight"], img_html, DESIGN["card_inner_pad"],
            badge_html, product_url, DESIGN["text_primary"], title,
            rating_html, proof_html, price_html,
            product_url, DESIGN["btn_card"], DESIGN["btn_card_bg"], DESIGN["btn_card_text"])

        rows_html += '<tr>%s</tr>' % cells

    grid_html = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">%s</table>' % rows_html

    return header_html + '<tr><td style="padding:%s;background:%s;" class="mobile-pad">%s</td></tr>' % (DESIGN["grid_pad"], DESIGN["body_bg"], grid_html)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_best_seller_proof — spotlight glow, badge_bg/text"
```

---

### Task 20: Rewrite render_feature_highlights()

**Files:**
- Modify: `block_registry.py:1118-1184`

**Key changes per spec 5.17:**
- Circle icons: `DESIGN["brand_glow"]` bg, `DESIGN["brand"]` icon color
- Text: `DESIGN["text_primary"]`
- Container: `DESIGN["surface"]` bg with `DESIGN["card_border"]` (instead of `trust_bg`/`trust_border`)
- All outer `<td>` get `background:DESIGN["body_bg"]`

- [ ] **Step 1: Replace render_feature_highlights() function**

```python
def render_feature_highlights(content):
    """Quick-scan feature list — dark card with circle icons."""
    items = content.get("items", [])
    if not items:
        return ""

    section_title = content.get("section_title", "Why LDAS")
    icon_type = content.get("icon_type", "check")
    columns = int(content.get("columns", 1))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;">%s</p>' % (
        DESIGN["label"], DESIGN["brand"], html_mod.escape(section_title)
    )

    if icon_type == "arrow":
        icon_char = "&#x2192;"
    elif icon_type == "dot":
        icon_char = "&bull;"
    else:
        icon_char = "&#x2713;"

    def _icon_html():
        return '<span style="display:inline-block;width:26px;height:26px;background:%s;border-radius:50%%;text-align:center;font-size:13px;color:%s;line-height:26px;">%s</span>' % (
            DESIGN["brand_glow"], DESIGN["brand"], icon_char)

    if columns == 2 and len(items) >= 4:
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
        rows = ""
        for item in items[:8]:
            text = html_mod.escape(str(item))
            rows += '''<tr>
  <td style="width:38px;vertical-align:middle;padding:7px 0;">%s</td>
  <td style="%s;color:%s;padding:7px 0;">%s</td>
</tr>''' % (_icon_html(), DESIGN["body"], DESIGN["text_primary"], text)

        inner = '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">%s</table>' % rows

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%s;border-radius:12px;%s;">
    <tr><td style="padding:16px 20px;">%s</td></tr>
  </table>
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, DESIGN["surface"], DESIGN["card_border"], inner)
```

- [ ] **Step 2: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "feat: dark render_feature_highlights — dark surface card, glow icons"
```

---

### Task 21: Remove unused module-level constants

**Files:**
- Modify: `block_registry.py:25-26`

After all renderers are updated, the old `BRAND_COLOR_LIGHT` and `ACCENT_COLOR` constants are no longer referenced. Remove them.

- [ ] **Step 1: Delete lines 25-26**

Remove:
```python
BRAND_COLOR_LIGHT = "#e8f0ff"
ACCENT_COLOR      = "#0428aa"
```

- [ ] **Step 2: Verify no references remain**

Run: `grep -n "BRAND_COLOR_LIGHT\|ACCENT_COLOR" block_registry.py`
Expected: No output (no remaining references)

- [ ] **Step 3: Verify syntax + commit**

```bash
python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"
git add block_registry.py
git commit -m "chore: remove unused BRAND_COLOR_LIGHT and ACCENT_COLOR constants"
```

---

### Task 22: Smoke test — render all block types

- [ ] **Step 1: Run Python smoke test**

Run a quick Python script that imports `block_registry` and calls every renderer with minimal data to ensure nothing crashes:

```bash
cd "C:/Users/davin/Claude Work Folder/mailenginehub-repo"
python -c "
from block_registry import render_template_blocks, BLOCK_TYPES, _BLOCK_RENDERERS

# Test each renderer with minimal content
test_products = [{'title': 'Test', 'image_url': '', 'price': '9.99', 'product_url': 'https://example.com', 'compare_price': '', 'short_description': 'Desc'}]
test_products2 = test_products + [{'title': 'Test2', 'image_url': '', 'price': '19.99', 'product_url': 'https://example.com', 'compare_price': '29.99', 'short_description': 'Desc2'}]

for block_type, renderer in _BLOCK_RENDERERS.items():
    defaults = BLOCK_TYPES.get(block_type, {}).get('defaults', {})
    try:
        html = renderer(defaults, products=test_products2, discount={'code': 'TEST', 'value_display': '10%', 'display_text': 'Test', 'expires_text': '2026-12-31'})
        status = 'OK (%d chars)' % len(html) if html else 'OK (empty)'
    except Exception as e:
        status = 'FAIL: %s' % e
    print('%s: %s' % (block_type, status))
print('All renderers tested.')
"
```
Expected: All 17 renderers report OK (some may be empty due to missing required fields — that's fine as long as none crash).

- [ ] **Step 2: Verify no white bleed — check all renderers output body_bg**

Quick audit: every `<td>` in every `<tr>` must have `background:#111111` (or a dark token value). The DESIGN dict change + renderer updates handle this. Visual verification will be done in email testing.

- [ ] **Step 3: Final commit**

```bash
git add block_registry.py
git commit -m "feat: dark email redesign — all 17 block renderers complete"
```

---

### Task 23: Deploy and test

- [ ] **Step 1: Deploy to VPS**

```bash
scp -i ~/.ssh/mailengine_vps "C:/Users/davin/Claude Work Folder/mailenginehub-repo/block_registry.py" root@mailenginehub.com:/var/www/mailengine/block_registry.py
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"
```

- [ ] **Step 2: Regenerate showcase templates**

```bash
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "/var/www/mailengine/venv/bin/python /var/www/mailengine/create_showcase_templates.py"
```

- [ ] **Step 3: Send test emails and verify**

Send test emails from the VPS to verify the dark redesign renders correctly in Gmail web. Check:
- Dark bg seamless with header
- Text legibility (white on dark)
- Product spotlight glow visible
- CTA contrast (white button on dark)
- Footer transition (abrupt dark → light is intentional)
