# Content-Rich Email Blocks — Design Spec

## Context

The dark email redesign is complete (18 blocks). Now we expand the block library with 7 new content-rich modules sourced from ldas.ca product pages, landing pages, and brand content. These blocks fill gaps the persuasion playbook identified: product education, competitive differentiation, use-case segmentation, and brand storytelling.

All 7 blocks follow the existing system contract — no new infrastructure.

**Existing system contract (unchanged):**
- Block schema: `{ "label", "required", "optional", "defaults" }` in `BLOCK_TYPES` dict
- Renderer: `def render_<type>(content, **kwargs) -> str` returning `<tr>` HTML
- Dispatch: lambda entry in `_BLOCK_RENDERERS` dict
- Family gates: `allowed_blocks` in `TEMPLATE_FAMILIES` per journey in `condition_engine.py`
- Validation: block-specific checks in `validate_template()`
- Design tokens: all styling via `DESIGN` dict (dark theme, 44 tokens)
- Product data: `{ title, image_url, price, product_url, compare_price, short_description }`

**Global rules for all 7 new blocks:**
- All user-provided string fields must be HTML-escaped via `html.escape()` before insertion into rendered output (consistent with all existing renderers)
- None of the 7 new blocks require the `products` kwarg — all data comes from `content`. Renderer signatures are `def render_<type>(content)` and dispatch lambdas are `lambda content, **kw: render_<type>(content)`
- Each BLOCK_TYPES entry must include a `"label"` string for the UI (e.g., `"label": "Competitor Comparison"`)

**Files to modify:**
- `block_registry.py` — BLOCK_TYPES entries, render functions, _BLOCK_RENDERERS, validation
- `condition_engine.py` — TEMPLATE_FAMILIES allowed_blocks
- `create_showcase_templates.py` — showcase templates for each new block

---

## Module 1: `competitor_comparison`

**Purpose:** Side-by-side feature comparison between LDAS and named competitors. Check/X grid that makes LDAS the obvious winner. Mirrors the comparison table on the ldas.ca homepage.

**Best journey stages:**
- High-Intent Browse — primary differentiator tool for evaluators
- Browse Recovery — "Still deciding? See how we compare"
- Win-Back E2 — "See what makes us different"

**NOT appropriate for:**
- Cart/Checkout Recovery — they already chose LDAS, don't re-introduce competitors
- Post-Purchase — irrelevant after buying
- Any email already using `comparison` or `spec_table` — information overload

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `competitors` | list[str] | 1-2 competitor names (e.g., `["BlueParrott", "Jabra"]`) |
| `rows` | list[dict] | Each: `{ "feature": str, "ldas": bool, "competitors": list[bool] }`. 4-8 rows |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `"How We Compare"` | Branded uppercase label |
| `ldas_label` | str | `"LDAS"` | Column header for LDAS column |

**Defaults:**
```python
"defaults": {
    "section_title": "How We Compare",
    "ldas_label": "LDAS",
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Section title in `DESIGN["label"]` style, `DESIGN["text_tertiary"]` color, centered
- Table: first column = feature names (`text_secondary`), second column = LDAS (highlighted with `brand_glow` background), remaining columns = competitors
- LDAS column header gets `DESIGN["brand"]` color, bold
- Competitor column headers in `text_tertiary`
- Checkmark: `&#10003;` in `DESIGN["savings_green"]` (#34d399)
- X mark: `&#10007;` in `#ef4444`
- Alternating row backgrounds: `DESIGN["body_bg"]` / `DESIGN["surface"]`
- LDAS column cells get `DESIGN["brand_glow"]` background throughout
- Table has `DESIGN["card_border"]` on outer edge, `DESIGN["card_radius"]` corners

**Validation:**
- `competitors` must be a list of 1-2 strings
- `rows` must be a list of 4-8 dicts
- Each row must have `feature` (str), `ldas` (bool), `competitors` (list[bool])
- Length of each row's `competitors` list must match length of `competitors` field

**Example:**
```python
{
    "block_type": "competitor_comparison",
    "content": {
        "competitors": ["BlueParrott", "Jabra"],
        "rows": [
            {"feature": "24-Hour Battery", "ldas": True, "competitors": [False, False]},
            {"feature": "Noise Cancelling", "ldas": True, "competitors": [True, True]},
            {"feature": "Multi-Device Pairing", "ldas": True, "competitors": [False, True]},
            {"feature": "Under $90 CAD", "ldas": True, "competitors": [False, False]},
            {"feature": "Canadian Support", "ldas": True, "competitors": [False, False]},
            {"feature": "30-Day Free Returns", "ldas": True, "competitors": [True, False]}
        ]
    }
}
```

---

## Module 2: `spec_table`

**Purpose:** Product specification comparison table. 2-3 LDAS products across columns, spec rows down the side. Helps evaluators decide between models without leaving the email. Mirrors the trucker landing page spec table.

**Best journey stages:**
- High-Intent Browse — primary evaluation tool
- Browse Recovery — "Quick comparison of what you viewed"
- Welcome E2 — "Our lineup at a glance"

**NOT appropriate for:**
- Cart/Checkout Recovery — they already chose a product
- Post-Purchase — irrelevant after buying
- Any email already using `comparison` or `competitor_comparison`

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `products` | list[dict] | 2-3 products. Each: `{ "name": str, "specs": dict }` where spec keys match `rows[].key` |
| `rows` | list[dict] | Each: `{ "label": str, "key": str }`. 4-8 spec rows |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `"Compare Specs"` | Branded uppercase label |
| `highlight_index` | int | `-1` | Column to highlight (0-indexed). -1 = no highlight |

**Defaults:**
```python
"defaults": {
    "section_title": "Compare Specs",
    "highlight_index": -1,
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Section title in `DESIGN["label"]` style, `DESIGN["text_tertiary"]` color, centered
- Table: first column = spec labels (`text_secondary`, left-aligned), product columns right
- Product name headers in `text_primary`, bold, centered
- Highlighted column gets `DESIGN["brand_glow"]` background on all cells
- Alternating row backgrounds: `DESIGN["body_bg"]` / `DESIGN["surface"]`
- Spec values in `text_primary`, centered
- "Best" value in each row: bold (determined by renderer — highest numeric value, or "Yes" over "No")
- Table has `DESIGN["card_border"]` on outer edge, `DESIGN["card_radius"]` corners

**Validation:**
- `products` must be 2-3 items, each with `name` (str) and `specs` (dict)
- `rows` must be 4-8 items, each with `label` (str) and `key` (str)
- Every `rows[].key` must exist in every `products[].specs` dict

**Example:**
```python
{
    "block_type": "spec_table",
    "content": {
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
    }
}
```

---

## Module 3: `stat_callout`

**Purpose:** 3 bold hero-style numbers that grab skimmers. Big number, small label. Inspired by ldas.ca G7 page impact stats section ("500mAh | 10M range | 30% lighter").

**Best journey stages:**
- Every journey — universal utility block
- Below `product_hero` — quantifies the product
- Standalone — social proof stats ("2,000+ Reviews | 4.8 Stars | 30-Day Returns")
- Welcome E1 — brand impact numbers

**NOT appropriate for:**
- Never inappropriate — adapts to any context. Just don't use more than once per email.

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `stats` | list[dict] | Exactly 3. Each: `{ "value": str, "label": str }`. Value max 10 chars. Label max 20 chars |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `""` | If set, shows branded label above stats. Empty = no label |
| `accent_color` | str | `""` | Hex color for value numbers. Empty = uses `DESIGN["brand"]` |

**Defaults:**
```python
"defaults": {
    "section_title": "",
    "accent_color": "",
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Optional section title in `DESIGN["label"]` style, `DESIGN["text_tertiary"]` color, centered
- 3 equal-width `<td>` cells in an inner table (33.3% each)
- Value: 28px, bold, `accent_color` or `DESIGN["brand"]`, centered
- Label: 12px, `DESIGN["text_secondary"]`, centered, below value
- Vertical dividers between cells: 1px `DESIGN["surface_border"]`
- Padding: `DESIGN["section_pad"]`

**Validation:**
- `stats` must be exactly 3 items
- Each must have `value` (str, max 10 chars) and `label` (str, max 20 chars)

**Example:**
```python
{
    "block_type": "stat_callout",
    "content": {
        "stats": [
            {"value": "24hr", "label": "Talk Time"},
            {"value": "96%", "label": "Noise Cancelled"},
            {"value": "500mAh", "label": "Case Battery"}
        ]
    }
}
```

---

## Module 4: `whats_included`

**Purpose:** "What's in the box" list. Builds anticipation before purchase and reduces "what do I get?" uncertainty. Directly from ldas.ca G7/G3 product pages which list headset, ear tips, charging case, cable, and guide.

**Best journey stages:**
- High-Intent Browse — removes "what do I actually get?" uncertainty
- Post-Purchase E1 — builds anticipation for delivery ("Here's what's on the way")
- Cart Recovery E1 — reminds them of the full package value

**NOT appropriate for:**
- Welcome E1 — too product-specific for a brand intro
- Win-Back — they need re-engagement, not box contents
- Browse Recovery E1 — too detailed for a casual nudge

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `items` | list[str] | 4-8 items. Each max 40 chars. e.g., "1x G7 Bluetooth Headset" |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `"What's Included"` | Branded uppercase label |
| `product_name` | str | `""` | If set, title becomes "What's in the [product_name] Box" |
| `image_url` | str | `""` | Optional product/box image shown left of the list |

**Defaults:**
```python
"defaults": {
    "section_title": "What's Included",
    "product_name": "",
    "image_url": "",
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Section title: if `product_name` set, use `"What's in the {product_name} Box"`, else use `section_title`. `DESIGN["label"]` style, `DESIGN["text_tertiary"]`, centered
- **Without image:** Centered list. Each item as a row with checkmark icon (circle with `DESIGN["brand_glow"]` bg, `DESIGN["brand"]` checkmark) + item text in `DESIGN["text_primary"]`. 8px gap between items
- **With image:** Inner table — 40% left column with image (border-radius `DESIGN["card_radius"]`, on `DESIGN["surface"]` bg), 60% right column with list. Wrapped in card with `DESIGN["card_bg"]`/`DESIGN["card_border"]`
- Items are left-aligned, icon + text inline

**Validation:**
- `items` must be 4-8 strings, each max 40 chars
- `image_url` if provided must be non-empty string

**Example:**
```python
{
    "block_type": "whats_included",
    "content": {
        "product_name": "G7 Headset",
        "items": [
            "1x G7 Bluetooth Headset",
            "1x 500mAh Charging Case",
            "5x Ear Tips (S/M/L)",
            "1x USB-C Charging Cable",
            "1x Quick Start Guide"
        ]
    }
}
```

---

## Module 5: `faq`

**Purpose:** Educational question-and-answer block. Neutral, informative tone — not persuasive like `objection_handling`. Sources from the 7-question FAQ on the ldas.ca trucker landing page plus product page content.

**Best journey stages:**
- Welcome E2-3 — educate new subscribers about the product category
- High-Intent Browse — answer evaluation questions
- Browse Recovery — "Quick answers about [product]"
- Win-Back E1 — "Things you might be wondering"

**NOT appropriate for:**
- Cart/Checkout Recovery — use `objection_handling` instead (persuasive, not educational)
- Post-Purchase E1 — they just bought, don't raise questions
- Any email already using `objection_handling` — both answer questions, pick one tone

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `items` | list[dict] | 2-4 Q&As. Each: `{ "question": str, "answer": str }`. Answer max 160 chars |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `"Common Questions"` | Branded uppercase label |

**Defaults:**
```python
"defaults": {
    "section_title": "Common Questions",
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Section title in `DESIGN["label"]` style, `DESIGN["text_tertiary"]`, centered
- Each Q&A pair:
  - Question row: "Q" circle badge (`DESIGN["brand_glow"]` bg, `DESIGN["brand"]` text, 24px circle) + question text in `DESIGN["text_primary"]`, bold, 15px
  - Answer row: "A" circle badge (`DESIGN["surface"]` bg, `DESIGN["text_secondary"]` text, 24px circle) + answer text in `DESIGN["text_secondary"]`, regular weight, 14px
  - 16px gap between Q and A
  - Subtle divider (`DESIGN["divider_color"]`, 1px) between Q&A pairs, 20px vertical spacing
- No card wrapper — flat on dark body like `why_choose_this`

**Validation:**
- `items` must be 2-4 dicts
- Each must have `question` (str) and `answer` (str, max 160 chars)

**Example:**
```python
{
    "block_type": "faq",
    "content": {
        "section_title": "Common Questions",
        "items": [
            {"question": "Which headset is best for truck driving?", "answer": "The TH11 is built for truckers \u2014 40hr talk time, boom mic for highway noise, and dual-device pairing."},
            {"question": "Can I pair with my phone and GPS at the same time?", "answer": "Yes. All LDAS headsets support multipoint Bluetooth \u2014 connect two devices simultaneously."},
            {"question": "How long does the battery actually last?", "answer": "TH11: 40hrs talk. G10: 36hrs. G7: 20hrs + 72hrs with charging case. Real-world tested."}
        ]
    }
}
```

---

## Module 6: `use_case_match`

**Purpose:** "Which product is right for you?" segmentation block. Shows 2-3 use cases with a recommended product for each. Mirrors the trucker landing page's OTR vs city vs discreet driver segmentation.

**Best journey stages:**
- Welcome E2 — help new subscribers find their product
- Browse Recovery — "Not sure which one? Here's a quick guide"
- Win-Back E1 — "Find your perfect match" re-engagement
- High-Intent Browse — decision helper for multi-product viewers

**NOT appropriate for:**
- Cart/Checkout Recovery — they already chose
- Post-Purchase — irrelevant after buying
- Any email already using `comparison` or `spec_table` — too many product selectors

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `cases` | list[dict] | 2-3 cases. Each: `{ "persona": str, "description": str, "product_name": str, "product_url": str }` |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `"Find Your Perfect Match"` | Branded uppercase label |
| `cta_text` | str | `"Shop Now"` | Per-case CTA button text |

**Defaults:**
```python
"defaults": {
    "section_title": "Find Your Perfect Match",
    "cta_text": "Shop Now",
}
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Section title in `DESIGN["label"]` style, `DESIGN["text_tertiary"]`, centered
- Cards laid out horizontally (equal width: 50% for 2 cases, 33% for 3 cases)
- Each card:
  - `DESIGN["card_bg"]` background, `DESIGN["card_border"]`, `DESIGN["card_radius"]`
  - Persona name: `DESIGN["text_primary"]`, bold, 16px, centered
  - Description: `DESIGN["text_secondary"]`, 13px, centered, 2 lines max
  - Product name: `DESIGN["brand"]` color, 14px, bold, centered
  - CTA button: `DESIGN["btn_card"]` style with `DESIGN["btn_card_bg"]`/`DESIGN["btn_card_text"]`
  - Padding: `DESIGN["card_inner_pad"]`
- 12px gap between cards

**Validation:**
- `cases` must be 2-3 dicts
- Each must have `persona` (str), `description` (str), `product_name` (str), `product_url` (str)
- `product_url` must start with `http` or `/`

**Example:**
```python
{
    "block_type": "use_case_match",
    "content": {
        "section_title": "Find Your Perfect Match",
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
    }
}
```

---

## Module 7: `brand_story`

**Purpose:** Configurable brand narrative block with variants for mission, sustainability, and Canadian heritage. Tells the "who we are" story that builds emotional connection. Sources from ldas.ca About page, sustainability page, and brand messaging throughout the site.

**Best journey stages:**
- Welcome E1-2 — introduce the brand story to new subscribers
- Win-Back E1 — re-establish emotional connection
- Post-Purchase E2 — deepen brand relationship after the transaction

**NOT appropriate for:**
- Cart/Checkout Recovery — action-focused, not storytelling
- High-Intent Browse — product-focused, not brand-focused
- Any email already using `feature_highlights` — both are brand-level messaging

**Required fields:**

| Field | Type | Notes |
|---|---|---|
| `headline` | str | Bold headline, max 50 chars |
| `body` | str | 1-3 sentences, max 200 chars |

**Optional fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `section_title` | str | `""` | Branded uppercase label above headline. Empty = no label |
| `variant` | str | `"mission"` | `"mission"`, `"sustainability"`, `"heritage"` — sets default badges |
| `badges` | list[dict] | variant defaults | Each: `{ "icon": str, "text": str }`. 2-4 badges in a row below body |
| `cta_text` | str | `""` | Optional CTA link text. Empty = no CTA |
| `cta_url` | str | `""` | CTA destination. Empty = no CTA. Use `BRAND_URL` from imports when linking to homepage |

**Defaults:**
```python
"defaults": {
    "section_title": "",
    "variant": "mission",
    "badges": [],  # renderer uses variant defaults when empty
    "cta_text": "",
    "cta_url": "",
}
```

**Default badges by variant:**

Mission:
```python
[
    {"icon": "&#127911;", "text": "Premium Audio"},
    {"icon": "&#127809;", "text": "Canadian Brand"},
    {"icon": "&#128172;", "text": "24/7 Support"},
    {"icon": "&#11088;", "text": "4.8/5 Rated"},
]
```

Sustainability:
```python
[
    {"icon": "&#9851;", "text": "95% Recyclable Packaging"},
    {"icon": "&#127793;", "text": "Carbon-Conscious Shipping"},
    {"icon": "&#127464;&#127462;", "text": "Ships from Ontario"},
    {"icon": "&#128230;", "text": "Minimal Waste Design"},
]
```

Heritage:
```python
[
    {"icon": "&#127809;", "text": "Proudly Canadian"},
    {"icon": "&#128205;", "text": "Brampton, Ontario"},
    {"icon": "&#128737;", "text": "ISED Approved"},
    {"icon": "&#129309;", "text": "Family-Owned"},
]
```

**Render spec:**
- Outer `<tr>` with `<td>` having `background: DESIGN["body_bg"]`
- Centered text block, max-width 520px
- Section title (if set): `DESIGN["label"]` style, `DESIGN["text_tertiary"]`, centered
- Headline: `DESIGN["h2"]` style, `DESIGN["text_primary"]`, centered
- Body: `DESIGN["body"]` style, `DESIGN["text_secondary"]`, centered, 12px below headline
- Badge row (20px below body): badges inline, separated by `&middot;` in `DESIGN["text_tertiary"]`. Each badge: icon (HTML entity) + text in `DESIGN["text_tertiary"]`, 12px font
- Optional CTA (16px below badges): `DESIGN["btn_primary"]` with `DESIGN["btn_primary_bg"]`/`DESIGN["btn_primary_text"]`

**Validation:**
- `headline` must be str, max 50 chars
- `body` must be str, max 200 chars
- `variant` must be one of `"mission"`, `"sustainability"`, `"heritage"`
- `badges` if provided must be 2-4 dicts, each with `icon` (str) and `text` (str)

**Example:**
```python
{
    "block_type": "brand_story",
    "content": {
        "variant": "heritage",
        "headline": "Proudly Canadian. Built for the Road.",
        "body": "LDAS Electronics is a Canadian-owned company based in Brampton, Ontario. We design professional-grade audio and dash cam gear for drivers who depend on clarity and reliability every day.",
        "cta_text": "Our Story",
        "cta_url": "https://ldas.ca/pages/about-us"
    }
}
```

---

## Stacking Rules (New Blocks + Existing)

**Never stack together in one email:**
- `faq` + `objection_handling` — both answer questions. Pick educational or persuasive
- `competitor_comparison` + `spec_table` — both are comparison grids. Information overload
- `competitor_comparison` + `comparison` — too many comparison formats
- `brand_story` + `feature_highlights` — both are brand-level messaging
- `use_case_match` + `comparison` — both help choose between products
- `stat_callout` + `stat_callout` — one set of stats per email

**Good stacks (complement each other):**
- `product_hero` -> `stat_callout` -> `why_choose_this` -> `cta` — show, quantify, persuade, convert
- `product_hero` -> `spec_table` -> `driver_testimonial` -> `cta` — show, compare specs, prove, convert
- `use_case_match` -> `competitor_comparison` -> `cta` — segment, differentiate, convert
- `whats_included` -> `trust_reassurance` -> `cta` — anticipation, safety, convert
- `brand_story` -> `best_seller_proof` -> `cta` — who we are, what's popular, convert
- `product_hero` -> `whats_included` -> `stat_callout` -> `cta` — show, contents, specs, convert
- `faq` -> `trust_reassurance` -> `cta` — educate, reassure, convert

---

## Family Constraint Updates

New `allowed_blocks` additions per family in `condition_engine.py`:

| Family | Add These New Blocks |
|---|---|
| `welcome` | `stat_callout`, `faq`, `use_case_match`, `brand_story` |
| `browse_recovery` | `competitor_comparison`, `spec_table`, `stat_callout`, `faq`, `use_case_match` |
| `high_intent_browse`* | `competitor_comparison`, `spec_table`, `stat_callout`, `whats_included`, `faq`, `use_case_match` |
| `cart_recovery` | `stat_callout`, `whats_included` |
| `checkout_recovery` | `stat_callout` |
| `post_purchase` | `stat_callout`, `whats_included`, `brand_story` |
| `winback` | `competitor_comparison`, `stat_callout`, `faq`, `use_case_match`, `brand_story` |
| `promo` | All 7 new blocks |

*Note: `high_intent_browse` family may not exist yet in `condition_engine.py` — if not, create it with all currently-appropriate blocks plus the new ones.

---

## Module Compatibility Matrix (All 25 Blocks)

| Module | Welcome | Browse | Hi-Intent | Cart | Checkout | Post-Purch | Win-Back |
|---|---|---|---|---|---|---|---|
| `competitor_comparison` | - | ✓ | ✓ | - | - | - | ✓ E2 |
| `spec_table` | - | ✓ | ✓ | - | - | - | - |
| `stat_callout` | ✓ E1 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `whats_included` | - | - | ✓ | ✓ E1 | - | ✓ E1 | - |
| `faq` | ✓ E2-3 | ✓ | ✓ | - | - | - | ✓ E1 |
| `use_case_match` | ✓ E2 | ✓ | ✓ | - | - | - | ✓ E1 |
| `brand_story` | ✓ E1-2 | - | - | - | - | ✓ E2 | ✓ E1 |

---

## Showcase Templates

Each new block gets a showcase template (added to `create_showcase_templates.py`). Template IDs will be auto-assigned (continuing from 26). Each showcase demonstrates the block with realistic LDAS product data.

---

## Verification

1. **Schema test:** Each BLOCK_TYPES entry has correct required/optional/defaults
2. **Render test:** Each renderer produces valid `<tr>` HTML using only DESIGN tokens
3. **Fallback test:** Each module renders with required fields only (all optional omitted)
4. **Empty state test:** Graceful empty string return when data lists are empty
5. **Family gate test:** Each module only allowed in designated families
6. **Stacking test:** "Never stack" rules documented for template authors
7. **Inbox test:** Deploy to VPS, send test emails, verify in Gmail web + mobile
8. **Mobile test:** All modules stack single-column, readable fonts, tappable CTAs
