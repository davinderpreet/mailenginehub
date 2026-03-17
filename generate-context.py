#!/usr/bin/env python3
"""
Auto-generates CLAUDE.md, MEMORY.md, and DEPLOY_LOG.md by scanning the actual codebase.
Run: python generate-context.py
Called automatically by deploy.sh before each deploy.

Produces DETAILED documentation — not just tables, but full descriptions of every
module, model, route group, data flow, and architecture pattern.
"""

import re
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


# ============================================================================
#  DETAILED KNOWLEDGE BASE (static, maintained by developers/AI)
#  These descriptions are embedded so generate-context.py can produce rich docs
#  without needing AI at generation time. Update when files change significantly.
# ============================================================================

FILE_DESCRIPTIONS = {
    "app.py": {
        "brief": "Flask application — all routes, scheduler, webhooks, auth",
        "detail": """Main Flask application with HTTP Basic Auth (admin:DavinderS@1993), APScheduler integration,
and 90+ routes organized into functional groups. Imports studio_routes as a Blueprint.
Contains all webhook handlers (SES bounce/complaint, Shopify customer/order/checkout),
tracking pixel endpoints, and the full APScheduler configuration (7 interval jobs + 12 nightly crons).
Template filters: json_filter, to_eastern. Uses Gunicorn on port 5000 behind nginx.""",
        "key_functions": [
            "dashboard() — Main dashboard with stats cards, recent activity, warmup status",
            "contacts() — Contact list with search, pagination, segment filters",
            "ses_webhook() — Processes SES bounce/complaint/delivery notifications via SNS",
            "webhook_shopify_order_create() — Shopify order webhook -> enrichment + trigger evaluation",
            "send_campaign() — Campaign send with preflight checks + delivery queue",
            "_process_flow_enrollments() — Every 60s: advance flow steps, send scheduled emails",
            "_process_delivery_queue_wrapper() — Every 30s: drain queue respecting warmup limits",
        ],
    },
    "database.py": {
        "brief": "All 53 Peewee ORM models + init_db() + migration helpers",
        "detail": """SQLite database via Peewee ORM. All models inherit BaseModel which sets the database.
init_db() creates all tables with safe=True. Models span 6 domains:
(1) Core: Contact, EmailTemplate, Campaign, CampaignEmail
(2) Flows: Flow, FlowStep, FlowEnrollment, FlowEmail
(3) Shopify: ShopifyOrder, ShopifyOrderItem, ShopifyCustomer, AbandonedCheckout
(4) Intelligence: CustomerProfile (50+ fields), ContactScore (RFM), MessageDecision
(5) AI/Studio: KnowledgeEntry, StudioJob, TemplateCandidate, AIModelConfig
(6) Learning: OutcomeLog, ActionPerformance, TemplatePerformance, ModelWeights, LearningConfig""",
    },
    "block_registry.py": {
        "brief": "Email template block rendering engine — 26 block types, validation, personalization",
        "detail": """Defines BLOCK_TYPES registry (26 types) with schema (required/optional fields, defaults, label, category).
Each block type has a dedicated renderer (_render_hero, _render_text, _render_product_grid, etc.)
that produces responsive HTML table rows for email clients. Supports {{first_name}}, {{city}},
{{total_orders}} token replacement. All rendering uses dark-theme design (bg: #0d1020).
Block categories: content (hero, text, image, divider), CTA (cta, urgency), product (product_grid,
product_hero, comparison, spec_table, bundle_value), social proof (driver_testimonial, best_seller_proof,
feature_highlights, why_choose_this, faq, stat_callout), trust (trust_reassurance, features_benefits,
objection_handling, use_case_match, whats_included, brand_story, competitor_comparison), discount.""",
        "key_functions": [
            "render_template_blocks(template, contact) — Full email render from blocks_json",
            "validate_template(blocks_json_str, family_key) — Returns warnings list",
            "render_block(block_dict, contact) — Single block -> HTML <tr>",
            "_sanitize_html(text) — XSS prevention for user content",
        ],
    },
    "condition_engine.py": {
        "brief": "Journey-aware template families, per-contact variant resolution, family constraints",
        "detail": """Implements Phase 2 conditional logic. Defines 8 TEMPLATE_FAMILIES (welcome, browse_recovery,
cart_recovery, checkout_recovery, post_purchase, winback, high_intent_browse, promo) each with
allowed_blocks, required_blocks, recommended_order, and max_blocks. Condition schema: {field, op, value}
with 9 fields (lifecycle_stage, customer_type, total_orders, total_spent, days_since_last_order,
has_used_discount, tags, source) and 7 operators (eq, neq, gt, lt, in, contains, not_contains).
Variant resolution is first-match-wins: each block can have a variants array with conditions + content override.
At send time, the engine evaluates conditions against the contact's profile to pick the right variant.""",
        "key_functions": [
            "get_contact_context(contact) — Builds flat evaluation dict from Contact + CustomerProfile",
            "evaluate_conditions(conditions, context) — AND logic, returns bool",
            "resolve_block_variants(block, context) — Returns (resolved_content, explain_dict)",
            "enforce_family_constraints(blocks, family_key) — Returns (is_valid, errors list)",
        ],
    },
    "ai_engine.py": {
        "brief": "Autonomous nightly AI pipeline — RFM scoring, Claude-powered plan generation, execution",
        "detail": """Two-phase nightly pipeline:
Phase 1 (2:30 UTC): score_all_contacts() — RFM algorithm scores every contact.
  Recency (days since last open) x0.4 + Frequency (open rate) x0.4 + Monetary (total_spent) x0.2 = 0-100.
  Assigns RFM segments: new, champion, loyal, potential, at_risk, lapsed.
Phase 2 (2:30 UTC): generate_daily_plan() — Prompts Claude with segment counts, available templates,
  template performance, and recent send history. Claude returns JSON array of actions
  [{segment, template_id, subject_override, reason, max_sends}]. Execution respects 3-day recency
  filter and daily cap (180 live, 5 sandbox). Also provides generate_personalized_email() for
  on-demand AI email composition and update_template_performance() for rolling stats.""",
        "key_functions": [
            "score_all_contacts() — RFM scoring for all contacts, updates ContactScore table",
            "generate_daily_plan() — Claude generates send plan, stores AIMarketingPlan",
            "execute_plan(plan) — Sends emails per plan actions, logs to AIDecisionLog",
            "generate_personalized_email(email, purpose) — On-demand AI email generation",
            "update_template_performance() — Rolls up open/click/revenue rates per template",
        ],
    },
    "ai_provider.py": {
        "brief": "Multi-model AI abstraction — Anthropic (Claude), OpenAI (GPT), OpenRouter (200+ models)",
        "detail": """Provider pattern: AIProvider base class with complete(system_prompt, user_prompt, max_tokens) -> str.
Three implementations: AnthropicProvider (Claude via anthropic SDK), OpenAIProvider (GPT via openai SDK),
OpenRouterProvider (Kimi K2.5 + 200+ models via OpenAI-compatible API, handles reasoning model output).
Factory function get_provider(config=None) queries AIModelConfig table for the default active model,
falls back to claude-haiku-4-5-20251001 with ANTHROPIC_API_KEY env var. All providers return plain
strings; caller handles JSON parsing. Errors raise AIProviderError(message, provider, model_id).""",
        "key_functions": [
            "get_provider(config=None) — Factory: returns provider from config or default AIModelConfig",
            "AnthropicProvider.complete() — Claude API via anthropic.Anthropic().messages.create()",
            "OpenAIProvider.complete() — GPT API via openai.OpenAI().chat.completions.create()",
            "OpenRouterProvider.complete() — OpenRouter API with reasoning model support",
        ],
    },
    "ai_content.py": {
        "brief": "AI-assisted block content authoring — field-level generation with safety caps and audit logging",
        "detail": """Phase 3 of the template architecture: AI generates content for specific block fields at send time.
Writable fields per block type: hero (headline, subheadline), text (paragraphs), cta (text),
urgency (message), discount (display_text, expires_text, value_display), product_grid (section_title).
Field length caps enforced: headline 120 chars, paragraph 500 chars. HTML/markdown stripped.
Falls back to template default if AI fails. Every generation logged to AIRenderLog for telemetry.
Also provides personalize_text_field() for lightweight per-contact personalization.""",
        "key_functions": [
            "generate_block_content(block_type, contact, family, fallback, purpose) — AI content merged with fallback",
            "personalize_text_field(field_name, template_text, contact, fallback) — Send-time personalization",
            "generate_template_content(blocks, family, contact) — Batch generation for all blocks",
        ],
    },
    "studio_skills.py": {
        "brief": "6-skill AI pipeline for template generation — block selection, content composition, validation",
        "detail": """Composable skill functions: skill_name(context: dict, provider: AIProvider) -> dict.
Context dict carries accumulated state through the pipeline: family, product_focus, tone, knowledge,
performance data, block_sequence, blocks, subject, preview_text, reasoning.
Skills: (1) select_block_sequence — AI picks 4-6 blocks within family constraints, validates against
allowed_blocks, retries 1x on failure. (2) compose_hero — headline 3-6 words + subheadline max 10 words.
(3) compose_text — 1-2 paragraphs, each 1-2 sentences. (4) compose_generic_block — handles all other
block types using BLOCK_TYPES schema. (5) compose_subject_line — subject max 50 chars + preview max 90 chars.
(6) validate_and_fix — pure Python, runs validate_template() + enforce_family_constraints(), auto-fixes
disallowed blocks and missing required fields. System prompt enforces concise copy rules: sentences
<15 words, no filler, billboard style, JSON-only output.""",
        "key_functions": [
            "select_block_sequence(context, provider) — AI selects block order within family constraints",
            "compose_hero(context, provider) — Generates hero headline + subheadline",
            "compose_text(context, provider) — Generates body copy paragraphs",
            "compose_generic_block(context, provider, block_type) — Content for any block type",
            "compose_subject_line(context, provider) — Subject line + preview text",
            "validate_and_fix(context) — Pure-Python validation + auto-fix pass",
            "_parse_json_response(text) — Handles markdown fences, reasoning model outputs",
            "_build_knowledge_summary(knowledge, block_type) — Filters relevant knowledge, truncates to 2000 chars",
        ],
    },
    "template_studio.py": {
        "brief": "Studio orchestrator — runs skill pipeline, manages jobs, approval/rejection, intelligence scoring",
        "detail": """TemplateStudio class orchestrates the full generation flow:
generate(family, product_focus, tone, model_config_id) creates a StudioJob, builds context
(knowledge entries + performance data), runs all 6 skills sequentially, saves TemplateCandidate,
marks job done/error. approve_candidate() converts candidate to standard EmailTemplate row
(blocks_json format, tagged with family). reject_candidate() stores rejection reason.
get_intelligence_score() computes 0-100 knowledge base score: product_catalog (25 pts),
brand_copy (20 pts), testimonials (15 pts), blog_posts (10 pts), competitor_intel (10 pts),
FAQs (10 pts), performance_data (10 pts). Returns breakdown + actionable suggestions.""",
        "key_functions": [
            "TemplateStudio.generate(family, product_focus, tone, model_config_id) — Full pipeline",
            "TemplateStudio.approve_candidate(candidate_id) — Candidate -> EmailTemplate",
            "TemplateStudio.reject_candidate(candidate_id, reason) — Mark rejected",
            "TemplateStudio.get_intelligence_score() — 0-100 with breakdown and suggestions",
        ],
    },
    "studio_routes.py": {
        "brief": "Flask Blueprint for /studio/* — knowledge base, generation, jobs, models, sources, scraping",
        "detail": """Blueprint registered in app.py. Route groups:
Dashboard (/studio) — intelligence score widget, recent jobs, quick actions.
Knowledge (/studio/knowledge) — CRUD for knowledge entries, type-filtered list, add/edit/delete.
Pending review (/studio/knowledge/pending) — approval queue for auto-scraped entries.
Generation (/studio/generate) — form with family, product focus, tone, model selection -> POST triggers pipeline.
Jobs (/studio/jobs, /studio/jobs/<id>) — generation job list and detail with candidate cards.
Candidates (/studio/candidates/<id>/approve|reject|preview) — approve/reject with full HTML preview.
Models (/studio/models) — AIModelConfig CRUD (add Anthropic/OpenAI/OpenRouter providers).
Sources (/studio/sources) — ScrapeSource CRUD with run/toggle/fix actions.
Scrape log (/studio/scrape-log) — historical log of scraping runs.
API (/studio/api/intelligence-score) — JSON endpoint for dashboard polling.""",
    },
    "knowledge_scraper.py": {
        "brief": "Auto-enrichment pipeline — scrapes products, blogs, competitors, FAQs into knowledge base",
        "detail": """Scheduled nightly (4:30 UTC). Scrapes configured ScrapeSource URLs:
Shopify product catalog (prices, specs, images), blog posts (custom URLs), competitor intel
(BlueParrott, Jabra, Poly product pages), email design intel (Mailchimp tips), testimonials, FAQs.
Uses AI classifier (Claude) to compute relevance_score (0-100) and categorize content.
Deduplication via content_hash prevents re-scraping. Output goes to KnowledgeEntry with
is_active=False (staged for human review via /studio/knowledge/pending). Tracks all runs
in ScrapeLog with items_found/staged/skipped/errored counts.""",
        "key_functions": [
            "run_enrichment() — Main entry: iterates active ScrapeSource rows, dispatches by type",
            "scrape_shopify_products(source) — Fetches product catalog via Shopify API",
            "scrape_blog(source) — Fetches blog post content from URL",
            "scrape_competitor(source) — Extracts product/pricing from competitor pages",
            "classify_content(text, source_type) — AI classifies and scores relevance",
        ],
    },
    "next_best_message.py": {
        "brief": "Deterministic decision engine — 10 action types, per-contact scoring with cooldowns",
        "detail": """Nightly (4:00 UTC) after intelligence. For each active contact, scores 10 action types:
reorder_reminder (purchase cycle + last sent timing), cross_sell (category diversity + recency),
upsell (AOV progression), new_product (browsing + intent), winback (churn risk + days since purchase),
education (engagement gaps), loyalty_reward (VIP status + LTV), discount_offer (discount sensitivity),
wait (fatigue/frequency cap default no-op), switch_channel (email fatigue -> SMS).
Each scorer returns (score 0-100, reason, eligible bool, rejection_reason).
Picks highest-scoring eligible action. Action-specific cooldowns enforced: reorder >=14d,
cross_sell >=21d, winback >=30d, etc. All actions + rejections logged to MessageDecision table.""",
        "key_functions": [
            "decide_for_contact(contact) — Scores all 10 actions, picks best eligible",
            "_score_reorder_reminder(contact, profile) — Purchase cycle analysis",
            "_score_cross_sell(contact, profile) — Category diversity scoring",
            "_score_winback(contact, profile) — Churn risk + days since purchase",
            "_score_wait(contact, profile) — Fatigue/frequency default action",
            "run_nightly_decisions() — Batch: decide for all active contacts",
        ],
    },
    "customer_intelligence.py": {
        "brief": "Nightly enrichment — lifecycle stage, customer type, intent, churn risk, send window, LTV",
        "detail": """Nightly (3:30 UTC). Computes complete intelligence profile per contact from all data sources:
(1) Lifecycle stage (8 states): prospect, new_customer, active_buyer, loyal, vip, at_risk, churned, reactivated.
(2) Customer type (8 types): vip, loyal, discount_seeker, repeat, one_time, browser, dormant, unknown.
(3) Intent score (0-100): website engagement, product views, search activity, time on site.
(4) Reorder likelihood (0-100): RFM-based repeat purchase probability.
(5) Churn risk (0-100): predicts abandonment based on gaps + engagement decline.
(6) Category affinity (JSON dict): scores per product category from purchases + browsing.
(7) Next purchase category (string): predicted from affinity + time patterns.
(8) Preferred send window (hour + day_of_week): when contact most likely to engage.
(9) Channel preference (email/SMS): based on engagement patterns.
(10) Intelligence summary (plain English narrative).
Data sources: Contact, CustomerProfile, CustomerActivity, ShopifyOrder, OutcomeLog.
Each field has a computed confidence level. All stored in CustomerProfile (42+ fields).""",
        "key_functions": [
            "enrich_all_contacts() — Batch enrichment for all contacts",
            "compute_lifecycle_stage(contact) — 8-state lifecycle with confidence",
            "compute_customer_type(contact) — Priority-ordered type assignment",
            "compute_intent_score(contact) — 0-100 from behavioral signals",
            "compute_churn_risk(contact) — 0-100 abandonment probability",
            "compute_category_affinity(contact) — Per-category purchase + browse scores",
            "compute_preferred_send_window(contact) — Optimal hour + day",
        ],
    },
    "identity_resolution.py": {
        "brief": "Cross-channel identity stitching — email, session, Shopify ID, cart/checkout token matching",
        "detail": """Canonical entry point for all identity resolution. resolve_identity() takes any combination
of email, session_id, shopify_id, cart_token, checkout_token and stitches to a single Contact.
Multi-identifier cascade: (1) Email match (exact), (2) Session ID match (anonymous events),
(3) Shopify ID match (webhook data), (4) Checkout/cart token match (highest confidence).
Confidence levels: exact, probable, anonymous_only. Uses durable IdentityJob queue for async
processing. Post-stitching replay: re-evaluates PendingTrigger rows (browse, cart, checkout
recovery) for newly identified contacts. Logs to ActionLedger with RC_IDENTITY_* reason codes.""",
        "key_functions": [
            "resolve_identity(email, session_id, shopify_id, ...) — Main stitching function",
            "process_identity_jobs() — Drain IdentityJob queue",
            "replay_triggers(contact) — Re-evaluate pending triggers after stitching",
        ],
    },
    "campaign_planner.py": {
        "brief": "Aggregate decisions into campaign opportunities — scoring, preflight simulation, ranking",
        "detail": """Nightly (4:15 UTC) after next-best-message. Groups MessageDecision rows by action_type
into campaign opportunities across 8 types: reorder, cross_sell, upsell, new_product, winback,
education, loyalty_reward, discount_offer. For each opportunity: computes segment size, avg
engagement score, predicted revenue (CONVERSION_RATES x segment_size x AOV), simulates preflight
(warmup headroom, fatigue, complaint risk). Quality score (0-100): segment_size (20 pts) +
avg_engagement (15 pts) + revenue (15 pts) - complaint_risk (-10 to block) - fatigue (-10 pts).
Stores SuggestedCampaign rows with quality_score, urgency, subject_line_angles, target_template_id,
accepted/dismissed flags. Dashboard shows opportunities ranked by score.""",
        "key_functions": [
            "scan_opportunities() — Group decisions into campaigns, score, rank",
            "simulate_preflight(campaign) — Check warmup headroom, fatigue, complaints",
            "compute_quality_score(opportunity) — 0-100 multi-factor score",
        ],
    },
    "delivery_engine.py": {
        "brief": "Email delivery queue — priority-based, warmup-compliant, shadow/sandbox/live modes",
        "detail": """Separates email generation from sending via DeliveryQueue model. enqueue_email() stages
emails with priority (checkout_abandoned=10 highest, contact_created=50 lowest).
process_queue() runs every 30s: drains by priority, respects warmup phase caps.
8 warmup phases: Ignition (50/day, 3d) -> Spark (150, 4d) -> Gaining Trust (350, 7d) ->
Building (750, 7d) -> Momentum (1500, 7d) -> Scaling (3000, 7d) -> High Volume (7000, 7d) ->
Full Send (999999, 99d). Delivery modes: live (send via SES), shadow (mark as shadowed, no SES),
sandbox (SES sandbox mode with 5/day cap). SystemConfig.delivery_mode controls the mode.""",
        "key_functions": [
            "enqueue_email(contact, email_type, ...) — Stage email in queue with priority",
            "process_queue() — Drain queue respecting warmup limits and delivery mode",
            "_get_warmup_remaining() — Calculate remaining daily capacity",
        ],
    },
    "email_sender.py": {
        "brief": "AWS SES integration — MIME-based, RFC 8058 one-click unsubscribe, suppression checks",
        "detail": """Sends emails via boto3 SES raw send. Builds MIME multipart (text/plain + text/html).
Injects tracking params into store links (meh_t token). Adds RFC 8058 one-click unsubscribe headers
(List-Unsubscribe, List-Unsubscribe-Post). Adds Feedback-ID and Precedence: bulk headers.
SES configuration set: mailenginehub-production. Checks SuppressionEntry table before sending
(bounces, complaints, manual suppressions). Converts HTML to plain text for alternative part.
test_ses_connection() validates SES credentials and configuration.""",
        "key_functions": [
            "send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body, ...) — Send via SES",
            "test_ses_connection(test_email) — Validate SES setup",
        ],
    },
    "email_shell.py": {
        "brief": "Universal email wrapper — LDAS-branded header, dark theme body, CAN-SPAM footer",
        "detail": """Wraps any email body HTML in the standard LDAS template: header with logo on dark gradient
background (radial blue glow), unified dark navy body (#0d1020), CAN-SPAM compliant footer
(physical address: 35 Capreol Court, Toronto, ON M5V 4B3; unsubscribe link; social links).
Responsive design: 600px container, mobile stacking. wrap_email(body_html, preview_text, unsubscribe_url)
returns a complete HTML document ready for email clients.""",
    },
    "learning_engine.py": {
        "brief": "Self-learning pipeline — template scoring, action effectiveness, optimal frequency computation",
        "detail": """Nightly (5:30 UTC). Three computations:
(1) compute_template_scoring(): Rolling 30-day performance per template -> TemplatePerformance
  (open_rate, click_rate, conversion_rate, revenue_per_send). Also per-segment breakdown ->
  TemplateSegmentPerformance.
(2) compute_action_effectiveness(): Action type performance per segment -> ActionPerformance
  (action_type, segment, sample_size, rates, revenue). Sample size threshold: 50 = full confidence.
(3) compute_optimal_frequency(): Personalized send gap per contact based on engagement history ->
  Updates ContactScore.optimal_gap_hours. Replaces static 16h cap with learned optimal timing.""",
        "key_functions": [
            "run_learning_engine() — Main entry: runs all three computations",
            "compute_template_scoring() — Rolling 30d template performance",
            "compute_action_effectiveness() — Action type performance per segment",
            "compute_optimal_frequency() — Personalized send gap per contact",
        ],
    },
    "learning_config.py": {
        "brief": "Key-value config store — learning phases, kill switches, DB-backed without restart",
        "detail": """Simple key-value store backed by LearningConfig model. No app restart needed.
get_learning_enabled() returns bool (kill switch). get_learning_phase() returns phase:
observation (<30 days OR <500 outcomes — don't act on learnings),
conservative (30-60 days OR <20 purchases — cautious adjustments),
active (>=60 days AND >=20 purchases — full optimization).
set_learning_phase_override(phase) forces a specific phase (for regression detection).
IMPORTANT: Use LearningConfig.get_val(key, default) / LearningConfig.set_val(key, value) pattern.""",
    },
    "strategy_optimizer.py": {
        "brief": "Apply learned insights — template recommendations, frequency caps, action adjustments, sunset policy",
        "detail": """Nightly (6:00 UTC). Reads learning engine outputs and applies them:
get_template_recommendations(segment) — ranked templates by performance (engagement/revenue target).
get_contact_frequency_cap(contact_id) — personalized send gap replacing static 16h cap.
get_action_score_adjustment(action_type, segment) — multiplier (strong +30%, weak -30%).
execute_sunset_policy() — marks churned contacts for final win-back with guardrails:
  observation phase = shadow only, conservative = threshold 90, active = threshold 85,
  volume cap: never sunset >2% of active list, purchase protection: skip if order in 90d,
  final email uses template id=16.""",
        "key_functions": [
            "get_template_recommendations(segment) — Ranked templates by learned performance",
            "get_contact_frequency_cap(contact_id) — Personalized send gap",
            "get_action_score_adjustment(action_type, segment) — Score multiplier",
            "execute_sunset_policy() — Guardrailed churned-contact handling",
        ],
    },
    "outcome_tracker.py": {
        "brief": "Nightly outcome collection — opened/clicked/purchased/revenue attribution for learning",
        "detail": """Nightly (5:00 UTC). Queries CampaignEmail + FlowEmail from last 48h.
Attributes purchases via last-touch within 72h window. Computes hours_to_open, hours_to_purchase.
Writes OutcomeLog entries (email_type, email_id, contact, template_id, action_type, segment,
opened, clicked, purchased, revenue, send_gap_hours). Re-checks 72h window for older emails
to catch delayed purchases. Data feeds into learning_engine.py for performance computation.""",
    },
    "profit_engine.py": {
        "brief": "Product profitability scoring — Shopify cost/inventory sync, margin computation, promo eligibility",
        "detail": """Syncs product commercial data from Shopify: cost_per_unit (from variant cost field),
inventory levels, sales velocity. Computes margin_pct per product. Margin estimates by type
when cost data unavailable: headsets 45%, dash cams 35%, accessories 55%, etc.
score_product_profitability(product_id) returns composite score: margin % + inventory level + velocity.
get_promotion_eligibility(product_id) recommends discount/promotion strategy.
Stored in ProductCommercial model (product_id, current_price, cost_per_unit, margin_pct, etc.).""",
    },
    "campaign_preflight.py": {
        "brief": "Pre-send validation — 10 checks before campaign send (warmup, complaints, fatigue, suppression)",
        "detail": """Gate function before sending any campaign. 10 checks:
(1) Warmup headroom — daily limit vs. today's sends. (2) Recipient count — >10 recommended.
(3) Subject line — exists and <100 chars. (4) Template — exists with blocks. (5) Complaint risk —
<5% safe, 5-10% warning, >10% block. (6) Fatigue — avg_fatigue <50 safe. (7) Suppression list —
excluded. (8) SPF/DKIM — configured. (9) Unsubscribe link — present. (10) Bounce domain analysis —
per-domain complaint rate. Output: PreflightLog with overall status (PASS/WARN/BLOCK) + detailed checks JSON.""",
    },
    "shopify_sync.py": {
        "brief": "Shopify customer/order sync — webhook handlers + nightly full sync + HMAC verification",
        "detail": """Two sync modes: (1) Webhook-driven real-time sync (customer create/update, order create,
checkout create) with HMAC SHA256 signature verification. (2) Nightly (2:00 UTC) full sync
via Shopify REST API — fetches all customers and orders, upserts ShopifyCustomer + ShopifyOrder
+ ShopifyOrderItem + Contact. Enriches Contact with total_orders, total_spent, first/last order dates.
Also incremental sync every 2s for recent changes.""",
    },
    "shopify_products.py": {
        "brief": "Shopify product catalog sync — ProductImageCache for email insertion",
        "detail": """sync_shopify_products() fetches all products via Shopify API, populates ProductImageCache
(product_id, product_title, image_url, product_url, price, compare_price, product_type, handle).
get_products_for_email(product_refs) returns product data formatted for email template insertion.""",
    },
    "shopify_enrichment.py": {
        "brief": "Contact enrichment from Shopify — order history, top products, cross-sell recommendations",
        "detail": """enrich_contact_from_shopify(contact) pulls full order history, computes totals,
infers category preferences from purchased products. get_top_products(contact) returns top 5
products by purchase frequency. get_product_recommendations(contact) generates cross-sell
recommendations based on category affinity + what similar customers bought.""",
    },
    "activity_sync.py": {
        "brief": "Email engagement sync — opens, clicks, unsubscribes from SES webhooks and tracking pixels",
        "detail": """Syncs email engagement events: Bounce, Complaint, Delivery, Open, Click, Send, Reject.
Updates CampaignEmail / FlowEmail with opened, opened_at, clicked, clicked_at timestamps.
Processes both SES webhook notifications and tracking pixel hits. Nightly (3:00 UTC) batch
reconciliation ensures no events missed.""",
    },
    "discount_engine.py": {
        "brief": "Dynamic discount generation — per-contact codes via Shopify price rules",
        "detail": """get_or_create_discount(email, purpose) returns a unique discount code for a contact.
Creates Shopify price rule + discount code via API if none exists. Tracks in GeneratedDiscount table.
get_discount_display(discount_info) formats for email insertion (code, expiry, value display).
Supports percentage and fixed-amount discounts with configurable expiry.""",
    },
    "cascade.py": {
        "brief": "Auto-cascade intelligence — propagate profile updates to related contacts (household, device, IP)",
        "detail": """When a CustomerProfile updates, cascade.py propagates relevant intelligence to related
contacts sharing household identifiers, device fingerprints, or IP addresses. Prevents
intelligence gaps for contacts that haven't been directly enriched yet.""",
    },
    "action_ledger.py": {
        "brief": "Comprehensive audit logging — every decision, trigger, send, and outcome recorded",
        "detail": """log_action(contact, email, trigger_type, source_type, source_id, ...) writes to ActionLedger.
Captures: trigger_type (browse, cart, checkout, tag, score_change, etc.), source_type (flow, campaign,
ai_engine, manual), status (pending, sent, failed, skipped), reason_code (RC_* constants),
template_id, enrollment_id, step_id. Every significant system action gets an audit trail entry.""",
    },
    "data_enrichment.py": {
        "brief": "General contact enrichment — activity aggregation, profile metrics computation",
        "detail": """Pulls CustomerActivity events, ShopifyOrder history, and engagement metrics
to compute and store derived fields in CustomerProfile. Bridges raw event data with
the intelligence layer.""",
    },
    "health_check.py": {
        "brief": "System health diagnostics — SES, database, Shopify, warmup status checks",
        "detail": """run_health_check() returns dict with status for each subsystem:
SES (credentials valid, send quota, bounce rate), Database (connection OK, table counts),
Shopify (API key valid, webhook registered), Warmup (phase, daily limit, health score),
Scheduler (all jobs running). Used by /settings page and monitoring.""",
    },
    "watchdog.py": {
        "brief": "Auto-restart watchdog — monitors app process, restarts on crash",
        "detail": """External process monitor that checks if the Flask app is responding.
Sends periodic health check requests to localhost:5000. If no response after retries,
triggers systemctl restart mailengine. Logs to watchdog_log.txt.""",
    },
    "system_map_data.py": {
        "brief": "System architecture visualization — 65+ nodes, relationships, stats for D3.js force graph",
        "detail": """build_system_map_nodes() returns 65+ nodes representing every system component
(routes, models, scheduled jobs, external services) with category, icon, and live stats.
build_system_map_edges() returns relationships between nodes (data flow, dependencies).
Consumed by /api/system-map/data endpoint, rendered as D3.js force-directed graph on /system-map page.""",
    },
    "token_utils.py": {
        "brief": "Signed token generation — HMAC-based tokens for tracking links and unsubscribe URLs",
        "detail": """create_token(data) generates URL-safe signed token encoding arbitrary data.
verify_token(token) decodes and verifies signature, returns data dict or None.
Used for tracking pixel URLs (/track/open/<token>), flow click tracking (/track/flow-click/<token>),
and unsubscribe links (/unsubscribe/<token>). Prevents URL tampering.""",
    },
    "email_templates.py": {
        "brief": "Seed template library — pre-built templates for each journey type in blocks_json format",
        "detail": """Defines seed templates for: welcome, browse_recovery, cart_recovery, checkout_recovery,
post_purchase, winback, loyalty, promo. Each template has subject, preview_text, and blocks_json
(hero + text + CTA + relevant blocks for the journey). Used by init_db() to populate
the EmailTemplate table on first run.""",
    },
    "flow_templates_seed.py": {
        "brief": "Seed flow definitions — pre-built automation flows with steps and timing",
        "detail": """Seed data for automation flows: Welcome Series (3 steps over 7 days),
Cart Recovery (2 steps at 1h + 24h), Post-Purchase Follow-up (2 steps at 3d + 14d),
Winback (2 steps at 30d + 60d). Each flow has trigger_type, steps with delay_hours and template.""",
    },
    "normalize_activity.py": {
        "brief": "Activity data normalization — standardizes event types and data formats",
        "detail": """Normalizes CustomerActivity event_type values and event_data JSON structure
across different sources (Shopify webhooks, tracking pixels, API events) into a
consistent schema for downstream processing by intelligence and decision engines.""",
    },
    "sns_verify.py": {
        "brief": "AWS SNS signature verification — validates webhook authenticity",
        "detail": """Verifies AWS SNS message signatures to ensure SES webhook notifications
are authentic. Downloads signing certificate, validates signature against message body.
Required for secure SES event processing (bounces, complaints, deliveries).""",
    },
    "convert_templates.py": {
        "brief": "Template migration — converts legacy HTML templates to blocks_json format",
        "detail": """Migration utility for converting old HTML-based EmailTemplate rows to the
new blocks_json format. Parses HTML structure, identifies block types (hero, text, CTA, etc.),
and generates corresponding blocks_json. One-time migration tool.""",
    },
    "create_showcase_templates.py": {
        "brief": "Showcase template generator — creates example templates demonstrating all block types",
    },
    "run.py": {
        "brief": "Application entry point — imports app, calls app.run()",
    },
    "trigger_sync.py": {
        "brief": "Trigger sync utility — manual trigger processing helper",
    },
    "discount_codes.py": {
        "brief": "Shopify discount code management — placeholder for expanded discount logic",
    },
    "generate-context.py": {
        "brief": "Auto-generates CLAUDE.md + MEMORY.md by scanning codebase (this file)",
    },
}

MODEL_DESCRIPTIONS = {
    "Contact": "Core email list entry. Every person in the system. Fields: email (unique), name, phone, tags (comma-separated), source (shopify/import/manual/api), subscribed (bool), sms_consent, Shopify enrichment fields (total_orders, total_spent, etc.). ~5,939 contacts.",
    "EmailTemplate": "Email template definition. template_format: 'blocks' (new) or 'legacy' (old HTML). blocks_json stores array of block dicts. template_family links to condition_engine families (welcome, cart_recovery, etc.). Used by campaigns and flows.",
    "Campaign": "One-time email campaign. Links to template, has segment_filter for targeting, status (draft/sending/sent). from_name/from_email for sender identity.",
    "CampaignEmail": "Per-recipient campaign send record. Tracks status (pending/sent/failed), opened/opened_at, clicked/clicked_at. FK to Campaign + Contact.",
    "WarmupConfig": "Singleton: warmup state. current_phase (1-8), emails_sent_today, daily reset tracking, SPF/DKIM/DMARC check flags, custom_daily_limit override.",
    "WarmupLog": "Daily warmup metrics log. One row per day with phase, daily_limit, emails_sent, emails_opened, emails_bounced.",
    "Flow": "Automation flow definition. trigger_type (contact_created, tag_added, checkout_abandoned, etc.), trigger_value, is_active, priority (lower = higher priority for dedup).",
    "FlowStep": "Step within a flow. step_order, delay_hours from trigger/previous step, template FK, optional from_name/from_email/subject_override.",
    "FlowEnrollment": "Contact's position in a flow. current_step index, next_send_at timestamp, status (active/completed/cancelled), paused_by_flow flag.",
    "FlowEmail": "Per-step flow email send record. FK to enrollment + step + contact. Tracks status, sent_at, opened, clicked.",
    "AbandonedCheckout": "Shopify abandoned checkout. shopify_checkout_id, email, checkout_url, total_price, line_items_json, recovered (bool), recovery timestamps.",
    "AgentMessage": "IT Agent chat history. role (user/assistant/system), content, tool_calls JSON. Powers /agent chat interface.",
    "CustomerProfile": "Intelligence profile per contact (50+ fields). Computed nightly by customer_intelligence.py. Includes: lifecycle_stage, customer_type, intent_score, churn_risk, reorder_likelihood, category_affinity_json, next_purchase_category, preferred_send_hour, preferred_send_day, channel_preference, intelligence_summary, LTV estimate, and confidence scores for each.",
    "ContactScore": "RFM scoring per contact. rfm_segment (new/champion/loyal/potential/at_risk/lapsed), engagement_score (0-100), recency_days, frequency_rate, monetary_value, optimal_gap_hours (learned send frequency).",
    "MessageDecision": "Per-contact next-best-message decision. action_type (reorder_reminder/cross_sell/etc.), action_score, reason, ranked_actions_json (all 10 actions scored), rejections_json (why each was rejected).",
    "MessageDecisionHistory": "Audit log of MessageDecision executions. Adds decision_date, execution status, outcome tracking.",
    "KnowledgeEntry": "AI knowledge base. entry_type: product_catalog, brand_copy, blog_post, competitor_intel, faq, testimonial, email_design_intel. metadata_json holds source_url, relevance_score, image_urls, reasoning. is_active=False means staged for review.",
    "AIModelConfig": "AI provider configuration. provider (anthropic/openai/openrouter), model_id, api_key_env (env var name), max_tokens, is_default flag. Queried by ai_provider.get_provider().",
    "StudioJob": "AI template generation job. status: pending/running/done/error. family (welcome/cart_recovery/etc.), input_json (product_focus, tone), FK to AIModelConfig.",
    "TemplateCandidate": "AI-generated template awaiting review. blocks_json (standard format), subject_line, preview_text, reasoning (AI explanation), status: pending/approved/rejected. FK to StudioJob, optional FK to EmailTemplate (set on approval).",
    "TemplatePerformance": "Rolling template metrics. sends, opens, clicks, open_rate, click_rate, revenue_total, revenue_per_send. Computed nightly by learning_engine.py.",
    "OutcomeLog": "Email outcome for learning. email_type (campaign/flow), opened, clicked, purchased (bool), revenue (float), send_gap_hours. Feeds into learning_engine.py.",
    "ActionPerformance": "Action type effectiveness per segment. sample_size, open_rate, click_rate, conversion_rate, revenue_per_send. Computed by learning_engine.py.",
    "TemplateSegmentPerformance": "Template performance broken down by contact segment. Enables segment-specific template recommendations.",
    "ModelWeights": "Computed optimal RFM weights. recency/frequency/monetary weights, evaluation_score, sample_size, phase. Updated by learning_engine.py.",
    "LearningConfig": "Key-value config store. IMPORTANT: Use LearningConfig.get_val(key, default) / set_val(key, value). NOT direct field access. Controls learning phases, kill switches, thresholds.",
    "DeliveryQueue": "Email staging queue. Priority-based (checkout_abandoned=10 highest). Drained every 30s by delivery_engine respecting warmup limits.",
    "ActionLedger": "Audit trail for every system action. trigger_type, source_type, source_id, status, reason_code, template_id. Comprehensive logging.",
    "IdentityJob": "Durable job queue for identity resolution. dedupe_key prevents duplicates, status (pending/processing/done/failed), result JSON.",
    "SuggestedCampaign": "Campaign opportunity from planner. campaign_type, quality_score (0-100), urgency, segment_size, eligible_contacts_json, predicted revenue, subject_line_angles.",
    "ProductCommercial": "Product profitability data. current_price, cost_per_unit, margin_pct, inventory, sales_velocity, promotion_eligibility. Synced from Shopify.",
    "ScrapeSource": "Knowledge enrichment source. source_type (shopify_products/blog/competitor/etc.), URL, scrape_frequency, is_active, last_scraped_at, config_json.",
    "ScrapeLog": "Scrape run audit log. items_found, items_staged, items_skipped, items_errored, error_message. FK to ScrapeSource.",
    "RejectionLog": "Rejected knowledge entries. Tracks what was rejected and why, prevents re-processing same content.",
    "ShopifyOrder": "Shopify order record. Full order data: order_total, subtotal, discount, tax, line items via ShopifyOrderItem FK. Synced via webhook + nightly.",
    "ShopifyOrderItem": "Line item within a ShopifyOrder. product_id, variant_id, sku, quantity, unit_price, discount, product_type.",
    "ShopifyCustomer": "Shopify customer record. shopify_id, orders_count, total_spent, tags, accepts_marketing. FK to Contact.",
    "CustomerActivity": "Behavioral event log. event_type (page_view, product_view, add_to_cart, search, checkout_start, etc.), event_data JSON, session_id, timestamps.",
    "SuppressionEntry": "Email suppression list. reason (bounce/complaint/manual), source, detail. Checked before every send.",
    "BounceLog": "Detailed bounce analysis. event_type (Bounce/Complaint), sub_type, diagnostic, recipient_domain. Used for deliverability scoring.",
    "AIGeneratedEmail": "History of AI-generated emails. purpose, subject, body, reasoning, profile_snapshot. Audit trail for AI sends.",
    "AIMarketingPlan": "Nightly AI plan. plan_json (array of actions), total_sends, status, ai_summary. Generated by ai_engine.py.",
    "AIDecisionLog": "Per-action audit for AI plan execution. plan FK, contact, template_id, segment, status (sent/skipped/failed).",
    "GeneratedDiscount": "Per-contact discount code. code, purpose, value, Shopify price_rule_id, expiry. Created by discount_engine.py.",
    "PreflightLog": "Campaign preflight results. overall (PASS/WARN/BLOCK), checks_json with per-check details.",
    "ProductImageCache": "Product image cache for email templates. product_id, image_url, product_url, price. Synced by shopify_products.py.",
    "SystemConfig": "Global system config. delivery_mode: live/shadow/sandbox. Controls whether emails actually send.",
    "AIRenderLog": "AI content rendering telemetry. template_id, block_index, field_name, render_ms, fallback_used. Powers /telemetry dashboard.",
    "OpportunityScanLog": "Nightly opportunity scan results. opportunities_found, total_eligible_contacts, scan_duration.",
    "PendingTrigger": "Unprocessed behavioral trigger. trigger_type (browse/cart/checkout), trigger_data, status. Processed by _check_passive_triggers every 30s.",
    "OmnisendOrder": "Legacy Omnisend order data. Imported during migration from Omnisend. Read-only historical data.",
    "OmnisendOrderItem": "Legacy Omnisend order line item. Historical data from migration.",
}

ROUTE_GROUPS = {
    "Dashboard & Overview": {
        "desc": "Main dashboard, system monitoring, and reporting pages",
        "routes": {
            "/": "Main dashboard — stat cards (contacts, campaigns, open rate, revenue), recent activity feed, warmup status, quick actions",
            "/activity": "Activity feed — real-time log of all system events (sends, opens, clicks, bounces, triggers)",
            "/system-map": "Interactive D3.js force graph — 65+ nodes showing all system components and data flow",
            "/telemetry": "AI rendering telemetry — success rates, latency, field-specific performance metrics",
            "/audit": "Audit dashboard — ActionLedger viewer with filtering by trigger type, source, status",
        },
    },
    "Contacts & Profiles": {
        "desc": "Contact management, import, Shopify sync, and customer intelligence profiles",
        "routes": {
            "/contacts": "Contact list — search, pagination, segment filters (all/subscribed/unsubscribed), import CSV button",
            "/contacts/import-csv": "CSV import handler — maps columns to Contact fields, deduplicates by email",
            "/contacts/sync-shopify": "Trigger Shopify customer sync — calls shopify_sync.sync_shopify_customers()",
            "/profiles": "Intelligence profiles list — all contacts with CustomerProfile data, search, lifecycle filters",
            "/profiles/<id>": "Profile detail — 50+ intelligence fields, purchase history, engagement timeline, AI email preview, quick send",
        },
    },
    "Email Templates": {
        "desc": "Template creation and editing (legacy HTML + blocks-based)",
        "routes": {
            "/templates": "Template library — list all templates with family tags, format badges, send counts",
            "/templates/new": "Create legacy HTML template (form-based)",
            "/templates/new-blocks": "Create blocks-based template — opens template builder",
            "/templates/<id>/edit": "Edit legacy template (form-based)",
            "/templates/<id>/edit-blocks": "Edit blocks template — opens template builder with existing blocks",
        },
    },
    "Campaigns": {
        "desc": "One-time email campaigns with preflight checks and sending",
        "routes": {
            "/campaigns": "Campaign list — all campaigns with status badges, send counts, open rates",
            "/campaigns/new": "Create campaign — select template, segment filter, from name/email",
            "/campaigns/<id>": "Campaign detail — recipient list, per-email status, open/click tracking",
            "/campaigns/<id>/send": "Send campaign — runs preflight checks, enqueues to delivery engine",
        },
    },
    "Automation Flows": {
        "desc": "Multi-step automated email sequences triggered by events",
        "routes": {
            "/flows": "Flow list — all flows with trigger types, step counts, enrollment stats, active toggle",
            "/flows/new": "Create flow — set trigger type (contact_created, tag_added, checkout_abandoned, etc.)",
            "/flows/<id>": "Flow detail — step timeline, enrollment list, per-step stats, add/delete steps",
            "/flows/<id>/toggle": "Enable/disable flow",
            "/flows/<id>/enroll-test": "Manually enroll a contact for testing",
        },
    },
    "AI Engine": {
        "desc": "Autonomous AI scoring, plan generation, and learning system",
        "routes": {
            "/ai-engine": "AI Engine dashboard — segment distribution, today's plan, decision log, run-now button",
            "/learning": "Learning dashboard — phase indicator, template performance, action effectiveness, model weights",
            "/campaign-planner": "Campaign planner — suggested campaigns from opportunity scanner, accept/dismiss",
            "/profits": "Profit dashboard — product profitability scores, margin analysis, promo eligibility",
            "/agent": "IT Agent chat — Claude-powered assistant for system questions",
        },
    },
    "AI Template Studio": {
        "desc": "AI-powered template generation with knowledge base and approval workflow",
        "routes": {
            "/studio": "Studio dashboard — intelligence score widget, recent jobs, quick generate",
            "/studio/knowledge": "Knowledge base — entries by type (products, brand copy, testimonials, etc.), add/edit/delete",
            "/studio/knowledge/pending": "Pending review queue — auto-scraped entries awaiting approval/rejection",
            "/studio/generate": "Generation form — select family, product focus, tone, AI model",
            "/studio/jobs": "Job list — all generation jobs with status, family, timestamps",
            "/studio/jobs/<id>": "Job detail — candidate cards with preview, approve/reject, AI reasoning",
            "/studio/models": "AI model config — add/manage Anthropic, OpenAI, OpenRouter providers",
            "/studio/sources": "Knowledge sources — scrape source URLs, run/toggle, frequency config",
            "/studio/scrape-log": "Scrape log — historical scraping runs with found/staged/skipped/errored counts",
        },
    },
    "Warmup & Delivery": {
        "desc": "IP warmup management and delivery settings",
        "routes": {
            "/warmup": "Warmup dashboard — 8-phase progress, daily stats chart, health score, checklist, domain analysis",
            "/settings": "Settings — delivery mode (live/shadow/sandbox), SES test, general config",
            "/sent-emails": "Sent email log — all sent emails across campaigns + flows, preview, status",
        },
    },
    "Webhooks & Tracking": {
        "desc": "Inbound webhooks from SES and Shopify, plus email engagement tracking",
        "routes": {
            "/webhooks/ses": "SES webhook — processes bounce/complaint/delivery/open/click notifications via SNS",
            "/webhooks/shopify/customer/create": "Shopify customer create webhook — upserts Contact + ShopifyCustomer",
            "/webhooks/shopify/customer/update": "Shopify customer update webhook — updates Contact fields",
            "/webhooks/shopify/order/create": "Shopify order webhook — creates ShopifyOrder, enriches Contact, triggers flows",
            "/webhooks/shopify/checkout/create": "Shopify checkout webhook — creates AbandonedCheckout for recovery flows",
            "/track/open/<token>": "Open tracking pixel — 1x1 transparent GIF, logs open to CampaignEmail/FlowEmail",
            "/track/flow-click/<token>": "Flow click tracking — redirects to target URL, logs click event",
        },
    },
    "API Endpoints": {
        "desc": "JSON API endpoints for AJAX calls, external integrations, and JavaScript-driven pages",
        "routes": {
            "/api/activity/feed": "Activity feed JSON — paginated events for activity page auto-refresh",
            "/api/agent/chat": "Agent chat API — sends message to Claude, returns response",
            "/api/ai-engine/run-now": "Trigger AI engine manually — runs scoring + plan generation",
            "/api/ai-engine/sample-email": "Generate sample AI email — preview without sending",
            "/api/campaign/recipient-count": "Count recipients for a segment filter — used by campaign form",
            "/api/identify": "Identity pixel — JavaScript tracking pixel for website visitor identification",
            "/api/subscribe": "Public subscribe endpoint — CORS-enabled for external forms",
            "/api/track": "Event tracking API — receives behavioral events from website JavaScript",
            "/api/system-map/data": "System map JSON — 65+ nodes and edges for D3.js visualization",
            "/api/learning/stats": "Learning stats JSON — for dashboard auto-refresh",
            "/api/telemetry/data": "Telemetry JSON — AI render stats for telemetry page auto-refresh",
            "/api/warmup/health": "Warmup health JSON — for warmup dashboard auto-refresh",
            "/api/templates/<id>/preview-blocks": "Render block template to HTML — for preview panel",
            "/api/templates/<id>/save-blocks": "Save blocks_json — from template builder drag-and-drop",
            "/api/templates/ai-generate-block": "AI generate single block content — for template builder",
            "/api/templates/ai-generate-template": "AI generate full template — for template builder",
        },
    },
}

TEMPLATE_DESCRIPTIONS = {
    "base.html": "Master layout — dark glass theme, sidebar navigation (all page links), topbar, CSS variables, Font Awesome icons, jQuery. All other templates extend this.",
    "dashboard.html": "Main dashboard — 4 stat cards (contacts, campaigns, open rate, revenue), recent activity table, warmup status card, quick action buttons.",
    "contacts.html": "Contact list — search bar, segment filter tabs, paginated table (email, name, source, subscribed, orders, spent), import CSV modal, Shopify sync button.",
    "profiles.html": "Intelligence profiles — search, lifecycle stage filter pills, sortable table (email, lifecycle, type, intent, churn risk, LTV, last decision).",
    "profile_detail.html": "Full contact profile (67KB) — intelligence summary card, lifecycle/type/intent/churn badges, purchase history timeline, engagement chart, category affinity radar, AI email preview modal, quick send form, decision history table.",
    "campaigns.html": "Campaign list — table with status badges (draft/sending/sent), send counts, open/click rates.",
    "campaign_form.html": "Create/edit campaign — template selector, segment filter builder, from name/email, reply-to.",
    "campaign_detail.html": "Campaign detail — recipient table with per-email status, opened/clicked indicators, error messages.",
    "campaign_planner.html": "AI campaign planner — suggested campaign cards with quality scores, accept/dismiss buttons, brief preview modal.",
    "templates.html": "Template library — cards with family badge, format badge (blocks/legacy), preview thumbnail, send count, edit/delete.",
    "template_builder.html": "Block template editor (35KB) — drag-and-drop block builder, live preview panel, block palette, property inspector, AI generate button.",
    "template_editor.html": "Legacy HTML template editor — code textarea, subject/preview_text fields, test send.",
    "flows.html": "Flow list — cards with trigger type icon, step count, enrollment count, active toggle, priority control.",
    "flow_detail.html": "Flow detail (19KB) — visual step timeline, per-step stats (sent/opened/clicked), enrollment table, add step form.",
    "ai_engine.html": "AI Engine dashboard (28KB) — segment distribution pie chart, today's plan table, decision log with filters, run-now button, sample email generator.",
    "learning_dashboard.html": "Learning dashboard (24KB) — phase indicator (observation/conservative/active), template performance table, action effectiveness heatmap, model weights display, toggle button.",
    "profit_dashboard.html": "Profit dashboard — product profitability table, margin analysis, promo eligibility recommendations.",
    "warmup.html": "Warmup dashboard (43KB) — 8-phase progress bar, daily send/open/bounce chart, health score gauge, deliverability checklist, domain analysis table, phase advance button.",
    "settings.html": "Settings — delivery mode selector (live/shadow/sandbox), SES test send, system config.",
    "sent_emails.html": "Sent email log — filterable table (campaign/flow emails), preview link, status, open/click timestamps.",
    "activity.html": "Activity feed — real-time event log with type filters, auto-refresh via /api/activity/feed polling.",
    "audit.html": "Audit dashboard — ActionLedger viewer with date range, trigger type, source filters, detail modal.",
    "telemetry.html": "Telemetry — 4 stat cards (total renders, success rate, fallback rate, avg latency), family performance table, field breakdown table. Auto-refreshes every 30s.",
    "agent.html": "IT Agent chat — ChatGPT-style interface, message bubbles, input field, sends to /api/agent/chat.",
    "system_map.html": "System map (43KB) — D3.js force graph, category filter pills, search, node detail panel, 65+ component nodes with live stats.",
    "unsubscribe.html": "Public unsubscribe page — standalone (no base.html), confirms unsubscribe action.",
    "studio/dashboard.html": "Studio dashboard — intelligence score gauge (0-100), per-category progress bars, recent jobs table, quick generate button.",
    "studio/generate.html": "Generation form — family dropdown, product focus input, tone selector, AI model dropdown, generate button.",
    "studio/job.html": "Job detail — candidate cards with subject line, block pills, AI reasoning expandable, approve/reject buttons, preview link.",
    "studio/jobs.html": "Jobs list — table with status badges (pending/running/done/error), family, timestamps, candidate counts.",
    "studio/knowledge.html": "Knowledge base — type filter tabs, entry cards with content preview, edit/delete, add form modal.",
    "studio/models.html": "AI models — add model form (provider/model_id/api_key_env/max_tokens), models table with default/active badges.",
    "studio/pending.html": "Pending review — entry cards with type badge, relevance score (color-coded), content preview, AI reasoning, approve/reject buttons.",
    "studio/sources.html": "Knowledge sources — source cards with type/URL, run/toggle buttons, add source form, run-all button.",
    "studio/scrape_log.html": "Scrape log — table with source name, status badge, found/staged/skipped/errored counts, error message tooltip.",
}

NIGHTLY_PIPELINE_DETAIL = """
### Nightly Pipeline (2:00-6:00 UTC) — Execution Order

Each job depends on the outputs of previous jobs. Order matters.

| Time | Job | What It Does | Depends On | Output |
|------|-----|-------------|------------|--------|
| 2:00 | `_run_nightly_shopify_sync` | Full customer + order sync from Shopify API | Shopify API | ShopifyCustomer, ShopifyOrder rows |
| 2:30 | `_run_nightly_contact_scoring` | RFM scoring: recency x0.4 + frequency x0.4 + monetary x0.2 | Contact, engagement data | ContactScore (segment, score 0-100) |
| 3:00 | `_run_nightly_activity_sync` | Batch reconcile email engagement events | SES webhooks, tracking pixels | CampaignEmail/FlowEmail updates |
| 3:30 | `_run_nightly_intelligence` | Compute 10 intelligence fields per contact | ContactScore, ShopifyOrder, CustomerActivity | CustomerProfile (50+ fields) |
| 3:45 | `_recalculate_deliverability_scores` | Bounce/complaint rate analysis per domain | BounceLog, CampaignEmail | Domain-level health metrics |
| 4:00 | `_run_nightly_decisions` | Score 10 action types per contact, pick best | CustomerProfile, ContactScore | MessageDecision per contact |
| 4:15 | `_run_nightly_opportunity_scan` | Group decisions into campaign opportunities | MessageDecision | SuggestedCampaign rows |
| 4:30 | `_run_nightly_knowledge_enrichment` | Scrape configured sources for knowledge base | ScrapeSource URLs | KnowledgeEntry (staged) |
| 4:45 | `_run_nightly_profit_scoring` | Sync product costs/inventory, compute margins | Shopify product API | ProductCommercial rows |
| 5:00 | `_run_outcome_tracker` | Attribute opens/clicks/purchases to emails | CampaignEmail, ShopifyOrder | OutcomeLog rows |
| 5:30 | `_run_learning_engine` | Compute template/action performance, optimal frequency | OutcomeLog | TemplatePerformance, ActionPerformance |
| 6:00 | `_run_strategy_optimizer` | Apply learnings: template recs, frequency caps, sunset | Learning outputs | Updated scoring parameters |

### Continuous Jobs (every 2-60 seconds)

| Interval | Job | Purpose |
|----------|-----|---------|
| 2s | `_run_incremental_shopify_sync` | Real-time Shopify order/customer changes |
| 10s | `_recover_pending_backlog` | Process stale PendingTrigger rows |
| 15s | `_check_abandoned_checkouts` | Detect new abandoned checkouts for recovery |
| 30s | `_process_delivery_queue_wrapper` | Drain DeliveryQueue respecting warmup limits |
| 30s | `_check_passive_triggers` | Evaluate browse/intent triggers against contacts |
| 30s | `_process_identity_jobs_wrapper` | Process IdentityJob queue (stitching) |
| 60s | `_process_flow_enrollments` | Advance flow steps, send scheduled flow emails |
"""

ARCHITECTURE_OVERVIEW = """
### How Data Flows Through the System

```
                    EXTERNAL
    Shopify ──webhook──> app.py ──> identity_resolution ──> Contact
    Website ──pixel────> app.py ──> CustomerActivity
    SES ────webhook────> app.py ──> BounceLog / CampaignEmail updates

                    NIGHTLY PIPELINE (2am-6am UTC)
    shopify_sync ──> Contact enrichment
    ai_engine ──────> ContactScore (RFM segments)
    customer_intelligence ──> CustomerProfile (50+ fields)
    next_best_message ──────> MessageDecision (best action per contact)
    campaign_planner ───────> SuggestedCampaign (opportunities)
    outcome_tracker ────────> OutcomeLog (learning data)
    learning_engine ────────> TemplatePerformance, ActionPerformance
    strategy_optimizer ─────> Applied learnings (frequency caps, template recs)

                    SENDING PIPELINE
    Campaign/Flow/AI Plan ──> delivery_engine.enqueue_email()
    delivery_engine.process_queue() ──> email_sender.send_campaign_email()
    email_sender ──> AWS SES ──> Recipient inbox
    (respects warmup phase limits, delivery mode, suppression list)

                    AI TEMPLATE STUDIO
    KnowledgeEntry (products, brand copy, etc.)
    + TemplatePerformance (what works)
    ──> studio_skills pipeline (6 skills)
    ──> TemplateCandidate (pending review)
    ──> Human approves ──> EmailTemplate (in library)
```

### Key Design Principles
- **Modular pipeline**: Each phase isolated (scoring -> intelligence -> decision -> campaign -> send)
- **Audit-heavy**: ActionLedger captures every decision point
- **Learning loop**: OutcomeLog -> learning_engine -> strategy_optimizer -> better decisions
- **Guardrailed AI**: Learning phases (observation/conservative/active) with auto-rollback
- **Human-in-the-loop**: Studio templates require approval before entering library
- **Warmup-compliant**: All sending respects IP warmup phase limits
"""


# ============================================================================
#  EXTRACTION FUNCTIONS (dynamic — scan actual code)
# ============================================================================

def extract_routes(filepath, prefix=""):
    """Extract @app.route or @bp.route entries from a Python file."""
    routes = []
    if not filepath.exists():
        return routes
    content = filepath.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'\s*@\w+\.route\("([^"]+)"(?:,\s*methods=\[([^\]]+)\])?\)', line)
        if m:
            path = prefix + m.group(1)
            methods = m.group(2) or '"GET"'
            methods = methods.replace('"', '').replace("'", "")
            func_name = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                fm = re.match(r'\s*def (\w+)\(', lines[j])
                if fm:
                    func_name = fm.group(1)
                    break
            routes.append({
                "path": path,
                "methods": methods,
                "func": func_name,
                "line": i + 1,
            })
    return routes


def extract_models(filepath):
    """Extract Peewee model classes and their fields."""
    models = []
    if not filepath.exists():
        return models
    content = filepath.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    current_model = None
    current_fields = []
    current_line = 0

    for i, line in enumerate(lines):
        cm = re.match(r'^class (\w+)\((\w+)\):', line)
        if cm and cm.group(2) in ("BaseModel", "Model"):
            if current_model:
                models.append({"name": current_model, "fields": current_fields, "line": current_line})
            current_model = cm.group(1)
            current_fields = []
            current_line = i + 1
            continue

        if current_model:
            fm = re.match(r'\s+(\w+)\s*=\s*(CharField|TextField|IntegerField|FloatField|BooleanField|DateTimeField|DateField|ForeignKeyField|AutoField|DecimalField|BigIntegerField|SmallIntegerField)\b', line)
            if fm:
                current_fields.append(fm.group(1))
            elif re.match(r'^class \w+', line) or (re.match(r'^\S', line) and not line.startswith('#') and line.strip()):
                if current_model != "BaseModel":
                    models.append({"name": current_model, "fields": current_fields, "line": current_line})
                current_model = None
                current_fields = []
                cm2 = re.match(r'^class (\w+)\((\w+)\):', line)
                if cm2 and cm2.group(2) in ("BaseModel", "Model"):
                    current_model = cm2.group(1)
                    current_fields = []
                    current_line = i + 1

    if current_model and current_model != "BaseModel":
        models.append({"name": current_model, "fields": current_fields, "line": current_line})

    return models


def extract_scheduled_jobs(filepath):
    """Extract APScheduler job definitions."""
    jobs = []
    if not filepath.exists():
        return jobs
    content = filepath.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'_scheduler\.add_job\((\w+),\s*"(\w+)"(?:,\s*(?:seconds|minutes|hours)=(\d+)|,\s*hour=(\d+),\s*minute=(\d+))', content):
        func = m.group(1)
        sched_type = m.group(2)
        if sched_type == "interval":
            interval_val = m.group(3)
            jobs.append({"func": func, "schedule": f"every {interval_val}s", "type": "interval"})
        elif sched_type == "cron":
            hour = m.group(4)
            minute = m.group(5)
            jobs.append({"func": func, "schedule": f"{hour}:{minute.zfill(2)} UTC", "type": "cron"})
    return jobs


def extract_templates(templates_dir):
    """List all templates with their extends/blocks."""
    templates = []
    if not templates_dir.exists():
        return templates
    for f in sorted(templates_dir.rglob("*.html")):
        rel = f.relative_to(templates_dir)
        size = f.stat().st_size
        content = f.read_text(encoding="utf-8", errors="replace")
        extends = ""
        em = re.search(r'{%\s*extends\s*["\']([^"\']+)', content)
        if em:
            extends = em.group(1)
        templates.append({"name": str(rel), "size": size, "extends": extends})
    return templates


def get_file_stats():
    """Get line counts for key files."""
    stats = []
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith(("test_", "preview_", "investigate_", "inspect_", "patch_", "phase1_", "i_", "i4_", "i9_", "fix_", "check_", "show_", "diagnose_", "backfill_")):
            continue
        lines = len(f.read_text(encoding="utf-8", errors="replace").split("\n"))
        stats.append({"name": f.name, "lines": lines})
    return stats


def get_git_log(n=10):
    """Get recent git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}", "--format=%h %ai %s"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        return []


# ============================================================================
#  DOCUMENT GENERATORS
# ============================================================================

def generate_claude_md():
    """Generate slim CLAUDE.md (~2.5K chars). Full detail lives in REFERENCE.md."""

    file_stats = get_file_stats()
    total_lines = sum(s["lines"] for s in file_stats)

    # Top 10 files by size
    top_files = sorted(file_stats, key=lambda x: -x["lines"])[:10]
    file_table = ""
    for s in top_files:
        name = s["name"]
        info = FILE_DESCRIPTIONS.get(name, {})
        brief = info.get("brief", "")
        if brief:
            file_table += f"| `{name}` | {s['lines']:,} | {brief} |\n"

    doc = f"""# MailEngineHub — Project Context
> Email marketing platform for LDAS Electronics. Flask + SQLite (Peewee) + Amazon SES + Gunicorn.
> {len(file_stats)} files, {total_lines:,} lines. For full detail: read `REFERENCE.md`

## Deployment
- **Repo**: `C:\\Users\\davin\\Claude Work Folder\\mailenginehub-repo\\`
- **VPS**: `root@mailenginehub.com:/var/www/mailengine/` | SSH: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com`
- **Deploy**: `bash deploy.sh` | **Sync from VPS**: `bash sync-from-vps.sh`
- **Live**: https://mailenginehub.com | **Auth**: admin:DavinderS@1993
- **NEVER** scp without committing | **NEVER** edit VPS without committing after
- **DO NOT USE** `email-platform\\` or `mailenginehub\\` folders (outdated)

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
{file_table}
## Gotchas
- `LearningConfig`: use `get_val(key, default)` / `set_val(key, value)` — NOT field access
- Card colors: purple, cyan, green, pink (NOT blue/orange)
- Delivery modes: live (SES), shadow (no SES), sandbox (5/day)
- Warmup phases: 1=50/day -> 8=unlimited
- Template families: welcome, browse_recovery, cart_recovery, checkout_recovery, post_purchase, winback, high_intent_browse, promo
- 26 block types: hero, text, cta, urgency, product_grid, product_hero, spec_table, faq, etc.
- UI: Dark glass theme, `--bg:#07091a`, `--purple:#7c3aed`, `--cyan:#06b6d4`, `--green:#10b981`, `--pink:#ec4899`
"""

    return doc


def generate_reference_md():
    """Generate full-detail REFERENCE.md with model fields, function signatures, etc."""

    app_routes = extract_routes(ROOT / "app.py")
    studio_routes = extract_routes(ROOT / "studio_routes.py", prefix="/studio")
    all_routes = app_routes + studio_routes
    models = extract_models(ROOT / "database.py")
    templates = extract_templates(ROOT / "templates")
    file_stats = get_file_stats()
    total_lines = sum(s["lines"] for s in file_stats)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = f"""# MailEngineHub -- Full Reference
> Auto-generated on {now}. This file is NOT loaded into conversation context.
> Read on-demand when you need model fields, function signatures, or file details.

---

## Database Models ({len(models)} models) — Full Field Lists

"""

    for m in models:
        name = m["name"]
        if name == "BaseModel":
            continue
        fields_str = ", ".join(m["fields"])
        desc = MODEL_DESCRIPTIONS.get(name, "")
        doc += f"### `{name}` (line {m['line']})\n"
        if desc:
            doc += f"{desc}\n"
        doc += f"- **Fields**: {fields_str}\n\n"

    doc += f"""---

## Python Files — Detailed ({len(file_stats)} files, {total_lines:,} lines)

"""

    for s in sorted(file_stats, key=lambda x: -x["lines"]):
        name = s["name"]
        info = FILE_DESCRIPTIONS.get(name, {})
        brief = info.get("brief", "")
        detail = info.get("detail", "")
        key_funcs = info.get("key_functions", [])

        doc += f"### `{name}` ({s['lines']:,} lines)\n"
        if brief:
            doc += f"**{brief}**\n\n"
        if detail:
            doc += f"{detail.strip()}\n\n"
        if key_funcs:
            doc += "Key functions:\n"
            for kf in key_funcs:
                doc += f"- `{kf}`\n"
            doc += "\n"

    doc += f"""---

## Routes — Full Detail ({len(all_routes)} total)

"""

    for group_name, group_info in ROUTE_GROUPS.items():
        doc += f"### {group_name}\n{group_info['desc']}\n\n"
        doc += "| Route | Methods | Function | Line | Description |\n"
        doc += "|---|---|---|---|---|\n"
        for r in sorted(all_routes, key=lambda x: x["path"]):
            if r["path"] in group_info["routes"]:
                desc = group_info["routes"][r["path"]]
                doc += f"| `{r['path']}` | {r['methods']} | `{r['func']}` | {r['line']} | {desc} |\n"
        doc += "\n"

    listed = set()
    for g in ROUTE_GROUPS.values():
        listed.update(g["routes"].keys())
    unlisted = [r for r in all_routes if r["path"] not in listed]
    if unlisted:
        doc += "### Other Routes\n\n| Route | Methods | Function | Line |\n|---|---|---|---|\n"
        for r in sorted(unlisted, key=lambda x: x["path"]):
            doc += f"| `{r['path']}` | {r['methods']} | `{r['func']}` | {r['line']} |\n"
        doc += "\n"

    doc += f"""---

## HTML Templates ({len(templates)} files)

"""

    for t in templates:
        name = t["name"]
        size_kb = f"{t['size'] / 1024:.1f}KB"
        desc = TEMPLATE_DESCRIPTIONS.get(name, "")
        doc += f"- **`{name}`** ({size_kb}, extends {t['extends'] or 'none'})"
        if desc:
            doc += f" -- {desc}"
        doc += "\n"

    return doc


def generate_memory_md():
    """Generate brief MEMORY.md (~25 lines). Loaded into every conversation context."""

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = f"""# MailEngineHub -- Project Memory
> Updated {now}. Full docs: CLAUDE.md (concise) and REFERENCE.md (detailed) in mailenginehub-repo.

## Deployment (MUST FOLLOW)
- **Repo**: `C:\\Users\\davin\\Claude Work Folder\\mailenginehub-repo\\`
- **Deploy**: `cd mailenginehub-repo && bash deploy.sh`
- **Sync from VPS**: `bash sync-from-vps.sh`
- **DO NOT USE** `email-platform\\` or `mailenginehub\\` folders (outdated)
- Never scp without committing. Never edit VPS without committing after.

## Quick Reference
- **Stack**: Flask + SQLite (Peewee) + Amazon SES + Gunicorn
- **VPS**: `root@mailenginehub.com:/var/www/mailengine/` (SSH key: `~/.ssh/mailengine_vps`)
- **GitHub**: https://github.com/davinderpreet/mailenginehub
- **Live**: https://mailenginehub.com | **Auth**: admin:DavinderS@1993
- **Restart**: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"`

## Key Gotchas
- `LearningConfig`: use `get_val(key, default)` / `set_val(key, value)` — NOT field access
- Card colors: purple, cyan, green, pink (NOT blue/orange)
- Delivery modes: live (SES), shadow (no SES), sandbox (5/day)
- SES: sandbox mode (awaiting production). Warmup: Phase 1, 50/day
"""

    return doc


def append_deploy_log(commit_hash, commit_msg):
    """Append to DEPLOY_LOG.md."""
    log_path = ROOT / "DEPLOY_LOG.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not log_path.exists():
        header = "# MailEngineHub -- Deploy Log\n\nAutomatically updated by `deploy.sh` after each deploy.\n\n---\n\n"
        log_path.write_text(header, encoding="utf-8")

    existing = log_path.read_text(encoding="utf-8", errors="replace")

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        changed = result.stdout.strip()
    except Exception:
        changed = "(unknown)"

    entry = f"""### {now} -- `{commit_hash}`

**{commit_msg}**

Files changed:
```
{changed}
```

---

"""

    parts = existing.split("---\n\n", 1)
    if len(parts) == 2:
        new_content = parts[0] + "---\n\n" + entry + parts[1]
    else:
        new_content = existing + "\n" + entry

    log_path.write_text(new_content, encoding="utf-8")
    return log_path


if __name__ == "__main__":
    import sys

    print("Scanning codebase...")

    # Warn about files/models missing from description dictionaries
    file_stats = get_file_stats()
    for fs in file_stats:
        if fs["name"] not in FILE_DESCRIPTIONS:
            print(f"  [WARN] New file '{fs['name']}' has no entry in FILE_DESCRIPTIONS — add one for detailed context!")
    models = extract_models(ROOT / "database.py")
    for m in models:
        if m["name"] != "BaseModel" and m["name"] not in MODEL_DESCRIPTIONS:
            print(f"  [WARN] New model '{m['name']}' has no entry in MODEL_DESCRIPTIONS — add one for detailed context!")

    # Generate CLAUDE.md (with size guard — must stay under 5K to avoid bloating context)
    MAX_CLAUDE_MD = 5_000
    claude_md = generate_claude_md()
    if len(claude_md) > MAX_CLAUDE_MD:
        print(f"  [WARN] CLAUDE.md is {len(claude_md):,} chars (limit {MAX_CLAUDE_MD:,})")
        print(f"         Truncating to stay within context budget.")
        # Truncate at the last complete section before the limit
        truncated = claude_md[:MAX_CLAUDE_MD]
        last_section = truncated.rfind("\n---\n")
        if last_section > MAX_CLAUDE_MD // 2:
            claude_md = truncated[:last_section] + "\n\n---\n\n> **Truncated** — full detail in REFERENCE.md\n"
        else:
            claude_md = truncated + "\n\n> **Truncated** — full detail in REFERENCE.md\n"
    out_path = ROOT / "CLAUDE.md"
    out_path.write_text(claude_md, encoding="utf-8")
    print(f"  [OK] CLAUDE.md generated ({len(claude_md):,} chars)")

    # Generate REFERENCE.md (full detail, NOT loaded into context)
    reference_md = generate_reference_md()
    ref_path = ROOT / "REFERENCE.md"
    ref_path.write_text(reference_md, encoding="utf-8")
    print(f"  [OK] REFERENCE.md generated ({len(reference_md):,} chars)")

    # Generate MEMORY.md
    memory_md = generate_memory_md()
    memory_path = Path.home() / ".claude" / "projects" / "C--Users-davin-Claude-Work-Folder" / "memory" / "MEMORY.md"
    if memory_path.parent.exists():
        memory_path.write_text(memory_md, encoding="utf-8")
        print(f"  [OK] MEMORY.md generated ({len(memory_md):,} chars)")
    else:
        print(f"  [SKIP] MEMORY.md path not found: {memory_path.parent}")

    # If called with --deploy <hash> <msg>, also update deploy log
    if len(sys.argv) >= 4 and sys.argv[1] == "--deploy":
        commit_hash = sys.argv[2]
        commit_msg = " ".join(sys.argv[3:])
        append_deploy_log(commit_hash, commit_msg)
        print(f"  [OK] DEPLOY_LOG.md updated")

    print("Done.")
