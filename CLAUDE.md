# MailEngineHub — Project Context
> Email marketing platform for LDAS Electronics. Flask + SQLite (Peewee) + Amazon SES + Gunicorn.
> 63 files, 42,114 lines. For full detail: read `REFERENCE.md`

## Deployment
- **Repo**: `C:\Users\davin\Claude Work Folder\mailenginehub-repo\`
- **VPS**: `root@mailenginehub.com:/var/www/mailengine/` | SSH: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com`
- **Deploy**: `bash deploy.sh` | **Sync from VPS**: `bash sync-from-vps.sh`
- **Live**: https://mailenginehub.com | **Auth**: admin:DavinderS@1993
- **NEVER** scp without committing | **NEVER** edit VPS without committing after
- **DO NOT USE** `email-platform\` or `mailenginehub\` folders (outdated)

## Architecture (one-liner)
```
Shopify webhooks + website pixel -> identity_resolution -> Contact/CustomerActivity
Nightly 2-6am: shopify_sync -> ai_engine(RFM) -> customer_intelligence -> next_best_message -> campaign_planner -> outcome_tracker -> learning_engine -> strategy_optimizer
Sending: enqueue -> delivery_engine(warmup) -> email_sender(SES)
Studio: knowledge + performance -> studio_skills(6 AI skills) -> candidate -> approve -> template
```

## Key Files (top 10 by importance)
| File | Lines | Role |
|------|-------|------|
| `app.py` | 7,354 | Flask application — all routes, scheduler, webhooks, auth |
| `block_registry.py` | 2,499 | Email template block rendering engine — 26 block types, validation, personalization |
| `database.py` | 1,993 | All 53 Peewee ORM models + init_db() + migration helpers |
| `account_manager.py` | 1,726 | Account Manager AI — autonomous nightly email campaign planning and execution via Claude |
| `customer_intelligence.py` | 1,429 | Nightly enrichment — lifecycle stage, customer type, intent, churn risk, send window, LTV |
| `generate-context.py` | 1,364 | Auto-generates CLAUDE.md, REFERENCE.md, MEMORY.md by scanning codebase (this file) |
| `flow_runtime.py` | 1,326 | Flow send package builder — centralizes flow render/decision logic (Phase 3) |
| `am_runtime.py` | 1,243 | AM decision engine — structured action ranking, product/offer selection, template_engine rendering (Phase 4) |
| `identity_resolution.py` | 1,084 | Cross-channel identity stitching — email, session, Shopify ID, cart/checkout token matching |
| `template_engine.py` | 1,043 | Shared template rendering & validation engine — single render path for all preview/send/studio/preflight |

## Gotchas
- `LearningConfig`: use `get_val(key, default)` / `set_val(key, value)` — NOT field access
- Card colors: purple, cyan, green, pink (NOT blue/orange)
- Delivery modes: live (SES), shadow (no SES), sandbox (5/day)
- Warmup phases: 1=50/day -> 8=unlimited
- Template families: welcome, browse_recovery, cart_recovery, checkout_recovery, post_purchase, winback, high_intent_browse, promo
- 26 block types: hero, text, cta, urgency, product_grid, product_hero, spec_table, faq, etc.
- UI: Dark glass theme, `--bg:#07091a`, `--purple:#7c3aed`, `--cyan:#06b6d4`, `--green:#10b981`, `--pink:#ec4899`
