# System Architecture Map — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live interactive D3.js network graph at `/system-map` visualizing all 65 system components and their connections, with real-time stats from the database.

**Architecture:** Single Flask route serves a Jinja2 template that loads D3.js v7 from CDN. A JSON API endpoint (`/api/system-map/data`) returns all node definitions with live stats + edge definitions. The template polls this endpoint every 30 seconds to keep stats fresh without resetting the graph layout.

**Tech Stack:** Flask, Jinja2, D3.js v7 (CDN), Peewee ORM (existing), dark glass CSS theme (existing)

**Spec:** `docs/superpowers/specs/2026-03-17-system-map-design.md`

**Deploy:** `scp -i ~/.ssh/mailengine_vps <files> root@mailenginehub.com:/var/www/mailengine/` then `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `system_map_data.py` | CREATE | Node definitions (65), edge definitions (~59), live stat queries |
| `app.py` | MODIFY | Add `/system-map` route + `/api/system-map/data` endpoint (imports from system_map_data.py) |
| `templates/system_map.html` | CREATE | Full page: D3 force graph, controls bar, detail panel, polling logic |
| `templates/base.html` | MODIFY | Add "System Map" sidebar entry |

---

## Chunk 1: Backend API

### Task 1: Add the `/api/system-map/data` endpoint and helper functions to `app.py`

**Files:**
- Create: `system_map_data.py` — contains `_build_system_map_nodes()` and `_build_system_map_edges()` (keeps app.py from growing further past 5,700 lines)
- Modify: `app.py` — add 2 routes that import and call the builder functions from `system_map_data.py`

This is the data backbone. The API returns JSON with `nodes` (65 items, each with id/label/category/icon/stats/link) and `edges` (~59 items, each with source/target/type/tooltip). Live stats come from existing DB models wrapped in try/except.

- [ ] **Step 1: Create `system_map_data.py` with `build_system_map_nodes()` function**

Create a new file `system_map_data.py` in the repo root. This function returns a list of 65 node dicts. Each node has: `id`, `label`, `category`, `icon`, `stats` (dict), `link` (dashboard URL or null).

**Imports needed at top of `system_map_data.py`:**
```python
from datetime import datetime, date
from database import (
    Contact, EmailTemplate, Campaign, CampaignEmail,
    WarmupConfig, Flow, FlowEnrollment, FlowEmail,
    BounceLog, SuppressionEntry, ShopifyOrder,
    ContactScore, CustomerProfile, MessageDecision,
    DeliveryQueue, LearningConfig, SystemConfig,
    AbandonedCheckout, PendingTrigger, IdentityJob,
    TemplateCandidate, SuggestedCampaign
)
from peewee import fn
```

Note: `DeliveryQueue`, `LearningConfig`, and `SystemConfig` all exist in `database.py` — use them for live queries.

Stats queries are wrapped per-node in try/except — if a query fails, stats = `{}`. The function queries these existing models for live data:
- `Contact.select().count()`
- `WarmupConfig.get_or_none()` → `current_phase`, `emails_sent_today`, `daily_limit`
- `FlowEnrollment.select().where(FlowEnrollment.status == 'active').count()`
- `BounceLog.select().where(fn.DATE(BounceLog.created_at) == date.today()).count()`
- `ContactScore.select().count()`
- `CustomerProfile.select().count()`
- `MessageDecision.select().count()`
- `CampaignEmail.select().count()` and `FlowEmail.select().count()`
- `ShopifyOrder.select().count()`
- `SuppressionEntry.select().count()`
- `EmailTemplate.select().count()`
- `Campaign.select().where(Campaign.status == 'sending').count()`
- `DeliveryQueue.select().count()` (queue size)
- `LearningConfig.get_or_none()` → enabled/disabled
- `SystemConfig.get_or_none()` → delivery_mode
- `CampaignEmail.select().where(CampaignEmail.opened == True, fn.DATE(CampaignEmail.created_at) == date.today()).count()` (opens today)
- `FlowEmail.select().where(FlowEmail.opened == True, fn.DATE(FlowEmail.sent_at) == date.today()).count()` (flow opens today)
- `IdentityJob.select().where(IdentityJob.status == 'pending').count()` (pending identity jobs)
- `PendingTrigger.select().count()` (backlog triggers)

Nodes for subsystems that don't track run history (profit_engine, outcome_tracker, strategy_optimizer, knowledge_scraper, cascade_engine, ai_content_gen) return `stats: {}` — the frontend shows "--" for missing stats.

Category X-position hints (used by frontend for force layout):
```
CATEGORY_X = {
    "external": 0.05,
    "webhook": 0.20,
    "data": 0.35,
    "intelligence": 0.50,
    "content": 0.50,
    "execution": 0.70,
    "learning": 0.85,
    "database": 0.90,
}
```

Each node dict includes `"xHint": CATEGORY_X[category]` so the frontend can use it as a forceX target.

All 65 nodes must be defined following the spec's Complete Node List tables exactly (IDs, labels, icons, categories).

- [ ] **Step 2: Add `build_system_map_edges()` function to `system_map_data.py`**

Returns a list of ~59 edge dicts. Each edge: `{"source": "node_id", "target": "node_id", "type": "realtime|scheduled|continuous|feedback", "tooltip": "description"}`.

Define all edges from the spec's Edge Definitions section. Count each arrow (→) as one edge:
- Inbound: 10 edges (type=realtime) — e.g., shopify_store→wh_customer, wh_customer→identity_resolution, etc.
- Cascade: 4 edges (type=realtime)
- Nightly pipeline: 8 edges (type=scheduled) — chain of 9 nodes = 8 edges
- Learning pipeline: 3 edges (type=scheduled for first two, type=feedback for the two back-connections)
- Execution: 8 edges (type=continuous)
- Content: 7 edges (type=realtime)
- Tracking: 7 edges (type=realtime)
- Database writes: 12 edges (type=realtime)

Total: ~59 explicitly defined edges. Additional edges can be added during implementation if connections are discovered.

- [ ] **Step 3: Add the two Flask routes to `app.py`**

Add these routes to `app.py`. No new model imports needed in app.py — the builder functions handle their own imports.

```python
@app.route("/system-map")
def system_map():
    return render_template("system_map.html")

@app.route("/api/system-map/data")
def system_map_api():
    from system_map_data import build_system_map_nodes, build_system_map_edges
    nodes = build_system_map_nodes()
    edges = build_system_map_edges()
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("UTC")).astimezone(ZoneInfo("America/Toronto"))
    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "updated_at": now_et.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_nodes": len(nodes),
            "total_edges": len(edges)
        }
    })
```

No auth decorator — the global `before_request` hook handles auth for all non-public routes.

- [ ] **Step 4: Deploy and verify the API works**

Deploy both `system_map_data.py` and `app.py` to VPS:
```bash
scp -i ~/.ssh/mailengine_vps system_map_data.py root@mailenginehub.com:/var/www/mailengine/system_map_data.py
scp -i ~/.ssh/mailengine_vps app.py root@mailenginehub.com:/var/www/mailengine/app.py
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"
```

Then test the API returns valid JSON with the correct node/edge counts.

- [ ] **Step 5: Commit**

```bash
git add system_map_data.py app.py
git commit -m "feat: add /system-map route and /api/system-map/data endpoint with 65 nodes"
```

---

## Chunk 2: Frontend Template — Canvas, Nodes, Edges

### Task 2: Create `templates/system_map.html` with D3 force graph

**Files:**
- Create: `templates/system_map.html`

This is the main template. It extends `base.html` and contains all the D3.js graph logic. Due to the size (65 nodes, 82 edges, drag/zoom/filter/search/panel), this will be a substantial template.

- [ ] **Step 1: Create the template skeleton**

```
{% extends "base.html" %}
{% block title %}System Map — MailEngineHub{% endblock %}
{% block page_title %}System Architecture{% endblock %}
{% block content %}
```

**Important layout note:** The content renders inside `div.main > div.page-content` which sits to the right of the 260px sidebar. The `.page-content` div has default padding. Override this padding for the system map page to maximize canvas space:

```css
/* Override page-content padding for full-bleed canvas */
.smap-wrapper { margin: -28px -32px -28px -32px; }
```

Structure inside content block (wrapped in `.smap-wrapper`):
1. **Controls bar** (sticky div at top, ~50px): filter pills (8 category toggles), search input, reset button, live indicator ("Last updated Xs ago" + green pulse dot)
2. **SVG canvas** (fills remaining height via `calc(100vh - 115px)` — accounts for 64px topbar + 50px controls bar + 1px border)
3. **Detail panel** (absolute positioned right side, 350px, hidden by default)
4. **Loading spinner** (centered in canvas, shown until first data load)
5. **Error state** — if initial fetch fails, replace spinner with "Failed to load system map" + retry button

- [ ] **Step 2: Add the CSS styles**

All styles go in a `<style>` block inside the template (following the codebase pattern of inline styles per template). Key styles:

- `.smap-controls` — sticky bar with flex layout, gap between pills
- `.smap-pill` — category filter toggle (rounded, colored border, clickable, `.active` state)
- `.smap-search` — dark input matching `var(--surface)` background
- `.smap-canvas` — full width, calculated height, `overflow: hidden`
- `.smap-panel` — slide-out detail panel, `position: absolute; right: 0; top: 0; width: 350px; height: 100%; background: var(--bg); border-left: 1px solid var(--border); transform: translateX(100%); transition: transform 0.2s`
- `.smap-panel.open` — `transform: translateX(0)`
- `.smap-spinner` — centered loading animation
- Node styling via D3 (not CSS classes): `foreignObject` containing HTML divs for rounded rectangles with icon + label + stats
- Edge styling: SVG path with stroke-dasharray for different edge types

Color map for categories (matching spec):
```javascript
const COLORS = {
    external: "#f59e0b",
    webhook: "#ec4899",
    data: "#06b6d4",
    intelligence: "#7c3aed",
    execution: "#10b981",
    content: "#a855f7",
    learning: "#ef4444",
    database: "rgba(255,255,255,0.15)"
};
```

- [ ] **Step 3: Add the D3 graph initialization script**

`<script src="https://d3js.org/d3.v7.min.js"></script>` in the template.

Main script flow:
1. `fetchData()` — calls `/api/system-map/data`, returns `{nodes, edges, meta}`
2. `initGraph(data)` — creates SVG, defines zoom behavior, creates force simulation, renders nodes + edges
3. `updateStats(data)` — updates only the stat text on existing nodes (no layout change)
4. Start polling: `setInterval(() => fetchData().then(updateStats), 30000)`

Force simulation setup (from spec):
```javascript
const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id(d => d.id).distance(120).strength(0.3))
    .force("charge", d3.forceManyBody().strength(-300))
    .force("x", d3.forceX(d => d.xHint * width).strength(0.15))
    .force("y", d3.forceY(height / 2).strength(0.05))
    .alphaDecay(0.02);
```

Zoom behavior:
```javascript
const zoom = d3.zoom()
    .scaleExtent([0.3, 3])
    .on("zoom", (event) => container.attr("transform", event.transform));
svg.call(zoom);
```

- [ ] **Step 4: Render edges**

For each edge, render a curved SVG path with arrowhead marker:
- `realtime` — solid stroke, 2px, 50% opacity
- `scheduled` — dashed (stroke-dasharray: "5,5"), 1.5px, 40% opacity
- `continuous` — dotted (stroke-dasharray: "2,4"), 1.5px, 40% opacity
- `feedback` — solid, 1px, 25% opacity

Arrowhead markers defined in SVG `<defs>`. **Note:** `refX` must be tuned so arrows stop at the node border, not inside the foreignObject. With 140x70px nodes, start with `refX: 75` (half-width + margin) and adjust during visual testing:
```javascript
svg.append("defs").selectAll("marker")
    .data(["realtime","scheduled","continuous","feedback"])
    .join("marker")
    .attr("id", d => `arrow-${d}`)
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 75).attr("refY", 0)
    .attr("markerWidth", 6).attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path").attr("d", "M0,-5L10,0L0,5")
    .attr("fill", "#666");
```

Edge paths use `d3.linkHorizontal()` or a custom arc function for curved lines. Color = source node's category color at the specified opacity.

Hover on edge: brighten to 80% opacity, show tooltip with edge description.

- [ ] **Step 5: Render nodes**

Each node is a `foreignObject` in the SVG containing an HTML div. This gives us full control over text, icons, and styling without SVG text limitations.

Node structure (HTML inside foreignObject):
```html
<div class="smap-node" style="border-color: {categoryColor}40; background: rgba(r,g,b,0.06);">
    <div class="smap-node-header">
        <i class="fas {icon}"></i>
        <span class="smap-node-label">{label}</span>
    </div>
    <div class="smap-node-stats">{stat1} · {stat2}</div>
</div>
```

Regular nodes: 140x70px foreignObject. Database mini-nodes: 110x50px.

Node drag behavior:
```javascript
const drag = d3.drag()
    .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.1).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on("end", (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; });
```

Hover: brighten border to 100% opacity, add box-shadow glow, highlight connected edges.

Click: open detail panel (Task 3).

- [ ] **Step 6: Commit**

```bash
git add templates/system_map.html
git commit -m "feat: create system map template with D3 force graph, 65 nodes, 82 edges"
```

---

## Chunk 3: Frontend — Filters, Search, Detail Panel, Polling

### Task 3: Add filter toggles, search, detail panel, and live polling

**Files:**
- Modify: `templates/system_map.html`

- [ ] **Step 1: Implement category filter toggles**

8 pills in the controls bar (one per category). Each pill shows category name + node count. Clicking toggles visibility:

```javascript
function toggleCategory(cat) {
    activeFilters[cat] = !activeFilters[cat];
    // Update pill active state
    d3.select(`#pill-${cat}`).classed("active", activeFilters[cat]);
    // Show/hide nodes
    nodeGroups.style("display", d => activeFilters[d.category] ? null : "none");
    // Show/hide edges where source or target is in hidden category
    edgePaths.style("display", d => {
        const srcVis = activeFilters[nodesById[d.source.id || d.source].category];
        const tgtVis = activeFilters[nodesById[d.target.id || d.target].category];
        return (srcVis && tgtVis) ? null : "none";
    });
}
```

All filters start ON. Pills are colored with their category color when active, dimmed when off.

- [ ] **Step 2: Implement search**

Search input in controls bar. On keyup, filters nodes by label match (case-insensitive). Non-matching nodes dim to 15% opacity. Matching nodes stay full brightness. Clear search restores all to full opacity.

```javascript
searchInput.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    if (!q) { nodeGroups.style("opacity", 1); edgePaths.style("opacity", null); return; }
    nodeGroups.style("opacity", d => d.label.toLowerCase().includes(q) ? 1 : 0.12);
    edgePaths.style("opacity", 0.05);
});
```

- [ ] **Step 3: Implement the detail panel**

Clicking a node opens a slide-out panel on the right side (350px). Panel contains:
- Close button (X) top-right
- Node icon + label (large)
- Category badge (colored pill)
- Description text
- Stats section (all stats for this node, formatted as label: value pairs)
- "Connected To" section: list of inbound and outbound node names with edge type badges
- Dashboard link button (if node has a `link` property)

```javascript
function openPanel(node) {
    const panel = document.getElementById("detail-panel");
    panel.querySelector(".panel-title").textContent = node.label;
    panel.querySelector(".panel-icon").className = `fas ${node.icon}`;
    panel.querySelector(".panel-category").textContent = node.category;
    panel.querySelector(".panel-category").style.color = COLORS[node.category];
    // ... populate stats, connections, link
    panel.classList.add("open");
}
```

Clicking the canvas background or pressing Escape closes the panel.

- [ ] **Step 4: Implement live polling with stat refresh**

Every 30 seconds, fetch `/api/system-map/data` and update only the stats text on existing nodes. Do NOT recreate the graph or reset positions.

```javascript
let lastUpdate = Date.now();

async function refreshStats() {
    try {
        const resp = await fetch("/api/system-map/data");
        const data = await resp.json();
        // Update stats text on each node
        data.nodes.forEach(n => {
            const existing = nodesById[n.id];
            if (existing) existing.stats = n.stats;
        });
        // Re-render stat text in foreignObjects
        nodeGroups.select(".smap-node-stats").html(d => formatStats(d.stats));
        // Update header
        lastUpdate = Date.now();
        updateTimestamp();
    } catch (e) {
        console.warn("System map refresh failed:", e);
    }
}

setInterval(refreshStats, 30000);
setInterval(updateTimestamp, 1000); // Updates "Xs ago" every second
```

The live indicator in the controls bar shows a green pulsing dot + "Live · Updated Xs ago".

- [ ] **Step 5: Add the reset button**

Reset button re-centers the graph (zoom to fit) and turns all category filters back ON:
```javascript
function resetView() {
    // Reset zoom
    svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity);
    // Reset filters
    Object.keys(activeFilters).forEach(k => activeFilters[k] = true);
    d3.selectAll(".smap-pill").classed("active", true);
    nodeGroups.style("display", null).style("opacity", 1);
    edgePaths.style("display", null).style("opacity", null);
    // Clear search
    searchInput.value = "";
}
```

- [ ] **Step 6: Commit**

```bash
git add templates/system_map.html
git commit -m "feat: add filters, search, detail panel, and 30s live polling to system map"
```

---

## Chunk 4: Sidebar Entry + Deploy + Verify

### Task 4: Add sidebar entry and deploy to VPS

**Files:**
- Modify: `templates/base.html` — add sidebar link

- [ ] **Step 1: Add sidebar entry to `base.html`**

Insert after the `<a href="/agent"` link and before the `<a href="/settings"` link. Search for `IT Agent` in base.html to find the exact insertion point — do NOT rely on line numbers as they shift.

```html
    <a href="/system-map" class="nav-item {% if '/system-map' in request.path %}active{% endif %}">
      <i class="fas fa-project-diagram"></i> System Map
      <span class="nav-badge" style="background:var(--cyan);">NEW</span>
    </a>
```

- [ ] **Step 2: Deploy all files to VPS**

```bash
scp -i ~/.ssh/mailengine_vps system_map_data.py root@mailenginehub.com:/var/www/mailengine/system_map_data.py
scp -i ~/.ssh/mailengine_vps app.py root@mailenginehub.com:/var/www/mailengine/app.py
scp -i ~/.ssh/mailengine_vps templates/system_map.html root@mailenginehub.com:/var/www/mailengine/templates/system_map.html
scp -i ~/.ssh/mailengine_vps templates/base.html root@mailenginehub.com:/var/www/mailengine/templates/base.html
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"
```

- [ ] **Step 3: Verify the deployment**

1. Check service is running: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl status mailengine --no-pager | head -5"`
2. Check API returns data: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "curl -s -u user:pass http://localhost:5000/api/system-map/data | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"Nodes: {len(d[\"nodes\"])}, Edges: {len(d[\"edges\"])}\")'"`
3. Navigate to `https://mailenginehub.com/system-map` in Chrome and take a screenshot to verify the graph renders correctly
4. Verify sidebar shows "System Map" link
5. Verify node stats are populated (contact count, warmup phase, etc.)
6. Verify filter pills toggle node visibility
7. Verify clicking a node opens the detail panel
8. Wait 30 seconds and verify stats refresh without graph layout resetting

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat: add System Map sidebar entry and deploy to VPS"
```
