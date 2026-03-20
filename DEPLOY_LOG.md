# MailEngineHub — Deploy Log

Automatically updated by `deploy.sh` after each deploy.

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

