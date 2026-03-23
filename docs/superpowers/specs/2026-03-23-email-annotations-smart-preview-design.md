# Email Annotations & Smart Preview Text

**Date**: 2026-03-23
**Status**: Approved
**Scope**: Two-tier enhancement to email inbox presence

---

## Problem

Emails from MailEngineHub appear as plain entries in Gmail/Yahoo inboxes. Competitors like BestBuy show colored discount badges ("62% off", "Expires tomorrow") using schema markup, which dramatically increases open rates. Our discount system already generates real Shopify-synced codes with expiry dates — we just don't surface them in the inbox.

## Solution

### Tier 1: Gmail/Yahoo JSON-LD Annotations

Inject `<script type="application/ld+json">` into the email `<head>` at render time, **only when a real discount is attached** to the email.

#### Data Flow

```
render_template_blocks(template, contact, discount=...)
  └─> discount object already contains: code, value, discount_type, expires_at
       └─> build_offer_annotation(discount) -> JSON-LD dict
            └─> wrap_email(html, offer_meta=json_ld) -> injects into <head>
```

#### Annotation Schema

**Percentage discount:**
```json
{
  "@context": "http://schema.org",
  "@type": "DiscountOffer",
  "description": "5% off your order",
  "discountCode": "CART5A2F",
  "availabilityStarts": "2026-03-23T10:00:00-04:00",
  "availabilityEnds": "2026-03-25T10:00:00-04:00"
}
```

**Free shipping discount:**
```json
{
  "@context": "http://schema.org",
  "@type": "DiscountOffer",
  "description": "Free shipping",
  "discountCode": "SHIP8B3C",
  "availabilityStarts": "2026-03-23T10:00:00-04:00",
  "availabilityEnds": "2026-03-26T10:00:00-04:00"
}
```

#### Behavior by Email Type

| Family | Discount? | Annotation Injected? |
|--------|-----------|---------------------|
| cart_recovery | Always (5%, 48h) | YES |
| browse_recovery | Always (free ship, 72h) | YES |
| welcome | Always (5%, 14d) | YES |
| winback | Sometimes (10%, 7d) | YES if discount present |
| high_intent_browse | Always (free ship, 72h) | YES |
| loyalty_reward | Always (10%, 7d) | YES |
| promo | Campaign-dependent | YES if discount present |
| education/nurture | Never | NO |
| post_purchase | Never | NO |

#### Truthfulness Guarantee

- Annotation code = email body code = Shopify code (same `GeneratedDiscount` object)
- Annotation expiry = `GeneratedDiscount.expires_at` (real Shopify Price Rule end date)
- No discount attached = no annotation injected = no badge shown
- Never fake or hardcode a deal claim

### Tier 2: Smart Dynamic Preview Text

Auto-generate a richer preview text at render time that includes discount context. Static `preview_text` field on `EmailTemplate` remains as fallback.

#### Logic

```
IF discount exists AND template has static preview_text:
   preview = "{value_display} - Code: {code} - {static_preview_text}"

IF discount exists AND no static preview_text:
   preview = "{value_display} - Use code {code} before {expires_text}"

IF no discount:
   preview = static preview_text (unchanged, as today)
```

#### Examples

| Family | Discount | Generated Preview |
|--------|----------|------------------|
| Cart Recovery | 5% off, CART5A2F, 48h | "5% off - Code: CART5A2F - Items in your cart are waiting" |
| Browse Recovery | Free ship, SHIP8B3C, 72h | "Free shipping - Code: SHIP8B3C - expires in 3 days" |
| Welcome | 5% off, WELCOME9D1, 14d | "5% off your first order - Code: WELCOME9D1" |
| Education | None | "Tips for maintaining your Bluetooth headset" (static) |
| Post Purchase | None | "Thank you for your order!" (static) |

---

## Files Modified

| File | Change |
|------|--------|
| `email_shell.py` | Add `offer_meta` param to `wrap_email()`, add `_build_schema_annotation()` helper, inject JSON-LD into `<head>` |
| `block_registry.py` | Pass discount metadata through to `wrap_email()` as `offer_meta`, generate smart preview text before wrapping |

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

**Strategies:**
| Purpose | Type | Value | Expires |
|---------|------|-------|---------|
| cart_abandonment | percentage | 5% | 48h |
| browse_abandonment | free_shipping | 100% | 72h |
| winback | percentage | 10% | 168h |
| welcome | percentage | 5% | 336h |
| loyalty_reward | percentage | 10% | 168h |
| high_intent | free_shipping | 100% | 72h |
| smart_escalation | percentage | 10% | 168h |

## Testing

- Send test emails for each template family with discount attached — verify JSON-LD present in source
- Send test emails without discount — verify no JSON-LD
- Verify Gmail Promotions tab shows badge (may take a few sends to appear)
- Verify preview text includes discount info when present
- Verify preview text falls back to static when no discount

## Risks

- Gmail may cache annotations at delivery time — if discount expires before open, badge still shows (expected, same as BestBuy)
- Gmail Promotions tab rendering is not instant — may take a few sends before badges appear consistently
- Yahoo support for JSON-LD is less documented than Gmail but uses the same schema.org format
