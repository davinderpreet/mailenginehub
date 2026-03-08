# MailEngine — Claude Project Context

Built for Davinder. In-house email marketing platform replacing Klaviyo/Omnisend.
Powered by Flask + SQLite (Peewee ORM) + Amazon SES.

---

## How to Start

```bash
cd "C:/Users/davin/Claude Work Folder/email-platform"
python watchdog.py
```

Opens at: http://localhost:5000

The watchdog keeps the server alive automatically — use this instead of `run.py`.
- Pings the server every 20 seconds
- Restarts automatically if it crashes or returns HTTP 500
- Logs to `watchdog.log` (decisions) and `server.log` (Flask output)
- Run `python health_check.py` to diagnose any issues manually

---

## File Map

| File | Purpose |
|---|---|
| `run.py` | Entry point — loads .env, calls init_db(), starts Flask |
| `app.py` | All routes + warmup logic + health scoring |
| `database.py` | All Peewee ORM models + `init_db()` + `get_warmup_config()` |
| `email_sender.py` | Amazon SES sending via boto3 |
| `shopify_sync.py` | Shopify customer pull via REST API |
| `.env` | AWS + Shopify credentials (never commit this) |
| `email_platform.db` | SQLite database (auto-created on first run) |
| `templates/` | Jinja2 HTML templates (all extend `base.html`) |

---

## Database Models

- `Contact` — email list (email, first_name, last_name, tags, subscribed, source)
- `EmailTemplate` — reusable HTML email templates
- `Campaign` — a send job (name, from, template_id, segment_filter, status)
- `CampaignEmail` — one row per contact per campaign (status, opened, bounced)
- `WarmupConfig` — singleton row (id=1) with warmup state + 6 checklist booleans
- `WarmupLog` — one row per day for deliverability charts
- `Flow` — automation flow (name, trigger_type, trigger_value, is_active)
- `FlowStep` — one email step in a flow (step_order, delay_hours, template_id, from_email)
- `FlowEnrollment` — tracks one contact's progress through a flow (current_step, next_send_at, status)
- `FlowEmail` — one email sent by a flow step (status, opened)

---

## All Routes

| Route | Method | What it does |
|---|---|---|
| `/` | GET | Dashboard with stats |
| `/contacts` | GET | Contact list (search, tag filter, pagination) |
| `/contacts/import-csv` | POST | Upload CSV file |
| `/contacts/sync-shopify` | POST | Pull Shopify customers |
| `/contacts/unsubscribe/<email>` | GET | Unsubscribe page |
| `/templates` | GET | Template grid |
| `/templates/new` | GET/POST | Create template |
| `/templates/<id>/edit` | GET/POST | Edit template |
| `/templates/<id>/delete` | POST | Delete template |
| `/campaigns` | GET | Campaign list |
| `/campaigns/new` | GET/POST | Create campaign |
| `/campaigns/<id>` | GET | Campaign detail + stats |
| `/campaigns/<id>/send` | POST | Send/resume campaign |
| `/track/open/<campaign_id>/<contact_id>` | GET | Email open tracking pixel |
| `/warmup` | GET | Deliverability & warmup dashboard |
| `/warmup/toggle` | POST | Enable/disable warmup mode |
| `/warmup/checklist` | POST | Save checklist items |
| `/warmup/advance-phase` | POST | Manual phase advance |
| `/api/warmup/health` | GET | JSON health stats (polled every 15s) |
| `/api/contacts/count` | GET | JSON contact count |
| `/api/campaign/<id>/status` | GET | JSON campaign status |
| `/settings` | GET | AWS/Shopify config display |
| `/settings/test-ses` | POST | Send test email via SES |
| `/flows` | GET | List all flows with stats |
| `/flows/new` | GET/POST | Create new flow |
| `/flows/<id>` | GET | Flow detail — step builder + enrollments |
| `/flows/<id>/toggle` | POST | Enable/disable flow |
| `/flows/<id>/delete` | POST | Delete flow |
| `/flows/<id>/steps/add` | POST | Add step to flow |
| `/flows/<id>/steps/<step_id>/delete` | POST | Remove a step |
| `/flows/<id>/enroll-test` | POST | Manually enroll a test contact |
| `/api/flows/<id>/stats` | GET | JSON flow stats |
| `/track/flow-open/<enrollment_id>/<step_id>` | GET | Flow email open pixel |

---

## Campaign Status Flow

```
draft → sending → sent
              ↓
           paused  (warmup daily limit hit — user can resume)
```

---

## Warmup Phases

```
Phase 1 — Ignition:      50/day   (3 days)
Phase 2 — Spark:         150/day  (4 days)
Phase 3 — Gaining Trust: 350/day  (7 days)
Phase 4 — Building:      750/day  (7 days)
Phase 5 — Momentum:      1500/day (7 days)
Phase 6 — Scaling:       3000/day (7 days)
Phase 7 — High Volume:   7000/day (7 days)
Phase 8 — Full Send:     unlimited
```

Auto-advances when: open_rate >= 15% AND bounce_rate < 3% AND required days elapsed.

---

## Health Score (0-100)

| Item | Points |
|---|---|
| SPF record | 10 |
| DKIM signing | 10 |
| DMARC policy | 8 |
| SES production access | 10 |
| List cleaned | 6 |
| Subdomain sending | 6 |
| Open rate >= 20% | 25 |
| Bounce rate < 1% | 25 |

---

## Environment Variables (.env)

```
AWS_ACCESS_KEY_ID=        # from AWS IAM → Users → Security Credentials
AWS_SECRET_ACCESS_KEY=    # same
AWS_REGION=us-east-1
DEFAULT_FROM_EMAIL=       # must be verified in SES
SHOPIFY_STORE_URL=        # https://your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=     # shpat_... from Shopify Admin → Apps → Develop Apps
FLASK_ENV=development
```

---

## Common Errors & Fixes

### App won't start
1. Run `health_check.py` first: `python health_check.py`
2. Missing packages: `pip install -r requirements.txt`
3. Emoji crash on Windows: remove emoji from any new `print()` statements — use ASCII only
4. Port 5000 already in use: `netstat -ano | findstr :5000` then kill the PID

### SES errors
- `InvalidClientTokenId` — AWS keys are wrong or not in .env
- `MessageRejected` — From email not verified in SES, or still in sandbox mode
- `Throttling` — Sending too fast; warmup rate limiting handles this

### Database errors
- `OperationalError: no such table` — Database schema changed; delete `email_platform.db` and restart
- `IntegrityError: UNIQUE constraint` — Duplicate email on import; safe to ignore (already in contacts)

### Template rendering errors
- Jinja2 `UndefinedError` — A template variable is missing from the route's `render_template()` call
- Check the specific template and match it to the route in `app.py`

---

## Key Functions

- `_compute_health_score(config)` — returns int 0-100 based on checklist + CampaignEmail stats
- `_check_phase_advance(config)` — auto-advances warmup phase if metrics are good
- `_update_warmup_log(phase, limit)` — writes today's WarmupLog row
- `_send_campaign_async(campaign_id)` — background thread, enforces warmup daily limits
- `_get_campaign_contacts(campaign)` — returns list of subscribed contacts matching segment
- `get_warmup_config()` — returns WarmupConfig singleton (creates if missing)
- `init_db()` — connects + creates tables + seeds 3 starter templates

---

## Dependencies

```
flask>=3.0.0
peewee>=3.17.0       # ORM for SQLite
boto3>=1.34.0        # AWS SES
requests>=2.31.0     # Shopify API
python-dotenv>=1.0.0 # .env loading
```

Python 3.10+ required. Tested on 3.14.

---

## Design Patterns

- Templates all extend `templates/base.html`
- Primary colour: #6366f1 (indigo)
- All form POSTs redirect after save (PRG pattern)
- Background sends use `threading.Thread(daemon=True)`
- Flash messages: `flash("text", "success"|"error"|"warning")`
- No external DB server — pure SQLite via Peewee
