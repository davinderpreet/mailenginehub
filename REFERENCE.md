# MailEngineHub -- Full Reference
> Auto-generated on 2026-03-19 16:02. This file is NOT loaded into conversation context.
> Read on-demand when you need model fields, function signatures, or file details.

---

## Database Models (55 models) — Full Field Lists

### `Contact` (line 22)
Core email list entry. Every person in the system. Fields: email (unique), name, phone, tags (comma-separated), source (shopify/import/manual/api), subscribed (bool), sms_consent, Shopify enrichment fields (total_orders, total_spent, etc.). ~5,939 contacts.
- **Fields**: email, first_name, last_name, phone, tags, source, subscribed, sms_consent, created_at, shopify_id, city, country, total_orders, total_spent, fatigue_score, spam_risk_score, suppression_reason, suppression_source, suppression_until, last_open_at, last_click_at, emails_received_7d, emails_received_30d

### `EmailTemplate` (line 66)
Email template definition. template_format: 'blocks' (new) or 'legacy' (old HTML). blocks_json stores array of block dicts. template_family links to condition_engine families (welcome, cart_recovery, etc.). Used by campaigns and flows.
- **Fields**: name, subject, preview_text, html_body, shell_version, template_format, blocks_json, template_family, ai_enabled, block_ai_overrides, created_at, updated_at

### `Campaign` (line 84)
One-time email campaign. Links to template, has segment_filter for targeting, status (draft/sending/sent). from_name/from_email for sender identity.
- **Fields**: name, from_name, from_email, reply_to, template_id, segment_filter, status, created_at, sent_at

### `CampaignEmail` (line 99)
Per-recipient campaign send record. Tracks status (pending/sent/failed), opened/opened_at, clicked/clicked_at. FK to Campaign + Contact.
- **Fields**: campaign, contact, status, error_msg, opened, opened_at, clicked, clicked_at, created_at

### `WarmupConfig` (line 114)
Singleton: warmup state. current_phase (1-8), emails_sent_today, daily reset tracking, SPF/DKIM/DMARC check flags, custom_daily_limit override.
- **Fields**: is_active, warmup_started_at, current_phase, emails_sent_today, last_reset_date, check_spf, check_dkim, check_dmarc, check_sandbox, check_list_cleaned, check_subdomain

### `WarmupLog` (line 133)
Daily warmup metrics log. One row per day with phase, daily_limit, emails_sent, emails_opened, emails_bounced.
- **Fields**: log_date, phase, daily_limit, emails_sent, emails_opened, emails_bounced

### `Flow` (line 146)
Automation flow definition. trigger_type (contact_created, tag_added, checkout_abandoned, etc.), trigger_value, is_active, priority (lower = higher priority for dedup).
- **Fields**: name, description, trigger_type, trigger_value, is_active, priority, created_at

### `FlowStep` (line 160)
Step within a flow. step_order, delay_hours from trigger/previous step, template FK, optional from_name/from_email/subject_override.
- **Fields**: flow, step_order, delay_hours, template, from_name, from_email, subject_override

### `FlowEnrollment` (line 174)
Contact's position in a flow. current_step index, next_send_at timestamp, status (active/completed/cancelled), paused_by_flow flag.
- **Fields**: flow, contact, current_step, enrolled_at, next_send_at, status, paused_by_flow

### `FlowEmail` (line 189)
Per-step flow email send record. FK to enrollment + step + contact. Tracks status, sent_at, opened, clicked.
- **Fields**: enrollment, step, contact, status, sent_at, opened, opened_at, clicked, clicked_at

### `AutoEmail` (line 205)
- **Fields**: contact, template, subject, status, error_msg, opened, clicked, sent_at, opened_at, clicked_at, ses_message_id, auto_run_date

### `AbandonedCheckout` (line 224)
Shopify abandoned checkout. shopify_checkout_id, email, checkout_url, total_price, line_items_json, recovered (bool), recovery timestamps.
- **Fields**: shopify_checkout_id, email, contact, checkout_url, total_price, currency, line_items_json, recovered, recovered_at, abandoned_at, enrolled_in_flow, created_at

### `AgentMessage` (line 243)
IT Agent chat history. role (user/assistant/system), content, tool_calls JSON. Powers /agent chat interface.
- **Fields**: role, content, tool_calls, created_at

### `OmnisendOrder` (line 282)
Legacy Omnisend order data. Imported during migration from Omnisend. Read-only historical data.
- **Fields**: contact, email, order_id, order_number, order_total, currency, payment_status, fulfillment_status, discount_code, discount_amount, shipping_city, shipping_province, ordered_at, created_at

### `OmnisendOrderItem` (line 303)
Legacy Omnisend order line item. Historical data from migration.
- **Fields**: order, product_id, product_title, variant_title, sku, quantity, unit_price, discount, vendor

### `CustomerProfile` (line 319)
Intelligence profile per contact (50+ fields). Computed nightly by customer_intelligence.py. Includes: lifecycle_stage, customer_type, intent_score, churn_risk, reorder_likelihood, category_affinity_json, next_purchase_category, preferred_send_hour, preferred_send_day, channel_preference, intelligence_summary, LTV estimate, and confidence scores for each.
- **Fields**: contact, email, total_orders, total_spent, avg_order_value, first_order_at, last_order_at, days_since_last_order, avg_days_between_orders, top_products, top_categories, all_products_bought, price_tier, has_used_discount, discount_sensitivity, total_items_bought, city, province, profile_summary, last_computed_at, checkout_abandonment_count, last_active_at, total_page_views, total_product_views, website_engagement_score, last_viewed_product, churn_risk, predicted_next_order_date, predicted_ltv, product_recommendations, lifecycle_stage, customer_type, intent_score, reorder_likelihood, category_affinity_json, next_purchase_category, preferred_send_hour, preferred_send_dow, channel_preference, confidence_lifecycle, confidence_intent, confidence_reorder, confidence_category, confidence_send_window, confidence_channel, confidence_discount, churn_risk_score, confidence_churn, intelligence_summary, last_intelligence_at

### `ShopifyOrder` (line 403)
Shopify order record. Full order data: order_total, subtotal, discount, tax, line items via ShopifyOrderItem FK. Synced via webhook + nightly.
- **Fields**: contact, shopify_order_id, order_number, email, first_name, last_name, order_total, subtotal, total_tax, total_discounts, currency, financial_status, fulfillment_status, discount_codes, shipping_city, shipping_province, source_name, tags, ordered_at, created_at

### `ShopifyOrderItem` (line 430)
Line item within a ShopifyOrder. product_id, variant_id, sku, quantity, unit_price, discount, product_type.
- **Fields**: order, shopify_line_id, product_id, variant_id, product_title, variant_title, sku, quantity, unit_price, total_discount, vendor, product_type

### `ShopifyCustomer` (line 449)
Shopify customer record. shopify_id, orders_count, total_spent, tags, accepts_marketing. FK to Contact.
- **Fields**: contact, shopify_id, email, first_name, last_name, phone, orders_count, total_spent, tags, city, province, country, accepts_marketing, shopify_created_at, last_order_at, last_synced_at

### `CustomerActivity` (line 473)
Behavioral event log. event_type (page_view, product_view, add_to_cart, search, checkout_start, etc.), event_data JSON, session_id, timestamps.
- **Fields**: contact, email, event_type, event_data, source, source_ref, session_id, checkout_token, cart_token, shopify_customer_id, occurred_at, created_at, stitched_at, stitched_by

### `ProductImageCache` (line 516)
Product image cache for email templates. product_id, image_url, product_url, price. Synced by shopify_products.py.
- **Fields**: product_id, product_title, image_url, product_url, price, compare_price, product_type, handle, last_synced

### `GeneratedDiscount` (line 532)
Per-contact discount code. code, purpose, value, Shopify price_rule_id, expiry. Created by discount_engine.py.
- **Fields**: contact, email, code, purpose, discount_type, value, shopify_price_rule_id, shopify_discount_id, expires_at, used, used_at, created_at

### `SuppressionEntry` (line 552)
Email suppression list. reason (bounce/complaint/manual), source, detail. Checked before every send.
- **Fields**: email, reason, source, detail, created_at

### `BounceLog` (line 564)
Detailed bounce analysis. event_type (Bounce/Complaint), sub_type, diagnostic, recipient_domain. Used for deliverability scoring.
- **Fields**: email, event_type, sub_type, diagnostic, campaign_id, timestamp, recipient_domain, template_id, subject_family, ses_message_id

### `PreflightLog` (line 583)
Campaign preflight results. overall (PASS/WARN/BLOCK), checks_json with per-check details.
- **Fields**: campaign_id, overall, checks_json, created_at

### `ContactScore` (line 1162)
RFM scoring per contact. rfm_segment (new/champion/loyal/potential/at_risk/lapsed), engagement_score (0-100), recency_days, frequency_rate, monetary_value, optimal_gap_hours (learned send frequency).
- **Fields**: contact, rfm_segment, recency_days, frequency_rate, monetary_value, engagement_score, last_scored_at, optimal_gap_hours, sunset_score, sunset_executed, sunset_executed_at

### `PendingTrigger` (line 1181)
Unprocessed behavioral trigger. trigger_type (browse/cart/checkout), trigger_data, status. Processed by _check_passive_triggers every 30s.
- **Fields**: email, contact, trigger_type, trigger_data, detected_at, status, enrolled_at, processed_at

### `AIGeneratedEmail` (line 1196)
History of AI-generated emails. purpose, subject, body, reasoning, profile_snapshot. Audit trail for AI sends.
- **Fields**: email, contact, purpose, subject, body_text, body_html, reasoning, profile_snapshot, generated_at, sent, sent_at

### `AIMarketingPlan` (line 1215)
Nightly AI plan. plan_json (array of actions), total_sends, status, ai_summary. Generated by ai_engine.py.
- **Fields**: plan_date, plan_json, total_sends, status, ai_summary, created_at

### `AIDecisionLog` (line 1228)
Per-action audit for AI plan execution. plan FK, contact, template_id, segment, status (sent/skipped/failed).
- **Fields**: plan, contact, template_id, segment, subject_used, status, sent_at, created_at

### `MessageDecision` (line 1243)
Per-contact next-best-message decision. action_type (reorder_reminder/cross_sell/etc.), action_score, reason, ranked_actions_json (all 10 actions scored), rejections_json (why each was rejected).
- **Fields**: contact, email, action_type, action_score, action_reason, action_email_purpose, ranked_actions_json, rejections_json, lifecycle_stage, fatigue_score, emails_received_7d, churn_risk_score, intent_score, reorder_likelihood, discount_sensitivity, days_since_last_order, suppression_active, risk_level, suppression_reason, decided_at, expires_at

### `MessageDecisionHistory` (line 1271)
Audit log of MessageDecision executions. Adds decision_date, execution status, outcome tracking.
- **Fields**: contact, email, decision_date, action_type, action_score, action_reason, action_email_purpose, ranked_actions_json, rejections_json, was_executed, executed_at, lifecycle_stage, fatigue_score, churn_risk_score, intent_score, reorder_likelihood, decided_at

### `SuggestedCampaign` (line 1298)
Campaign opportunity from planner. campaign_type, quality_score (0-100), urgency, segment_size, eligible_contacts_json, predicted revenue, subject_line_angles.
- **Fields**: scan_date, campaign_type, campaign_name, target_description, segment_size, eligible_contacts_json, quality_score, urgency, recommended_send_window, recommended_channel, recommended_offer_type, predicted_revenue, predicted_conversions, predicted_complaint_risk, safe_send_volume, preflight_status, preflight_warnings_json, brief_text, status, accepted_at, executed_at, metrics_json, predicted_margin_pct, predicted_profit, discount_cost, net_profit, top_products_json, margin_warning, deliverability_risk_score, created_at

### `OpportunityScanLog` (line 1335)
Nightly opportunity scan results. opportunities_found, total_eligible_contacts, scan_duration.
- **Fields**: scan_date, opportunities_found, total_eligible_contacts, scan_duration_seconds, created_at

### `ProductCommercial` (line 1347)
Product profitability data. current_price, cost_per_unit, margin_pct, inventory, sales_velocity, promotion_eligibility. Synced from Shopify.
- **Fields**: product_id, product_title, sku, product_type, current_price, compare_price, cost_per_unit, margin_pct, margin_source, inventory_level, inventory_location, days_of_stock, stock_pressure, units_sold_30d, units_sold_90d, revenue_30d, revenue_90d, profit_30d, profit_90d, return_rate, avg_discount_given, promotion_eligible, promotion_reason, profitability_score, last_synced, last_computed

### `SystemConfig` (line 1384)
Global system config. delivery_mode: live/shadow/sandbox. Controls whether emails actually send.
- **Fields**: delivery_mode, updated_at

### `ActionLedger` (line 1393)
Audit trail for every system action. trigger_type, source_type, source_id, status, reason_code, template_id. Comprehensive logging.
- **Fields**: contact, email, trigger_type, source_type, source_id, enrollment_id, step_id, status, reason_code, reason_detail, template_id, subject, preview_text, generated_html, ses_message_id, priority, created_at

### `DeliveryQueue` (line 1433)
Email staging queue. Priority-based (checkout_abandoned=10 highest). Drained every 30s by delivery_engine respecting warmup limits.
- **Fields**: contact, email, email_type, source_id, enrollment_id, step_id, template_id, from_name, from_email, subject, html, unsubscribe_url, priority, status, error_msg, ledger_id, campaign_id, auto_email_id, created_at, sent_at, scheduled_at

### `IdentityJob` (line 1474)
Durable job queue for identity resolution. dedupe_key prevents duplicates, status (pending/processing/done/failed), result JSON.
- **Fields**: contact_id, email, source, dedupe_key, job_type, job_data, status, result, attempts, max_attempts, error_msg, created_at, started_at, completed_at

### `AIRenderLog` (line 1495)
AI content rendering telemetry. template_id, block_index, field_name, render_ms, fallback_used. Powers /telemetry dashboard.
- **Fields**: template_id, contact_id, block_index, field_name, generated_text, fallback_used, render_ms, model_name, error_summary, created_at

### `KnowledgeEntry` (line 1518)
AI knowledge base. entry_type: product_catalog, brand_copy, blog_post, competitor_intel, faq, testimonial, email_design_intel. metadata_json holds source_url, relevance_score, image_urls, reasoning. is_active=False means staged for review.
- **Fields**: entry_type, title, content, metadata_json, is_active, is_rejected, created_at, updated_at

### `AIModelConfig` (line 1534)
AI provider configuration. provider (anthropic/openai/openrouter), model_id, api_key_env (env var name), max_tokens, is_default flag. Queried by ai_provider.get_provider().
- **Fields**: provider, model_id, display_name, api_key_env, max_tokens, is_default, is_active, created_at

### `StudioJob` (line 1549)
AI template generation job. status: pending/running/done/error. family (welcome/cart_recovery/etc.), input_json (product_focus, tone), FK to AIModelConfig.
- **Fields**: job_type, status, family, input_json, model_config, error_message, created_at, completed_at

### `TemplateCandidate` (line 1564)
AI-generated template awaiting review. blocks_json (standard format), subject_line, preview_text, reasoning (AI explanation), status: pending/approved/rejected. FK to StudioJob, optional FK to EmailTemplate (set on approval).
- **Fields**: job, blocks_json, subject_line, preview_text, reasoning, metadata_json, status, approved_at, template, created_at

### `TemplatePerformance` (line 1581)
Rolling template metrics. sends, opens, clicks, open_rate, click_rate, revenue_total, revenue_per_send. Computed nightly by learning_engine.py.
- **Fields**: template, sends, opens, clicks, open_rate, click_rate, revenue_total, revenue_per_send, conversion_rate, sample_size, learning_flag, last_computed

### `OutcomeLog` (line 1600)
Email outcome for learning. email_type (campaign/flow), opened, clicked, purchased (bool), revenue (float), send_gap_hours. Feeds into learning_engine.py.
- **Fields**: email_type, email_id, contact, template_id, action_type, segment, opened, clicked, purchased, unsubscribed, revenue, hours_to_open, hours_to_purchase, sent_at, subject_line, send_gap_hours, created_at

### `ActionPerformance` (line 1627)
Action type effectiveness per segment. sample_size, open_rate, click_rate, conversion_rate, revenue_per_send. Computed by learning_engine.py.
- **Fields**: action_type, segment, sample_size, open_rate, click_rate, conversion_rate, revenue_per_send, avg_score, last_computed

### `TemplateSegmentPerformance` (line 1646)
Template performance broken down by contact segment. Enables segment-specific template recommendations.
- **Fields**: template, segment, sample_size, open_rate, click_rate, conversion_rate, revenue_per_send, last_computed

### `ModelWeights` (line 1664)
Computed optimal RFM weights. recency/frequency/monetary weights, evaluation_score, sample_size, phase. Updated by learning_engine.py.
- **Fields**: recency_weight, frequency_weight, monetary_weight, evaluation_score, sample_size, phase, created_at

### `LearningConfig` (line 1678)
Key-value config store. IMPORTANT: Use LearningConfig.get_val(key, default) / set_val(key, value). NOT direct field access. Controls learning phases, kill switches, thresholds.
- **Fields**: key, value, updated_at

### `ScrapeSource` (line 1706)
Knowledge enrichment source. source_type (shopify_products/blog/competitor/etc.), URL, scrape_frequency, is_active, last_scraped_at, config_json.
- **Fields**: source_type, source_name, url, scrape_frequency, is_active, last_scraped_at, config_json, created_at

### `ScrapeLog` (line 1719)
Scrape run audit log. items_found, items_staged, items_skipped, items_errored, error_message. FK to ScrapeSource.
- **Fields**: source, started_at, completed_at, status, items_found, items_staged, items_skipped, items_errored, error_message

### `RejectionLog` (line 1733)
Rejected knowledge entries. Tracks what was rejected and why, prevents re-processing same content.
- **Fields**: original_entry_type, source, title, content_snippet, source_url, content_hash, created_at

### `PostmasterMetric` (line 1746)
- **Fields**: date, domain, spam_rate, ip_reputation, domain_reputation, spf_success_rate, dkim_success_rate, dmarc_success_rate, inbound_encryption_rate, outbound_encryption_rate, delivery_error_rate, raw_json, fetched_at

---

## Python Files — Detailed (54 files, 31,514 lines)

### `app.py` (6,845 lines)
**Flask application — all routes, scheduler, webhooks, auth**

Main Flask application with HTTP Basic Auth (admin:DavinderS@1993), APScheduler integration,
and 90+ routes organized into functional groups. Imports studio_routes as a Blueprint.
Contains all webhook handlers (SES bounce/complaint, Shopify customer/order/checkout),
tracking pixel endpoints, and the full APScheduler configuration (7 interval jobs + 12 nightly crons).
Template filters: json_filter, to_eastern. Uses Gunicorn on port 5000 behind nginx.

Key functions:
- `dashboard() — Main dashboard with stats cards, recent activity, warmup status`
- `contacts() — Contact list with search, pagination, segment filters`
- `ses_webhook() — Processes SES bounce/complaint/delivery notifications via SNS`
- `webhook_shopify_order_create() — Shopify order webhook -> enrichment + trigger evaluation`
- `send_campaign() — Campaign send with preflight checks + delivery queue`
- `_process_flow_enrollments() — Every 60s: advance flow steps, send scheduled emails`
- `_process_delivery_queue_wrapper() — Every 30s: drain queue respecting warmup limits`

### `block_registry.py` (2,404 lines)
**Email template block rendering engine — 26 block types, validation, personalization**

Defines BLOCK_TYPES registry (26 types) with schema (required/optional fields, defaults, label, category).
Each block type has a dedicated renderer (_render_hero, _render_text, _render_product_grid, etc.)
that produces responsive HTML table rows for email clients. Supports {{first_name}}, {{city}},
{{total_orders}} token replacement. All rendering uses dark-theme design (bg: #0d1020).
Block categories: content (hero, text, image, divider), CTA (cta, urgency), product (product_grid,
product_hero, comparison, spec_table, bundle_value), social proof (driver_testimonial, best_seller_proof,
feature_highlights, why_choose_this, faq, stat_callout), trust (trust_reassurance, features_benefits,
objection_handling, use_case_match, whats_included, brand_story, competitor_comparison), discount.

Key functions:
- `render_template_blocks(template, contact) — Full email render from blocks_json`
- `validate_template(blocks_json_str, family_key) — Returns warnings list`
- `render_block(block_dict, contact) — Single block -> HTML <tr>`
- `_sanitize_html(text) — XSS prevention for user content`

### `database.py` (1,775 lines)
**All 53 Peewee ORM models + init_db() + migration helpers**

SQLite database via Peewee ORM. All models inherit BaseModel which sets the database.
init_db() creates all tables with safe=True. Models span 6 domains:
(1) Core: Contact, EmailTemplate, Campaign, CampaignEmail
(2) Flows: Flow, FlowStep, FlowEnrollment, FlowEmail
(3) Shopify: ShopifyOrder, ShopifyOrderItem, ShopifyCustomer, AbandonedCheckout
(4) Intelligence: CustomerProfile (50+ fields), ContactScore (RFM), MessageDecision
(5) AI/Studio: KnowledgeEntry, StudioJob, TemplateCandidate, AIModelConfig
(6) Learning: OutcomeLog, ActionPerformance, TemplatePerformance, ModelWeights, LearningConfig

### `generate-context.py` (1,243 lines)
**Auto-generates CLAUDE.md, REFERENCE.md, MEMORY.md by scanning codebase (this file)**

### `identity_resolution.py` (1,084 lines)
**Cross-channel identity stitching — email, session, Shopify ID, cart/checkout token matching**

Canonical entry point for all identity resolution. resolve_identity() takes any combination
of email, session_id, shopify_id, cart_token, checkout_token and stitches to a single Contact.
Multi-identifier cascade: (1) Email match (exact), (2) Session ID match (anonymous events),
(3) Shopify ID match (webhook data), (4) Checkout/cart token match (highest confidence).
Confidence levels: exact, probable, anonymous_only. Uses durable IdentityJob queue for async
processing. Post-stitching replay: re-evaluates PendingTrigger rows (browse, cart, checkout
recovery) for newly identified contacts. Logs to ActionLedger with RC_IDENTITY_* reason codes.

Key functions:
- `resolve_identity(email, session_id, shopify_id, ...) — Main stitching function`
- `process_identity_jobs() — Drain IdentityJob queue`
- `replay_triggers(contact) — Re-evaluate pending triggers after stitching`

### `customer_intelligence.py` (1,004 lines)
**Nightly enrichment — lifecycle stage, customer type, intent, churn risk, send window, LTV**

Nightly (3:30 UTC). Computes complete intelligence profile per contact from all data sources:
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
Each field has a computed confidence level. All stored in CustomerProfile (42+ fields).

Key functions:
- `enrich_all_contacts() — Batch enrichment for all contacts`
- `compute_lifecycle_stage(contact) — 8-state lifecycle with confidence`
- `compute_customer_type(contact) — Priority-ordered type assignment`
- `compute_intent_score(contact) — 0-100 from behavioral signals`
- `compute_churn_risk(contact) — 0-100 abandonment probability`
- `compute_category_affinity(contact) — Per-category purchase + browse scores`
- `compute_preferred_send_window(contact) — Optimal hour + day`

### `knowledge_scraper.py` (952 lines)
**Auto-enrichment pipeline — scrapes products, blogs, competitors, FAQs into knowledge base**

Scheduled nightly (4:30 UTC). Scrapes configured ScrapeSource URLs:
Shopify product catalog (prices, specs, images), blog posts (custom URLs), competitor intel
(BlueParrott, Jabra, Poly product pages), email design intel (Mailchimp tips), testimonials, FAQs.
Uses AI classifier (Claude) to compute relevance_score (0-100) and categorize content.
Deduplication via content_hash prevents re-scraping. Output goes to KnowledgeEntry with
is_active=False (staged for human review via /studio/knowledge/pending). Tracks all runs
in ScrapeLog with items_found/staged/skipped/errored counts.

Key functions:
- `run_enrichment() — Main entry: iterates active ScrapeSource rows, dispatches by type`
- `scrape_shopify_products(source) — Fetches product catalog via Shopify API`
- `scrape_blog(source) — Fetches blog post content from URL`
- `scrape_competitor(source) — Extracts product/pricing from competitor pages`
- `classify_content(text, source_type) — AI classifies and scores relevance`

### `ai_engine.py` (797 lines)
**Autonomous nightly AI pipeline — RFM scoring, Claude-powered plan generation, execution**

Two-phase nightly pipeline:
Phase 1 (2:30 UTC): score_all_contacts() — RFM algorithm scores every contact.
  Recency (days since last open) x0.4 + Frequency (open rate) x0.4 + Monetary (total_spent) x0.2 = 0-100.
  Assigns RFM segments: new, champion, loyal, potential, at_risk, lapsed.
Phase 2 (2:30 UTC): generate_daily_plan() — Prompts Claude with segment counts, available templates,
  template performance, and recent send history. Claude returns JSON array of actions
  [{segment, template_id, subject_override, reason, max_sends}]. Execution respects 3-day recency
  filter and daily cap (180 live, 5 sandbox). Also provides generate_personalized_email() for
  on-demand AI email composition and update_template_performance() for rolling stats.

Key functions:
- `score_all_contacts() — RFM scoring for all contacts, updates ContactScore table`
- `generate_daily_plan() — Claude generates send plan, stores AIMarketingPlan`
- `execute_plan(plan) — Sends emails per plan actions, logs to AIDecisionLog`
- `generate_personalized_email(email, purpose) — On-demand AI email generation`
- `update_template_performance() — Rolls up open/click/revenue rates per template`

### `campaign_planner.py` (791 lines)
**Aggregate decisions into campaign opportunities — scoring, preflight simulation, ranking**

Nightly (4:15 UTC) after next-best-message. Groups MessageDecision rows by action_type
into campaign opportunities across 8 types: reorder, cross_sell, upsell, new_product, winback,
education, loyalty_reward, discount_offer. For each opportunity: computes segment size, avg
engagement score, predicted revenue (CONVERSION_RATES x segment_size x AOV), simulates preflight
(warmup headroom, fatigue, complaint risk). Quality score (0-100): segment_size (20 pts) +
avg_engagement (15 pts) + revenue (15 pts) - complaint_risk (-10 to block) - fatigue (-10 pts).
Stores SuggestedCampaign rows with quality_score, urgency, subject_line_angles, target_template_id,
accepted/dismissed flags. Dashboard shows opportunities ranked by score.

Key functions:
- `scan_opportunities() — Group decisions into campaigns, score, rank`
- `simulate_preflight(campaign) — Check warmup headroom, fatigue, complaints`
- `compute_quality_score(opportunity) — 0-100 multi-factor score`

### `next_best_message.py` (790 lines)
**Deterministic decision engine — 10 action types, per-contact scoring with cooldowns**

Nightly (4:00 UTC) after intelligence. For each active contact, scores 10 action types:
reorder_reminder (purchase cycle + last sent timing), cross_sell (category diversity + recency),
upsell (AOV progression), new_product (browsing + intent), winback (churn risk + days since purchase),
education (engagement gaps), loyalty_reward (VIP status + LTV), discount_offer (discount sensitivity),
wait (fatigue/frequency cap default no-op), switch_channel (email fatigue -> SMS).
Each scorer returns (score 0-100, reason, eligible bool, rejection_reason).
Picks highest-scoring eligible action. Action-specific cooldowns enforced: reorder >=14d,
cross_sell >=21d, winback >=30d, etc. All actions + rejections logged to MessageDecision table.

Key functions:
- `decide_for_contact(contact) — Scores all 10 actions, picks best eligible`
- `_score_reorder_reminder(contact, profile) — Purchase cycle analysis`
- `_score_cross_sell(contact, profile) — Category diversity scoring`
- `_score_winback(contact, profile) — Churn risk + days since purchase`
- `_score_wait(contact, profile) — Fatigue/frequency default action`
- `run_nightly_decisions() — Batch: decide for all active contacts`

### `condition_engine.py` (786 lines)
**Journey-aware template families, per-contact variant resolution, family constraints**

Implements Phase 2 conditional logic. Defines 8 TEMPLATE_FAMILIES (welcome, browse_recovery,
cart_recovery, checkout_recovery, post_purchase, winback, high_intent_browse, promo) each with
allowed_blocks, required_blocks, recommended_order, and max_blocks. Condition schema: {field, op, value}
with 9 fields (lifecycle_stage, customer_type, total_orders, total_spent, days_since_last_order,
has_used_discount, tags, source) and 7 operators (eq, neq, gt, lt, in, contains, not_contains).
Variant resolution is first-match-wins: each block can have a variants array with conditions + content override.
At send time, the engine evaluates conditions against the contact's profile to pick the right variant.

Key functions:
- `get_contact_context(contact) — Builds flat evaluation dict from Contact + CustomerProfile`
- `evaluate_conditions(conditions, context) — AND logic, returns bool`
- `resolve_block_variants(block, context) — Returns (resolved_content, explain_dict)`
- `enforce_family_constraints(blocks, family_key) — Returns (is_valid, errors list)`

### `convert_templates.py` (701 lines)
**Template migration — converts legacy HTML templates to blocks_json format**

Migration utility for converting old HTML-based EmailTemplate rows to the
new blocks_json format. Parses HTML structure, identifies block types (hero, text, CTA, etc.),
and generates corresponding blocks_json. One-time migration tool.

### `studio_skills.py` (675 lines)
**6-skill AI pipeline for template generation — block selection, content composition, validation**

Composable skill functions: skill_name(context: dict, provider: AIProvider) -> dict.
Context dict carries accumulated state through the pipeline: family, product_focus, tone, knowledge,
performance data, block_sequence, blocks, subject, preview_text, reasoning.
Skills: (1) select_block_sequence — AI picks 4-6 blocks within family constraints, validates against
allowed_blocks, retries 1x on failure. (2) compose_hero — headline 3-6 words + subheadline max 10 words.
(3) compose_text — 1-2 paragraphs, each 1-2 sentences. (4) compose_generic_block — handles all other
block types using BLOCK_TYPES schema. (5) compose_subject_line — subject max 50 chars + preview max 90 chars.
(6) validate_and_fix — pure Python, runs validate_template() + enforce_family_constraints(), auto-fixes
disallowed blocks and missing required fields. System prompt enforces concise copy rules: sentences
<15 words, no filler, billboard style, JSON-only output.

Key functions:
- `select_block_sequence(context, provider) — AI selects block order within family constraints`
- `compose_hero(context, provider) — Generates hero headline + subheadline`
- `compose_text(context, provider) — Generates body copy paragraphs`
- `compose_generic_block(context, provider, block_type) — Content for any block type`
- `compose_subject_line(context, provider) — Subject line + preview text`
- `validate_and_fix(context) — Pure-Python validation + auto-fix pass`
- `_parse_json_response(text) — Handles markdown fences, reasoning model outputs`
- `_build_knowledge_summary(knowledge, block_type) — Filters relevant knowledge, truncates to 2000 chars`

### `profit_engine.py` (654 lines)
**Product profitability scoring — Shopify cost/inventory sync, margin computation, promo eligibility**

Syncs product commercial data from Shopify: cost_per_unit (from variant cost field),
inventory levels, sales velocity. Computes margin_pct per product. Margin estimates by type
when cost data unavailable: headsets 45%, dash cams 35%, accessories 55%, etc.
score_product_profitability(product_id) returns composite score: margin % + inventory level + velocity.
get_promotion_eligibility(product_id) recommends discount/promotion strategy.
Stored in ProductCommercial model (product_id, current_price, cost_per_unit, margin_pct, etc.).

### `studio_routes.py` (639 lines)
**Flask Blueprint for /studio/* — knowledge base, generation, jobs, models, sources, scraping**

Blueprint registered in app.py. Route groups:
Dashboard (/studio) — intelligence score widget, recent jobs, quick actions.
Knowledge (/studio/knowledge) — CRUD for knowledge entries, type-filtered list, add/edit/delete.
Pending review (/studio/knowledge/pending) — approval queue for auto-scraped entries.
Generation (/studio/generate) — form with family, product focus, tone, model selection -> POST triggers pipeline.
Jobs (/studio/jobs, /studio/jobs/<id>) — generation job list and detail with candidate cards.
Candidates (/studio/candidates/<id>/approve|reject|preview) — approve/reject with full HTML preview.
Models (/studio/models) — AIModelConfig CRUD (add Anthropic/OpenAI/OpenRouter providers).
Sources (/studio/sources) — ScrapeSource CRUD with run/toggle/fix actions.
Scrape log (/studio/scrape-log) — historical log of scraping runs.
API (/studio/api/intelligence-score) — JSON endpoint for dashboard polling.

### `activity_sync.py` (613 lines)
**Email engagement sync — opens, clicks, unsubscribes from SES webhooks and tracking pixels**

Syncs email engagement events: Bounce, Complaint, Delivery, Open, Click, Send, Reject.
Updates CampaignEmail / FlowEmail with opened, opened_at, clicked, clicked_at timestamps.
Processes both SES webhook notifications and tracking pixel hits. Nightly (3:00 UTC) batch
reconciliation ensures no events missed.

### `flow_templates_seed.py` (593 lines)
**Seed flow definitions — pre-built automation flows with steps and timing**

Seed data for automation flows: Welcome Series (3 steps over 7 days),
Cart Recovery (2 steps at 1h + 24h), Post-Purchase Follow-up (2 steps at 3d + 14d),
Winback (2 steps at 30d + 60d). Each flow has trigger_type, steps with delay_hours and template.

### `shopify_enrichment.py` (541 lines)
**Contact enrichment from Shopify — order history, top products, cross-sell recommendations**

enrich_contact_from_shopify(contact) pulls full order history, computes totals,
infers category preferences from purchased products. get_top_products(contact) returns top 5
products by purchase frequency. get_product_recommendations(contact) generates cross-sell
recommendations based on category affinity + what similar customers bought.

### `ai_content.py` (516 lines)
**AI-assisted block content authoring — field-level generation with safety caps and audit logging**

Phase 3 of the template architecture: AI generates content for specific block fields at send time.
Writable fields per block type: hero (headline, subheadline), text (paragraphs), cta (text),
urgency (message), discount (display_text, expires_text, value_display), product_grid (section_title).
Field length caps enforced: headline 120 chars, paragraph 500 chars. HTML/markdown stripped.
Falls back to template default if AI fails. Every generation logged to AIRenderLog for telemetry.
Also provides personalize_text_field() for lightweight per-contact personalization.

Key functions:
- `generate_block_content(block_type, contact, family, fallback, purpose) — AI content merged with fallback`
- `personalize_text_field(field_name, template_text, contact, fallback) — Send-time personalization`
- `generate_template_content(blocks, family, contact) — Batch generation for all blocks`

### `email_templates.py` (480 lines)
**Seed template library — pre-built templates for each journey type in blocks_json format**

Defines seed templates for: welcome, browse_recovery, cart_recovery, checkout_recovery,
post_purchase, winback, loyalty, promo. Each template has subject, preview_text, and blocks_json
(hero + text + CTA + relevant blocks for the journey). Used by init_db() to populate
the EmailTemplate table on first run.

### `create_showcase_templates.py` (474 lines)
**Showcase template generator — creates example templates demonstrating all block types**

### `learning_engine.py` (461 lines)
**Self-learning pipeline — template scoring, action effectiveness, optimal frequency computation**

Nightly (5:30 UTC). Three computations:
(1) compute_template_scoring(): Rolling 30-day performance per template -> TemplatePerformance
  (open_rate, click_rate, conversion_rate, revenue_per_send). Also per-segment breakdown ->
  TemplateSegmentPerformance.
(2) compute_action_effectiveness(): Action type performance per segment -> ActionPerformance
  (action_type, segment, sample_size, rates, revenue). Sample size threshold: 50 = full confidence.
(3) compute_optimal_frequency(): Personalized send gap per contact based on engagement history ->
  Updates ContactScore.optimal_gap_hours. Replaces static 16h cap with learned optimal timing.

Key functions:
- `run_learning_engine() — Main entry: runs all three computations`
- `compute_template_scoring() — Rolling 30d template performance`
- `compute_action_effectiveness() — Action type performance per segment`
- `compute_optimal_frequency() — Personalized send gap per contact`

### `delivery_engine.py` (448 lines)
**Email delivery queue — priority-based, warmup-compliant, shadow/sandbox/live modes**

Separates email generation from sending via DeliveryQueue model. enqueue_email() stages
emails with priority (checkout_abandoned=10 highest, contact_created=50 lowest).
process_queue() runs every 30s: drains by priority, respects warmup phase caps.
8 warmup phases: Ignition (50/day, 3d) -> Spark (150, 4d) -> Gaining Trust (350, 7d) ->
Building (750, 7d) -> Momentum (1500, 7d) -> Scaling (3000, 7d) -> High Volume (7000, 7d) ->
Full Send (999999, 99d). Delivery modes: live (send via SES), shadow (mark as shadowed, no SES),
sandbox (SES sandbox mode with 5/day cap). SystemConfig.delivery_mode controls the mode.

Key functions:
- `enqueue_email(contact, email_type, ...) — Stage email in queue with priority`
- `process_queue() — Drain queue respecting warmup limits and delivery mode`
- `_get_warmup_remaining() — Calculate remaining daily capacity`

### `shopify_sync.py` (420 lines)
**Shopify customer/order sync — webhook handlers + nightly full sync + HMAC verification**

Two sync modes: (1) Webhook-driven real-time sync (customer create/update, order create,
checkout create) with HMAC SHA256 signature verification. (2) Nightly (2:00 UTC) full sync
via Shopify REST API — fetches all customers and orders, upserts ShopifyCustomer + ShopifyOrder
+ ShopifyOrderItem + Contact. Enriches Contact with total_orders, total_spent, first/last order dates.
Also incremental sync every 2s for recent changes.

### `system_map_data.py` (414 lines)
**System architecture visualization — 65+ nodes, relationships, stats for D3.js force graph**

build_system_map_nodes() returns 65+ nodes representing every system component
(routes, models, scheduled jobs, external services) with category, icon, and live stats.
build_system_map_edges() returns relationships between nodes (data flow, dependencies).
Consumed by /api/system-map/data endpoint, rendered as D3.js force-directed graph on /system-map page.

### `outcome_tracker.py` (403 lines)
**Nightly outcome collection — opened/clicked/purchased/revenue attribution for learning**

Nightly (5:00 UTC). Queries CampaignEmail + FlowEmail from last 48h.
Attributes purchases via last-touch within 72h window. Computes hours_to_open, hours_to_purchase.
Writes OutcomeLog entries (email_type, email_id, contact, template_id, action_type, segment,
opened, clicked, purchased, revenue, send_gap_hours). Re-checks 72h window for older emails
to catch delayed purchases. Data feeds into learning_engine.py for performance computation.

### `data_enrichment.py` (390 lines)
**General contact enrichment — activity aggregation, profile metrics computation**

Pulls CustomerActivity events, ShopifyOrder history, and engagement metrics
to compute and store derived fields in CustomerProfile. Bridges raw event data with
the intelligence layer.

### `template_studio.py` (378 lines)
**Studio orchestrator — runs skill pipeline, manages jobs, approval/rejection, intelligence scoring**

TemplateStudio class orchestrates the full generation flow:
generate(family, product_focus, tone, model_config_id) creates a StudioJob, builds context
(knowledge entries + performance data), runs all 6 skills sequentially, saves TemplateCandidate,
marks job done/error. approve_candidate() converts candidate to standard EmailTemplate row
(blocks_json format, tagged with family). reject_candidate() stores rejection reason.
get_intelligence_score() computes 0-100 knowledge base score: product_catalog (25 pts),
brand_copy (20 pts), testimonials (15 pts), blog_posts (10 pts), competitor_intel (10 pts),
FAQs (10 pts), performance_data (10 pts). Returns breakdown + actionable suggestions.

Key functions:
- `TemplateStudio.generate(family, product_focus, tone, model_config_id) — Full pipeline`
- `TemplateStudio.approve_candidate(candidate_id) — Candidate -> EmailTemplate`
- `TemplateStudio.reject_candidate(candidate_id, reason) — Mark rejected`
- `TemplateStudio.get_intelligence_score() — 0-100 with breakdown and suggestions`

### `discount_engine.py` (367 lines)
**Dynamic discount generation — per-contact codes via Shopify price rules**

get_or_create_discount(email, purpose) returns a unique discount code for a contact.
Creates Shopify price rule + discount code via API if none exists. Tracks in GeneratedDiscount table.
get_discount_display(discount_info) formats for email insertion (code, expiry, value display).
Supports percentage and fixed-amount discounts with configurable expiry.

### `strategy_optimizer.py` (337 lines)
**Apply learned insights — template recommendations, frequency caps, action adjustments, sunset policy**

Nightly (6:00 UTC). Reads learning engine outputs and applies them:
get_template_recommendations(segment) — ranked templates by performance (engagement/revenue target).
get_contact_frequency_cap(contact_id) — personalized send gap replacing static 16h cap.
get_action_score_adjustment(action_type, segment) — multiplier (strong +30%, weak -30%).
execute_sunset_policy() — marks churned contacts for final win-back with guardrails:
  observation phase = shadow only, conservative = threshold 90, active = threshold 85,
  volume cap: never sunset >2% of active list, purchase protection: skip if order in 90d,
  final email uses template id=16.

Key functions:
- `get_template_recommendations(segment) — Ranked templates by learned performance`
- `get_contact_frequency_cap(contact_id) — Personalized send gap`
- `get_action_score_adjustment(action_type, segment) — Score multiplier`
- `execute_sunset_policy() — Guardrailed churned-contact handling`

### `campaign_preflight.py` (324 lines)
**Pre-send validation — 10 checks before campaign send (warmup, complaints, fatigue, suppression)**

Gate function before sending any campaign. 10 checks:
(1) Warmup headroom — daily limit vs. today's sends. (2) Recipient count — >10 recommended.
(3) Subject line — exists and <100 chars. (4) Template — exists with blocks. (5) Complaint risk —
<5% safe, 5-10% warning, >10% block. (6) Fatigue — avg_fatigue <50 safe. (7) Suppression list —
excluded. (8) SPF/DKIM — configured. (9) Unsubscribe link — present. (10) Bounce domain analysis —
per-domain complaint rate. Output: PreflightLog with overall status (PASS/WARN/BLOCK) + detailed checks JSON.

### `ai_provider.py` (273 lines)
**Multi-model AI abstraction — Anthropic (Claude), OpenAI (GPT), OpenRouter (200+ models)**

Provider pattern: AIProvider base class with complete(system_prompt, user_prompt, max_tokens) -> str.
Three implementations: AnthropicProvider (Claude via anthropic SDK), OpenAIProvider (GPT via openai SDK),
OpenRouterProvider (Kimi K2.5 + 200+ models via OpenAI-compatible API, handles reasoning model output).
Factory function get_provider(config=None) queries AIModelConfig table for the default active model,
falls back to claude-haiku-4-5-20251001 with ANTHROPIC_API_KEY env var. All providers return plain
strings; caller handles JSON parsing. Errors raise AIProviderError(message, provider, model_id).

Key functions:
- `get_provider(config=None) — Factory: returns provider from config or default AIModelConfig`
- `AnthropicProvider.complete() — Claude API via anthropic.Anthropic().messages.create()`
- `OpenAIProvider.complete() — GPT API via openai.OpenAI().chat.completions.create()`
- `OpenRouterProvider.complete() — OpenRouter API with reasoning model support`

### `shopify_products.py` (265 lines)
**Shopify product catalog sync — ProductImageCache for email insertion**

sync_shopify_products() fetches all products via Shopify API, populates ProductImageCache
(product_id, product_title, image_url, product_url, price, compare_price, product_type, handle).
get_products_for_email(product_refs) returns product data formatted for email template insertion.

### `email_sanitizer.py` (253 lines)
### `email_sender.py` (249 lines)
**AWS SES integration — MIME-based, RFC 8058 one-click unsubscribe, suppression checks**

Sends emails via boto3 SES raw send. Builds MIME multipart (text/plain + text/html).
Injects tracking params into store links (meh_t token). Adds RFC 8058 one-click unsubscribe headers
(List-Unsubscribe, List-Unsubscribe-Post). Adds Feedback-ID and Precedence: bulk headers.
SES configuration set: mailenginehub-production. Checks SuppressionEntry table before sending
(bounces, complaints, manual suppressions). Converts HTML to plain text for alternative part.
test_ses_connection() validates SES credentials and configuration.

Key functions:
- `send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body, ...) — Send via SES`
- `test_ses_connection(test_email) — Validate SES setup`

### `watchdog.py` (243 lines)
**Auto-restart watchdog — monitors app process, restarts on crash**

External process monitor that checks if the Flask app is responding.
Sends periodic health check requests to localhost:5000. If no response after retries,
triggers systemctl restart mailengine. Logs to watchdog_log.txt.

### `migrate_templates.py` (200 lines)
**Template migration — converts legacy HTML templates to blocks_json format**

### `health_check.py` (197 lines)
**System health diagnostics — SES, database, Shopify, warmup status checks**

run_health_check() returns dict with status for each subsystem:
SES (credentials valid, send quota, bounce rate), Database (connection OK, table counts),
Shopify (API key valid, webhook registered), Warmup (phase, daily limit, health score),
Scheduler (all jobs running). Used by /settings page and monitoring.

### `cascade.py` (181 lines)
**Auto-cascade intelligence — propagate profile updates to related contacts (household, device, IP)**

When a CustomerProfile updates, cascade.py propagates relevant intelligence to related
contacts sharing household identifiers, device fingerprints, or IP addresses. Prevents
intelligence gaps for contacts that haven't been directly enriched yet.

### `postmaster_tools.py` (181 lines)
### `action_ledger.py` (170 lines)
**Comprehensive audit logging — every decision, trigger, send, and outcome recorded**

log_action(contact, email, trigger_type, source_type, source_id, ...) writes to ActionLedger.
Captures: trigger_type (browse, cart, checkout, tag, score_change, etc.), source_type (flow, campaign,
ai_engine, manual), status (pending, sent, failed, skipped), reason_code (RC_* constants),
template_id, enrollment_id, step_id. Every significant system action gets an audit trail entry.

### `rebuild_templates.py` (143 lines)
**Batch template rebuild — regenerates blocks_json for multiple templates**

### `normalize_activity.py` (138 lines)
**Activity data normalization — standardizes event types and data formats**

Normalizes CustomerActivity event_type values and event_data JSON structure
across different sources (Shopify webhooks, tracking pixels, API events) into a
consistent schema for downstream processing by intelligence and decision engines.

### `render_previews.py` (134 lines)
**Render preview HTML — generates preview files from block templates for testing**

### `email_shell.py` (132 lines)
**Universal email wrapper — LDAS-branded header, dark theme body, CAN-SPAM footer**

Wraps any email body HTML in the standard LDAS template: header with logo on dark gradient
background (radial blue glow), unified dark navy body (#0d1020), CAN-SPAM compliant footer
(physical address: 35 Capreol Court, Toronto, ON M5V 4B3; unsubscribe link; social links).
Responsive design: 600px container, mobile stacking. wrap_email(body_html, preview_text, unsubscribe_url)
returns a complete HTML document ready for email clients.

### `sns_verify.py` (106 lines)
**AWS SNS signature verification — validates webhook authenticity**

Verifies AWS SNS message signatures to ensure SES webhook notifications
are authentic. Downloads signing certificate, validates signature against message body.
Required for secure SES event processing (bounces, complaints, deliveries).

### `token_utils.py` (87 lines)
**Signed token generation — HMAC-based tokens for tracking links and unsubscribe URLs**

create_token(data) generates URL-safe signed token encoding arbitrary data.
verify_token(token) decodes and verifies signature, returns data dict or None.
Used for tracking pixel URLs (/track/open/<token>), flow click tracking (/track/flow-click/<token>),
and unsubscribe links (/unsubscribe/<token>). Prevents URL tampering.

### `learning_config.py` (72 lines)
**Key-value config store — learning phases, kill switches, DB-backed without restart**

Simple key-value store backed by LearningConfig model. No app restart needed.
get_learning_enabled() returns bool (kill switch). get_learning_phase() returns phase:
observation (<30 days OR <500 outcomes — don't act on learnings),
conservative (30-60 days OR <20 purchases — cautious adjustments),
active (>=60 days AND >=20 purchases — full optimization).
set_learning_phase_override(phase) forces a specific phase (for regression detection).
IMPORTANT: Use LearningConfig.get_val(key, default) / LearningConfig.set_val(key, value) pattern.

### `rebuild_one.py` (68 lines)
**Rebuild single template — utility to regenerate one template's blocks_json**

### `audit_send.py` (57 lines)
**Audit send utility — one-off script for auditing sent email records**

### `search_contact.py` (33 lines)
**Contact search utility — CLI helper to find contacts by email or name**

### `run.py` (19 lines)
**Application entry point — imports app, calls app.run()**

### `trigger_sync.py` (11 lines)
**Trigger sync utility — manual trigger processing helper**

### `discount_codes.py` (3 lines)
**Shopify discount code management — placeholder for expanded discount logic**

---

## Routes — Full Detail (122 total)

### Dashboard & Overview
Main dashboard, system monitoring, and reporting pages

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/` | GET | `dashboard` | 471 | Main dashboard — stat cards (contacts, campaigns, open rate, revenue), recent activity feed, warmup status, quick actions |
| `/activity` | GET | `activity_feed` | 5788 | Activity feed — real-time log of all system events (sends, opens, clicks, bounces, triggers) |
| `/audit` | GET | `audit_dashboard` | 4110 | Audit dashboard — ActionLedger viewer with filtering by trigger type, source, status |
| `/system-map` | GET | `system_map` | 6192 | Interactive D3.js force graph — 65+ nodes showing all system components and data flow |
| `/telemetry` | GET | `telemetry_dashboard` | 4146 | AI rendering telemetry — success rates, latency, field-specific performance metrics |

### Contacts & Profiles
Contact management, import, Shopify sync, and customer intelligence profiles

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/contacts` | GET | `contacts` | 524 | Contact list — search, pagination, segment filters (all/subscribed/unsubscribed), import CSV button |
| `/contacts/import-csv` | POST | `import_csv` | 639 | CSV import handler — maps columns to Contact fields, deduplicates by email |
| `/contacts/sync-shopify` | POST | `sync_shopify` | 730 | Trigger Shopify customer sync — calls shopify_sync.sync_shopify_customers() |
| `/profiles` | GET | `profiles_list` | 4647 | Intelligence profiles list — all contacts with CustomerProfile data, search, lifecycle filters |

### Email Templates
Template creation and editing (legacy HTML + blocks-based)

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/templates` | GET | `templates` | 1009 | Template library — list all templates with family tags, format badges, send counts |
| `/templates/new` | GET, POST | `new_template` | 1014 | Create legacy HTML template (form-based) |
| `/templates/new-blocks` | GET | `new_blocks_template` | 1050 | Create blocks-based template — opens template builder |

### Campaigns
One-time email campaigns with preflight checks and sending

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/campaigns` | GET | `campaigns` | 1692 | Campaign list — all campaigns with status badges, send counts, open rates |
| `/campaigns/new` | GET, POST | `new_campaign` | 1697 | Create campaign — select template, segment filter, from name/email |

### Automation Flows
Multi-step automated email sequences triggered by events

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/flows` | GET | `flows` | 3815 | Flow list — all flows with trigger types, step counts, enrollment stats, active toggle |
| `/flows/new` | GET, POST | `new_flow` | 3844 | Create flow — set trigger type (contact_created, tag_added, checkout_abandoned, etc.) |

### AI Engine
Autonomous AI scoring, plan generation, and learning system

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/agent` | GET | `agent` | 4546 | IT Agent chat — Claude-powered assistant for system questions |
| `/ai-engine` | GET | `ai_engine_dashboard` | 5305 | AI Engine dashboard — segment distribution, today's plan, decision log, run-now button |
| `/campaign-planner` | GET | `campaign_planner_page` | 5101 | Campaign planner — suggested campaigns from opportunity scanner, accept/dismiss |
| `/learning` | GET | `learning_dashboard` | 5458 | Learning dashboard — phase indicator, template performance, action effectiveness, model weights |
| `/profits` | GET | `profit_dashboard` | 5184 | Profit dashboard — product profitability scores, margin analysis, promo eligibility |

### AI Template Studio
AI-powered template generation with knowledge base and approval workflow

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/studio/generate` | GET | `generate_form` | 227 | Generation form — select family, product focus, tone, AI model |
| `/studio/generate` | POST | `generate_run` | 243 | Generation form — select family, product focus, tone, AI model |
| `/studio/jobs` | GET | `jobs_list` | 270 | Job list — all generation jobs with status, family, timestamps |
| `/studio/knowledge` | GET | `knowledge_list` | 129 | Knowledge base — entries by type (products, brand copy, testimonials, etc.), add/edit/delete |
| `/studio/knowledge/pending` | GET | `knowledge_pending` | 402 | Pending review queue — auto-scraped entries awaiting approval/rejection |
| `/studio/models` | GET | `models_list` | 365 | AI model config — add/manage Anthropic, OpenAI, OpenRouter providers |
| `/studio/scrape-log` | GET | `scrape_log` | 628 | Scrape log — historical scraping runs with found/staged/skipped/errored counts |
| `/studio/sources` | GET | `sources_list` | 507 | Knowledge sources — scrape source URLs, run/toggle, frequency config |

### Warmup & Delivery
IP warmup management and delivery settings

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/sent-emails` | GET | `sent_emails` | 1439 | Sent email log — all sent emails across campaigns + flows, preview, status |
| `/settings` | GET | `settings` | 4070 | Settings — delivery mode (live/shadow/sandbox), SES test, general config |
| `/warmup` | GET | `warmup_dashboard` | 2225 | Warmup dashboard — 8-phase progress, daily stats chart, health score, checklist, domain analysis |

### Webhooks & Tracking
Inbound webhooks from SES and Shopify, plus email engagement tracking

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/track/flow-click/<token>` | GET | `track_flow_click` | 2081 | Flow click tracking — redirects to target URL, logs click event |
| `/track/open/<token>` | GET | `track_open_token` | 2020 | Open tracking pixel — 1x1 transparent GIF, logs open to CampaignEmail/FlowEmail |
| `/webhooks/ses` | POST | `ses_webhook` | 124 | SES webhook — processes bounce/complaint/delivery/open/click notifications via SNS |
| `/webhooks/shopify/checkout/create` | POST | `webhook_shopify_checkout_create` | 840 | Shopify checkout webhook — creates AbandonedCheckout for recovery flows |
| `/webhooks/shopify/customer/create` | POST | `webhook_shopify_customer_create` | 749 | Shopify customer create webhook — upserts Contact + ShopifyCustomer |
| `/webhooks/shopify/customer/update` | POST | `webhook_shopify_customer_update` | 798 | Shopify customer update webhook — updates Contact fields |
| `/webhooks/shopify/order/create` | POST | `webhook_shopify_order_create` | 917 | Shopify order webhook — creates ShopifyOrder, enriches Contact, triggers flows |

### API Endpoints
JSON API endpoints for AJAX calls, external integrations, and JavaScript-driven pages

| Route | Methods | Function | Line | Description |
|---|---|---|---|---|
| `/api/activity/feed` | GET | `api_activity_feed` | 5851 | Activity feed JSON — paginated events for activity page auto-refresh |
| `/api/agent/chat` | POST | `api_agent_chat` | 4553 | Agent chat API — sends message to Claude, returns response |
| `/api/ai-engine/run-now` | POST | `ai_engine_run_now` | 5442 | Trigger AI engine manually — runs scoring + plan generation |
| `/api/ai-engine/sample-email` | POST | `ai_engine_sample_email` | 5400 | Generate sample AI email — preview without sending |
| `/api/campaign/recipient-count` | GET | `api_recipient_count` | 1719 | Count recipients for a segment filter — used by campaign form |
| `/api/identify` | POST, OPTIONS | `identify_visitor` | 5896 | Identity pixel — JavaScript tracking pixel for website visitor identification |
| `/api/learning/stats` | GET | `api_learning_stats` | 5668 | Learning stats JSON — for dashboard auto-refresh |
| `/api/subscribe` | POST, OPTIONS | `api_subscribe` | 5995 | Public subscribe endpoint — CORS-enabled for external forms |
| `/api/system-map/data` | GET | `system_map_api` | 6196 | System map JSON — 65+ nodes and edges for D3.js visualization |
| `/api/telemetry/data` | GET | `api_telemetry_data` | 4151 | Telemetry JSON — AI render stats for telemetry page auto-refresh |
| `/api/templates/ai-generate-block` | POST | `api_ai_generate_block` | 1252 | AI generate single block content — for template builder |
| `/api/templates/ai-generate-template` | POST | `api_ai_generate_template` | 1315 | AI generate full template — for template builder |
| `/api/track` | POST, OPTIONS | `track_event` | 5930 | Event tracking API — receives behavioral events from website JavaScript |
| `/api/warmup/health` | GET | `api_warmup_health` | 2494 | Warmup health JSON — for warmup dashboard auto-refresh |

### Other Routes

| Route | Methods | Function | Line |
|---|---|---|---|
| `/activity/sync` | POST | `activity_sync_trigger` | 6107 |
| `/api/agent/clear` | POST | `api_agent_clear` | 4635 |
| `/api/audit/details` | GET | `api_audit_details` | 4128 |
| `/api/audit/stats` | GET | `api_audit_stats` | 4123 |
| `/api/auto-pilot/preview/<int:item_id>` | GET | `auto_pilot_preview` | 5765 |
| `/api/campaign-planner/<int:sc_id>/accept` | POST | `campaign_planner_accept` | 5147 |
| `/api/campaign-planner/<int:sc_id>/brief` | GET | `campaign_planner_brief` | 5170 |
| `/api/campaign-planner/<int:sc_id>/dismiss` | POST | `campaign_planner_dismiss` | 5158 |
| `/api/campaign-planner/scan` | POST | `campaign_planner_scan` | 5136 |
| `/api/campaign/<int:campaign_id>/status` | GET | `api_campaign_status` | 4257 |
| `/api/contacts/count` | GET | `api_contacts_count` | 4253 |
| `/api/contacts/sync-status` | GET | `api_sync_status` | 741 |
| `/api/flows/<int:flow_id>/stats` | GET | `api_flow_stats` | 4020 |
| `/api/profiles/<int:contact_id>/decide` | POST | `recompute_decision` | 5257 |
| `/api/profiles/<int:contact_id>/intelligence` | POST | `recompute_intelligence` | 5268 |
| `/api/sanitize-contacts` | POST | `sanitize_contacts_api` | 700 |
| `/api/templates/<int:template_id>/preview-blocks` | GET | `preview_blocks_template` | 1166 |
| `/api/templates/<int:template_id>/save-blocks` | POST | `api_save_blocks` | 1119 |
| `/api/templates/<int:template_id>/test-send` | POST | `api_template_test_send` | 1374 |
| `/api/templates/create-blocks` | POST | `api_create_blocks_template` | 1087 |
| `/api/triggers/backlog` | GET | `api_trigger_backlog` | 4227 |
| `/auto-pilot` | GET | `auto_pilot_dashboard` | 5705 |
| `/campaigns/<int:campaign_id>` | GET | `campaign_detail` | 1731 |
| `/campaigns/<int:campaign_id>/send` | POST | `send_campaign` | 1757 |
| `/contacts/unsubscribe-oneclick` | POST | `unsubscribe_oneclick` | 986 |
| `/contacts/unsubscribe/<email>` | GET, POST | `unsubscribe` | 970 |
| `/flows/<int:flow_id>` | GET | `flow_detail` | 3862 |
| `/flows/<int:flow_id>/delete` | POST | `flow_delete` | 3923 |
| `/flows/<int:flow_id>/enroll-test` | POST | `flow_enroll_test` | 3984 |
| `/flows/<int:flow_id>/priority` | POST | `flow_update_priority` | 3909 |
| `/flows/<int:flow_id>/steps/<int:step_id>/delete` | POST | `flow_delete_step` | 3969 |
| `/flows/<int:flow_id>/steps/add` | POST | `flow_add_step` | 3938 |
| `/flows/<int:flow_id>/toggle` | POST | `flow_toggle` | 3899 |
| `/learning/toggle` | POST | `learning_toggle` | 5658 |
| `/profiles/<int:contact_id>` | GET | `profile_detail` | 4783 |
| `/profiles/<int:contact_id>/ai-email-preview` | POST | `ai_email_preview` | 5279 |
| `/profiles/<int:contact_id>/send-quick-email` | POST | `send_quick_email` | 5067 |
| `/sent-emails/preview/<email_type>/<int:email_id>` | GET | `sent_email_preview` | 1650 |
| `/settings/delivery-mode` | POST | `settings_delivery_mode` | 4084 |
| `/settings/test-ses` | POST | `test_ses` | 4096 |
| `/studio/api/intelligence-score` | GET | `api_intelligence_score` | 391 |
| `/studio/candidates/<int:id>/approve` | POST | `candidate_approve` | 316 |
| `/studio/candidates/<int:id>/preview` | GET | `candidate_preview` | 340 |
| `/studio/candidates/<int:id>/reject` | POST | `candidate_reject` | 329 |
| `/studio/jobs/<int:id>` | GET | `job_detail` | 295 |
| `/studio/knowledge/<int:id>/approve` | POST | `knowledge_approve` | 453 |
| `/studio/knowledge/<int:id>/delete` | POST | `knowledge_delete` | 215 |
| `/studio/knowledge/<int:id>/edit` | POST | `knowledge_edit` | 202 |
| `/studio/knowledge/<int:id>/reject` | POST | `knowledge_reject` | 464 |
| `/studio/knowledge/add` | POST | `knowledge_add` | 183 |
| `/studio/models/add` | POST | `models_add` | 372 |
| `/studio/sources/<int:id>/run` | POST | `sources_run` | 571 |
| `/studio/sources/<int:id>/toggle` | POST | `sources_toggle` | 560 |
| `/studio/sources/add` | POST | `sources_add` | 546 |
| `/studio/sources/fix` | POST | `sources_fix` | 594 |
| `/studio/sources/run-all` | POST | `sources_run_all` | 603 |
| `/templates/<int:template_id>/delete` | POST | `delete_template` | 1040 |
| `/templates/<int:template_id>/edit` | GET, POST | `edit_template` | 1027 |
| `/templates/<int:template_id>/edit-blocks` | GET | `edit_blocks_template` | 1068 |
| `/track/auto-click/<token>` | GET | `track_auto_click` | 2183 |
| `/track/auto-open/<int:contact_id>/<int:template_id>` | GET | `track_auto_open_legacy` | 2152 |
| `/track/auto-open/<token>` | GET | `track_auto_open` | 2111 |
| `/track/flow-open/<int:enrollment_id>/<int:step_id>` | GET | `track_flow_open` | 4038 |
| `/track/flow-open/<token>` | GET | `track_flow_open_token` | 2050 |
| `/track/open/<int:campaign_id>/<int:contact_id>` | GET | `track_open` | 1968 |
| `/unsubscribe/<token>` | GET, POST | `unsubscribe_token` | 1999 |
| `/warmup/advance-phase` | POST | `warmup_advance_phase` | 2481 |
| `/warmup/checklist` | POST | `warmup_checklist` | 2467 |
| `/warmup/toggle` | POST | `warmup_toggle` | 2452 |

---

## HTML Templates (36 files)

- **`activity.html`** (25.4KB, extends base.html) -- Activity feed — real-time event log with type filters, auto-refresh via /api/activity/feed polling.
- **`agent.html`** (16.4KB, extends base.html) -- IT Agent chat — ChatGPT-style interface, message bubbles, input field, sends to /api/agent/chat.
- **`ai_engine.html`** (28.5KB, extends base.html) -- AI Engine dashboard (28KB) — segment distribution pie chart, today's plan table, decision log with filters, run-now button, sample email generator.
- **`audit.html`** (10.5KB, extends base.html) -- Audit dashboard — ActionLedger viewer with date range, trigger type, source filters, detail modal.
- **`auto_pilot.html`** (11.1KB, extends base.html)
- **`base.html`** (22.9KB, extends none) -- Master layout — dark glass theme, sidebar navigation (all page links), topbar, CSS variables, Font Awesome icons, jQuery. All other templates extend this.
- **`campaign_detail.html`** (7.7KB, extends base.html) -- Campaign detail — recipient table with per-email status, opened/clicked indicators, error messages.
- **`campaign_form.html`** (4.6KB, extends base.html) -- Create/edit campaign — template selector, segment filter builder, from name/email, reply-to.
- **`campaign_planner.html`** (12.2KB, extends base.html) -- AI campaign planner — suggested campaign cards with quality scores, accept/dismiss buttons, brief preview modal.
- **`campaigns.html`** (2.3KB, extends base.html) -- Campaign list — table with status badges (draft/sending/sent), send counts, open/click rates.
- **`contacts.html`** (12.0KB, extends base.html) -- Contact list — search bar, segment filter tabs, paginated table (email, name, source, subscribed, orders, spent), import CSV modal, Shopify sync button.
- **`dashboard.html`** (12.0KB, extends base.html) -- Main dashboard — 4 stat cards (contacts, campaigns, open rate, revenue), recent activity table, warmup status card, quick action buttons.
- **`flow_detail.html`** (19.1KB, extends base.html) -- Flow detail (19KB) — visual step timeline, per-step stats (sent/opened/clicked), enrollment table, add step form.
- **`flows.html`** (9.1KB, extends base.html) -- Flow list — cards with trigger type icon, step count, enrollment count, active toggle, priority control.
- **`learning_dashboard.html`** (32.4KB, extends base.html) -- Learning dashboard (24KB) — phase indicator (observation/conservative/active), template performance table, action effectiveness heatmap, model weights display, toggle button.
- **`profile_detail.html`** (68.1KB, extends base.html) -- Full contact profile (67KB) — intelligence summary card, lifecycle/type/intent/churn badges, purchase history timeline, engagement chart, category affinity radar, AI email preview modal, quick send form, decision history table.
- **`profiles.html`** (20.2KB, extends base.html) -- Intelligence profiles — search, lifecycle stage filter pills, sortable table (email, lifecycle, type, intent, churn risk, LTV, last decision).
- **`profit_dashboard.html`** (11.5KB, extends base.html) -- Profit dashboard — product profitability table, margin analysis, promo eligibility recommendations.
- **`sent_emails.html`** (11.1KB, extends base.html) -- Sent email log — filterable table (campaign/flow emails), preview link, status, open/click timestamps.
- **`settings.html`** (9.8KB, extends base.html) -- Settings — delivery mode selector (live/shadow/sandbox), SES test send, system config.
- **`studio\dashboard.html`** (13.1KB, extends base.html)
- **`studio\generate.html`** (6.1KB, extends base.html)
- **`studio\job.html`** (7.6KB, extends base.html)
- **`studio\jobs.html`** (4.8KB, extends base.html)
- **`studio\knowledge.html`** (17.3KB, extends base.html)
- **`studio\models.html`** (6.7KB, extends base.html)
- **`studio\pending.html`** (9.6KB, extends base.html)
- **`studio\scrape_log.html`** (5.2KB, extends base.html)
- **`studio\sources.html`** (11.3KB, extends base.html)
- **`system_map.html`** (43.5KB, extends base.html) -- System map (43KB) — D3.js force graph, category filter pills, search, node detail panel, 65+ component nodes with live stats.
- **`telemetry.html`** (6.6KB, extends base.html) -- Telemetry — 4 stat cards (total renders, success rate, fallback rate, avg latency), family performance table, field breakdown table. Auto-refreshes every 30s.
- **`template_builder.html`** (35.4KB, extends base.html) -- Block template editor (35KB) — drag-and-drop block builder, live preview panel, block palette, property inspector, AI generate button.
- **`template_editor.html`** (9.2KB, extends base.html) -- Legacy HTML template editor — code textarea, subject/preview_text fields, test send.
- **`templates.html`** (6.1KB, extends base.html) -- Template library — cards with family badge, format badge (blocks/legacy), preview thumbnail, send count, edit/delete.
- **`unsubscribe.html`** (1.1KB, extends none) -- Public unsubscribe page — standalone (no base.html), confirms unsubscribe action.
- **`warmup.html`** (48.9KB, extends base.html) -- Warmup dashboard (43KB) — 8-phase progress bar, daily send/open/bounce chart, health score gauge, deliverability checklist, domain analysis table, phase advance button.
