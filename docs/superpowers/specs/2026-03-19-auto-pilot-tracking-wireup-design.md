# Auto-Pilot Email Tracking — Full Wire-Up Design

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Wire auto-pilot (`email_type="auto"`) emails into every tracking, analytics, and intelligence system in MailEngineHub

---

## Problem

Auto-pilot emails send successfully via SES but are invisible to the rest of the platform. The root cause: `_create_compat_record()` in `delivery_engine.py` has no branch for `email_type="auto"`, so no tracking record is created. Since every downstream system reads from `CampaignEmail` or `FlowEmail`, auto-pilot data is missing from 10 downstream integration points (plus 3 critical infrastructure fixes: compat record creation, open tracking route, click tracking).

## Approach

**New `AutoEmail` table** — clean separation from campaigns and flows. Requires wiring into all 13 downstream query locations but gives auto-pilot first-class status with its own data model.

Rejected alternatives:
- Synthetic `CampaignEmail` records: simpler but blurs the campaign/auto-pilot boundary
- Querying `DeliveryQueue` directly: wrong abstraction layer, no open/click fields

---

## 1. New `AutoEmail` Model

**File:** `database.py`

```python
class AutoEmail(BaseModel):
    contact        = ForeignKeyField(Contact, backref='auto_emails')
    template       = ForeignKeyField(EmailTemplate, null=True)
    subject        = CharField(default="")
    status         = CharField(default="queued")    # queued | sent | failed | bounced
    error_msg      = CharField(default="")
    opened         = BooleanField(default=False)
    clicked        = BooleanField(default=False)
    sent_at        = DateTimeField(default=datetime.now)
    opened_at      = DateTimeField(null=True)
    clicked_at     = DateTimeField(null=True)
    ses_message_id = CharField(default="")           # for bounce/complaint correlation
    auto_run_date  = DateField(null=True)            # groups by daily run, native date comparisons
```

**Design notes:**
- Removed `open_count`/`click_count` fields — neither `CampaignEmail` nor `FlowEmail` track these, and adding them only to `AutoEmail` creates an inconsistency. The boolean `opened`/`clicked` fields match the existing pattern.
- Changed `auto_run_date` from `CharField` to `DateField` for native SQLite date comparison queries.

**Migration:** `create_tables([AutoEmail])` in the existing migration block. Purely additive — no existing tables modified.

---

## 2. Root Fix: `_create_compat_record()` + `ses_message_id` Plumbing

### 2a. Propagate `ses_message_id` through the pipeline

**Problem:** `send_campaign_email()` in `email_sender.py` returns `msg_id`, and `delivery_engine.py` stores it on `ActionLedger` via `update_ledger_status()` (line 272), but it never reaches `_create_compat_record()`. The function signature currently takes only `(item, status)`.

**Fix — 3 changes:**

1. **`delivery_engine.py` line 257-272 (live send block):** Capture `msg_id` from `send_campaign_email()` return value (already happens at line 265). Pass it to `_create_compat_record()`:
   ```python
   _create_compat_record(item, status="sent", ses_message_id=msg_id)
   ```

2. **`_create_compat_record()` signature:** Add `ses_message_id=""` parameter:
   ```python
   def _create_compat_record(item, status="sent", ses_message_id=""):
   ```

3. **`email_sender.py` line 119-127 (SES tags):** Add `template_id` to the SES message tags when `campaign_id == 0` (auto-pilot). The `send_campaign_email()` function needs a `template_id` parameter:
   ```python
   def send_campaign_email(from_email, from_name, to_email, subject, html,
                           campaign_id=0, template_id=0, ...):
       tags = [{"Name": "campaign_id", "Value": str(campaign_id)}]
       if template_id:
           tags.append({"Name": "template_id", "Value": str(template_id)})
   ```
   And in `delivery_engine.py` line 257-266, pass `template_id=item.template_id` when calling `send_campaign_email()`.

### 2b. Add `AutoEmail` branch in `_create_compat_record()`

**File:** `delivery_engine.py`, function `_create_compat_record()`

Since the `AutoEmail` row is created at enqueue time (see Section 3a), this branch **updates** the existing row instead of creating:

```python
elif email_type == "auto":
    # AutoEmail row was created at enqueue time with status="queued"
    # Look it up via a reference stored on the DeliveryQueue item
    auto_email_id = getattr(item, 'auto_email_id', None)
    if auto_email_id:
        AutoEmail.update(
            status=status,
            sent_at=datetime.now(),
            ses_message_id=ses_message_id
        ).where(AutoEmail.id == auto_email_id).execute()
```

**Requires:** Add `auto_email_id` field to `DeliveryQueue` model (IntegerField, default=0) to link queue items to their pre-created `AutoEmail` row.

---

## 3. Tracking Routes

### 3a. Open Tracking

**File:** `app.py` — new route

```
/track/auto-open/<token>
```

Token: `itsdangerous.URLSafeSerializer` encoding `auto_email_id` (same pattern as campaign tokens).

Handler:
1. Decode token, find `AutoEmail` row
2. Set `opened=True`, `opened_at=now()`
3. Update `Contact.last_open_at = now()`
4. Call `cascade_contact(contact)` to refresh engagement scores
5. Return 1x1 transparent GIF

**Chicken-and-egg timing fix:** The tracking pixel URL needs `auto_email_id` in the token, but `AutoEmail` is currently created after sending (in `_create_compat_record()`). To solve this:

1. **Create `AutoEmail` at enqueue time** with `status="queued"` in the auto-pilot scheduler, before building the HTML
2. Use the new `AutoEmail.id` to generate the signed tracking pixel URL
3. In `_create_compat_record()`, instead of creating a new row, **update the existing row** to `status="sent"` and set `ses_message_id`
4. If send fails, update to `status="failed"` with `error_msg`

This mirrors how `DeliveryQueue` works — record created before send, status updated after.

### 3b. Click Tracking

**File:** `app.py` — new route

```
/track/auto-click/<token>?url=<encoded_destination>
```

Token encodes `auto_email_id`.

Handler:
1. Decode token, find `AutoEmail` row
2. Set `clicked=True`, `clicked_at=now()`
3. Update `Contact.last_click_at = now()`
4. Call `cascade_contact(contact)`
5. 302 redirect to decoded destination URL

**Click wrapping — built from scratch:** There is no existing campaign click-tracking route (only `/track/flow-click/<token>` for flows using base64 `enrollment_id:step_id`). Auto-pilot click tracking is new infrastructure:

1. Token uses `itsdangerous.URLSafeSerializer` encoding `auto_email_id` (consistent with the open tracking token, NOT the base64 pattern used by flows)
2. Destination URL encoded via `urllib.parse.quote()` in the `?url=` parameter
3. Link rewriting: before enqueuing, the auto-pilot scheduler runs a regex or BeautifulSoup pass over the HTML to rewrite every `<a href="...">` (excluding unsubscribe links) to go through `/track/auto-click/<token>?url=<encoded_original>`
4. The `auto_email_id` is available because we create the `AutoEmail` row at enqueue time (see Section 3a timing fix)

### 3c. Unsubscribe Fix

**File:** `app.py`, auto-pilot scheduler section (~line 6155)

Replace hardcoded plain-email URL:
```python
# BEFORE (insecure):
unsub_url = f"https://mailenginehub.com/unsubscribe/{contact.email}"

# AFTER (signed token):
unsub_url = _make_unsubscribe_url(contact)
```

One-line change. Uses the existing secure token-based `/unsubscribe/<token>` route.

### 3d. SES Tags Fix

**File:** Auto-pilot email enqueue section

When calling `enqueue_email()` for auto-pilot, pass `template_id` as a SES tag:
```python
enqueue_email(
    ...,
    campaign_id=0,
    template_id=template.id,  # NEW: enables bounce attribution
    email_type="auto"
)
```

This lets the SES webhook handler attribute bounces/complaints to the specific template.

---

## 4. Downstream Integration (10 Points)

### 4.1 Dashboard Stats (`/` route, app.py ~line 445)

Add to `total_sent` and `total_opened`:
```python
auto_sent = AutoEmail.select().where(AutoEmail.status == "sent").count()
auto_opened = AutoEmail.select().where(AutoEmail.opened == True).count()
total_sent = campaign_sent + flow_sent + auto_sent
total_opened = campaign_opened + flow_opened + auto_opened
```

### 4.2 Sent Emails Page (`/sent-emails`, app.py ~line 1397)

Add third query block:
- `AutoEmail` joined to `Contact` and `EmailTemplate`
- Source label: `"Auto-Pilot"`
- Merge into combined list, sort by `sent_at`, paginate

**Filter logic fix:** The current `email_type` request arg filter uses `!= "flow"` / `!= "campaign"` logic, which would cause auto-pilot emails to appear in both campaign and flow views. Refactor to explicit three-way switch:
```python
if email_type == "campaign":
    # query CampaignEmail only
elif email_type == "flow":
    # query FlowEmail only
elif email_type == "auto":
    # query AutoEmail only
else:
    # query all three, merge
```
Also add `"auto"` as an option in the type filter dropdown on the frontend.

### 4.3 Contact Profile (`/profile/<id>`, app.py ~line 4494)

Add third query:
- Last 20 `AutoEmail` rows for that contact
- Join to `EmailTemplate` for source name
- Merge with campaign + flow email history

### 4.4 Health Score (`_compute_health_score`, app.py ~line 335)

Include `AutoEmail` sent/opened/bounced counts in the 14-day performance score (the 40-point component).

### 4.5 WarmupLog (daily stats, app.py ~line 419)

Add `AutoEmail` sent/opened/bounced counts for today to the daily `WarmupLog` update.

### 4.6 Outcome Tracker (`outcome_tracker.py`)

New function `_process_auto_emails()`:
- Query `AutoEmail` where `sent_at` is within last 48 hours
- For each, create `OutcomeLog` row with `email_type="auto"`
- Track: opens, clicks, purchases (within 72-hour attribution window, matching existing `_attribute_purchase()` default of `window_hours=72`), unsubscribes
- Same structure as existing `_process_campaign_emails()`
- Call from main `run()` alongside the other two processors

### 4.7 Learning Engine (`learning_engine.py`)

**No code changes.** Learning engine reads from `OutcomeLog` which is email_type-agnostic. Once outcome_tracker produces `email_type="auto"` rows, all derived metrics (template scoring, action effectiveness, frequency optimization, sunset scores) automatically include auto-pilot data.

### 4.8 SES Webhook Handler (bounces/complaints, app.py ~line 124)

Add fallback lookup:
- When `campaign_id == 0` (or missing), query `AutoEmail` by `ses_message_id`
- If found: set `AutoEmail.status = "bounced"` and `AutoEmail.error_msg` with bounce details
- Existing suppression logic (by email address) continues unchanged — contacts still get suppressed regardless

### 4.9 Warmup Domain Stats

Include `AutoEmail` counts in the warmup dashboard's domain breakdown section (if present). Search for queries that group `CampaignEmail`/`FlowEmail` by email domain — add `AutoEmail` to the same aggregation.

### 4.10 Live Activity Page

**No change needed.** This page shows website/Shopify behavior events, not email sending. No email type (campaign, flow, or auto) appears here.

---

## 5. Backfill Historical Auto-Pilot Sends

Query `DeliveryQueue` for past `email_type="auto"` sends:
```python
past_auto = DeliveryQueue.select().where(
    DeliveryQueue.email_type == "auto",
    DeliveryQueue.status == "sent"
)
for item in past_auto:
    AutoEmail.create(
        contact=item.contact,
        template=item.template_id,
        subject=item.subject,
        status="sent",
        sent_at=item.sent_at or item.created_at,
        ses_message_id="",        # not recoverable
        auto_run_date=(item.sent_at or item.created_at).strftime("%Y-%m-%d"),
        opened=False,             # tracking pixel was 404, no data
        clicked=False
    )
```

**Limitations:**
- `ses_message_id` not recoverable — bounce attribution won't work for historical sends
- `opened`/`clicked` will be False — tracking pixel was returning 404, so no engagement data exists
- These records will appear in Sent Emails, Contact Profile, and Dashboard counts

Run as a one-time migration script after table creation.

---

## 6. Token Security

All tracking URLs use `itsdangerous.URLSafeSerializer` with the app's secret key:
- No plain IDs in URLs (prevents enumeration)
- Same pattern used by existing campaign open/click tracking
- Token contains only `auto_email_id` — minimal data exposure

---

## 7. Testing Plan

1. Deploy with `AutoEmail` table creation
2. Run backfill migration
3. Trigger one auto-pilot run in shadow mode
4. Verify:
   - `AutoEmail` row created with correct fields
   - Tracking pixel URL returns 200 + 1px GIF (not 404)
   - Click tracking URL redirects correctly
   - Email appears on Sent Emails page with "Auto-Pilot" label
   - Email appears on Contact Profile timeline
   - Dashboard total_sent count increases
   - Health score includes auto-pilot sends
   - WarmupLog daily counters include auto-pilot
   - Outcome tracker creates `OutcomeLog` rows on next nightly run
   - Unsubscribe link uses signed token URL
5. Send a real auto-pilot email (live mode), open it, click a link
6. Verify open/click recorded in `AutoEmail`, `Contact.last_open_at` updated, `cascade_contact()` fired

---

## 8. Rollback

`AutoEmail` is an independent additive table. If anything breaks:
- Drop the table to revert to current behavior (auto-pilot sends invisible but still sending)
- No existing tables are modified by this design
- Remove the new routes and revert the `_create_compat_record()` branch

---

## Files Modified

| File | Changes |
|------|---------|
| `database.py` | Add `AutoEmail` model, add `auto_email_id` field to `DeliveryQueue`, add to `create_tables()` |
| `delivery_engine.py` | Update `_create_compat_record()` signature to accept `ses_message_id`, add `email_type="auto"` branch, pass `template_id` to `send_campaign_email()` |
| `email_sender.py` | Add `template_id` parameter to `send_campaign_email()`, include as SES tag when present |
| `app.py` | Add `/track/auto-open/<token>` route (new, with `itsdangerous` token) |
| `app.py` | Add `/track/auto-click/<token>` route (new, built from scratch — no campaign click route exists to mirror) |
| `app.py` | Auto-pilot scheduler: create `AutoEmail` at enqueue time, generate signed tracking pixel URL, add click-wrapping pass on HTML, fix unsubscribe URL to use `_make_unsubscribe_url()` |
| `app.py` | Update dashboard stats to include `AutoEmail` counts |
| `app.py` | Update `/sent-emails` to query `AutoEmail` + refactor filter to explicit three-way switch + add "Auto-Pilot" to type dropdown |
| `app.py` | Update `/profile/<id>` to query `AutoEmail` for contact email history |
| `app.py` | Update `_compute_health_score` to include `AutoEmail` in 14-day performance |
| `app.py` | Update WarmupLog daily update to include `AutoEmail` counts |
| `app.py` | Update SES webhook handler: fallback lookup by `ses_message_id` on `AutoEmail` when `campaign_id == 0` |
| `outcome_tracker.py` | Add `_process_auto_emails()` function with 72-hour attribution window |
| `app.py` | Backfill migration script (one-time) |
