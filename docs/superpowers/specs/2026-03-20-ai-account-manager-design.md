# AI Account Manager — Design Spec

> **Date:** 2026-03-20
> **Status:** Approved
> **Goal:** Replace batch "score-and-blast" email marketing with per-contact AI strategist that builds 6-month conversion plans, generates daily emails, learns from human feedback, and graduates to autonomous sending.

---

## Problem

The current nightly pipeline (NBM + auto-scheduler) treats all contacts the same — score, pick an action, generate email, send. There is no long-term strategy per contact, no memory of what was tried before, no learning from rejection/approval feedback, and no human gate before sending.

## Solution

An **AI Account Manager** system where Claude acts as a personal marketing strategist for each of the ~6K contacts. It reads full profile data, competitive intelligence, and business context to build and evolve a 6-month conversion strategy per contact. Every email goes through a human approval queue (initially), and the AI learns from approvals, rejections, and edits to gradually earn autonomous sending privileges.

---

## 1. Data Models

### 1.1 ContactStrategy

Stores the AI's living strategy per contact. One row per contact.

```
ContactStrategy (database.py)  — table_name = "contact_strategies"
  contact              FK(Contact, unique, CASCADE)

  # AI-generated 6-month plan
  strategy_json        TextField(default='{}')
  # Structure:
  # {
  #   "goal": "convert_browser_to_first_purchase",
  #   "timeline_months": 6,
  #   "phases": [
  #     {"name": "Education", "months": "1-2", "tactics": [...]},
  #     {"name": "Social Proof", "months": "2-3", "tactics": [...]},
  #     ...
  #   ],
  #   "business_context": "Trucker who browsed headsets 3x...",
  #   "key_insight": "Browses on weekends, likely long-haul driver..."
  # }

  # Current state
  current_phase         CharField(default='')
  current_phase_num     IntegerField(default=1)
  next_action_date      DateTimeField(null=True)
  next_action_type      CharField(default='')

  # Learning from feedback
  total_approved        IntegerField(default=0)
  total_rejected        IntegerField(default=0)
  total_edited          IntegerField(default=0)
  rejection_reasons     TextField(default='[]')   # JSON array of feedback

  # Autonomy
  confidence_score      IntegerField(default=0)   # 0-100, rolling last 30 decisions
  autonomous            BooleanField(default=False)

  # Enrollment
  enrolled              BooleanField(default=False)  # Only enrolled contacts are processed

  # Meta
  strategy_version      IntegerField(default=1)
  created_at            DateTimeField
  updated_at            DateTimeField
  last_reviewed_at      DateTimeField(null=True)
```

### 1.2 AMPendingReview

Approval queue — emails waiting for human review. Named AMPendingReview (not AMPendingReview) to avoid confusion with DeliveryQueue which is the send queue.

```
AMPendingReview (database.py)  — table_name = "am_pending_reviews"
  contact              FK(Contact)
  strategy             FK(ContactStrategy)

  # Generated email
  subject              CharField
  preheader            CharField(default='')
  body_html            TextField
  reasoning            TextField            # AI explains why this email, why now
  strategy_context     TextField(default='') # What phase, what the plan says

  # Review
  status               CharField(default='pending')  # pending | approved | rejected | edited
  reviewer_notes       TextField(default='')
  edited_html          TextField(default='')
  edited_subject       CharField(default='')

  # Sending details
  action_type          CharField
  send_at              DateTimeField(null=True)

  created_at           DateTimeField
  reviewed_at          DateTimeField(null=True)
```

### 1.3 PromptVersion

Version-controlled editable prompts.

```
PromptVersion (database.py)  — table_name = "prompt_versions"
  prompt_key           CharField(index)    # e.g. "am_system_prompt"
  version              IntegerField
  content              TextField
  change_note          CharField(default='')
  is_active            BooleanField(default=False)
  created_at           DateTimeField
```

**Prompt keys:**
- `am_system_prompt` — AI identity & role
- `am_business_brief` — Products, competitors, upgrade paths, seasonal patterns
- `am_strategy_prompt` — How to build/update the 6-month plan
- `am_email_generation_prompt` — Copy style, length, CTA approach, discount rules
- `am_learning_prompt` — How to interpret feedback
- `am_evaluation_prompt` — How to decide "is today an action day?"

### 1.4 CompetitorProduct

Structured competitive intelligence (extracted from existing knowledge scraper data).

```
CompetitorProduct (database.py)  — table_name = "competitor_products"
  brand                CharField           # "Jabra", "BlueParrott", "Poly"
  product_name         CharField
  price                FloatField(null=True)
  key_features         TextField(default='[]')   # JSON array
  weaknesses           TextField(default='[]')   # JSON — where LDAS wins
  ldas_product_id      CharField(default='')     # Shopify product ID of LDAS equivalent
  comparison_summary   TextField(default='')     # AI-generated one-liner
  source_url           CharField(default='')
  last_scraped         DateTimeField
```

---

## 2. Core Engine — account_manager.py

New file. The AI strategist brain.

### 2.1 Nightly Flow (runs at 3:45 AM — before NBM at 4:00 AM)

The Account Manager runs before NBM so it can claim its enrolled contacts first. NBM then runs at 4:00 AM for remaining contacts as normal.

**Runtime estimate:** 100 contacts × 0.5s = ~50s. 300 contacts = ~2.5 min. 6K contacts = ~50 min (would need batching at this scale — see enrollment controls below).

```
run_account_manager():
  0. Pre-compute cross-account learnings ONCE (cached for all contacts):
     - Aggregate stats from OutcomeLog + TemplatePerformance + ActionPerformance
     - Load business brief + competitor data
     - Load editable prompts from PromptVersion (active versions, queried as
       latest version WHERE is_active=True per prompt_key)

  For each ContactStrategy WHERE enrolled=True:
    1. Skip checks (before any API call):
       - Contact unsubscribed → skip
       - Contact in SuppressionEntry → skip
       - Contact has sunset_score >= 85 → skip
       - Contact.last_reviewed_at is today → skip (already processed)

    2. Load existing strategy (or create initial if strategy_json is empty)

    3. Gather full profile:
       - Contact fields (name, email, city, country, tags)
       - CustomerProfile (purchases, browsing, price tier, intent, lifecycle, category affinity)
       - ContactScore (RFM segment, engagement, sunset score)
       - Recent CustomerActivity (page views, product views)
       - Email history (AutoEmail + CampaignEmail — sent, opened, clicked)
       - AbandonedCheckout (if exists)
       - Previous rejection/edit feedback from ContactStrategy
       - Relevant products from ProductCommercial (margins, stock, promo eligibility)
       - Relevant competitor products from CompetitorProduct

    4. Claude API call (Haiku) with assembled prompt:
       - System prompt (am_system_prompt)
       - Business brief (am_business_brief)
       - Strategy prompt (am_strategy_prompt)
       - Evaluation prompt (am_evaluation_prompt)
       - Full profile data
       - Current strategy
       - Feedback history
       - Cross-account learnings (pre-computed)

       Claude responds with JSON:
       {
         "action": "send_email" | "wait" | "update_strategy_only",
         "strategy_update": { ... } or null,
         "email": { "subject", "preheader", "body", "cta_text", "cta_url" } or null,
         "reasoning": "why this decision",
         "phase_update": "Education" or null,
         "next_action_date": "2026-03-24" or null,
         "next_action_type": "product_comparison" or null
       }

    5. Route the result:
       - "wait" → Update last_reviewed_at, save any strategy updates
       - "send_email" + autonomous=True → generate full HTML via generate_personalized_email() → enqueue to DeliveryQueue
       - "send_email" + autonomous=False → generate full HTML → create AMPendingReview row
       - "update_strategy_only" → Save updated strategy_json, bump version

    6. Rate limit: 0.5s sleep between Claude calls
```

### 2.2 Error Handling

```
Per-contact error handling:
  - Each contact is wrapped in try/except — one failure does not stop the run
  - Claude API timeout/error → log warning, skip contact, retry next night
  - Malformed JSON response → attempt json.loads with fallback regex extraction,
    if still fails → log + skip
  - Global circuit breaker: if error rate exceeds 20% of processed contacts,
    halt the run and log alert
  - All errors logged to ActionLedger with reason_code="am_error"
```

### 2.3 Enrollment Controls

```
Enrollment is managed via LearningConfig (get_val/set_val):
  - am_enrollment_mode: "manual" | "auto_high_value" | "auto_all"
    - manual: only contacts explicitly enrolled via dashboard
    - auto_high_value: auto-enroll contacts with total_spent > $50 or intent_score > 60
    - auto_all: enroll all subscribed contacts (Phase 3+)
  - am_max_daily_contacts: max contacts processed per run (default 200, prevents runaway API cost)
  - am_enabled: "true" | "false" — master switch for the nightly job
```

### 2.4 Business Knowledge Context (injected into every Claude call)

**Layer 1 — Product Catalog:**
- All LDAS products from ProductImageCache + ProductCommercial
- Includes: title, price, margin, inventory, sales velocity, promotion eligibility
- Product descriptions and tags (new fields from enhanced Shopify sync)

**Layer 2 — Competitive Intelligence:**
- CompetitorProduct table: brand, product, price, features, weaknesses, LDAS equivalent
- Comparison summaries: "Jabra 75t is $149 no ANC. LDAS X50 is $89 with ANC."

**Layer 3 — Business Brief (editable via am_business_brief prompt):**
- Who LDAS is, target market (truckers), value props
- Product upgrade paths (budget headset → pro headset → bundle)
- Reorder cycles (ear cushions ~6mo, cables ~4mo, headset ~18mo)
- Competitive positioning vs Jabra, BlueParrott, Poly, Amazon generics
- Seasonal patterns (fleet renewals, Black Friday, summer deals)

### 2.5 Cross-Account Learnings

Aggregate patterns injected into the prompt so the AI learns from all 6K contacts:

```
Built from OutcomeLog + TemplatePerformance + ActionPerformance:
- "Education emails: 42% open rate, 8% click rate, 3% conversion"
- "Discount emails on first touch: 18% open rate (avoid for new contacts)"
- "Contacts who browsed 3+ times convert 64% after product comparison email"
- "Winback emails work best 30-45 days after last activity, not 60+"
- "Morning sends (9-11am) outperform afternoon by 23%"
```

---

## 3. Approval Dashboard

### 3.1 Routes (in app.py)

| Route | Method | Purpose |
|-------|--------|---------|
| `/account-manager` | GET | Main dashboard — pending email queue |
| `/account-manager/approve/<id>` | POST | Approve a AMPendingReview → move to DeliveryQueue |
| `/account-manager/reject/<id>` | POST | Reject with reason → log feedback to ContactStrategy |
| `/account-manager/edit/<id>` | POST | Submit edit notes → regenerate or save manual edits |
| `/account-manager/regenerate/<id>` | POST | Regenerate email with feedback via Claude API |
| `/account-manager/contact/<id>` | GET | Contact drill-down — full strategy + history |
| `/account-manager/prompts` | GET/POST | Prompt editor — view/edit/version all 6 prompts |
| `/account-manager/prompts/preview` | POST | Test a prompt against a specific contact |
| `/account-manager/bulk-approve` | POST | Approve multiple selected emails at once |
| `/account-manager/settings` | GET/POST | Autonomy threshold, rollout scope |

### 3.2 Main Dashboard (account_manager.html)

**Header stats (4 cards):**
- Pending (purple) — emails awaiting review
- Approved today (green) — approved count
- Rejected today (pink) — rejected count
- AI Approval Rate (cyan) — rolling % to track autonomy readiness

**Filter bar:** All | High Value | New Browser | At Risk | Winback | By phase

**Email queue:** Cards showing:
- Contact email + name
- Current strategy phase + month
- Action type + AI reasoning
- Subject line preview
- Buttons: Preview Email | Approve | Edit | Reject

**Approve All Visible** button for bulk operations.

**AI Learning Progress banner:**
- Progress bar toward 90% approval rate target
- Count of approved / rejected / edited
- Estimated days to autonomous mode
- Enable Autonomous Mode button (greyed until 90%+)

### 3.3 Edit Modal

- Editable subject line field
- Rendered HTML email preview
- Feedback textarea ("Make CTA more urgent, subject too long")
- Buttons: Regenerate with Feedback | Edit HTML Directly | Save & Approve | Cancel

### 3.4 Reject Modal

- Rejection reason textarea ("Too pushy for first-touch")
- Rejection stored in ContactStrategy.rejection_reasons + global learnings

### 3.5 Contact Drill-Down

- Current phase card (phase name, goal, next action, confidence)
- Full 6-month strategy timeline (all phases, tactics, sent/scheduled/pending markers)
- Profile summary (lifecycle, intent, churn risk, browsing, orders, spend)
- Email history (all emails sent with open/click/approve status)
- Action buttons: Override Strategy | Pause Contact | Mark Autonomous

### 3.6 Prompt Editor Page

- Tab per prompt (System Prompt, Business Brief, Strategy, Email Style, Learning Rules, Evaluation)
- Full textarea editor with version indicator (e.g. "v1.3")
- Available variables reference panel
- Change note input on save
- Buttons: Save Draft | Save & Apply | Revert to Previous Version
- Test with Contact: pick a contact, preview what AI would generate

---

## 4. File Changes

### New Files
| File | Purpose |
|------|---------|
| `account_manager.py` | Core AI strategist engine |
| `templates/account_manager.html` | Approval dashboard + contact drill-down |
| `templates/prompt_editor.html` | Prompt configuration UI |

### Modified Files
| File | Changes |
|------|---------|
| `database.py` | Add 4 models: ContactStrategy, AMPendingReview, PromptVersion, CompetitorProduct. Add all 4 to `init_db()` create_tables list. |
| `app.py` | Add routes for /account-manager/*, prompt editor, approval actions, nightly scheduler job at 3:45 AM. Account Manager settings use `LearningConfig` (get_val/set_val) for am_enabled, am_enrollment_mode, am_max_daily_contacts. |
| `ai_engine.py` | Extend generate_personalized_email() to accept strategy context + business brief |
| `knowledge_scraper.py` | Extract structured CompetitorProduct from existing competitor_intel entries |
| `shopify_sync.py` | Pull product descriptions, tags, collections from Shopify API |

### Unchanged Files
| File | Why |
|------|-----|
| `block_registry.py` | Email rendering unchanged — account manager uses existing generate_personalized_email() |
| `delivery_engine.py` | Queue processing unchanged — approved emails go into DeliveryQueue as normal |
| `email_sender.py` | SES sending unchanged |
| `outcome_tracker.py` | Tracks opens/clicks/purchases unchanged — results feed back to ContactStrategy |
| `learning_engine.py` | Computes aggregate metrics unchanged — cross-account learnings pulled from here |
| `customer_intelligence.py` | Profile enrichment unchanged — account manager reads this data |
| `next_best_message.py` | Stays alive for contacts without ContactStrategy |

---

## 5. Pipeline Integration

### Nightly Schedule Change

```
2:30am  RFM scoring                    (unchanged)
3:30am  Customer intelligence          (unchanged)
3:45am  Account Manager                (NEW — processes enrolled contacts before NBM)
4:00am  Next Best Message              (unchanged — runs for non-enrolled contacts)
4:15am  Campaign planner               (unchanged)
4:30am  Auto-scheduler                 (unchanged — skips contacts with active ContactStrategy)
Every 30s  Delivery engine             (unchanged)
5:00am  Outcome tracker                (unchanged + feeds back to ContactStrategy)
5:30am  Learning engine                (unchanged + provides cross-account learnings)
```

### Coexistence with Existing System

- Auto-scheduler checks: if contact has a ContactStrategy, skip (account manager handles them)
- NBM continues running for all contacts (its decisions inform initial strategy creation)
- Gradual rollout: start with 50-100 contacts, expand as AI earns trust

---

## 6. Autonomy Model

### Confidence Score Calculation
- Based on a **rolling window of the last 30 decisions** (not cumulative forever)
- +3 per approval (email was good as-is)
- +1 per edit-then-approve (email needed tweaks but idea was right)
- -5 per rejection (wrong decision)
- Capped at 0-100
- Recalculated on each new decision using only the last 30 entries

### Global Autonomous Mode
- Tracks rolling approval rate across ALL contacts
- Target: 90%+ approval rate to unlock "Enable Autonomous Mode" button
- When enabled: contacts with confidence_score >= 75 auto-send
- Contacts below 75 still go to approval queue
- Dashboard always shows recently auto-sent emails for spot-checking

---

## 7. API Cost Estimate

| Scenario | Daily Calls | Cost/Day |
|----------|-------------|----------|
| 100 active contacts | ~100 Haiku calls | ~$0.10-0.30 |
| 300 active contacts | ~300 Haiku calls | ~$0.30-0.90 |
| 6K full review (unlikely) | ~6,000 calls | ~$6-18 |
| Regenerations from edits | ~10-20/day | ~$0.03-0.06 |

Most contacts will be in "wait" phases on any given day, so realistic daily cost is $0.30-1.00.

---

## 8. Rollout Plan

1. **Phase 1 — Build:** Models + engine + dashboard. Run in parallel with existing system.
2. **Phase 2 — Pilot:** Enable for 50-100 high-value or recent-browser contacts. Full manual review.
3. **Phase 3 — Scale:** Expand to all 6K contacts. AI learns from weeks of feedback.
4. **Phase 4 — Autonomy:** Enable autonomous mode after 90%+ approval rate. Spot-check dashboard.
