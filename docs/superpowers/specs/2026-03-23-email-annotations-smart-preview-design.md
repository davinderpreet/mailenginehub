# Email Annotations & Smart Preview Text

**Date**: 2026-03-23
**Status**: Approved
**Scope**: Two-tier enhancement to email inbox presence

---

## Problem

Emails from MailEngineHub appear as plain entries in Gmail/Yahoo inboxes. Competitors like BestBuy show colored discount badges ("62% off", "Expires tomorrow") using schema markup, which dramatically increases open rates. Our discount system already generates real Shopify-synced codes with expiry dates — we just don't surface them in the inbox.

## Solution

### Tier 1: Gmail/Yahoo JSON-LD Annotations

Inject `<script type="application/ld+json">` into the email `<head>` at render time, **only when the `discount` parameter passed to `wrap_email()` is non-None and has a valid `expires_at`**.

#### Data Flow

```
block_registry.py: render_template_blocks(template, contact, discount=...)
  │
  ├─> [Tier 2] Enrich preview_text with discount context (if discount present)
  │
  └─> email_shell.py: wrap_email(body_html, preview_text, unsubscribe_url, offer_meta=...)
       └─> [Tier 1] _build_schema_annotation(offer_meta) -> JSON-LD in <head>
```

**Ownership split:**
- `block_registry.py` — computes enriched preview text, builds `offer_meta` dict from discount object
- `email_shell.py` — receives `offer_meta` dict, serializes to JSON-LD, injects into `<head>`

#### Annotation Schema

The `@context` MUST use `http://schema.org/` (http, not https). The `@type` is `DiscountOffer` — the Gmail-recognized type for deal badges.

**Percentage discount:**
```json
[{
  "@context": "http://schema.org/",
  "@type": "DiscountOffer",
  "description": "5% off your order",
  "discountCode": "CART5A2F6B",
  "availabilityStarts": "2026-03-23T10:00:00+00:00",
  "availabilityEnds": "2026-03-25T10:00:00+00:00"
}]
```

**Free shipping discount:**
```json
[{
  "@context": "http://schema.org/",
  "@type": "DiscountOffer",
  "description": "Free shipping",
  "discountCode": "SHIP8B3C2D",
  "availabilityStarts": "2026-03-23T10:00:00+00:00",
  "availabilityEnds": "2026-03-26T10:00:00+00:00"
}]
```

**Timestamps**: `GeneratedDiscount.expires_at` is stored in UTC. Format as ISO 8601 with `+00:00` offset. No timezone conversion needed.

#### Injection Trigger

The annotation is injected if and only if:
1. The `discount` parameter is non-None at render time
2. `discount` has a valid `expires_at` (not None, not in the past)
3. `discount` has a non-empty `code`

If `expires_at` is None or missing, skip annotation injection silently — the email still sends normally without a badge.

#### Behavior by Email Type

This table maps discount **purposes** (from `DISCOUNT_STRATEGIES`) to the email types that use them. The annotation is agnostic to template family — it fires whenever a discount object is present.

| Email Purpose | Discount Strategy | Annotation? |
|---------------|------------------|-------------|
| cart_recovery / checkout_recovery | cart_abandonment (5%, 48h) | YES |
| browse_recovery / high_intent_browse | browse_abandonment / high_intent (free ship, 72h) | YES |
| welcome | welcome (5%, 336h) | YES |
| winback | winback (10%, 168h) | YES if discount attached |
| upsell | upsell (5%, 120h) | YES if discount attached |
| re_engagement | re_engagement (5%, 168h) | YES if discount attached |
| loyalty_reward (via AI engine) | loyalty_reward (10%, 168h) | YES |
| promo campaign | smart_escalation or custom | YES if discount attached |
| education / content nurture | None — discounts explicitly forbidden | NO |
| post_purchase | None | NO |

**Key principle**: The code does NOT check template family. It checks: is `discount` non-None? If yes → annotate. If no → skip.

#### Truthfulness Guarantee

- Annotation code = email body code = Shopify code (same `GeneratedDiscount` object)
- Annotation expiry = `GeneratedDiscount.expires_at` (real Shopify Price Rule end date)
- No discount attached = no annotation injected = no badge shown
- Never fake or hardcode a deal claim

### Tier 2: Smart Dynamic Preview Text

Auto-generate a richer preview text at render time that includes discount context. Static `preview_text` field on `EmailTemplate` remains as fallback. This enrichment happens in `block_registry.py` BEFORE calling `wrap_email()`.

#### Logic

```
IF discount exists AND discount has code AND template has static preview_text:
   preview = "{value_display} - Code: {code} - {static_preview_text}"

IF discount exists AND discount has code AND no static preview_text:
   preview = "{value_display} - Use code {code} before {expires_text}"

IF no discount:
   preview = static preview_text (unchanged, as today)
```

#### Examples

| Email Type | Discount | Static Preview | Generated Preview |
|------------|----------|---------------|------------------|
| Cart Recovery | 5% off, CART5A2F6B, 48h | "Items in your cart are waiting" | "5% OFF - Code: CART5A2F6B - Items in your cart are waiting" |
| Browse Recovery | Free ship, SHIP8B3C2D, 72h | None | "Free Shipping - Use code SHIP8B3C2D before it expires in 3 days" |
| Welcome | 5% off, WELCOME9D1E4F, 14d | "Welcome to LDAS Electronics" | "5% OFF - Code: WELCOME9D1E4F - Welcome to LDAS Electronics" |
| Education | None | "Tips for maintaining your headset" | "Tips for maintaining your headset" (static, unchanged) |
| Post Purchase | None | "Thank you for your order!" | "Thank you for your order!" (static, unchanged) |

---

## Files Modified

| File | Change |
|------|--------|
| `email_shell.py` | Add `offer_meta` param to `wrap_email()`, add `_build_schema_annotation(offer_meta)` helper, inject JSON-LD `<script>` into `<head>` |
| `block_registry.py` | Build `offer_meta` dict from discount object, enrich `preview_text` with discount context, pass both to `wrap_email()` |

## Files NOT Modified

- `database.py` — no new models needed
- `discount_engine.py` — already provides all required fields
- `app.py` — flow discount attachment logic unchanged
- `ai_engine.py` — AI email discount logic unchanged

## Discount System Reference

All discounts are created via `get_or_create_discount(email, purpose)` which:
1. Checks for existing active (unexpired, unused) discount for this purpose
2. If none, creates a new one via Shopify Price Rules API
3. Returns: `{code, value, discount_type, expires_at}`

**All Strategies:**
| Purpose | Type | Value | Expires | Prefix |
|---------|------|-------|---------|--------|
| cart_abandonment | percentage | 5% | 48h | CART |
| browse_abandonment | free_shipping | 100% | 72h | SHIP |
| winback | percentage | 10% | 168h | WB |
| welcome | percentage | 5% | 336h | WELCOME |
| loyalty_reward | percentage | 10% | 168h | VIP |
| upsell | percentage | 5% | 120h | UP |
| re_engagement | percentage | 5% | 168h | RE |
| high_intent | free_shipping | 100% | 72h | HI |
| smart_escalation | percentage | 10% | 168h | SAVE |

## Testing

- Send test emails with discount attached — verify JSON-LD present in email source HTML
- Send test emails without discount — verify no JSON-LD block
- Send email with `expires_at=None` — verify no annotation, no error
- Verify Gmail Promotions tab shows badge (may take a few sends to appear)
- Verify preview text includes discount info when present
- Verify preview text falls back to static when no discount
- Use Google's Structured Data Testing Tool to validate JSON-LD format

## Risks

- Gmail may cache annotations at delivery time — if discount expires before open, badge still shows (expected, same as BestBuy)
- Gmail Promotions tab rendering is not instant — may take a few sends before badges appear consistently
- Yahoo support for JSON-LD is less documented than Gmail but uses the same schema.org format
- Gmail silently ignores malformed annotations — use Google's annotation tester to validate before going live
- JSON-LD adds ~200-300 bytes per email — negligible impact on email size
- `GeneratedDiscount.expires_at` allows null — spec handles this by skipping annotation when null
