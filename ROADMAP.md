# MailEngineHub — Improvement Roadmap
> Created: 2026-03-19 | Status: Planning | Last Updated: 2026-03-19

---

## Tier 1 — High Impact, Ship Soon
*These unlock real revenue or fix real friction. Each is a focused session.*

| # | Feature | Category | Status | Notes |
|---|---------|----------|--------|-------|
| 1 | **Auto-Pilot Actually Sends** | Revenue Engine | ⬜ Not Started | Wire Auto-Pilot so accepted Campaign Planner suggestions auto-create and send campaigns. Safety guardrails: daily limits, complaint thresholds, human approval for first N runs. Highest-ROI feature. |
| 2 | **A/B Testing for Subject Lines & Content** | Revenue Engine | ⬜ Not Started | Split-send: 2-4 variants, 10-20% test cohort, auto-pick winner by open/click rate after N hours, send winner to remainder. Feeds into self-learning loop. |
| 3 | **Campaign Creation Wizard** | UX | ⬜ Not Started | Replace single form with step-by-step wizard: Choose audience → Pick/generate template → Preview & personalize → Schedule or send. Live preview each step. |
| 4 | **Split app.py Into Blueprints** | Scale | ⬜ Not Started | 6,400+ lines in one file. Break into Flask Blueprints: core, campaign, flow, api, webhook, studio, intelligence, system. Pure refactor, no behavior change. |
| 5 | **Dashboard 2.0 — Revenue-Focused** | UX | ⬜ Not Started | Add: revenue attribution, conversion funnel (sent→opened→clicked→purchased), top campaigns, AI recommendations, "money on the table" widget from Campaign Planner. |
| 6 | **Send Time Optimization** | Revenue Engine | ⬜ Not Started | Wire existing preferred_send_hour/preferred_send_dow per contact into delivery engine. Emails go out at each contact's optimal time. Could boost opens 15-25%. |
| 7 | **Real Revenue Attribution** | Revenue Engine | ⬜ Not Started | Track which emails drive which orders. Attribute Shopify orders to last email opened/clicked within 7-day window. Show revenue per campaign, per flow, per template. |

---

## Tier 2 — Medium-Term, Strong Value
*Bigger lifts that compound over time. Build on Tier 1 foundations.*

| # | Feature | Category | Status | Notes |
|---|---------|----------|--------|-------|
| 8 | **Visual Flow Builder (Drag & Drop)** | UX | ⬜ Not Started | Replace form-based flow editor with visual canvas. Nodes for triggers, delays, emails, conditions. React Flow or Drawflow. |
| 9 | **Advanced Segment Builder** | Revenue + UX | ⬜ Not Started | Visual segment builder with AND/OR conditions: lifecycle_stage + days_since_order + total_spent + tags + engagement_score. Live count preview. Save for reuse. |
| 10 | **Email Preview (Desktop + Mobile + Dark Mode)** | UX | ⬜ Not Started | Toggle desktop/mobile/dark mode in template editor. Iframe-based rendering with device frames. |
| 11 | **AI Content Blocks That Learn** | Revenue Engine | ⬜ Not Started | Close self-learning loop: feed template performance data into AI content generation. AI gets measurably better at writing over time. |
| 12 | **Contact Timeline View** | UX | ⬜ Not Started | Visual timeline on Customer Profiles: every email, open, click, purchase, flow enrollment — chronological. Full customer journey at a glance. |
| 13 | **Flow Actions Beyond Email** | Revenue Engine | ⬜ Not Started | Add flow action types: add/remove tag, update profile, send webhook, create Shopify discount, wait for condition, branch (if/else). |
| 14 | **Campaign Analytics Dashboard** | UX | ⬜ Not Started | Post-send analytics: open rate curve, click map, revenue attributed, device breakdown, domain breakdown, unsubscribe reasons. |
| 15 | **Template Gallery with AI Generation** | UX + Revenue | ⬜ Not Started | Pre-built gallery by purpose (welcome, cart recovery, winback). One-click customize. Make discovery feel like Canva for emails. |
| 16 | **SMS Channel (Twilio/SNS)** | Revenue Engine | ⬜ Not Started | Wire up SMS via Twilio or AWS SNS. Start transactional (abandoned cart, order confirm), expand to marketing. Multi-channel = higher conversion. |
| 17 | **Migrate to PostgreSQL** | Scale | ⬜ Not Started | SQLite → PostgreSQL. Peewee makes it relatively painless. Unlocks concurrent writes, better JSON queries, full-text search. Needed at 50k+ contacts. |

---

## Tier 3 — Ambitious, Game-Changing
*These transform the platform. Build on everything above.*

| # | Feature | Category | Status | Notes |
|---|---------|----------|--------|-------|
| 18 | **Multi-Tenant SaaS Mode** | Business Model | ⬜ Not Started | User accounts, workspaces, per-tenant SES config, Stripe billing, usage limits. Tool → Product. |
| 19 | **AI Copywriter Agent** | Revenue Engine | ⬜ Not Started | Always-on agent watching knowledge base + catalog + performance data. Proactively drafts campaigns. Acts like a junior marketing hire. |
| 20 | **Predictive Send Volume Optimization** | Revenue Engine | ⬜ Not Started | ML model predicting optimal send frequency per contact. Prevent over-sending (unsubscribes) and under-sending (missed revenue). |
| 21 | **Real-Time Analytics (WebSocket)** | UX | ⬜ Not Started | Replace polling with WebSockets. Live opens, clicks, purchases streaming in. Live campaign counter. |
| 22 | **Shopify App Store Integration** | Growth | ⬜ Not Started | Package as Shopify app with OAuth install. Merchants install from app store, auto-configure webhooks. Massive distribution. |
| 23 | **AI Subject Line Generator with Prediction** | Revenue Engine | ⬜ Not Started | Generate 10 variants, score against historical data, predict performance. Combine with A/B testing to validate and improve. |
| 24 | **Dynamic Content Personalization** | Revenue Engine | ⬜ Not Started | Unique content per contact: product recs, pricing by history, tone by engagement. Every email is 1-of-1. |
| 25 | **Deliverability Intelligence Dashboard** | Scale | ⬜ Not Started | Inbox placement by domain, blacklist monitoring, bounce patterns by ISP, reputation trend. Google Postmaster Tools API. |
| 26 | **Visual Popup/Form Builder** | Growth | ⬜ Not Started | Replace hardcoded meh-popup.js with visual editor: timing rules, targeting, A/B variants, exit-intent. |
| 27 | **Public API with API Keys** | Scale | ⬜ Not Started | REST API for contacts, campaigns, templates, analytics. API key auth, rate limiting, OpenAPI docs. Enables Zapier + custom integrations. |
| 28 | **Background Worker System (Celery/RQ)** | Scale | ⬜ Not Started | Move from APScheduler to Celery/RQ + Redis. Proper job queuing, retries, monitoring for heavy AI jobs and large sends. |
| 29 | **White-Label / Agency Mode** | Business Model | ⬜ Not Started | Multi-client dashboard for agencies. Per-client branding, separate lists, shared templates, consolidated billing. |
| 30 | **Mobile Dashboard (PWA)** | UX | ⬜ Not Started | Responsive + installable PWA. Push notifications for campaign completions, anomaly alerts, daily revenue summary. |

---

## Progress Log
| Date | Item | Action | Details |
|------|------|--------|---------|
| 2026-03-19 | Roadmap | Created | 30-item prioritized roadmap from brainstorming session |

---

*Update status to: ⬜ Not Started → 🔄 In Progress → ✅ Done → ❌ Dropped*
*When starting an item, add entry to Progress Log.*
