# Dark Email Body Redesign — Design Spec

**Date:** 2026-03-13
**Status:** Approved
**Scope:** All 17 block renderers in `block_registry.py` + DESIGN token system
**Approach:** Approach A — Dark body blocks inside existing white shell (no shell changes)

---

## 1. Goal

Transform LDAS email block renderers from light white-card-on-grey to a premium dark aesthetic inspired by Apple, Shift Robotics, Hims, Fitbit, and Google promotional emails. Full dark `#111111` body background, bold typography, inverted CTAs, and a product spotlight glow to prevent dark-on-dark disappearance.

## 2. Constraints

- `email_shell.py` is **untouched** — header and footer stay as-is
- `wrap_email()` function unchanged
- Block type registry schema (BLOCK_TYPES dict) unchanged — no DB migration
- `render_template_blocks()` master function unchanged
- `validate_template()` unchanged
- `resolve_products_for_contact()` unchanged
- All existing template JSON stays compatible — purely a rendering change

## 3. Architecture: Approach A

Each block renderer wraps its `<td>` content with `background:DESIGN["body_bg"]`. The dark body sits between the already-dark header (`#0a0a0a`) and the light footer (`#f8f8fc`). The shell's `BG_BODY = "#ffffff"` is never visible because every block covers it.

**Gap prevention — CRITICAL RULE:** Many block renderers output multiple `<tr>` elements (e.g., a header `<tr>` + a content `<tr>`). **Every `<td>` in every `<tr>` outputted by a renderer MUST have `background:DESIGN["body_bg"]` set.** If even one `<td>` misses it, the shell's white `#ffffff` bleeds through as a visible white line. During implementation, audit every `<tr>` output per renderer.

**Token usage rule:** All renderers MUST reference `DESIGN[...]` tokens, never raw hex values. The DESIGN dict is the single source of truth. This ensures future palette changes propagate everywhere.

**Footer transition:** The jump from dark body (`#111111`) to light footer (`#f8f8fc`) is intentionally abrupt — it acts as a natural "end of content" boundary, similar to the reference templates (Apple, Shift). No smoothing needed.

**Dark mode CSS conflict (known, accepted):** The shell's `@media (prefers-color-scheme: dark)` sets `.email-outer` to `#1a1a2e` (purple-tinted dark). Our body uses `#111111` (neutral dark). On clients respecting dark mode, there's a subtle outer-vs-body tint difference. This is acceptable — the outer padding area is narrow (8px) and barely visible. A future shell update can harmonize this if needed.

## 4. Dark Design System Tokens

### Color Palette

```python
DESIGN = {
    # Dark backgrounds
    "body_bg":           "#111111",      # Main dark background — on EVERY <td>
    "surface":           "#1a1a1a",      # Elevated surface (cards)
    "surface_border":    "#2a2a2a",      # Subtle card borders
    "divider_color":     "#222222",      # Section dividers

    # Text on dark
    "text_primary":      "#ffffff",      # Headlines, titles, prices
    "text_secondary":    "#b0b0b0",      # Body copy, descriptions
    "text_tertiary":     "#707070",      # Captions, labels, muted

    # Brand (unchanged base, adjusted for dark)
    "brand":             "#063cff",      # LDAS primary blue
    "brand_light":       "#3366ff",      # Lighter blue for dark bg accents
    "brand_glow":        "rgba(6,60,255,0.15)",  # Blue tint for icon circles

    # Buttons (inverted for dark)
    "btn_primary_bg":    "#ffffff",
    "btn_primary_text":  "#111111",
    "btn_card_bg":       "#ffffff",
    "btn_card_text":     "#111111",

    # Prices
    "price_color":       "#ffffff",
    "price_strike":      "#666666",

    # Accents (brighter for dark bg visibility)
    "savings_green":     "#34d399",
    "star_gold":         "#fbbf24",
    "badge_bg":          "rgba(255,255,255,0.1)",
    "badge_text":        "#ffffff",
    "urgency_bg":        "rgba(251,191,36,0.1)",
    "urgency_border":    "rgba(251,191,36,0.25)",
    "urgency_text":      "#fbbf24",

    # Product spotlight glow (for dark products on dark bg)
    "spotlight":         "radial-gradient(ellipse at center, rgba(255,255,255,0.06) 0%, transparent 70%)",

    # Placeholder (no-image fallback)
    "placeholder_bg":    "#1a1a1a",
    "placeholder_text":  "#707070",

    # Spacing (unchanged)
    "section_pad":       "28px 30px",
    "section_pad_top":   "24px 30px 10px",
    "section_pad_tight": "8px 30px",
    "grid_pad":          "4px 24px 8px",

    # Cards (dark variant)
    "card_border":       "1px solid #2a2a2a",
    "card_radius":       "14px",
    "card_shadow":       "none",          # Shadows invisible on dark
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

    # Buttons (shorthand style strings — color applied separately via btn_*_bg/text tokens)
    "btn_primary":       "display:inline-block;text-decoration:none;padding:18px 48px;border-radius:10px;font-weight:700;font-size:16px;letter-spacing:0.3px;min-width:200px;text-align:center",
    "btn_card":          "display:inline-block;text-decoration:none;padding:14px 28px;border-radius:8px;font-weight:600;font-size:14px;text-align:center",
}
```

**Button composition pattern change:** The old `btn_primary`/`btn_card` shorthand strings included `color:#ffffff`. The new ones do NOT include color. Instead, apply colors explicitly:
```python
# OLD pattern:
'style="%s;background:%s;"' % (DESIGN["btn_primary"], BRAND_COLOR)

# NEW pattern:
'style="%s;background:%s;color:%s;"' % (DESIGN["btn_primary"], DESIGN["btn_primary_bg"], DESIGN["btn_primary_text"])
```

### Old-to-New Token Mapping

| Old Token | New Token | Notes |
|---|---|---|
| `card_bg` (#ffffff) | `card_bg` (#1a1a1a) | Same key, dark value |
| `card_border` | `card_border` | Same key, dark value |
| `card_shadow` | `card_shadow` | Same key, now `none` |
| `trust_bg` (#f7f8fc) | `surface` (#1a1a1a) | Renamed |
| `trust_border` | `card_border` | Consolidated |
| `trust_card_bg` (#ffffff) | `card_bg` (#1a1a1a) | Consolidated |
| `accent_light` | `brand_glow` | Renamed |
| `check_color` (BRAND_COLOR) | `brand` (#063cff) | Renamed |
| `badge_amber` (#f59e0b) | `badge_bg` + `badge_text` | Split into bg/text pair |
| `placeholder_bg` (#f0f0f5) | `placeholder_bg` (#1a1a1a) | Same key, dark value |
| `placeholder_text` (#a0aec0) | `placeholder_text` (#707070) | Same key, dark value |
| TEXT_DARK (#1a1a2e) | `text_primary` (#ffffff) | Module-level constant → token |
| TEXT_MID (#4a5568) | `text_secondary` (#b0b0b0) | Module-level constant → token |
| TEXT_LIGHT (#718096) | `text_tertiary` (#707070) | Module-level constant → token |

### Key Differences from Current

| Token | Current (Light) | New (Dark) |
|---|---|---|
| card_bg | `#ffffff` | `#1a1a1a` |
| card_border | `1px solid #e2e4ee` | `1px solid #2a2a2a` |
| card_shadow | `0 2px 12px rgba(0,0,0,0.06)` | `none` |
| body text color | `#4a5568` (TEXT_MID) | `#b0b0b0` |
| headline color | `#1a1a2e` (TEXT_DARK) | `#ffffff` |
| btn_primary | blue bg / white text | white bg / dark text |
| trust_bg | `#f7f8fc` | `#1a1a1a` |
| h1 font-size | 26px | 32px |
| h2 font-size | 20px | 22px |
| btn padding | 16px 40px | 18px 48px |

## 5. Block-by-Block Renderer Changes

### 5.1 render_hero()
- Background: `DESIGN["body_bg"]` (default dark) or custom gradient if set in template JSON
- Headline: 32px/800, `DESIGN["text_primary"]` (white) — ALWAYS white regardless of bg
- Subheadline: 17px, `DESIGN["text_secondary"]` — ALWAYS light grey
- CTA: `DESIGN["btn_primary_bg"]` / `DESIGN["btn_primary_text"]` (white bg / dark text)
- Hero images: Full-width, no padding, edge-to-edge
- **CRITICAL:** Remove the current `is_branded` logic that toggles text color based on bg. On dark, ALL text is always light. The old check (`BRAND_COLOR in bg`) caused dark text on dark bg. New logic: text is always white/light, regardless of bg_color value. If a template sets a light bg_color, the text will be hard to read — but no current templates do this, and new templates should use dark bg or gradients only.

### 5.2 render_text()
- Background: `#111111`
- Body paragraphs: 15px, `#b0b0b0`
- Section header label: `#707070` (tertiary, subtle on dark)

### 5.3 render_product_grid() + _render_product_card()
- Card: `#1a1a1a` bg, `#2a2a2a` border, no shadow
- Image area: spotlight gradient background behind image
- Title: 15px/700, white
- Price: white bold, strikethrough `#666666`
- CTA: White bg / dark text, full width
- Placeholder: `#1a1a1a` bg with `#707070` text

### 5.4 render_product_hero()
- Single elevated card on `#111111`
- Image: spotlight glow background
- Title: 22px/700, white
- SALE pill: `#34d399` bg
- CTA: Large white button, full width

### 5.5 render_comparison() (old) + render_comparison_module() (new)
- Cards: `#1a1a1a`, `#2a2a2a` border
- Highlighted: `#063cff` 2px border + brand accent bar top
- "Recommended" badge: `rgba(255,255,255,0.1)` bg, white text
- Text: white titles, `#b0b0b0` descriptions

### 5.6 render_trust()
- Single `#1a1a1a` background row (not 2x2 white card grid)
- Horizontal inline badges: emoji + white text
- Separated by `#2a2a2a` dividers

### 5.7 render_features()
- Check icons: brand blue on dark (`rgba(6,60,255,0.15)` bg circle)
- Text: white
- No card wrapper — flat on `#111111`

### 5.8 render_discount()
- **No changes** — already self-contained with brand gradient bg

### 5.9 render_cta()
- Primary: `DESIGN["btn_primary_bg"]` / `DESIGN["btn_primary_text"]` (white bg / dark text)
- Secondary link: `DESIGN["text_secondary"]` underline
- Larger padding: 18px 48px
- **Template-level `color` override:** The `cta` block type accepts a `color` parameter in JSON. In the dark redesign, this parameter is **ignored** — all CTA buttons use the inverted white style for consistency. The `color` field remains in the schema for backwards compatibility but has no visual effect.

### 5.10 render_urgency()
- Background: `DESIGN["urgency_bg"]` — `rgba(251,191,36,0.1)`
- Border: `DESIGN["urgency_border"]` — `rgba(251,191,36,0.25)`
- Text: `DESIGN["urgency_text"]` — `#fbbf24`
- **Intentional color shift:** Old urgency used amber-700 (`#d97706`) text. New uses amber-400 (`#fbbf24`) for better visibility on dark backgrounds. The warmer, brighter amber pops better against `#111111`.

### 5.11 render_divider()
- `1px solid #222222`

### 5.12 render_driver_testimonial()
- Card: `#1a1a1a`, `#2a2a2a` border, no shadow
- Left accent bar: brand blue (stays)
- Quote: 17px italic, white
- Stars: `#fbbf24`
- Attribution: `#b0b0b0`

### 5.13 render_why_choose_this()
- Circle icons: `rgba(6,60,255,0.15)` bg, brand blue icon
- Text: white
- Dividers: `#222222`
- No card wrapper — flat on dark body

### 5.14 render_objection_handling()
- Q circle: brand blue bg, A circle: `#34d399` bg
- Statement: red X circle, green check circle (same but on dark)
- Card: `#1a1a1a`, no shadow
- Questions: white, Answers: `#b0b0b0`

### 5.15 render_bundle_value()
- Mini-cards: `#1a1a1a`, `#2a2a2a` border
- Plus circles: `rgba(6,60,255,0.15)` bg
- Savings pill: `#34d399` bg (stays)
- Pricing: white bold

### 5.16 render_best_seller_proof()
- Same as product grid — dark cards, spotlight glow, white text
- Badge pills: `rgba(255,255,255,0.1)` bg, white text
- Stars: `#fbbf24`

### 5.17 render_feature_highlights()
- Same as why_choose_this — circle icons, white text, flat on dark
- 2-column layout preserved

## 6. Product Spotlight Glow

Every product image `<td>` gets:
```css
background: radial-gradient(ellipse at center, rgba(255,255,255,0.06) 0%, transparent 70%);
```

- Dark products: soft halo creates 6% brightness lift, edges visible
- Light products: glow invisible, no visual impact
- Email client fallback: degrades to flat `#111111` in Outlook desktop (acceptable)

## 7. Email Client Compatibility

- All colors inline (no CSS variables)
- Radial gradients: Apple Mail, Gmail, Outlook.com support; Outlook desktop degrades gracefully
- Dark mode media query in shell won't conflict — body is already dark
- Table-based layout preserved throughout

## 8. Files Changed

| File | Change |
|---|---|
| `block_registry.py` | DESIGN dict overhaul + all 17 render functions |

| File | No Change |
|---|---|
| `email_shell.py` | Untouched |
| `database.py` | No schema changes |
| Template JSON in DB | Fully compatible, no migration |
| `condition_engine.py` | Untouched |
| `shopify_products.py` | Untouched |

## 9. Testing

1. Run `create_showcase_templates.py` to regenerate showcase emails
2. Send test emails to `davinderpreet3+darktest@gmail.com`
3. Verify in Gmail web, Gmail mobile, Apple Mail
4. Check: dark bg seamless with header, text legibility, product spotlight glow, CTA contrast, footer transition
5. Deploy to VPS via scp + restart
