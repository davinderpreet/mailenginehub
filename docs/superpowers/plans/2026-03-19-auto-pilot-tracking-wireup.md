# Auto-Pilot Email Tracking — Full Wire-Up Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire auto-pilot emails into every tracking, analytics, and intelligence system so they're no longer invisible.

**Architecture:** New `AutoEmail` table created at enqueue time (status=queued), updated to sent after delivery. Open/click tracking via signed `itsdangerous` tokens. All 10 downstream systems updated to include AutoEmail data alongside CampaignEmail and FlowEmail.

**Tech Stack:** Flask, Peewee ORM, SQLite, Amazon SES, itsdangerous

**Spec:** `docs/superpowers/specs/2026-03-19-auto-pilot-tracking-wireup-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `database.py` | Add `AutoEmail` model + `auto_email_id` on DeliveryQueue + migration | Modify |
| `delivery_engine.py` | Update `_create_compat_record()` + pass `ses_message_id` + `template_id` | Modify |
| `email_sender.py` | Add `template_id` param to `send_campaign_email()` + SES tag | Modify |
| `app.py` | New tracking routes, auto-pilot scheduler fix, 7 downstream integrations | Modify |
| `outcome_tracker.py` | Add `_process_auto_emails()` | Modify |

---

## Chunk 1: Database Model + Migration

### Task 1: Add `AutoEmail` model to database.py

**Files:**
- Modify: `database.py:202` (after FlowEmail class, before AbandonedCheckout)
- Modify: `database.py:752-769` (create_tables list)

- [ ] **Step 1: Add AutoEmail class after FlowEmail (line 202)**

Insert after line 202 (`table_name = "flow_emails"`), before `class AbandonedCheckout`:

```python
class AutoEmail(BaseModel):
    """One email sent by the auto-pilot scheduler — for tracking opens/clicks."""
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
    ses_message_id = CharField(default="")
    auto_run_date  = DateField(null=True)

    class Meta:
        table_name = "auto_emails"
```

- [ ] **Step 2: Add `auto_email_id` field to DeliveryQueue model**

In `database.py` at line 1393 (after `campaign_id` field in DeliveryQueue), add:

```python
    auto_email_id = IntegerField(default=0)        # links to AutoEmail.id for auto-pilot sends
```

- [ ] **Step 3: Add AutoEmail to create_tables list**

In `database.py` at line 753, add `AutoEmail` to the create_tables list. Insert after `FlowEmail`:

```python
         Flow, FlowStep, FlowEnrollment, FlowEmail, AutoEmail, AbandonedCheckout, AgentMessage,
```

- [ ] **Step 4: Add migration for auto_email_id column on delivery_queue**

Add a migration function after the existing migrations (around line 789):

```python
def _migrate_delivery_queue_auto_email_id():
    """Add auto_email_id column to delivery_queue for auto-pilot tracking."""
    try:
        cursor = db.execute_sql("PRAGMA table_info(delivery_queue)")
        cols = [row[1] for row in cursor.fetchall()]
        if "auto_email_id" not in cols:
            db.execute_sql("ALTER TABLE delivery_queue ADD COLUMN auto_email_id INTEGER DEFAULT 0")
            print("[MIGRATE] Added auto_email_id to delivery_queue")
    except Exception as e:
        print(f"[MIGRATE] delivery_queue auto_email_id: {e}")
```

Call it from `init_db()` after the existing migration calls (around line 789):

```python
    _migrate_delivery_queue_auto_email_id()
```

- [ ] **Step 5: Verify — run init_db() to create table**

```bash
cd "C:/Users/davin/Claude Work Folder/mailenginehub-repo"
python -c "from database import init_db; init_db()"
```

Expected: `[OK] Database ready` with no errors. The `auto_emails` table and `delivery_queue.auto_email_id` column should now exist.

- [ ] **Step 6: Commit**

```bash
git add database.py
git commit -m "feat: add AutoEmail model and auto_email_id on DeliveryQueue

New table for tracking auto-pilot email sends, opens, clicks.
Links DeliveryQueue items to their AutoEmail row for status updates."
```

---

### Task 2: Backfill historical auto-pilot sends

**Files:**
- Modify: `database.py` (add one-time migration function)

- [ ] **Step 1: Add backfill migration function**

Add after `_migrate_delivery_queue_auto_email_id()`:

```python
def _backfill_auto_emails():
    """One-time: create AutoEmail rows for past auto-pilot sends from DeliveryQueue."""
    try:
        existing = AutoEmail.select().count()
        if existing > 0:
            return  # already backfilled
        past_auto = (DeliveryQueue
                     .select()
                     .where(DeliveryQueue.email_type == "auto",
                            DeliveryQueue.status == "sent"))
        count = 0
        for item in past_auto:
            try:
                sent_time = item.sent_at or item.created_at
                AutoEmail.create(
                    contact=item.contact,
                    template=item.template_id or None,
                    subject=item.subject,
                    status="sent",
                    sent_at=sent_time,
                    ses_message_id="",
                    auto_run_date=sent_time.date() if sent_time else None,
                    opened=False,
                    clicked=False
                )
                count += 1
            except Exception as e:
                print(f"[BACKFILL] skip: {e}")
        if count:
            print(f"[BACKFILL] Created {count} AutoEmail rows from DeliveryQueue history")
    except Exception as e:
        print(f"[BACKFILL] auto_emails: {e}")
```

Call it from `init_db()` right after `_migrate_delivery_queue_auto_email_id()`:

```python
    _backfill_auto_emails()
```

- [ ] **Step 2: Verify — run init_db()**

```bash
python -c "from database import init_db, AutoEmail; init_db(); print(f'AutoEmail rows: {AutoEmail.select().count()}')"
```

Expected: Shows count of backfilled rows (matches number of past auto-pilot sends in DeliveryQueue).

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: backfill AutoEmail rows from historical DeliveryQueue auto-pilot sends"
```

---

## Chunk 2: Send Pipeline Plumbing

### Task 3: Add `template_id` parameter to `send_campaign_email()`

**Files:**
- Modify: `email_sender.py` — `send_campaign_email()` function signature + SES tags

- [ ] **Step 1: Read current send_campaign_email signature**

Find the function in `email_sender.py`. Look for `def send_campaign_email(`. Read the full signature and the SES tags section.

- [ ] **Step 2: Add `template_id=0` parameter**

The current signature at line 60 is:
```python
def send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body,
                        unsubscribe_url=None, campaign_id=None):
```

Change to (add `template_id=0` at the end — do NOT reorder existing params):
```python
def send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body,
                        unsubscribe_url=None, campaign_id=None, template_id=0):
```

- [ ] **Step 3: Add template_id as SES tag**

Find the SES tags section at line 119-127. The current code builds tags from `campaign_id`. After `if campaign_id:` block, add `template_id` tag:

```python
        tags = []
        if campaign_id:
            tags.append({"Name": "campaign_id", "Value": str(campaign_id)})
        if template_id:
            tags.append({"Name": "template_id", "Value": str(template_id)})
```

Also remove the dead code at lines 122-125 (`try: template_id = campaign_id...`) which does nothing useful.

- [ ] **Step 4: Commit**

```bash
git add email_sender.py
git commit -m "feat: add template_id param to send_campaign_email for SES tag attribution"
```

---

### Task 4: Update delivery_engine.py — pass ses_message_id + template_id

**Files:**
- Modify: `delivery_engine.py` — `_create_compat_record()` signature, live send block, auto branch

- [ ] **Step 1: Update `_create_compat_record()` signature**

Find `def _create_compat_record(` in `delivery_engine.py` at line 304. The current signature is:

```python
def _create_compat_record(item, status, error_msg=""):
```

Add `ses_message_id=""` parameter:

```python
def _create_compat_record(item, status, error_msg="", ses_message_id=""):
```

- [ ] **Step 2: Add `email_type == "auto"` branch**

Inside `_create_compat_record()`, after the `elif email_type == "campaign"` block, add:

```python
    elif email_type == "auto":
        auto_email_id = getattr(item, 'auto_email_id', 0)
        if auto_email_id:
            AutoEmail.update(
                status=status,
                sent_at=datetime.now(),
                ses_message_id=ses_message_id
            ).where(AutoEmail.id == auto_email_id).execute()
```

Add `from database import AutoEmail` to the imports at the top of the file if not already present.

- [ ] **Step 3: Pass template_id to send_campaign_email in the live send block**

Find the call to `send_campaign_email()` at `delivery_engine.py` line 257-266. The current call returns a 3-tuple `(success, error, msg_id)`. Add `template_id` while preserving the existing pattern:

```python
success, error, msg_id = send_campaign_email(
    to_email=item.email,
    to_name="",
    from_email=item.from_email,
    from_name=item.from_name,
    subject=item.subject,
    html_body=item.html,
    unsubscribe_url=item.unsubscribe_url,
    campaign_id=item.campaign_id or None,
    template_id=item.template_id,       # NEW
)
```

**CRITICAL:** Do NOT change the return value destructuring — it's a 3-tuple `(success, error, msg_id)`, not a single value.

- [ ] **Step 4: Pass ses_message_id to _create_compat_record — BOTH success and failure paths**

At line 273 (success path), change:
```python
_create_compat_record(item, status="sent")
```
To:
```python
_create_compat_record(item, status="sent", ses_message_id=msg_id or "")
```

At line 287 (failure path), change:
```python
_create_compat_record(item, status="failed", error_msg=error or "")
```
To:
```python
_create_compat_record(item, status="failed", error_msg=error or "", ses_message_id="")
```

- [ ] **Step 5: Commit**

```bash
git add delivery_engine.py
git commit -m "feat: wire ses_message_id + template_id through delivery pipeline

- _create_compat_record() now accepts ses_message_id
- New auto branch updates existing AutoEmail row on send
- template_id passed to SES for bounce attribution"
```

---

## Chunk 3: Tracking Routes (Open + Click)

### Task 5: Add auto-open tracking route

**Files:**
- Modify: `app.py` — add new route near existing `/track/open/` routes

- [ ] **Step 1: Find the existing tracking routes**

Search for `@app.route("/track/open/` in `app.py`. The existing routes are around lines 1880-1960. We'll add the new route right after them.

- [ ] **Step 2: Add the `/track/auto-open/<token>` route**

Add after the last existing open tracking route:

```python
@app.route("/track/auto-open/<token>")
def track_auto_open(token):
    """Track opens for auto-pilot emails using signed token."""
    try:
        from itsdangerous import URLSafeSerializer
        s = URLSafeSerializer(app.secret_key, salt="auto-open")
        auto_email_id = s.loads(token)

        ae = AutoEmail.get_or_none(AutoEmail.id == auto_email_id)
        if ae and not ae.opened:
            AutoEmail.update(
                opened=True,
                opened_at=datetime.now()
            ).where(AutoEmail.id == auto_email_id).execute()

            # Update contact engagement
            Contact.update(last_open_at=datetime.now()).where(
                Contact.id == ae.contact_id
            ).execute()
            try:
                from cascade import cascade_contact
                cascade_contact(ae.contact_id, trigger="auto_open")
            except Exception:
                pass

    except Exception as e:
        print(f"[AUTO-OPEN] Error: {e}")

    # Always return tracking pixel
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return pixel, 200, {"Content-Type": "image/gif", "Cache-Control": "no-cache, no-store"}
```

Make sure `AutoEmail` is imported at the top of `app.py` (add to the existing `from database import ...` line).

- [ ] **Step 3: Verify the route responds**

Test locally or verify the syntax compiles:

```bash
python -c "
import app
with app.app.test_client() as c:
    # Test with invalid token - should return pixel anyway
    resp = c.get('/track/auto-open/invalid-token')
    print(f'Status: {resp.status_code}, Content-Type: {resp.content_type}')
    assert resp.status_code == 200
    assert resp.content_type == 'image/gif'
    print('PASS: auto-open route returns pixel')
"
```

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add /track/auto-open/<token> route for auto-pilot email open tracking"
```

---

### Task 6: Add auto-click tracking route

**Files:**
- Modify: `app.py` — add new route after the auto-open route

- [ ] **Step 1: Add the `/track/auto-click/<token>` route**

Add right after the auto-open route:

```python
@app.route("/track/auto-click/<token>")
def track_auto_click(token):
    """Track clicks for auto-pilot emails. Redirects to destination URL."""
    import urllib.parse
    destination = request.args.get("url", "")

    try:
        from itsdangerous import URLSafeSerializer
        s = URLSafeSerializer(app.secret_key, salt="auto-click")
        auto_email_id = s.loads(token)

        ae = AutoEmail.get_or_none(AutoEmail.id == auto_email_id)
        if ae and not ae.clicked:
            AutoEmail.update(
                clicked=True,
                clicked_at=datetime.now()
            ).where(AutoEmail.id == auto_email_id).execute()

            # Update contact engagement
            Contact.update(last_click_at=datetime.now()).where(
                Contact.id == ae.contact_id
            ).execute()
            try:
                from cascade import cascade_contact
                cascade_contact(ae.contact_id, trigger="auto_click")
            except Exception:
                pass

    except Exception as e:
        print(f"[AUTO-CLICK] Error: {e}")

    # Redirect to destination (or home if missing)
    if destination:
        return redirect(urllib.parse.unquote(destination))
    return redirect("https://mailenginehub.com")
```

- [ ] **Step 2: Commit**

```bash
git add app.py
git commit -m "feat: add /track/auto-click/<token> route for auto-pilot click tracking"
```

---

### Task 7: Fix auto-pilot scheduler — create AutoEmail at enqueue, add tracking URLs

**Files:**
- Modify: `app.py` — auto-pilot scheduler section (~line 6150-6280)

This is the most complex task. The auto-pilot scheduler currently:
1. Picks a template for each contact
2. Renders the HTML
3. Hardcodes a broken tracking pixel URL
4. Uses an insecure unsubscribe URL
5. Enqueues via `enqueue_email()`

We need to:
1. Create `AutoEmail` row first (status=queued)
2. Generate signed tracking pixel URL using `auto_email_id`
3. Wrap all links for click tracking
4. Fix unsubscribe URL
5. Store `auto_email_id` on the DeliveryQueue item

- [ ] **Step 1: Find the auto-pilot scheduler section**

Search for the tracking pixel URL generation in `app.py` (~line 6243). Read the surrounding 100 lines to understand the full flow.

- [ ] **Step 2: Create AutoEmail row before building HTML**

Find where the template is selected for each contact. Before the HTML is built, add:

```python
# Create AutoEmail row for tracking (status=queued, updated to sent after delivery)
from database import AutoEmail
auto_email = AutoEmail.create(
    contact=contact,
    template=template.id if template else None,
    subject=final_subject,
    status="queued",
    auto_run_date=datetime.now().date()
)
auto_email_id = auto_email.id
```

- [ ] **Step 3: Replace broken tracking pixel URL**

Find the line that builds the tracking pixel (~line 6243). Replace:

```python
# BEFORE:
pixel_url = f"https://mailenginehub.com/track/auto-open/{contact.id}/{template.id}"
```

With:

```python
# AFTER — signed token
from itsdangerous import URLSafeSerializer
_s = URLSafeSerializer(app.secret_key, salt="auto-open")
_open_token = _s.dumps(auto_email_id)
pixel_url = f"https://mailenginehub.com/track/auto-open/{_open_token}"
```

- [ ] **Step 4: Add click-wrapping for all links**

After the HTML is rendered but before enqueuing, add a link-rewriting pass:

```python
import re
import urllib.parse
from itsdangerous import URLSafeSerializer

_click_s = URLSafeSerializer(app.secret_key, salt="auto-click")
_click_token = _click_s.dumps(auto_email_id)

def _wrap_auto_links(html, click_token):
    """Rewrite <a href="..."> to go through click tracker, excluding unsubscribe links."""
    def replacer(match):
        original_url = match.group(1)
        # Don't wrap unsubscribe links or tracking pixels
        if "unsubscribe" in original_url.lower() or "track/" in original_url:
            return match.group(0)
        encoded = urllib.parse.quote(original_url, safe="")
        return f'href="https://mailenginehub.com/track/auto-click/{click_token}?url={encoded}"'
    return re.sub(r'href="([^"]+)"', replacer, html)

final_html = _wrap_auto_links(final_html, _click_token)
```

- [ ] **Step 5: Fix unsubscribe URL**

Find where the unsubscribe URL is set (~line 6155). Replace:

```python
# BEFORE:
unsub_url = f"https://mailenginehub.com/unsubscribe/{contact.email}"
```

With:

```python
# AFTER — signed token
unsub_url = _make_unsubscribe_url(contact)
```

- [ ] **Step 6: Add `auto_email_id` param to `enqueue_email()` and pass it**

First, update `enqueue_email()` in `delivery_engine.py` at line 91-94. Add `auto_email_id=0` to the signature:

```python
def enqueue_email(contact, email_type, source_id, enrollment_id, step_id,
                  template_id, from_name, from_email, subject, html,
                  unsubscribe_url, priority, ledger_id, campaign_id=0,
                  scheduled_at=None, auto_email_id=0):
```

Then in the `_create_kwargs` dict at line 116-133, add:

```python
        auto_email_id=auto_email_id,
```

This ensures the `auto_email_id` is stored on the `DeliveryQueue` row when created at line 136.

Then, in the auto-pilot scheduler's `enqueue_email()` call in `app.py`, pass `auto_email_id=auto_email_id`.

- [ ] **Step 7: Handle send failure — update AutoEmail status**

In the delivery engine's failure handling path, if `email_type == "auto"`, update the AutoEmail row:

```python
elif email_type == "auto":
    auto_email_id = getattr(item, 'auto_email_id', 0)
    if auto_email_id:
        AutoEmail.update(
            status="failed",
            error_msg=str(error)
        ).where(AutoEmail.id == auto_email_id).execute()
```

- [ ] **Step 8: Commit**

```bash
git add app.py delivery_engine.py
git commit -m "feat: auto-pilot scheduler creates AutoEmail at enqueue time

- AutoEmail row created with status=queued before HTML rendering
- Signed tracking pixel URL (replaces broken /track/auto-open/<id>/<id>)
- Click wrapping on all links via /track/auto-click/<token>
- Secure unsubscribe URL via _make_unsubscribe_url()
- auto_email_id stored on DeliveryQueue for status updates"
```

---

## Chunk 4: Downstream Integration — Dashboard + Sent Emails + Profile

### Task 8: Update dashboard stats

**Files:**
- Modify: `app.py` — dashboard route (`/`), stats computation section

- [ ] **Step 1: Find dashboard stats computation**

Search for `total_sent` or `Emails Sent` in `app.py`. The dashboard route is at the top (`/`). Find where `CampaignEmail.select().where(...)` counts are computed (~line 449-452).

- [ ] **Step 2: Add AutoEmail counts**

After the existing campaign + flow counts, add:

```python
auto_sent = AutoEmail.select().where(AutoEmail.status == "sent").count()
auto_opened = AutoEmail.select().where(AutoEmail.opened == True).count()
```

Then update the totals:

```python
total_sent = campaign_sent + flow_sent + auto_sent
total_opened = campaign_opened + flow_opened + auto_opened
```

Find where `total_sent` and `total_opened` are computed and add the `auto_*` values.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: include AutoEmail in dashboard total sent/opened/open rate stats"
```

---

### Task 9: Update Sent Emails page

**Files:**
- Modify: `app.py` — `/sent-emails` route (~line 1397)

- [ ] **Step 1: Read the current sent-emails route**

Find `/sent-emails` in `app.py`. Read the full handler to understand the current query structure and the `email_type` filter logic.

- [ ] **Step 2: Refactor filter to three-way switch**

Replace the current `email_type != "flow"` / `email_type != "campaign"` logic with explicit switching:

```python
email_type = request.args.get("type", "all")

campaign_emails = []
flow_emails = []
auto_emails = []

if email_type in ("all", "campaign"):
    # existing CampaignEmail query
    campaign_emails = [...]  # keep existing query

if email_type in ("all", "flow"):
    # existing FlowEmail query
    flow_emails = [...]  # keep existing query

if email_type in ("all", "auto"):
    # NEW: AutoEmail query
    auto_query = (AutoEmail
                  .select(AutoEmail, Contact, EmailTemplate)
                  .join(Contact, on=(AutoEmail.contact == Contact.id))
                  .switch(AutoEmail)
                  .join(EmailTemplate, JOIN.LEFT_OUTER, on=(AutoEmail.template == EmailTemplate.id))
                  .where(AutoEmail.status == "sent")
                  .order_by(AutoEmail.sent_at.desc()))

    for ae in auto_query:
        auto_emails.append({
            "type": "auto",
            "source": f"Auto-Pilot",
            "template_name": ae.template.name if ae.template else "Unknown",
            "contact_email": ae.contact.email,
            "contact_name": f"{ae.contact.first_name} {ae.contact.last_name}".strip(),
            "status": ae.status,
            "opened": ae.opened,
            "clicked": ae.clicked,
            "sent_at": ae.sent_at,
        })
```

Merge `auto_emails` into the combined list alongside campaign_emails and flow_emails, sort by `sent_at`, and paginate.

- [ ] **Step 3: Add "Auto-Pilot" to the type filter dropdown in the template**

Find the sent-emails template (likely inline HTML or a templates file). Add an option for the type filter:

```html
<option value="auto" {% if type == 'auto' %}selected{% endif %}>Auto-Pilot</option>
```

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: show AutoEmail on Sent Emails page with type filter support"
```

---

### Task 10: Update Contact Profile email history

**Files:**
- Modify: `app.py` — `/profile/<contact_id>` route (~line 4494)

- [ ] **Step 1: Find the email activity query section**

Search for `email_activity` or `campaign_emails` in the profile route. The existing code queries last 20 CampaignEmail rows and last 20 FlowEmail rows, then merges them.

- [ ] **Step 2: Add AutoEmail query**

After the existing flow email query, add:

```python
# Auto-pilot emails
auto_emails_raw = (AutoEmail
                   .select(AutoEmail, EmailTemplate)
                   .join(EmailTemplate, JOIN.LEFT_OUTER, on=(AutoEmail.template == EmailTemplate.id))
                   .where(AutoEmail.contact == contact.id, AutoEmail.status == "sent")
                   .order_by(AutoEmail.sent_at.desc())
                   .limit(20))

for ae in auto_emails_raw:
    email_activity.append({
        "type": "auto",
        "source": f"Auto-Pilot: {ae.template.name if ae.template else 'Unknown'}",
        "status": ae.status,
        "opened": ae.opened,
        "clicked": ae.clicked,
        "sent_at": ae.sent_at,
    })
```

The existing code should already sort `email_activity` by `sent_at` and take the top 20 — verify this handles the merged list correctly.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: show AutoEmail in contact profile email activity timeline"
```

---

## Chunk 5: Downstream Integration — Health, Warmup, Webhooks

### Task 11: Update health score calculation

**Files:**
- Modify: `app.py` — `_compute_health_score()` function (~line 335)

- [ ] **Step 1: Find the health score function**

Search for `_compute_health_score` in `app.py`. Find where it queries `CampaignEmail` and `FlowEmail` for the 14-day performance score (~40 points component).

- [ ] **Step 2: Add AutoEmail to the performance counts**

After the existing campaign + flow counts, add:

```python
auto_sent_14d = AutoEmail.select().where(
    AutoEmail.status == "sent",
    AutoEmail.sent_at >= cutoff_14d
).count()
auto_opened_14d = AutoEmail.select().where(
    AutoEmail.opened == True,
    AutoEmail.sent_at >= cutoff_14d
).count()
auto_bounced_14d = AutoEmail.select().where(
    AutoEmail.status == "bounced",
    AutoEmail.sent_at >= cutoff_14d
).count()
```

Add these to the respective totals used for the performance score calculation.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: include AutoEmail in deliverability health score 14-day performance"
```

---

### Task 12: Update WarmupLog daily counters

**Files:**
- Modify: `app.py` — WarmupLog update section (~line 419)

- [ ] **Step 1: Find the WarmupLog update section**

Search for `WarmupLog` updates in `app.py`. Find where it counts today's `CampaignEmail` + `FlowEmail` sent/opened/bounced.

- [ ] **Step 2: Add AutoEmail counts to daily totals**

After the existing counts, add:

```python
auto_sent_today = AutoEmail.select().where(
    AutoEmail.status == "sent",
    AutoEmail.sent_at >= today_start
).count()
auto_opened_today = AutoEmail.select().where(
    AutoEmail.opened == True,
    AutoEmail.sent_at >= today_start
).count()
auto_bounced_today = AutoEmail.select().where(
    AutoEmail.status == "bounced",
    AutoEmail.sent_at >= today_start
).count()
```

Add these to the WarmupLog `emails_sent`, `emails_opened`, `emails_bounced` values.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: include AutoEmail in WarmupLog daily trend counters"
```

---

### Task 13: Update SES webhook handler for AutoEmail bounce attribution

**Files:**
- Modify: `app.py` — SES/SNS webhook handler (~line 124)

- [ ] **Step 1: Find the SES notification handler**

Search for `sns` or `bounce` handling in `app.py`. Find the section that processes bounce/complaint notifications and creates `BounceLog` entries.

- [ ] **Step 2: Add AutoEmail fallback lookup**

After the existing bounce processing (which uses `campaign_id` for attribution), add a fallback for auto-pilot:

```python
# Auto-pilot bounce attribution
if campaign_id == 0 and ses_message_id:
    auto_email = AutoEmail.get_or_none(AutoEmail.ses_message_id == ses_message_id)
    if auto_email:
        AutoEmail.update(
            status="bounced",
            error_msg=bounce_type or complaint_type or "bounce"
        ).where(AutoEmail.id == auto_email.id).execute()
```

This goes after the existing BounceLog creation and suppression handling. The contact suppression still works by email address regardless — this just adds attribution to the specific AutoEmail row.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: attribute SES bounces/complaints to AutoEmail when campaign_id=0"
```

---

## Chunk 6: Outcome Tracker + Final Integration

### Task 14: Add `_process_auto_emails()` to outcome_tracker.py

**Files:**
- Modify: `outcome_tracker.py` — add new function + call from `run()`

- [ ] **Step 1: Read the existing `_process_campaign_emails()` function**

Read `outcome_tracker.py` to understand the pattern. Find `_process_campaign_emails()` and note:
- How it queries CampaignEmail for the time window
- How it creates OutcomeLog rows
- How it calls `_attribute_purchase()`
- The fields it populates

- [ ] **Step 2: Add `_process_auto_emails()` function**

Model it on `_process_campaign_emails()`:

```python
def _process_auto_emails(hours=48):
    """Process recent auto-pilot emails and create OutcomeLog entries."""
    from database import AutoEmail, OutcomeLog, Contact, EmailTemplate
    cutoff = datetime.now() - timedelta(hours=hours)

    auto_emails = (AutoEmail
                   .select(AutoEmail, Contact)
                   .join(Contact, on=(AutoEmail.contact == Contact.id))
                   .where(AutoEmail.status == "sent",
                          AutoEmail.sent_at >= cutoff))

    created = 0
    errors = 0
    for ae in auto_emails:
        # Skip if already logged
        exists = OutcomeLog.select().where(
            OutcomeLog.email_type == "auto",
            OutcomeLog.email_id == ae.id
        ).exists()
        if exists:
            continue

        # Attribute purchase within 72-hour window
        purchased, revenue, hours_to_purchase = _attribute_purchase(
            ae.contact, ae.sent_at, window_hours=72
        )

        # Check if contact unsubscribed after this email
        unsubscribed = (not ae.contact.subscribed and
                        ae.contact.created_at < ae.sent_at)

        # Compute hours to open
        hours_to_open = None
        if ae.opened and ae.opened_at and ae.sent_at:
            hours_to_open = (ae.opened_at - ae.sent_at).total_seconds() / 3600

        # Get segment from CustomerProfile if available
        segment = ""
        try:
            from database import CustomerProfile
            cp = CustomerProfile.get_or_none(CustomerProfile.contact == ae.contact)
            if cp:
                segment = cp.lifecycle_stage or ""
        except Exception:
            pass

        try:
            OutcomeLog.create(
                email_type="auto",
                email_id=ae.id,
                contact=ae.contact,
                template_id=ae.template_id or 0,
                action_type="auto_pilot",
                segment=segment,
                opened=ae.opened,
                clicked=ae.clicked,
                purchased=purchased,
                unsubscribed=unsubscribed,
                revenue=revenue,
                hours_to_open=hours_to_open,
                hours_to_purchase=hours_to_purchase,
                sent_at=ae.sent_at,
                subject_line=ae.subject[:200] if ae.subject else "",
                send_gap_hours=None  # TODO: compute from last email to this contact
            )
            created += 1
        except Exception as e:
            errors += 1
            print(f"[OUTCOME] auto skip {ae.id}: {e}")

    print(f"[OUTCOME] Processed auto-pilot emails: {created} new OutcomeLog rows, {errors} errors")
    return created, errors
```

- [ ] **Step 3: Call from the main `run()` function**

Find the `run()` function in `outcome_tracker.py`. After the calls to `_process_campaign_emails()` and `_process_flow_emails()`, add:

```python
    _process_auto_emails()
```

- [ ] **Step 4: Commit**

```bash
git add outcome_tracker.py
git commit -m "feat: add _process_auto_emails() to outcome tracker

Creates OutcomeLog rows for auto-pilot emails with 72-hour
purchase attribution window. Learning engine picks these up
automatically since OutcomeLog is email_type-agnostic."
```

---

### Task 15: Ensure AutoEmail import everywhere

**Files:**
- Modify: `app.py` — imports section
- Modify: `delivery_engine.py` — imports section
- Modify: `outcome_tracker.py` — imports section

- [ ] **Step 1: Add AutoEmail to app.py imports**

Find the `from database import ...` line at the top of `app.py`. Add `AutoEmail` to the list:

```python
from database import (Contact, EmailTemplate, Campaign, CampaignEmail, ..., AutoEmail)
```

- [ ] **Step 2: Add AutoEmail to delivery_engine.py imports**

Find the `from database import ...` line. Add `AutoEmail`.

- [ ] **Step 3: Verify no import errors**

```bash
python -c "from database import AutoEmail; print('OK')"
python -c "import app; print('app.py imports OK')"
python -c "import delivery_engine; print('delivery_engine imports OK')"
python -c "import outcome_tracker; print('outcome_tracker imports OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app.py delivery_engine.py outcome_tracker.py
git commit -m "chore: add AutoEmail imports to all modules that reference it"
```

---

### Task 16: Deploy and verify end-to-end

**Files:**
- No code changes — deployment and verification

- [ ] **Step 1: Run the full app locally to check for errors**

```bash
cd "C:/Users/davin/Claude Work Folder/mailenginehub-repo"
python -c "
from database import init_db, AutoEmail, DeliveryQueue
init_db()
print(f'AutoEmail rows: {AutoEmail.select().count()}')
print(f'DeliveryQueue auto items: {DeliveryQueue.select().where(DeliveryQueue.email_type == \"auto\").count()}')
print('All OK')
"
```

- [ ] **Step 2: Deploy to VPS**

```bash
cd "C:/Users/davin/Claude Work Folder/mailenginehub-repo"
bash deploy.sh
```

- [ ] **Step 3: Verify on live site**

1. Check dashboard — total sent count should now include backfilled auto-pilot emails
2. Check Sent Emails page — filter by "Auto-Pilot" should show historical sends
3. Check a contact profile that received an auto-pilot email — should appear in timeline
4. Wait for next auto-pilot run, then verify:
   - AutoEmail row created with status="sent"
   - Tracking pixel URL returns 200
   - Open email → AutoEmail.opened becomes True
   - Click link → AutoEmail.clicked becomes True, redirect works

- [ ] **Step 4: Final commit if any deployment fixes needed**

```bash
git add -A
git commit -m "fix: deployment adjustments for auto-pilot tracking wire-up"
```
