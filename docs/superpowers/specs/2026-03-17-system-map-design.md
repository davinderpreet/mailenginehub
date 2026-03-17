# System Architecture Map — Design Spec

**Date:** 2026-03-17
**Route:** `/system-map`
**API:** `GET /api/system-map/data`
**Stack:** D3.js force-directed graph, Flask route, Jinja2 template

---

## Overview

A live interactive network graph visualizing the entire MailEngineHub architecture. 65 draggable nodes across 7 color-coded categories connected by ~82 directional edges. Served at `/system-map` inside the existing Flask app on the VPS. Pulls real-time stats from the database every 30 seconds.

**Purpose:** Give a non-developer operator a visual understanding of what the platform does, what triggers what, and what's running right now — so they can refine the system to increase customer LTV through email marketing.

---

## Node Categories

| Category | Color Variable | Hex | Node Count |
|----------|---------------|-----|------------|
| External Sources | `--amber` | #f59e0b | 4 |
| Webhooks & Triggers | `--pink` | #ec4899 | 10 |
| Data & Enrichment | `--cyan` | #06b6d4 | 8 |
| Intelligence | `--purple` | #7c3aed | 7 |
| Execution | `--green` | #10b981 | 9 |
| Content | `--purple2` | #a855f7 | 7 |
| Learning & Tracking | `--red` | #ef4444 | 8 |
| Database Tables | `--border` (dim) | rgba(255,255,255,0.15) | 12 |

**Total: 65 nodes, ~82 edges**

---

## Complete Node List

### External Sources (4)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `shopify_store` | Shopify Store | fa-shopify | orders synced today |
| `amazon_ses` | Amazon SES | fa-envelope | delivery mode (live/shadow/sandbox) |
| `openrouter_llm` | OpenRouter LLM | fa-brain | last generation time |
| `shopify_pixel` | Shopify Pixel | fa-code | - |

### Webhooks & Triggers (10)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `wh_customer` | Shopify Customer Webhook | fa-user-plus | last fired |
| `wh_order` | Shopify Order Webhook | fa-shopping-cart | last fired |
| `wh_checkout` | Shopify Checkout Webhook | fa-credit-card | last fired |
| `wh_ses_bounce` | SES Bounce Webhook | fa-exclamation-triangle | bounces today |
| `api_track` | /api/track (behavior) | fa-eye | events today |
| `api_identify` | /api/identify (session) | fa-fingerprint | identifications today |
| `api_subscribe` | /api/subscribe (popup) | fa-bell | subscribes today |
| `checker_abandoned` | Abandoned Checkout Checker | fa-clock | every 15 min, last check |
| `checker_passive` | Passive Trigger Checker | fa-hourglass | every 30 min, last check |
| `checker_backlog` | Backlog Recovery | fa-redo | every 10 min, pending count |

### Data & Enrichment (8)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `identity_resolution` | Identity Resolution | fa-link | identities resolved today |
| `identity_queue` | Identity Job Queue | fa-tasks | pending jobs |
| `shopify_sync_nightly` | Shopify Sync (nightly) | fa-sync | last run, records synced |
| `shopify_sync_incr` | Shopify Sync (incremental) | fa-sync-alt | every 2h, last run |
| `activity_sync` | Activity Sync | fa-stream | last run, events synced |
| `shopify_enrichment` | Shopify Enrichment | fa-user-cog | profiles built |
| `knowledge_scraper` | Knowledge Scraper | fa-spider | last run, items enriched |
| `contact_db` | Contact Database | fa-database | total contacts |

### Intelligence (7)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `ai_scoring` | AI Scoring (RFM) | fa-chart-bar | contacts scored, segments |
| `customer_intelligence` | Customer Intelligence | fa-brain | lifecycle stages count |
| `next_best_message` | Next Best Message | fa-bullseye | decisions made |
| `campaign_planner` | Campaign Planner | fa-lightbulb | pending suggestions |
| `profit_engine` | Profit Engine | fa-dollar-sign | products scored |
| `deliverability_scoring` | Deliverability Scoring | fa-shield-alt | avg fatigue score |
| `cascade_engine` | Cascade Engine | fa-bolt | cascades today |

### Execution (9)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `flow_processor` | Flow Processor | fa-cogs | every 60s, active enrollments |
| `delivery_queue` | Delivery Queue | fa-paper-plane | queued count, processing rate |
| `delivery_engine` | Delivery Engine | fa-truck | emails sent today |
| `warmup_engine` | Warmup Engine | fa-fire | phase, sent/limit today |
| `condition_engine` | Condition Engine | fa-filter | - |
| `campaign_sender` | Campaign Sender | fa-bullhorn | active campaigns |
| `email_renderer` | Email Renderer | fa-paint-brush | - |
| `discount_engine` | Discount Engine | fa-tag | codes generated |
| `suppression_check` | Suppression Check | fa-ban | suppressed contacts |

### Content (7)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `template_studio` | Template Studio | fa-palette | total templates |
| `block_registry` | Block Registry | fa-th-large | block types |
| `ai_content_gen` | AI Content Generator | fa-magic | last generation |
| `ai_provider` | AI Provider (LLM) | fa-robot | model in use |
| `template_perf` | Template Performance | fa-chart-line | tracked templates |
| `template_candidates` | Template Candidates | fa-flask | pending candidates |
| `suggested_campaigns` | Suggested Campaigns | fa-lightbulb | pending suggestions |

### Learning & Tracking (8)
| ID | Label | Icon | Live Stats |
|----|-------|------|------------|
| `open_tracker` | Open Pixel Tracker | fa-envelope-open | opens today |
| `click_tracker` | Click Tracker | fa-mouse-pointer | clicks today |
| `bounce_handler` | Bounce/Complaint Handler | fa-exclamation-circle | bounces today |
| `action_ledger` | Action Ledger | fa-book | entries today |
| `outcome_tracker` | Outcome Tracker | fa-search | last run, emails analyzed |
| `learning_engine` | Learning Engine | fa-graduation-cap | phase, last run |
| `strategy_optimizer` | Strategy Optimizer | fa-chess | last run, adjustments |
| `learning_config` | Learning Config | fa-toggle-on | enabled/disabled |

### Database Tables (12 mini-nodes)
| ID | Label | Live Stats |
|----|-------|------------|
| `db_contact` | Contact | row count |
| `db_profile` | CustomerProfile | row count |
| `db_score` | ContactScore | row count |
| `db_decision` | MessageDecision | row count |
| `db_enrollment` | FlowEnrollment | active count |
| `db_campaign_email` | CampaignEmail | row count |
| `db_flow_email` | FlowEmail | row count |
| `db_shopify_order` | ShopifyOrder | row count |
| `db_bounce_log` | BounceLog | row count |
| `db_suppression` | SuppressionEntry | row count |
| `db_delivery_queue` | DeliveryQueue | row count |
| `db_warmup_config` | WarmupConfig | current phase |

---

## Edge Definitions

### Edge Types (Visual Styles)
| Type | CSS Style | Meaning |
|------|-----------|---------|
| `realtime` | Solid line, 2px, brighter opacity | Webhook/API-triggered, immediate |
| `scheduled` | Dashed line (5,5), 1.5px | Nightly cron job chain |
| `continuous` | Dotted line (2,4), 1.5px, animated | Daemon loop (every Ns) |
| `feedback` | Thin solid, 1px, dimmer | Learning feedback loop |

### Key Edge Chains

**Inbound (real-time):**
- shopify_store → wh_customer → identity_resolution → cascade_engine
- shopify_store → wh_order → identity_resolution → cascade_engine
- shopify_store → wh_checkout → checker_abandoned
- shopify_pixel → api_track → identity_queue
- shopify_pixel → api_identify → identity_resolution
- shopify_pixel → api_subscribe → identity_resolution
- amazon_ses → wh_ses_bounce → bounce_handler → db_suppression

**Cascade (real-time):**
- cascade_engine → shopify_enrichment → db_profile
- cascade_engine → ai_scoring → db_score
- cascade_engine → customer_intelligence → db_profile
- cascade_engine → next_best_message → db_decision

**Nightly pipeline (scheduled):**
- shopify_sync_nightly → activity_sync → ai_scoring → customer_intelligence → deliverability_scoring → next_best_message → campaign_planner → knowledge_scraper → profit_engine

**Learning pipeline (scheduled):**
- outcome_tracker → learning_engine → strategy_optimizer
- strategy_optimizer → next_best_message (feedback)
- strategy_optimizer → template_perf (feedback)

**Execution (continuous):**
- next_best_message → flow_processor
- flow_processor → condition_engine → email_renderer → delivery_queue
- campaign_sender → email_renderer → delivery_queue
- delivery_queue → suppression_check → warmup_engine → delivery_engine → amazon_ses

**Content:**
- campaign_planner → suggested_campaigns
- suggested_campaigns → template_studio
- template_studio → block_registry → email_renderer
- ai_content_gen → ai_provider → openrouter_llm
- template_studio → ai_content_gen
- template_studio → template_candidates
- template_perf → template_studio (feedback)

**Tracking (real-time):**
- amazon_ses → open_tracker → db_campaign_email
- amazon_ses → open_tracker → db_flow_email
- amazon_ses → click_tracker → db_campaign_email
- open_tracker → action_ledger
- click_tracker → action_ledger
- bounce_handler → action_ledger
- delivery_engine → action_ledger

**Database writes:**
- identity_resolution → db_contact
- shopify_enrichment → db_profile
- ai_scoring → db_score
- customer_intelligence → db_profile
- next_best_message → db_decision
- flow_processor → db_enrollment
- delivery_engine → db_campaign_email
- delivery_engine → db_flow_email
- wh_order → db_shopify_order
- delivery_queue → db_delivery_queue
- warmup_engine → db_warmup_config
- bounce_handler → db_bounce_log

---

## UI Layout

### Top Bar (fixed, 60px)
- Left: "System Architecture" title + "Live - Last updated 30s ago" subtitle
- Center: 7 category filter toggle pills (colored, clickable)
- Right: Search input + Reset button

### Canvas (full viewport minus top bar)
- Background: `var(--bg)` (#07091a)
- D3 force-directed graph
- Pan: click+drag background
- Zoom: scroll wheel (0.3x - 3x)
- Drag: click+drag any node

### Node Visual
- Rounded rectangle (140x70px base, scales with zoom)
- Top line: icon + bold label
- Bottom line: 1-2 stat values in dimmer text
- Border: 1px solid category color at 40% opacity
- Background: `var(--surface)` with slight category color tint
- Hover: border brightens to 100%, glow shadow
- Database mini-nodes: smaller (100x45px), dimmer border

### Edge Visual
- Curved paths (D3 linkArc)
- Arrowhead markers showing direction
- Color: source node's category color at 30% opacity
- Hover: brightens to 80%, tooltip appears

### Detail Panel (slide-out, right side, 350px)
- Triggered by clicking a node
- Dark background with border-left
- Shows: name, description, category, all live stats, connected nodes list, dashboard link
- Close button (X) top-right

---

## Backend Implementation

**Auth note:** No auth decorator needed. The global `before_request` hook (`require_auth()` in app.py lines 80-96) protects all non-public routes automatically.

### Flask Route
```python
@app.route("/system-map")
def system_map():
    return render_template("system_map.html")
```

### API Endpoint
```python
@app.route("/api/system-map/data")
def system_map_data():
    nodes = _build_system_map_nodes()   # hardcoded node defs + live stat queries
    edges = _build_system_map_edges()   # hardcoded edge defs
    return jsonify({"nodes": nodes, "edges": edges, "meta": {...}})
```

### Error Handling
Each node's stat gathering is wrapped in a per-node try/except. If a query fails (table missing, schema changed), that node's stats return `null`. The API always returns HTTP 200 with whatever data it can gather — never a 500. On the client side, `null` stats display as "--".

### Live Stat Queries (in `_build_system_map_nodes()`)
Each node's stats dict is populated by a lightweight DB query. Stats marked with * are placeholders until the underlying subsystem tracks run history — they return `null` for now and show "--".

**Available now (query existing models):**
- `Contact.select().count()`
- `DeliveryQueue.select().count()`
- `WarmupConfig.get_or_none()` → phase, emails_sent_today
- `FlowEnrollment.select().where(FlowEnrollment.status == 'active').count()`
- `BounceLog.select().where(fn.DATE(BounceLog.created_at) == today).count()`
- `ContactScore.select().count()`
- `CustomerProfile.select().count()`
- `MessageDecision.select().count()`
- `CampaignEmail.select().count()`, `FlowEmail.select().count()`
- `ShopifyOrder.select().count()`
- `SuppressionEntry.select().count()`
- `EmailTemplate.select().count()`
- `Campaign.select().where(Campaign.status == 'sending').count()`
- `LearningConfig` → enabled/disabled
- `SystemConfig` → delivery_mode

**Placeholder stats (return null / "--"):**
- `profit_engine` → "products scored" *
- `outcome_tracker` → "emails analyzed" *
- `strategy_optimizer` → "adjustments" *
- `knowledge_scraper` → "items enriched" *
- `cascade_engine` → "cascades today" *
- `ai_content_gen` → "last generation" *

No new database tables. All reads from existing models.

### Template
Single file: `templates/system_map.html`
- Extends `base.html` (preserves sidebar navigation)
- Filter pills and search go inside the `{% block content %}` area at the top, styled as a fixed controls bar above the canvas
- Canvas fills remaining viewport height using `calc(100vh - topbar - controls)`
- Loads D3.js v7 from CDN (`d3js.org`)
- All graph logic in a `<script>` block
- Polls `/api/system-map/data` every 30 seconds
- Fetch calls use default credentials (browser sends Basic Auth automatically for same-origin requests)

### Loading State
On initial page load, before the first API response arrives, the canvas shows a centered spinner with "Loading system map..." text. This prevents a blank canvas flash.

### Sidebar Entry
Added to `base.html` under SYSTEM section, after "IT Agent" and before "Settings":
```html
<a href="/system-map"><i class="fas fa-project-diagram"></i> System Map</a>
```

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `templates/system_map.html` | CREATE | Full page template with D3 graph |
| `templates/base.html` | MODIFY | Add sidebar entry |
| `app.py` | MODIFY | Add `/system-map` route + `/api/system-map/data` endpoint |

**No new Python modules. No new database tables. No new dependencies (D3 loaded from CDN).**

---

## Force Layout & Initial Positioning

The D3 force simulation uses category-based `forceX` / `forceY` position hints so the graph settles into a readable left-to-right data flow on every page load (not a random hairball).

**X-axis bands (left → right = data flow direction):**
| Category | Target X (% of width) |
|----------|----------------------|
| External Sources | 5% |
| Webhooks & Triggers | 20% |
| Data & Enrichment | 35% |
| Intelligence | 50% |
| Content | 50% (clustered with Intelligence) |
| Execution | 70% |
| Learning & Tracking | 85% |
| Database Tables | 90% |

**Y-axis:** No forced bands — nodes spread vertically via the charge repulsion force. This keeps vertical space organic.

**Force parameters:**
- `forceLink`: distance 120, strength 0.3
- `forceManyBody`: strength -300 (repulsion)
- `forceX`: strength 0.15 (gentle pull toward category band)
- `forceY`: strength 0.05 (weak centering)
- `alphaDecay`: 0.02 (settles in ~3 seconds)

This produces a stable, reproducible layout where the data flow reads naturally from left (Shopify) to right (Database/Learning).

---

## Refresh Behavior

- Page load: fetch `/api/system-map/data`, render full graph
- Every 30s: re-fetch, update stats on existing nodes (no layout reset)
- Node positions preserved during refresh (only stat text updates)
- Top bar shows "Live - Last updated Xs ago" with green pulse dot
