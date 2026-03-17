# MailEngineHub — Claude Project Context

Built for Davinder. In-house email marketing platform for LDAS Electronics, replacing Klaviyo/Omnisend.
Stack: Flask + SQLite (Peewee ORM) + Amazon SES + Gunicorn on VPS.

---

## ⚠️ DEPLOYMENT RULES — READ FIRST ⚠️

**NEVER edit files directly on the VPS via SSH.** All changes go through git.

### The Workflow

```
1. Edit files in THIS local repo (mailenginehub-repo/)
2. Test locally if possible
3. git add + git commit
4. Run: bash deploy.sh
5. deploy.sh handles: snapshot VPS → copy files → commit on VPS → restart → smoke test
```

### If You Must Edit on VPS (emergency hotfix)

```
1. SSH in and make the fix
2. IMMEDIATELY run on VPS: cd /var/www/mailengine && git add -A && git commit -m "hotfix: describe"
3. Then sync back: bash sync-from-vps.sh (from local repo)
4. Commit locally: git add -A && git commit -m "Sync hotfix from VPS"
```

### Rollback

```bash
# On VPS — roll back to previous commit:
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com
cd /var/www/mailengine
git log --oneline -5          # find the good commit
git checkout <commit> -- .    # restore files
systemctl restart mailengine
```

### NEVER Do These Things

- ❌ `scp` local files to VPS without committing first
- ❌ Edit VPS files without committing on VPS after
- ❌ Overwrite VPS app.py from local (local may be behind)
- ❌ Run patch scripts that modify app.py via string replacement
- ❌ Leave VPS changes uncommitted at end of session

---

## Project Layout

| Location | Purpose |
|---|---|
| `C:\Users\davin\Claude Work Folder\mailenginehub-repo\` | **Primary local git repo** (work here) |
| `root@mailenginehub.com:/var/www/mailengine/` | **VPS production** (has its own git) |
| `https://github.com/davinderpreet/mailenginehub` | **GitHub remote** |
| `C:\Users\davin\Claude Work Folder\email-platform\` | OLD local copy — DO NOT USE |
| `C:\Users\davin\Claude Work Folder\mailenginehub\` | Older git repo — DO NOT USE |

### VPS Access

```bash
SSH_KEY=~/.ssh/mailengine_vps
ssh -i $SSH_KEY root@mailenginehub.com
# Service: systemctl restart mailengine
# Logs: journalctl -u mailengine -n 50
# App runs on port 5000 behind nginx
# Auth: admin:DavinderS@1993
```

---

## Scripts

| Script | What it does |
|---|---|
| `deploy.sh` | Deploy local → VPS (with pre-flight, snapshot, smoke test, auto-rollback) |
| `sync-from-vps.sh` | Pull VPS → local (for when VPS was edited directly) |

---

## File Map

| File | Purpose |
|---|---|
| `app.py` | All routes (~90 routes, ~5800 lines) |
| `database.py` | All Peewee ORM models (~60+ models) + `init_db()` |
| `block_registry.py` | Email template block rendering system |
| `studio_routes.py` | AI Studio Flask Blueprint (`/studio/*`) |
| `studio_skills.py` | AI skills pipeline for email generation |
| `template_studio.py` | Template Studio orchestrator |
| `knowledge_scraper.py` | Knowledge base auto-enrichment pipeline |
| `ai_engine.py` | Nightly AI scoring + daily plan generation |
| `next_best_message.py` | Decision engine for per-contact email selection |
| `learning_engine.py` | Self-learning analysis pipeline |
| `outcome_tracker.py` | Email outcome collection (opens, clicks, purchases) |
| `strategy_optimizer.py` | Apply learned insights to sending strategy |
| `learning_config.py` | Key-value config for self-learning layer |
| `delivery_engine.py` | Email delivery with warmup compliance |
| `email_sender.py` | Low-level AWS SES sending via boto3 |
| `email_shell.py` | Unified header + footer wrapper for all emails |
| `system_map_data.py` | System architecture visualization data (65 nodes) |
| `campaign_planner.py` | AI campaign opportunity scanner |
| `profit_engine.py` | Product profitability scoring |
| `customer_intelligence.py` | Customer profile enrichment |
| `condition_engine.py` | Flow step conditional logic |
| `cascade.py` | Auto-cascade intelligence rules |
| `shopify_sync.py` | Shopify customer/order sync |
| `activity_sync.py` | Email activity (opens/clicks) sync |
| `templates/` | Jinja2 HTML templates (extend `base.html`) |
| `templates/studio/` | AI Studio templates |

---

## Key Routes

| Route | Page |
|---|---|
| `/` | Dashboard |
| `/contacts` | Contact list |
| `/templates` | Email templates |
| `/campaigns` | Campaign list |
| `/campaigns/<id>` | Campaign detail + stats |
| `/flows` | Automation flows |
| `/flows/<id>` | Flow detail + step builder |
| `/warmup` | Deliverability & warmup dashboard |
| `/sent-emails` | Unified email log (campaign + flow) |
| `/profiles` | Customer profiles list |
| `/profiles/<id>` | Customer profile detail |
| `/ai-engine` | AI Engine dashboard (segments, daily plan) |
| `/studio` | AI Template Studio |
| `/campaign-planner` | AI Campaign suggestions |
| `/profits` | Profit Brain dashboard |
| `/learning` | Self-Learning dashboard |
| `/audit` | Action audit ledger |
| `/telemetry` | Telemetry dashboard |
| `/activity` | Live activity feed |
| `/agent` | IT Agent chat |
| `/system-map` | System architecture visualization |
| `/settings` | AWS/Shopify config |

---

## UI Design System (Dark Theme)

All templates extend `base.html` (dark glass theme). Key CSS variables:
- `--bg: #07091a`, `--surface: rgba(255,255,255,0.035)`, `--border: rgba(255,255,255,0.07)`
- `--purple: #7c3aed`, `--purple2: #a855f7`, `--cyan: #06b6d4`, `--pink: #ec4899`
- `--green: #10b981`, `--amber: #f59e0b`, `--red: #ef4444`
- Stat card colors: `purple`, `cyan`, `green`, `pink` (NOT blue/orange)

---

## Database

- SQLite via Peewee ORM (`email_platform.db`)
- ~60+ models in `database.py`
- 5,939 contacts, 1,904 Shopify orders
- `LearningConfig` uses key-value pattern: `LearningConfig.get_val(key, default)` / `LearningConfig.set_val(key, value)`

---

## Nightly Pipeline (scheduled in app.py)

| Time (ET) | Job |
|---|---|
| 1:00 AM | `score_all_contacts()` — AI scoring |
| 2:00 AM | `generate_daily_plan()` — daily send plan |
| 3:00 AM | Activity sync |
| 4:00 AM | Decision engine |
| 4:30 AM | Deliverability score recalculation |
| 5:00 AM | Outcome tracking |
| 5:30 AM | Learning analysis |
| 6:00 AM | Strategy optimization |

---

## Environment Variables (.env — NEVER commit)

```
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
DEFAULT_FROM_EMAIL=
SHOPIFY_STORE_URL=
SHOPIFY_ACCESS_TOKEN=
```

---

## Common Errors

- `UndefinedError` in template → Missing variable in route's `render_template()` call
- `AttributeError` on model → Field doesn't exist; check `database.py` for actual field names
- Service won't start → `journalctl -u mailengine -n 50` for error
- Route 404 → Check `app.py` for `@app.route` or `studio_routes.py` for `/studio/*`
