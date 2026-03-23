# Email Annotations & Smart Preview Text — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gmail/Yahoo JSON-LD discount badges and dynamic preview text to all emails that carry a real Shopify discount code.

**Architecture:** Two changes in two files. `discount_engine.py` gets a one-line addition to `get_discount_display()` to preserve the raw `expires_at` datetime. `email_shell.py` gets a new helper `_build_schema_annotation()` and an `offer_meta` parameter on `wrap_email()`. `block_registry.py` builds the `offer_meta` dict and enriches the preview text before calling `wrap_email()`.

**Tech Stack:** Python, JSON-LD (schema.org DiscountOffer), HTML email

**Spec:** `docs/superpowers/specs/2026-03-23-email-annotations-smart-preview-design.md`

---

## File Structure

| File | Responsibility | Change Type |
|------|---------------|-------------|
| `discount_engine.py` | Add `expires_at` to display dict return | Modify (1 line) |
| `email_shell.py` | Accept `offer_meta`, build JSON-LD, inject into `<head>` | Modify (~25 lines) |
| `block_registry.py` | Build `offer_meta` from discount, enrich preview text, pass to `wrap_email()` | Modify (~20 lines) |

---

## Chunk 1: Core Implementation

### Task 1: Preserve `expires_at` in discount display dict

The `get_discount_display()` function currently returns `{code, display_text, value_display, expires_text}` but drops the raw `expires_at` datetime. The JSON-LD annotation needs the ISO 8601 timestamp. Add it to the return dict.

**Files:**
- Modify: `discount_engine.py:278-322` — `get_discount_display()` function

- [ ] **Step 1: Add `expires_at` to the return dict**

In `discount_engine.py`, the `get_discount_display()` function at line 317-322 returns a dict. Add the raw `expires_at` datetime to it:

```python
# Line 317-322 — change the return dict from:
    return {
        "code": code,
        "display_text": display_text,
        "value_display": value_display,
        "expires_text": expires_text,
    }

# TO:
    return {
        "code": code,
        "display_text": display_text,
        "value_display": value_display,
        "expires_text": expires_text,
        "expires_at": expires,
    }
```

The `expires` variable is already assigned at line 294: `expires = discount_info["expires_at"]`. This is a `datetime` object (UTC) or `None`.

- [ ] **Step 2: Verify no breakage**

The returned dict is consumed by:
- `block_registry.py` render functions (accesses `.code`, `.value_display`, `.display_text`, `.expires_text`)
- `app.py` flow sending logic (passes dict to `render_template_blocks`)

Adding a new key to a dict is non-breaking — existing consumers just ignore keys they don't access.

- [ ] **Step 3: Commit**

```bash
git add discount_engine.py
git commit -m "feat: preserve expires_at datetime in discount display dict"
```

---

### Task 2: Add JSON-LD annotation support to `email_shell.py`

Add a helper function that builds the JSON-LD `DiscountOffer` annotation, and update `wrap_email()` to accept and inject it.

**Files:**
- Modify: `email_shell.py:1-131` — add helper + update `wrap_email()`

- [ ] **Step 1: Add `_build_schema_annotation()` helper**

Add this function after the constants (after line 31), before `wrap_email()`:

```python
import json

def _build_schema_annotation(offer_meta):
    """
    Build a JSON-LD DiscountOffer annotation for Gmail/Yahoo inbox badges.

    Args:
        offer_meta: dict with keys:
            - description: str ("5% off your order" or "Free shipping")
            - discountCode: str (the Shopify discount code)
            - availabilityStarts: str (ISO 8601 UTC timestamp)
            - availabilityEnds: str (ISO 8601 UTC timestamp)

    Returns:
        str: <script type="application/ld+json">...</script> block, or ""
    """
    if not offer_meta:
        return ""
    # Must have at least one of description, discountCode, or availabilityEnds
    if not any(offer_meta.get(k) for k in ("description", "discountCode", "availabilityEnds")):
        return ""

    annotation = [{
        "@context": "http://schema.org/",
        "@type": "DiscountOffer",
    }]
    for key in ("description", "discountCode", "availabilityStarts", "availabilityEnds"):
        if offer_meta.get(key):
            annotation[0][key] = offer_meta[key]

    return '<script type="application/ld+json">' + json.dumps(annotation) + '</script>\n'
```

- [ ] **Step 2: Update `wrap_email()` signature and inject annotation**

Change the function signature at line 34 from:

```python
def wrap_email(body_html, preview_text="", unsubscribe_url="{{unsubscribe_url}}"):
```

To:

```python
def wrap_email(body_html, preview_text="", unsubscribe_url="{{unsubscribe_url}}", offer_meta=None):
```

Then inject the annotation into the `<head>`. Find the `</head>` close tag in the HTML template string (currently right before `<body`). Insert the annotation before `</head>`:

Change the line (around line 71-72):

```python
  }
</style>
</head>
```

To:

```python
  }
</style>
''' + _build_schema_annotation(offer_meta) + '''</head>
```

- [ ] **Step 3: Commit**

```bash
git add email_shell.py
git commit -m "feat: add JSON-LD DiscountOffer annotation support to email shell"
```

---

### Task 3: Build `offer_meta` and enrich preview text in `block_registry.py`

This is where both tiers come together. After blocks are rendered and before `wrap_email()` is called, build the `offer_meta` dict from the discount object and enrich the preview text.

**Files:**
- Modify: `block_registry.py:2059-2060` — the two lines right before `wrap_email()` call

- [ ] **Step 1: Add the `offer_meta` builder and preview enrichment**

Replace lines 2059-2060:

```python
    preview_text = getattr(template, "preview_text", "") or ""
    full_html = wrap_email(body_html, preview_text=preview_text, unsubscribe_url="{{unsubscribe_url}}")
```

With:

```python
    preview_text = getattr(template, "preview_text", "") or ""

    # ── Tier 2: Enrich preview text with discount context ──
    if discount and discount.get("code"):
        _val = discount.get("value_display", "")
        _code = discount["code"]
        if preview_text:
            preview_text = "%s - Code: %s - %s" % (_val, _code, preview_text)
        else:
            _exp = discount.get("expires_text", "")
            preview_text = "%s - Use code %s before %s" % (_val, _code, _exp) if _exp else "%s - Code: %s" % (_val, _code)

    # ── Tier 1: Build JSON-LD offer annotation for Gmail/Yahoo badges ──
    offer_meta = None
    if discount and discount.get("code") and discount.get("expires_at"):
        from datetime import datetime, timezone
        _expires_at = discount["expires_at"]
        # Only annotate if expiry is in the future
        if hasattr(_expires_at, 'isoformat') and _expires_at > datetime.now(timezone.utc):
            _now = datetime.now(timezone.utc)
            offer_meta = {
                "description": discount.get("display_text", ""),
                "discountCode": discount["code"],
                "availabilityStarts": _now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "availabilityEnds": _expires_at.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            }

    full_html = wrap_email(body_html, preview_text=preview_text, unsubscribe_url="{{unsubscribe_url}}", offer_meta=offer_meta)
```

- [ ] **Step 2: Commit**

```bash
git add block_registry.py
git commit -m "feat: add discount annotations and smart preview text to email rendering"
```

---

### Task 4: Manual verification

- [ ] **Step 1: Check email preview in the journey preview page**

Navigate to `https://mailenginehub.com/journey-preview` and inspect a cart_recovery email's HTML source. Look for the `<script type="application/ld+json">` block in the `<head>`.

- [ ] **Step 2: Check an education email has NO annotation**

In the same preview page, find an education/nurture email. Verify NO JSON-LD block is present in the source.

- [ ] **Step 3: Check preview text enrichment**

In the HTML source of a discount email, verify the preheader `<div>` contains the enriched text with the discount code and value.

- [ ] **Step 4: Commit all changes and deploy**

```bash
bash deploy.sh
```
