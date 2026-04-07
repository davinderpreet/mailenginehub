# MailEngineHub — Deploy Log

Automatically updated by `deploy.sh` after each deploy.

---

### 2026-04-07 11:13 -- `0394b02`

**3-layer AM copy architecture: input filtering + named slots + output validation**

Files changed:
```
CLAUDE.md
REFERENCE.md
am_runtime.py
intelligence_layer.py
tests/test_am_runtime.py
```

---

### 2026-04-07 10:43 -- `0eebd03`

**Round 3.1: fix education seed template + AI action-type bleeding**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
am_runtime.py
```

---

### 2026-04-07 10:22 -- `4cbab28`

**Remove product_grid from loyalty seed template**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
```

---

### 2026-04-05 16:18 -- `2355670`

**Round 3 AM email quality: fix 5 root causes from inbox audit**

Files changed:
```
account_manager.py
am_runtime.py
block_registry.py
```

---

### 2026-04-05 15:27 -- `8dada99`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-04-05 10:04 -- `de9000a`

**Fix 5 AM email quality issues from inbox audit**

Files changed:
```
REFERENCE.md
am_runtime.py
block_registry.py
email_sender.py
tests/test_am_runtime.py
```

---

### 2026-04-04 15:52 -- `86792fa`

**Move AM templates to promo family for full block support**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
am_runtime.py
```

---

### 2026-04-04 15:45 -- `ae712ac`

**Fix loyalty template: replace stat_callout with features_benefits**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
```

---

### 2026-04-04 15:40 -- `497a320`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
DEPLOY_LOG.md
```

---

### 2026-04-04 15:40 -- `97bfe8e`

**Add ai_provider fallback when OpenRouter key is unavailable**

Files changed:
```
REFERENCE.md
```

---

### 2026-04-04 15:33 -- `f711b95`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-04-04 13:26 -- `eda7a3d`

**Add preview_text to AM seed templates + fix seed to persist it**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
```

---

### 2026-04-04 13:22 -- `a9210c7`

**Fix AM block override: use block_type/content keys matching block_registry**

Files changed:
```
CLAUDE.md
REFERENCE.md
am_runtime.py
```

---

### 2026-04-04 13:07 -- `a19fd29`

**Fix AM runtime blockers: timing crash + openai import fallback**

Files changed:
```
am_runtime.py
tests/test_am_runtime.py
```

---

### 2026-04-02 13:17 -- `baff8a9`

**Upgrade send window algorithm: correlate send hour → open speed across all journeys**

Files changed:
```
REFERENCE.md
customer_intelligence.py
```

---

### 2026-04-02 12:05 -- `d31701e`

**Add test inbox redirect mode for lifecycle testing**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
delivery_engine.py
templates/account_manager.html
tests/test_delivery_guards.py
```

---

### 2026-04-01 16:10 -- `50cde17`

**Fix P1: normalize strategy_phase on ready decisions, lock template during review regeneration**

Files changed:
```
CLAUDE.md
REFERENCE.md
am_runtime.py
tests/test_am_runtime.py
```

---

### 2026-04-01 12:55 -- `74fe154`

**Phase 4 AM Core: remove dead AI strategist code, deprecate old template path, add 26 behavioral tests**

Files changed:
```
REFERENCE.md
account_manager.py
am_runtime.py
tests/test_am_runtime.py
```

---

### 2026-03-31 12:55 -- `72ef221`

**Fix ShopifyOrder field name: order_total not total_price**

Files changed:
```
REFERENCE.md
customer_intelligence.py
```

---

### 2026-03-31 12:52 -- `caa60b7`

**Fix post-purchase flow bug: multi-signal Guard 2, immediate buyer state, repair utility**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
customer_intelligence.py
delivery_engine.py
tests/test_delivery_guards.py
```

---

### 2026-03-31 10:54 -- `86837dc`

**chore: add *.db-shm and *.db-wal to gitignore**

Files changed:
```
.gitignore
```

---

### 2026-03-30 16:47 -- `f05be36`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-30 14:43 -- `ac809b5`

**feat: merchandising quality upgrades — merch enrichment, multi-product expansion, smarter offer ladder**

Files changed:
```
CLAUDE.md
REFERENCE.md
flow_runtime.py
intelligence_layer.py
tests/test_flow_runtime.py
```

---

### 2026-03-30 13:44 -- `6482a8b`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-30 12:54 -- `750eba7`

**fix: commercial-grade template/validation/approve fixes**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
app.py
flow_runtime.py
migrate_templates.py
tests/test_flow_runtime.py
```

---

### 2026-03-30 11:59 -- `ae1a3d7`

**fix: restore strict validation, fix offer/product consistency properly**

Files changed:
```
CLAUDE.md
REFERENCE.md
block_registry.py
flow_runtime.py
migrate_templates.py
template_engine.py
tests/test_flow_runtime.py
```

---

### 2026-03-30 11:13 -- `1754b6e`

**fix: treat offer/product consistency as non-fatal for flow renders**

Files changed:
```
CLAUDE.md
REFERENCE.md
flow_runtime.py
```

---

### 2026-03-30 11:07 -- `41e1aaf`

**fix: timezone-naive vs aware datetime comparison in discount block rendering**

Files changed:
```
CLAUDE.md
REFERENCE.md
block_registry.py
```

---

### 2026-03-30 11:03 -- `4ad4121`

**fix: add missing ActionLedger import in _process_flow_enrollments**

Files changed:
```
REFERENCE.md
app.py
```

---

### 2026-03-30 10:57 -- `5aec814`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-29 14:29 -- `644ee89`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-29 15:13 -- `800e963`

**feat: Phase 1 (Intelligence Layer) + Phase 2 (Template Engine) architecture rebuild**

New files: shared_constants.py, intelligence_layer.py, product_intelligence.py, template_engine.py
Modified: app.py, campaign_preflight.py, studio_routes.py, customer_intelligence.py, data_enrichment.py, database.py, profit_engine.py, shopify_enrichment.py, generate-context.py
Tests: 35 template_engine + 38 intelligence_layer = 73 total

---

### 2026-03-28 12:46 -- `66ed4b9`

**fix: resolve timezone-aware vs naive datetime comparison crashing all profile pages**

Files changed:
```
REFERENCE.md
discount_engine.py
templates/profile_detail.html
```

---

### 2026-03-27 15:49 -- `a2c727a`

**feat: OpenRouter for AM, architecture fixes, cost tracking dashboard**

Files changed:
```
REFERENCE.md
account_manager.py
app.py
database.py
delivery_engine.py
discount_engine.py
requirements.txt
templates/account_manager.html
```

---

### 2026-03-24 10:18 -- `2c86cbf`

**fix: show actual send_at time for AM emails in Sent Emails page**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-24 10:13 -- `f1770e1`

**fix: import AMPendingReview and ContactStrategy in app.py**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-23 13:48 -- `5f724f6`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-23 11:32 -- `148c343`

**feat: add discount annotations and smart preview text to email rendering**

Files changed:
```
CLAUDE.md
REFERENCE.md
block_registry.py
```

---

### 2026-03-22 15:19 -- `673f26b`

**Add Shopify Custom Pixel — full checkout funnel tracking**

Files changed:
```
REFERENCE.md
app.py
normalize_activity.py
static/js/meh-pixel-shopify.js
templates/activity.html
```

---

### 2026-03-22 13:39 -- `25a7bce`

**Close 4 tracking gaps — add-to-cart, email engagement, UTM, time-on-page**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/activity.html
```

---

### 2026-03-22 13:24 -- `95c8e7e`

**Add real-time profile rebuild — debounced, session-aware**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-22 13:10 -- `cee724d`

**Fix product mismatch in emails — real-time activity lookup**

Files changed:
```
CLAUDE.md
REFERENCE.md
block_registry.py
```

---

### 2026-03-22 12:52 -- `31d8da4`

**Strip Profit Brain to essentials — full editability, no bloat**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
profit_engine.py
templates/profit_dashboard.html
```

---

### 2026-03-22 12:31 -- `c3e3b3c`

**Redesign /profits table — full names, always-visible save, full-width layout**

Files changed:
```
REFERENCE.md
templates/profit_dashboard.html
```

---

### 2026-03-22 12:22 -- `7f5f7de`

**Fix smart discount: match products by title when product_id missing**

Files changed:
```
CLAUDE.md
REFERENCE.md
profit_engine.py
```

---

### 2026-03-22 12:18 -- `02ba91b`

**Add manual cost editing + profit-aware discount escalation**

Files changed:
```
account_manager.py
app.py
discount_engine.py
profit_engine.py
templates/profit_dashboard.html
```

---

### 2026-03-22 11:41 -- `a5a8e2e`

**Fix AM templates — add real default content + update seed to refresh existing**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
```

---

### 2026-03-22 11:33 -- `9188724`

**Add /journey-preview page — render every email across all flows + AM**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/journey_preview.html
```

---

### 2026-03-22 11:21 -- `47f06dd`

**Integrate learning system into Flows + AM — data-driven decisions**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
app.py
delivery_engine.py
deploy.sh
learning_context.py
```

---

### 2026-03-22 11:01 -- `51a8970`

**Remove NBM system — Flows + AM + Campaigns are the only email senders**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
campaign_planner.py
cascade.py
outcome_tracker.py
```

---

### 2026-03-22 10:24 -- `8e6042c`

**Fix warmup/deliverability page — all cards now wired to real data**

Files changed:
```
app.py
templates/warmup.html
```

---

### 2026-03-21 16:46 -- `08e7721`

**Fix SES webhook: allow SNS messages without SigningCertURL**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-21 16:34 -- `e54aab3`

**Fix spam rate crisis: enrollment caps, subscription recheck, flow ceiling**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
delivery_engine.py
```

---

### 2026-03-21 16:02 -- `d748658`

**UI fixes: hide Telemetry nav, add Campaign stats, Template filters, fix Unknown label**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/base.html
templates/campaigns.html
templates/templates.html
```

---

### 2026-03-21 15:56 -- `8258269`

**Simplify AI Engine page — remove cruft, focus on segments and churn**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/ai_engine.html
```

---

### 2026-03-21 15:50 -- `01274cf`

**Redesign dashboard — replace BS metrics with actionable data**

Files changed:
```
REFERENCE.md
app.py
templates/dashboard.html
```

---

### 2026-03-21 14:30 -- `32d34b9`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-21 13:28 -- `c4f16a2`

**chore: add enriched contacts export to gitignore**

Files changed:
```
.gitignore
```

---

### 2026-03-21 12:57 -- `ddd4c5b`

**feat: AM emails use block templates as guardrails with AI-personalized content**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
app.py
```

---

### 2026-03-21 12:35 -- `20aabd5`

**feat: show AI Account Manager strategy on customer profile page**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/profile_detail.html
```

---

### 2026-03-21 12:30 -- `62be158`

**feat: Account Manager overhaul — lightweight nightly mode + reliability fixes**

Files changed:
```
.gitignore
CLAUDE.md
REFERENCE.md
account_manager.py
app.py
database.py
```

---

### 2026-03-21 11:29 -- `54bdb30`

**fix: discount codes — UTC timezone, Shopify sync gating, redemption tracking**

Files changed:
```
app.py
discount_engine.py
```

---

### 2026-03-20 17:13 -- `54670e4`

**Remove Auto-Scheduler + Auto-Pilot dashboard (replaced by AM)**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
migrate_to_am.py
templates/auto_pilot.html
templates/base.html
```

---

### 2026-03-20 17:00 -- `01c31de`

**Two umbrellas only: Flows or AM — disable Auto-Scheduler, add migration script**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
migrate_to_am.py
```

---

### 2026-03-20 16:49 -- `85ef4d0`

**Flow tags on contacts + always-on flow→AM handover (no setting needed)**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
app.py
delivery_engine.py
```

---

### 2026-03-20 16:40 -- `d755d1f`

**Flow → AI Account Manager handover: skip contacts mid-flow, auto-enroll after flows complete**

Files changed:
```
account_manager.py
app.py
delivery_engine.py
templates/account_manager.html
```

---

### 2026-03-20 16:24 -- `ff37dc6`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-20 15:53 -- `c7bc373`

**fix: increase max_tokens to 4000 for strategy + email response**

Files changed:
```
REFERENCE.md
account_manager.py
```

---

### 2026-03-20 15:50 -- `2c57582`

**fix: correct ShopifyOrderItem and ShopifyOrder field names**

Files changed:
```
REFERENCE.md
account_manager.py
```

---

### 2026-03-20 15:42 -- `4a3ce27`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-20 15:39 -- `809d49f`

**fix: correct field names in gather_business_context()**

Files changed:
```
CLAUDE.md
REFERENCE.md
account_manager.py
```

---

### 2026-03-20 15:36 -- `e43f802`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-20 15:34 -- `f4caea8`

**fix: remove @requires_auth decorator — app uses before_request auth**

Files changed:
```
REFERENCE.md
app.py
```

---

### 2026-03-20 13:52 -- `8b2fa4b`

**Rebuild /system-map page — clean architecture dashboard replacing D3.js node graph**

Files changed:
```
templates/system_map.html
```

---

### 2026-03-20 13:28 -- `6230df7`

**Purge ldas-electronics.com — all references now use ldas.ca**

Files changed:
```
REFERENCE.md
app.py
campaign_planner.py
templates/template_editor.html
```

---

### 2026-03-20 13:17 -- `11f5c92`

**Fix old domain in product URLs + add safety net for ldas.ca**

Files changed:
```
CLAUDE.md
REFERENCE.md
email_templates.py
```

---

### 2026-03-20 13:13 -- `f60a5d0`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-20 12:31 -- `5c23c48`

**Smart per-contact AI email personalization for nightly pipeline**

Files changed:
```
CLAUDE.md
REFERENCE.md
ai_engine.py
app.py
next_best_message.py
```

---

### 2026-03-20 12:02 -- `dc274ac`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-20 11:17 -- `8612c0e`

**Connect discount codes across all email paths — reuse existing codes**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
block_registry.py
discount_engine.py
```

---

### 2026-03-20 10:43 -- `e8b08b2`

**Add discount codes section to customer profile page**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/profile_detail.html
```

---

### 2026-03-20 10:35 -- `c8dbba4`

**Fix: force-send Step 1 path also uses unique discount codes for block templates**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 16:03 -- `c455a17`

**fix: remove invalid @requires_auth decorator — route already auth-protected by before_request**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 15:46 -- `8a4df22`

**fix: flow emails bypass warmup + dedup prevents re-queuing**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
delivery_engine.py
```

---

### 2026-03-19 15:30 -- `015de9c`

**fix: remove warmup gate from flow processor — flow emails should always enqueue, delivery engine handles limits**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 15:19 -- `f92cea2`

**fix: send Welcome Step 1 immediately for popup subscribers so discount code arrives before browse flow preempts**

Files changed:
```
identity_resolution.py
```

---

### 2026-03-19 15:03 -- `cf6f0dd`

**fix: force-send Welcome Step 1 before pausing for higher-priority flow**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 14:27 -- `0dbed3b`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-19 13:48 -- `c99a734`

**feat: Gmail Postmaster Tools dashboard + setup instructions**

Files changed:
```
REFERENCE.md
templates/warmup.html
```

---

### 2026-03-19 13:31 -- `6e9c492`

**feat: 3-tier timezone resolution — province → city → country for local send time**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 13:26 -- `cf40d64`

**feat: global ecommerce send-time curve as Tier 2 fallback for unknown contacts**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-19 13:19 -- `df566b1`

**fix: send time optimization — learn from 1 open + spread unknown contacts across business hours**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
customer_intelligence.py
```

---

### 2026-03-19 13:07 -- `78ff644`

**fix: add auto email type to sent email preview route**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-18 12:58 -- `8111b5e`

**Fix auto-scheduler: resolve discount codes, cart items, checkout URLs**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-18 12:54 -- `84b3f8b`

**Fix auto-scheduler: wrap emails in full shell with header/footer/logo**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-18 12:51 -- `3a526c0`

**Auto-Pilot UI: add template names and email preview modal**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/auto_pilot.html
```

---

### 2026-03-18 12:37 -- `757c7ac`

**Add Auto-Pilot dashboard UI page to view auto-scheduled emails**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/auto_pilot.html
templates/base.html
```

---

### 2026-03-18 12:28 -- `451f627`

**Auto-Pilot: autonomous per-contact email scheduler**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
database.py
delivery_engine.py
```

---

### 2026-03-18 12:08 -- `abc371b`

**Fix weekly trend: replace canvas with placeholder when < 2 weeks data**

Files changed:
```
REFERENCE.md
templates/learning_dashboard.html
```

---

### 2026-03-18 12:04 -- `ba4ef04`

**Fix weekly trend chart: show building state when < 2 weeks of data**

Files changed:
```
REFERENCE.md
templates/learning_dashboard.html
```

---

### 2026-03-18 11:45 -- `bd52be9`

**Add audience health, intelligence insights, and guardrail sections to learning dashboard**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
templates/learning_dashboard.html
```

---

### 2026-03-18 11:31 -- `3e69286`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-18 11:20 -- `670c133`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-18 11:11 -- `0ffaa7c`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-18 10:56 -- `87839c4`

**Wire self-learning intelligence into sending pipeline**

Files changed:
```
CLAUDE.md
REFERENCE.md
ai_engine.py
app.py
delivery_engine.py
```

---

### 2026-03-17 20:13 -- `dc6b227`

**Fix social media links in email footer**

Files changed:
```
REFERENCE.md
email_shell.py
```

---

### 2026-03-17 19:20 -- `f32c358`

**Fix cart abandonment: 5min scan interval + product personalization from pixel**

Files changed:
```
CLAUDE.md
REFERENCE.md
app.py
```

---

### 2026-03-17 19:13 -- `2f4860b`

**Fix cart_abandonment trigger alias to match checkout_abandoned flow**

Files changed:
```
app.py
```

---

### 2026-03-17 18:42 -- `42d2b12`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
REFERENCE.md
```

---

### 2026-03-17 17:15 -- `9404947`

**Slim down CLAUDE.md from 25K to 3K chars to prevent context window crashes**

Files changed:
```
CLAUDE.md
REFERENCE.md
generate-context.py
```

---

### 2026-03-17 15:21 — `6164cfd`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
CLAUDE.md
```

---

### 2026-03-17 15:13 — `ecd3c54`

**Auto-update CLAUDE.md before deploy**

Files changed:
```
CLAUDE.md
```

---

