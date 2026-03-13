# Content-Rich Email Blocks Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 new content-rich email blocks (competitor_comparison, spec_table, stat_callout, whats_included, faq, use_case_match, brand_story) to the block registry, with family constraints and showcase templates.

**Architecture:** Each block follows the existing pattern: BLOCK_TYPES entry + render function + _BLOCK_RENDERERS lambda + validation in validate_template() + family gates in condition_engine.py. All renderers return `<tr>` HTML using the DESIGN token dict. No new infrastructure.

**Tech Stack:** Python, HTML email tables, existing DESIGN token system in block_registry.py

**Spec:** `docs/superpowers/specs/2026-03-13-content-rich-blocks-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `block_registry.py` | Modify | Add 7 BLOCK_TYPES entries (after line 291), 7 render functions (after line 1198), 7 _BLOCK_RENDERERS lambdas (after line 1300), 7 validation blocks (after line 1611) |
| `condition_engine.py` | Modify | Add new block names to allowed_blocks in 7 TEMPLATE_FAMILIES entries |
| `create_showcase_templates.py` | Modify | Add 7 showcase template entries to SHOWCASE_TEMPLATES list |

---

## Chunk 1: Block Types + Renderers (Blocks 1-4)

### Task 1: Add BLOCK_TYPES entries for all 7 new blocks

**Files:**
- Modify: `block_registry.py:291` (after the closing `}` of BLOCK_TYPES dict, insert before it)

- [ ] **Step 1: Add all 7 BLOCK_TYPES entries**

Insert these entries right before the closing `}` of the BLOCK_TYPES dict (currently at line 292):

```python
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
        "required": ["products", "rows"],
        "optional": ["section_title", "highlight_index"],
        "defaults": {
            "section_title": "Compare Specs",
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add BLOCK_TYPES entries for 7 content-rich blocks"
```

---

### Task 2: Implement render_competitor_comparison()

**Files:**
- Modify: `block_registry.py` (insert after render_feature_highlights, before render_divider — around line 1199)

- [ ] **Step 1: Write the renderer**

Insert after `render_feature_highlights()` (after line 1198), before `render_divider()`:

```python
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

    num_cols = 1 + 1 + len(competitors)  # feature + LDAS + competitors
    ldas_w = "25%%"
    comp_w = "25%%"
    feat_w = "%d%%%%" % max(100 - 25 - 25 * len(competitors), 25)

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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_competitor_comparison() block renderer"
```

---

### Task 3: Implement render_spec_table()

**Files:**
- Modify: `block_registry.py` (insert after render_competitor_comparison)

- [ ] **Step 1: Write the renderer**

Insert after `render_competitor_comparison()`:

```python
def render_spec_table(content):
    """Product spec comparison table — 2-3 products with spec rows."""
    products = content.get("products", [])
    rows = content.get("rows", [])
    if len(products) < 2 or not rows:
        return ""

    products = products[:3]
    rows = rows[:8]
    section_title = html_mod.escape(content.get("section_title", "Compare Specs"))
    highlight_index = int(content.get("highlight_index", -1))

    header_html = '<p style="margin:0 0 16px;%s;color:%s;text-align:center;">%s</p>' % (
        DESIGN["label"], DESIGN["text_tertiary"], section_title
    )

    # Header row with product names
    prod_headers = ""
    for pi, prod in enumerate(products):
        bg = DESIGN["brand_glow"] if pi == highlight_index else DESIGN["surface"]
        prod_headers += '<td style="padding:10px 8px;text-align:center;font-weight:700;color:%s;background:%s;">%s</td>' % (
            DESIGN["text_primary"], bg, html_mod.escape(prod.get("name", ""))
        )
    thead = '<tr><td style="padding:10px 8px;background:%s;">&nbsp;</td>%s</tr>' % (
        DESIGN["surface"], prod_headers
    )

    # Spec rows
    tbody = ""
    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        label = html_mod.escape(row.get("label", ""))
        key = row.get("key", "")
        bg = DESIGN["surface"] if ri % 2 == 0 else DESIGN["body_bg"]

        cells = ""
        for pi, prod in enumerate(products):
            val = html_mod.escape(str(prod.get("specs", {}).get(key, "—")))
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_spec_table() block renderer"
```

---

### Task 4: Implement render_stat_callout()

**Files:**
- Modify: `block_registry.py` (insert after render_spec_table)

- [ ] **Step 1: Write the renderer**

Insert after `render_spec_table()`:

```python
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_stat_callout() block renderer"
```

---

### Task 5: Implement render_whats_included()

**Files:**
- Modify: `block_registry.py` (insert after render_stat_callout)

- [ ] **Step 1: Write the renderer**

Insert after `render_stat_callout()`:

```python
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

    # Build checklist items
    check_icon = '<span style="display:inline-block;width:26px;height:26px;background:%s;border-radius:50%%;text-align:center;font-size:13px;color:%s;line-height:26px;">&#x2713;</span>' % (
        DESIGN["brand_glow"], DESIGN["brand"]
    )

    list_rows = ""
    for item in items[:8]:
        text = html_mod.escape(str(item))[:40]
        list_rows += '''<tr>
  <td style="width:38px;vertical-align:middle;padding:6px 0;">%s</td>
  <td style="font-size:14px;color:%s;padding:6px 0;line-height:1.5;">%s</td>
</tr>''' % (check_icon, DESIGN["text_primary"], text)

    list_html = '<table role="presentation" cellpadding="0" cellspacing="0" border="0">%s</table>' % list_rows

    if image_url:
        # Image + list layout in card
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
        # Centered list, no card
        inner = '<div style="max-width:360px;margin:0 auto;">%s</div>' % list_html

    return '''<tr><td style="padding:%s;background:%s;" class="mobile-pad">
  %s%s
</td></tr>''' % (DESIGN["section_pad"], DESIGN["body_bg"], header_html, inner)
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_whats_included() block renderer"
```

---

## Chunk 2: Block Types + Renderers (Blocks 5-7)

### Task 6: Implement render_faq()

**Files:**
- Modify: `block_registry.py` (insert after render_whats_included)

- [ ] **Step 1: Write the renderer**

Insert after `render_whats_included()`:

```python
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_faq() block renderer"
```

---

### Task 7: Implement render_use_case_match()

**Files:**
- Modify: `block_registry.py` (insert after render_faq)

- [ ] **Step 1: Write the renderer**

Insert after `render_faq()`:

```python
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_use_case_match() block renderer"
```

---

### Task 8: Implement render_brand_story()

**Files:**
- Modify: `block_registry.py` (insert after render_use_case_match)

- [ ] **Step 1: Add variant badge defaults constant**

Insert near the top of the file (after the DESIGN dict, around line 100, before BLOCK_TYPES), add:

```python
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
```

- [ ] **Step 2: Write the renderer**

Insert after `render_use_case_match()`:

```python
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

    # Use variant defaults if no custom badges
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

    # Badge row
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

    # Optional CTA
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
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
git add block_registry.py
git commit -m "feat: add render_brand_story() block renderer with variant badges"
```

---

## Chunk 3: Dispatch + Validation + Family Gates

### Task 9: Add _BLOCK_RENDERERS entries

**Files:**
- Modify: `block_registry.py:1300` (add entries before closing `}` of _BLOCK_RENDERERS dict)

- [ ] **Step 1: Add 7 dispatch lambdas**

Insert after the `"feature_highlights"` entry (line 1300), before the closing `}`:

```python
    # Content-rich modules
    "competitor_comparison": lambda content, **kw: render_competitor_comparison(content),
    "spec_table":           lambda content, **kw: render_spec_table(content),
    "stat_callout":         lambda content, **kw: render_stat_callout(content),
    "whats_included":       lambda content, **kw: render_whats_included(content),
    "faq":                  lambda content, **kw: render_faq(content),
    "use_case_match":       lambda content, **kw: render_use_case_match(content),
    "brand_story":          lambda content, **kw: render_brand_story(content),
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add _BLOCK_RENDERERS dispatch for 7 content-rich blocks"
```

---

### Task 10: Add validation rules

**Files:**
- Modify: `block_registry.py` (insert after the `feature_highlights` validation block, around line 1611)

- [ ] **Step 1: Add validation for all 7 new blocks**

Insert after the `feature_highlights` validation block (after line 1611):

```python
        # ── Content-rich module validations ──
        if block_type == "competitor_comparison":
            competitors = content.get("competitors", [])
            if not isinstance(competitors, list) or len(competitors) < 1 or len(competitors) > 2:
                warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): 'competitors' must be a list of 1-2 names" % block_num})
            rows_data = content.get("rows", [])
            if not isinstance(rows_data, list):
                warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): 'rows' must be a list" % block_num})
            elif len(rows_data) < 4 or len(rows_data) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Competitor Comparison): %d rows -- aim for 4-8" % (block_num, len(rows_data))})
            else:
                for ri, row in enumerate(rows_data):
                    if not isinstance(row, dict) or "feature" not in row:
                        warnings.append({"level": "error", "message": "Block %d (Competitor Comparison): row %d must have 'feature', 'ldas', 'competitors'" % (block_num, ri + 1)})

        if block_type == "spec_table":
            products_data = content.get("products", [])
            if not isinstance(products_data, list) or len(products_data) < 2 or len(products_data) > 3:
                warnings.append({"level": "error", "message": "Block %d (Spec Table): 'products' must be a list of 2-3 items" % block_num})
            else:
                for pi, prod in enumerate(products_data):
                    if not isinstance(prod, dict) or "name" not in prod or "specs" not in prod:
                        warnings.append({"level": "error", "message": "Block %d (Spec Table): product %d must have 'name' and 'specs'" % (block_num, pi + 1)})
            rows_data = content.get("rows", [])
            if not isinstance(rows_data, list) or len(rows_data) < 4 or len(rows_data) > 8:
                warnings.append({"level": "warning", "message": "Block %d (Spec Table): aim for 4-8 spec rows" % block_num})

        if block_type == "stat_callout":
            stats = content.get("stats", [])
            if not isinstance(stats, list) or len(stats) != 3:
                warnings.append({"level": "error", "message": "Block %d (Stat Callout): 'stats' must be exactly 3 items" % block_num})
            else:
                for si, stat in enumerate(stats):
                    if not isinstance(stat, dict) or "value" not in stat or "label" not in stat:
                        warnings.append({"level": "error", "message": "Block %d (Stat Callout): stat %d must have 'value' and 'label'" % (block_num, si + 1)})

        if block_type == "whats_included":
            items_data = content.get("items", [])
            if not isinstance(items_data, list):
                warnings.append({"level": "error", "message": "Block %d (What's Included): 'items' must be a list" % block_num})
            elif len(items_data) < 4 or len(items_data) > 8:
                warnings.append({"level": "warning", "message": "Block %d (What's Included): aim for 4-8 items, got %d" % (block_num, len(items_data))})

        if block_type == "faq":
            items_data = content.get("items", [])
            if not isinstance(items_data, list):
                warnings.append({"level": "error", "message": "Block %d (FAQ): 'items' must be a list" % block_num})
            elif len(items_data) < 2 or len(items_data) > 4:
                warnings.append({"level": "warning", "message": "Block %d (FAQ): aim for 2-4 Q&A items, got %d" % (block_num, len(items_data))})
            else:
                for fi, fitem in enumerate(items_data):
                    if not isinstance(fitem, dict) or "question" not in fitem or "answer" not in fitem:
                        warnings.append({"level": "error", "message": "Block %d (FAQ): item %d must have 'question' and 'answer'" % (block_num, fi + 1)})

        if block_type == "use_case_match":
            cases_data = content.get("cases", [])
            if not isinstance(cases_data, list):
                warnings.append({"level": "error", "message": "Block %d (Use Case Match): 'cases' must be a list" % block_num})
            elif len(cases_data) < 2 or len(cases_data) > 3:
                warnings.append({"level": "error", "message": "Block %d (Use Case Match): need 2-3 cases, got %d" % (block_num, len(cases_data))})
            else:
                for ci, case in enumerate(cases_data):
                    if not isinstance(case, dict) or "persona" not in case or "product_name" not in case:
                        warnings.append({"level": "error", "message": "Block %d (Use Case Match): case %d must have 'persona', 'description', 'product_name', 'product_url'" % (block_num, ci + 1)})

        if block_type == "brand_story":
            headline_val = content.get("headline", "")
            if headline_val and len(headline_val) > 50:
                warnings.append({"level": "warning", "message": "Block %d (Brand Story): headline is %d chars -- keep under 50" % (block_num, len(headline_val))})
            body_val = content.get("body", "")
            if body_val and len(body_val) > 200:
                warnings.append({"level": "warning", "message": "Block %d (Brand Story): body is %d chars -- keep under 200" % (block_num, len(body_val))})
            variant_val = content.get("variant", "mission")
            if variant_val not in ("mission", "sustainability", "heritage"):
                warnings.append({"level": "error", "message": "Block %d (Brand Story): variant must be 'mission', 'sustainability', or 'heritage'" % block_num})
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('block_registry.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add block_registry.py
git commit -m "feat: add validation rules for 7 content-rich blocks"
```

---

### Task 11: Update TEMPLATE_FAMILIES in condition_engine.py

**Files:**
- Modify: `condition_engine.py:473-537` (TEMPLATE_FAMILIES dict)

- [ ] **Step 1: Add new blocks to each family's allowed_blocks**

Append new block names to each family's `allowed_blocks` list per the spec:

**welcome** (line 477) — append: `"stat_callout", "faq", "use_case_match", "brand_story"`

**browse_recovery** (line 486) — append: `"competitor_comparison", "spec_table", "stat_callout", "faq", "use_case_match"`

**cart_recovery** (line 495) — append: `"stat_callout", "whats_included"`

**checkout_recovery** (line 504) — append: `"stat_callout"`

**post_purchase** (line 513) — append: `"stat_callout", "whats_included", "brand_story"`

**winback** (line 522) — append: `"competitor_comparison", "stat_callout", "faq", "use_case_match", "brand_story"`

**promo** (line 531) — append: `"competitor_comparison", "spec_table", "stat_callout", "whats_included", "faq", "use_case_match", "brand_story"`

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('condition_engine.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add condition_engine.py
git commit -m "feat: add content-rich blocks to TEMPLATE_FAMILIES allowed_blocks"
```

---

## Chunk 4: Showcase Templates

### Task 12: Add showcase templates for all 7 new blocks

**Files:**
- Modify: `create_showcase_templates.py` (append 7 new template entries to SHOWCASE_TEMPLATES list)

- [ ] **Step 1: Add 7 showcase template entries**

Append these entries to the SHOWCASE_TEMPLATES list (before the closing `]`):

```python
    # ── Content-rich module showcases ──
    {
        "name": "Module: Competitor Comparison",
        "subject": "[Showcase] Competitor Comparison",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "See How We Stack Up", "subheadline": "Feature-by-feature comparison"}},
            {"block_type": "competitor_comparison", "content": {
                "competitors": ["BlueParrott", "Jabra"],
                "rows": [
                    {"feature": "24-Hour Battery", "ldas": True, "competitors": [False, False]},
                    {"feature": "Noise Cancelling", "ldas": True, "competitors": [True, True]},
                    {"feature": "Multi-Device Pairing", "ldas": True, "competitors": [False, True]},
                    {"feature": "Under $90 CAD", "ldas": True, "competitors": [False, False]},
                    {"feature": "Canadian Support", "ldas": True, "competitors": [False, False]},
                    {"feature": "30-Day Free Returns", "ldas": True, "competitors": [True, False]}
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop LDAS", "url": "https://ldas.ca"}}
        ]
    },
    {
        "name": "Module: Spec Table",
        "subject": "[Showcase] Spec Comparison Table",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "Compare Our Headsets", "subheadline": "Specs side by side"}},
            {"block_type": "spec_table", "content": {
                "section_title": "Compare Specs",
                "highlight_index": 0,
                "products": [
                    {"name": "TH11", "specs": {"talk": "40 hrs", "listen": "60 hrs", "noise": "96% AI", "multi": "Yes", "charge": "USB-C Fast", "price": "$89.99"}},
                    {"name": "G10", "specs": {"talk": "36 hrs", "listen": "60 hrs", "noise": "Dual-Mic", "multi": "Yes", "charge": "USB-C", "price": "$65.99"}},
                    {"name": "G7", "specs": {"talk": "20 hrs", "listen": "72 hrs (case)", "noise": "Single-Mic", "multi": "Yes", "charge": "USB-C", "price": "$54.99"}}
                ],
                "rows": [
                    {"label": "Talk Time", "key": "talk"},
                    {"label": "Listen Time", "key": "listen"},
                    {"label": "Noise Cancelling", "key": "noise"},
                    {"label": "Multi-Device", "key": "multi"},
                    {"label": "Charging", "key": "charge"},
                    {"label": "Price", "key": "price"}
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop All Headsets", "url": "https://ldas.ca/collections/all"}}
        ]
    },
    {
        "name": "Module: Stat Callout",
        "subject": "[Showcase] Stat Callout",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "By the Numbers", "subheadline": "LDAS TH11 Trucker Headset"}},
            {"block_type": "stat_callout", "content": {
                "stats": [
                    {"value": "40hr", "label": "Talk Time"},
                    {"value": "96%", "label": "Noise Cancelled"},
                    {"value": "BT 5.2", "label": "Bluetooth"}
                ]
            }},
            {"block_type": "stat_callout", "content": {
                "section_title": "Brand at a Glance",
                "stats": [
                    {"value": "2,000+", "label": "Reviews"},
                    {"value": "4.8", "label": "Star Rating"},
                    {"value": "30-Day", "label": "Free Returns"}
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}}
        ]
    },
    {
        "name": "Module: What's Included",
        "subject": "[Showcase] What's Included",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "Everything You Need", "subheadline": "What comes in the box"}},
            {"block_type": "whats_included", "content": {
                "product_name": "G7 Headset",
                "items": [
                    "1x G7 Bluetooth Headset",
                    "1x 500mAh Charging Case",
                    "5x Ear Tips (S/M/L)",
                    "1x USB-C Charging Cable",
                    "1x Quick Start Guide"
                ]
            }},
            {"block_type": "whats_included", "content": {
                "product_name": "TH11 Headset",
                "image_url": "https://ldas.ca/cdn/shop/files/trucker-bluetooth-headset-th11-ldas_1.jpg",
                "items": [
                    "1x TH11 Trucker Headset",
                    "1x Boom Mic Attachment",
                    "1x USB-C Charging Cable",
                    "1x Carrying Pouch",
                    "1x User Manual"
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}}
        ]
    },
    {
        "name": "Module: FAQ",
        "subject": "[Showcase] FAQ Block",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "Got Questions?", "subheadline": "We have answers"}},
            {"block_type": "faq", "content": {
                "section_title": "Common Questions",
                "items": [
                    {"question": "Which headset is best for truck driving?", "answer": "The TH11 is built for truckers — 40hr talk time, boom mic for highway noise, and dual-device pairing."},
                    {"question": "Can I pair with my phone and GPS at once?", "answer": "Yes. All LDAS headsets support multipoint Bluetooth — connect two devices simultaneously."},
                    {"question": "How long does the battery actually last?", "answer": "TH11: 40hrs talk. G10: 36hrs. G7: 20hrs plus 72hrs with the charging case."}
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop All Headsets", "url": "https://ldas.ca/collections/all"}}
        ]
    },
    {
        "name": "Module: Use Case Match",
        "subject": "[Showcase] Use Case Match",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "Find Your Perfect Match", "subheadline": "The right headset for your work style"}},
            {"block_type": "use_case_match", "content": {
                "cases": [
                    {
                        "persona": "Long-Haul Drivers",
                        "description": "20+ hours on the road. Need all-day battery and noise isolation.",
                        "product_name": "TH11 Trucker Headset",
                        "product_url": "https://ldas.ca/products/ldas-trucker-bluetooth-headset-th11"
                    },
                    {
                        "persona": "City & Local",
                        "description": "In and out of the cab. Quick pairing, compact fit.",
                        "product_name": "G7 Headset",
                        "product_url": "https://ldas.ca/products/ldas-bluetooth-headset-g7"
                    },
                    {
                        "persona": "Office & Fleet",
                        "description": "Desk calls all day. Comfort and dual-device pairing.",
                        "product_name": "G40 Office Headset",
                        "product_url": "https://ldas.ca/products/ldas-office-bluetooth-headset-g40"
                    }
                ]
            }},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}}
        ]
    },
    {
        "name": "Module: Brand Story",
        "subject": "[Showcase] Brand Story Variants",
        "family": "promo",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "Our Story", "subheadline": "Three variants of the brand story block"}},
            {"block_type": "brand_story", "content": {
                "variant": "heritage",
                "headline": "Proudly Canadian. Built for the Road.",
                "body": "LDAS Electronics is a Canadian-owned company based in Brampton, Ontario. We design professional-grade audio and dash cam gear for drivers who depend on clarity and reliability.",
                "cta_text": "Our Story",
                "cta_url": "https://ldas.ca/pages/about-us"
            }},
            {"block_type": "brand_story", "content": {
                "variant": "sustainability",
                "section_title": "Our Commitment",
                "headline": "Building a Greener Future",
                "body": "95% recyclable packaging today. 100% FSC-certified by 2026. Every shipment gets us closer to carbon-neutral operations."
            }},
            {"block_type": "brand_story", "content": {
                "variant": "mission",
                "headline": "Technology Enhances Life",
                "body": "We believe professional-grade audio should be accessible to every driver. Clear calls, long battery, fair prices. That simple."
            }},
            {"block_type": "cta", "content": {"text": "Shop LDAS", "url": "https://ldas.ca"}}
        ]
    },
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('create_showcase_templates.py', doraise=True)"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add create_showcase_templates.py
git commit -m "feat: add 7 showcase templates for content-rich blocks"
```

---

## Chunk 5: Smoke Test + Deploy

### Task 13: Smoke test all renderers locally

**Files:**
- No files modified — testing only

- [ ] **Step 1: Run smoke test**

Run:
```bash
cd "C:/Users/davin/Claude Work Folder/mailenginehub-repo"
python -c "
from block_registry import BLOCK_TYPES, _BLOCK_RENDERERS, DESIGN
import json

new_blocks = ['competitor_comparison', 'spec_table', 'stat_callout', 'whats_included', 'faq', 'use_case_match', 'brand_story']

test_data = {
    'competitor_comparison': {'competitors': ['BlueParrott'], 'rows': [{'feature': 'Battery', 'ldas': True, 'competitors': [False]}, {'feature': 'Noise Cancel', 'ldas': True, 'competitors': [True]}, {'feature': 'Price', 'ldas': True, 'competitors': [False]}, {'feature': 'Returns', 'ldas': True, 'competitors': [False]}]},
    'spec_table': {'products': [{'name': 'TH11', 'specs': {'talk': '40hr', 'price': '\$89'}}, {'name': 'G7', 'specs': {'talk': '20hr', 'price': '\$54'}}], 'rows': [{'label': 'Talk Time', 'key': 'talk'}, {'label': 'Price', 'key': 'price'}, {'label': 'Extra1', 'key': 'talk'}, {'label': 'Extra2', 'key': 'price'}]},
    'stat_callout': {'stats': [{'value': '24hr', 'label': 'Battery'}, {'value': '96%', 'label': 'Noise'}, {'value': '5.2', 'label': 'Bluetooth'}]},
    'whats_included': {'items': ['Headset', 'Charging case', 'USB cable', 'Ear tips']},
    'faq': {'items': [{'question': 'How long does battery last?', 'answer': 'Up to 40 hours talk time.'}, {'question': 'Is it Bluetooth?', 'answer': 'Yes, Bluetooth 5.2.'}]},
    'use_case_match': {'cases': [{'persona': 'Truckers', 'description': 'All day battery.', 'product_name': 'TH11', 'product_url': 'https://ldas.ca'}, {'persona': 'Office', 'description': 'Comfort.', 'product_name': 'G40', 'product_url': 'https://ldas.ca'}]},
    'brand_story': {'headline': 'Built for the Road', 'body': 'Canadian-owned audio gear for drivers.'},
}

for block_name in new_blocks:
    assert block_name in BLOCK_TYPES, f'{block_name} missing from BLOCK_TYPES'
    assert block_name in _BLOCK_RENDERERS, f'{block_name} missing from _BLOCK_RENDERERS'
    renderer = _BLOCK_RENDERERS[block_name]
    html = renderer(test_data[block_name])
    assert '<tr>' in html, f'{block_name} did not return <tr> HTML'
    assert DESIGN['body_bg'] in html, f'{block_name} missing body_bg'
    print(f'  OK  {block_name} ({len(html)} chars)')

print('All 7 new blocks pass smoke test')
"
```
Expected: All 7 blocks print OK with character counts, then "All 7 new blocks pass smoke test"

- [ ] **Step 2: Run validation smoke test**

Run:
```bash
python -c "
from block_registry import validate_template
import json

# Valid template
valid = json.dumps([
    {'block_type': 'hero', 'content': {'headline': 'Test'}},
    {'block_type': 'stat_callout', 'content': {'stats': [{'value': '1', 'label': 'a'}, {'value': '2', 'label': 'b'}, {'value': '3', 'label': 'c'}]}},
    {'block_type': 'cta', 'content': {'text': 'Click', 'url': 'https://ldas.ca'}}
])
warnings = validate_template(valid)
errors = [w for w in warnings if w['level'] == 'error']
assert not errors, f'Unexpected errors: {errors}'
print('Validation: valid template OK')

# Invalid stat_callout (wrong count)
invalid = json.dumps([
    {'block_type': 'stat_callout', 'content': {'stats': [{'value': '1', 'label': 'a'}]}},
    {'block_type': 'cta', 'content': {'text': 'Click', 'url': 'https://ldas.ca'}}
])
warnings = validate_template(invalid)
stat_errors = [w for w in warnings if 'Stat Callout' in w['message']]
assert stat_errors, 'Expected stat_callout validation error'
print('Validation: invalid stat_callout caught')
print('Validation smoke test passed')
"
```
Expected: Both tests print OK, then "Validation smoke test passed"

---

### Task 14: Deploy to VPS

**Files:**
- No files modified — deployment only

- [ ] **Step 1: Deploy all 3 modified files**

Run:
```bash
scp -i ~/.ssh/mailengine_vps "C:/Users/davin/Claude Work Folder/mailenginehub-repo/block_registry.py" root@mailenginehub.com:/var/www/mailengine/block_registry.py
scp -i ~/.ssh/mailengine_vps "C:/Users/davin/Claude Work Folder/mailenginehub-repo/condition_engine.py" root@mailenginehub.com:/var/www/mailengine/condition_engine.py
scp -i ~/.ssh/mailengine_vps "C:/Users/davin/Claude Work Folder/mailenginehub-repo/create_showcase_templates.py" root@mailenginehub.com:/var/www/mailengine/create_showcase_templates.py
```

- [ ] **Step 2: Restart service**

Run:
```bash
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine && systemctl status mailengine --no-pager -l | head -10"
```
Expected: `Active: active (running)`

- [ ] **Step 3: Regenerate showcase templates**

Run:
```bash
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "cd /var/www/mailengine && source venv/bin/activate && python create_showcase_templates.py 2>&1 | tail -20"
```
Expected: 7 new templates created (or updated), existing 8 updated

- [ ] **Step 4: Verify previews load**

Open in browser and verify no 500 errors:
- https://mailenginehub.com/api/templates/27/preview-blocks (Competitor Comparison)
- https://mailenginehub.com/api/templates/28/preview-blocks (Spec Table)
- https://mailenginehub.com/api/templates/29/preview-blocks (Stat Callout)
- https://mailenginehub.com/api/templates/30/preview-blocks (What's Included)
- https://mailenginehub.com/api/templates/31/preview-blocks (FAQ)
- https://mailenginehub.com/api/templates/32/preview-blocks (Use Case Match)
- https://mailenginehub.com/api/templates/33/preview-blocks (Brand Story)

Note: Template IDs may differ — check the create_showcase_templates.py output for actual IDs.

- [ ] **Step 5: Commit all changes if not already committed**

```bash
git add block_registry.py condition_engine.py create_showcase_templates.py
git commit -m "feat: complete 7 content-rich email blocks — ready for production"
```
