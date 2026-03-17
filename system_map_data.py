"""
System Architecture Map — data layer
Returns 65 nodes (with live DB stats) and ~75 edges for the
interactive system map visualization at /system-map.
"""

from datetime import date

CATEGORY_X = {
    "external":     0.05,
    "webhook":      0.20,
    "data":         0.35,
    "intelligence": 0.50,
    "content":      0.50,
    "execution":    0.70,
    "learning":     0.85,
    "database":     0.90,
}


def _safe(fn):
    """Run a callable; return {} on any error."""
    try:
        return fn()
    except Exception:
        return {}


def build_system_map_nodes():
    """Return list of 65 node dicts with live DB stats."""
    from database import (
        Contact, EmailTemplate, Campaign, CampaignEmail, WarmupConfig, WarmupLog,
        Flow, FlowStep, FlowEnrollment, FlowEmail, AgentMessage,
        CustomerProfile, ShopifyOrder, ShopifyOrderItem, ShopifyCustomer,
        CustomerActivity, ProductImageCache, GeneratedDiscount,
        SuppressionEntry, BounceLog, PreflightLog, ContactScore,
        PendingTrigger, AIGeneratedEmail, AIMarketingPlan, AIDecisionLog,
        MessageDecision, MessageDecisionHistory,
    )
    from peewee import fn

    # Pre-fetch commonly reused counts to avoid duplicate queries
    C = {}  # cached counts
    for key, query in [
        ("contacts",        lambda: Contact.select().count()),
        ("contacts_sub",    lambda: Contact.select().where(Contact.subscribed == True).count()),
        ("contacts_shopify",lambda: Contact.select().where(Contact.source == "shopify").count()),
        ("profiles",        lambda: CustomerProfile.select().count()),
        ("templates",       lambda: EmailTemplate.select().count()),
        ("shopify_orders",  lambda: ShopifyOrder.select().count()),
        ("shopify_customers",lambda: ShopifyCustomer.select().count()),
        ("order_items",     lambda: ShopifyOrderItem.select().count()),
        ("activities",      lambda: CustomerActivity.select().count()),
        ("campaign_emails", lambda: CampaignEmail.select().count()),
        ("flow_emails",     lambda: FlowEmail.select().count()),
        ("scores",          lambda: ContactScore.select().count()),
        ("decisions",       lambda: MessageDecision.select().count()),
        ("suppressed",      lambda: SuppressionEntry.select().count()),
        ("bounces_total",   lambda: BounceLog.select().count()),
        ("bounces_today",   lambda: BounceLog.select().where(fn.DATE(BounceLog.timestamp) == date.today()).count()),
        ("plans",           lambda: AIMarketingPlan.select().count()),
        ("generated_emails",lambda: AIGeneratedEmail.select().count()),
        ("discounts",       lambda: GeneratedDiscount.select().count()),
        ("discounts_used",  lambda: GeneratedDiscount.select().where(GeneratedDiscount.used == True).count()),
        ("triggers_total",  lambda: PendingTrigger.select().count()),
        ("triggers_cart",   lambda: PendingTrigger.select().where(PendingTrigger.trigger_type == "cart_abandonment").count()),
        ("triggers_pending",lambda: PendingTrigger.select().where(PendingTrigger.status == "pending").count()),
        ("flows_active",    lambda: Flow.select().where(Flow.is_active == True).count()),
        ("enrollments_active",lambda: FlowEnrollment.select().where(FlowEnrollment.status == "active").count()),
        ("flow_steps",      lambda: FlowStep.select().count()),
        ("campaigns_sending",lambda: Campaign.select().where(Campaign.status == "sending").count()),
        ("campaigns_sent",  lambda: Campaign.select().where(Campaign.status == "sent").count()),
        ("campaign_emails_sent",lambda: CampaignEmail.select().where(CampaignEmail.status == "sent").count()),
        ("opens_today",     lambda: CampaignEmail.select().where(CampaignEmail.opened == True, fn.DATE(CampaignEmail.created_at) == date.today()).count()),
        ("clicks_today",    lambda: CampaignEmail.select().where(CampaignEmail.clicked == True, fn.DATE(CampaignEmail.created_at) == date.today()).count()),
        ("flow_opens_today",lambda: FlowEmail.select().where(FlowEmail.opened == True, fn.DATE(FlowEmail.sent_at) == date.today()).count()),
        ("decision_history",lambda: MessageDecisionHistory.select().count()),
        ("decisions_executed",lambda: MessageDecisionHistory.select().where(MessageDecisionHistory.was_executed == True).count()),
        ("decisions_send",  lambda: MessageDecision.select().where(MessageDecision.action_type == "send").count()),
        ("intel_count",     lambda: CustomerProfile.select().where(CustomerProfile.last_intelligence_at.is_null(False)).count()),
        ("pixel_events",    lambda: CustomerActivity.select().where(CustomerActivity.source == "pixel").count()),
    ]:
        try:
            C[key] = query()
        except Exception:
            C[key] = None

    # Warmup config (singleton)
    try:
        _wc = WarmupConfig.get_or_none()
    except Exception:
        _wc = None

    nodes = []

    def _node(id, label, category, icon, stats=None, link=None):
        nodes.append({
            "id":       id,
            "label":    label,
            "category": category,
            "icon":     icon,
            "stats":    stats if stats else {},
            "link":     link,
            "xHint":    CATEGORY_X.get(category, 0.5),
        })

    # ── External Sources (4) ──────────────────────────────────────────────
    _node("shopify_store", "Shopify Store", "external", "fa-shopify",
          {"customers": C["shopify_customers"], "orders": C["shopify_orders"]})

    _node("amazon_ses", "Amazon SES", "external", "fa-envelope",
          {"campaign_emails": C["campaign_emails"], "flow_emails": C["flow_emails"]})

    _node("openrouter_llm", "OpenRouter LLM", "external", "fa-brain")

    _node("shopify_pixel", "Shopify Pixel", "external", "fa-code",
          {"activities": C["activities"]})

    # ── Webhooks & Triggers (10) ──────────────────────────────────────────
    _node("wh_customer", "Customer Webhook", "webhook", "fa-user-plus",
          {"contacts": C["contacts_shopify"]})

    _node("wh_order", "Order Webhook", "webhook", "fa-shopping-cart",
          {"orders": C["shopify_orders"]})

    _node("wh_checkout", "Checkout Webhook", "webhook", "fa-credit-card")

    _node("wh_ses_bounce", "SES Bounce Webhook", "webhook", "fa-exclamation-triangle",
          {"bounces_today": C["bounces_today"]})

    _node("api_track", "Track API", "webhook", "fa-eye",
          {"events": C["pixel_events"]})

    _node("api_identify", "Identify API", "webhook", "fa-fingerprint")

    _node("api_subscribe", "Subscribe API", "webhook", "fa-bell",
          {"subscribed": C["contacts_sub"]})

    _node("checker_abandoned", "Abandoned Cart Checker", "webhook", "fa-clock",
          {"pending": C["triggers_cart"]})

    _node("checker_passive", "Passive Trigger Checker", "webhook", "fa-hourglass",
          {"pending": C["triggers_pending"]})

    _node("checker_backlog", "Backlog Processor", "webhook", "fa-redo",
          {"total_triggers": C["triggers_total"]})

    # ── Data & Enrichment (8) ─────────────────────────────────────────────
    _node("identity_resolution", "Identity Resolution", "data", "fa-link",
          {"contacts": C["contacts"]})

    _node("identity_queue", "Identity Queue", "data", "fa-tasks")

    _node("shopify_sync_nightly", "Shopify Sync (Nightly)", "data", "fa-sync",
          {"customers": C["shopify_customers"], "orders": C["shopify_orders"]})

    _node("shopify_sync_incr", "Shopify Sync (Incremental)", "data", "fa-sync-alt",
          {"order_items": C["order_items"]})

    _node("activity_sync", "Activity Sync", "data", "fa-stream",
          {"activities": C["activities"]})

    _node("shopify_enrichment", "Shopify Enrichment", "data", "fa-user-cog",
          {"profiles": C["profiles"]}, link="/profiles")

    _node("knowledge_scraper", "Knowledge Scraper", "data", "fa-spider")

    _node("contact_db", "Contact Database", "data", "fa-database",
          {"total_contacts": C["contacts"], "subscribed": C["contacts_sub"]},
          link="/contacts")

    # ── Intelligence (7) ──────────────────────────────────────────────────
    _node("ai_scoring", "AI Scoring Engine", "intelligence", "fa-chart-bar",
          {"scored": C["scores"]}, link="/ai-engine")

    _node("customer_intelligence", "Customer Intelligence", "intelligence", "fa-brain",
          {"profiles": C["profiles"], "with_intelligence": C["intel_count"]},
          link="/ai-engine")

    _node("next_best_message", "Next Best Message", "intelligence", "fa-bullseye",
          {"decisions": C["decisions"], "send_actions": C["decisions_send"]},
          link="/ai-engine")

    _node("campaign_planner", "Campaign Planner", "intelligence", "fa-lightbulb",
          {"plans": C["plans"]}, link="/ai-engine")

    _node("profit_engine", "Profit Engine", "intelligence", "fa-dollar-sign",
          {"discounts_generated": C["discounts"]})

    _node("deliverability_scoring", "Deliverability Scoring", "intelligence", "fa-shield-alt",
          {"suppressed": C["suppressed"]}, link="/warmup")

    _node("cascade_engine", "Cascade Engine", "intelligence", "fa-bolt",
          {"profiles": C["profiles"]})

    # ── Execution (9) ─────────────────────────────────────────────────────
    _node("flow_processor", "Flow Processor", "execution", "fa-cogs",
          {"active_flows": C["flows_active"], "active_enrollments": C["enrollments_active"]},
          link="/flows")

    _node("delivery_queue", "Delivery Queue", "execution", "fa-paper-plane")

    _node("delivery_engine", "Delivery Engine", "execution", "fa-truck",
          {"campaign_sent": C["campaign_emails_sent"], "flow_sent": C["flow_emails"]})

    _node("warmup_engine", "Warmup Engine", "execution", "fa-fire",
          {"current_phase": _wc.current_phase if _wc else 0,
           "emails_sent_today": _wc.emails_sent_today if _wc else 0,
           "is_active": _wc.is_active if _wc else False},
          link="/warmup")

    _node("condition_engine", "Condition Engine", "execution", "fa-filter",
          {"flow_steps": C["flow_steps"]})

    _node("campaign_sender", "Campaign Sender", "execution", "fa-bullhorn",
          {"sending": C["campaigns_sending"], "sent": C["campaigns_sent"]},
          link="/campaigns")

    _node("email_renderer", "Email Renderer", "execution", "fa-paint-brush",
          {"templates": C["templates"]})

    _node("discount_engine", "Discount Engine", "execution", "fa-tag",
          {"generated": C["discounts"], "used": C["discounts_used"]})

    _node("suppression_check", "Suppression Check", "execution", "fa-ban",
          {"suppressed": C["suppressed"]}, link="/warmup")

    # ── Content (7) ───────────────────────────────────────────────────────
    _node("template_studio", "Template Studio", "content", "fa-palette",
          {"templates": C["templates"]}, link="/templates")

    _node("block_registry", "Block Registry", "content", "fa-th-large")

    _node("ai_content_gen", "AI Content Generator", "content", "fa-magic",
          {"generated": C["generated_emails"]})

    _node("ai_provider", "AI Provider", "content", "fa-robot")

    _node("template_perf", "Template Performance", "content", "fa-chart-line",
          {"templates_tracked": C["templates"]})

    _node("template_candidates", "Template Candidates", "content", "fa-flask")

    _node("suggested_campaigns", "Suggested Campaigns", "content", "fa-lightbulb")

    # ── Learning & Tracking (8) ───────────────────────────────────────────
    _node("open_tracker", "Open Tracker", "learning", "fa-envelope-open",
          {"campaign_opens_today": C["opens_today"], "flow_opens_today": C["flow_opens_today"]})

    _node("click_tracker", "Click Tracker", "learning", "fa-mouse-pointer",
          {"campaign_clicks_today": C["clicks_today"]})

    _node("bounce_handler", "Bounce Handler", "learning", "fa-exclamation-circle",
          {"bounces_today": C["bounces_today"], "total_bounces": C["bounces_total"]})

    _node("action_ledger", "Action Ledger", "learning", "fa-book",
          {"decisions": C["decision_history"]})

    _node("outcome_tracker", "Outcome Tracker", "learning", "fa-search",
          {"executed": C["decisions_executed"]})

    _node("learning_engine", "Learning Engine", "learning", "fa-graduation-cap",
          {"decision_history": C["decision_history"]})

    _node("strategy_optimizer", "Strategy Optimizer", "learning", "fa-chess",
          {"plans": C["plans"]})

    _node("learning_config", "Learning Config", "learning", "fa-toggle-on")

    # ── Database Tables (12 mini-nodes) ───────────────────────────────────
    _node("db_contact", "Contact", "database", "fa-table",
          {"rows": C["contacts"]})

    _node("db_profile", "CustomerProfile", "database", "fa-table",
          {"rows": C["profiles"]})

    _node("db_score", "ContactScore", "database", "fa-table",
          {"rows": C["scores"]})

    _node("db_decision", "MessageDecision", "database", "fa-table",
          {"rows": C["decisions"]})

    _node("db_enrollment", "FlowEnrollment", "database", "fa-table",
          {"rows": C["enrollments_active"]})

    _node("db_campaign_email", "CampaignEmail", "database", "fa-table",
          {"rows": C["campaign_emails"]})

    _node("db_flow_email", "FlowEmail", "database", "fa-table",
          {"rows": C["flow_emails"]})

    _node("db_shopify_order", "ShopifyOrder", "database", "fa-table",
          {"rows": C["shopify_orders"]})

    _node("db_bounce_log", "BounceLog", "database", "fa-table",
          {"rows": C["bounces_total"]})

    _node("db_suppression", "SuppressionEntry", "database", "fa-table",
          {"rows": C["suppressed"]})

    _node("db_delivery_queue", "DeliveryQueue", "database", "fa-table")

    _node("db_warmup_config", "WarmupConfig", "database", "fa-table",
          {"rows": 1 if _wc else 0})

    return nodes


def build_system_map_edges():
    """Return edge dicts describing data flow between nodes."""

    edges = []

    def _edge(source, target, etype, tooltip):
        edges.append({
            "source":  source,
            "target":  target,
            "type":    etype,
            "tooltip": tooltip,
        })

    # ── Inbound (realtime, 10 edges) ──────────────────────────────────────
    _edge("shopify_store",  "wh_customer",        "realtime",  "Shopify customer/create webhook")
    _edge("wh_customer",    "identity_resolution", "realtime",  "Resolve customer identity")
    _edge("identity_resolution", "cascade_engine", "realtime",  "Trigger enrichment cascade")
    _edge("shopify_store",  "wh_order",           "realtime",  "Shopify order/create webhook")
    _edge("wh_order",       "identity_resolution", "realtime",  "Resolve order customer identity")
    _edge("shopify_store",  "wh_checkout",        "realtime",  "Shopify checkout webhook")
    _edge("wh_checkout",    "checker_abandoned",   "realtime",  "Check for abandoned checkouts")
    _edge("shopify_pixel",  "api_track",          "realtime",  "Pixel tracking events")
    _edge("api_track",      "identity_queue",      "realtime",  "Queue identity resolution")
    _edge("shopify_pixel",  "api_identify",       "realtime",  "Pixel identify calls")
    _edge("api_identify",   "identity_resolution", "realtime",  "Resolve pixel identity")
    _edge("shopify_pixel",  "api_subscribe",      "realtime",  "Pixel subscribe events")
    _edge("api_subscribe",  "identity_resolution", "realtime",  "Resolve subscriber identity")
    _edge("amazon_ses",     "wh_ses_bounce",      "realtime",  "SES bounce/complaint SNS notification")
    _edge("wh_ses_bounce",  "bounce_handler",     "realtime",  "Process bounce event")
    _edge("bounce_handler", "db_suppression",     "realtime",  "Add to suppression list")

    # ── Cascade (realtime, 4 edges) ───────────────────────────────────────
    _edge("cascade_engine",  "shopify_enrichment",    "realtime",  "Enrich customer profile from Shopify")
    _edge("shopify_enrichment", "db_profile",         "realtime",  "Write enriched profile")
    _edge("cascade_engine",  "ai_scoring",            "realtime",  "Trigger RFM scoring")
    _edge("ai_scoring",      "db_score",              "realtime",  "Write contact score")
    _edge("cascade_engine",  "customer_intelligence", "realtime",  "Trigger intelligence computation")
    _edge("customer_intelligence", "db_profile",      "realtime",  "Update profile with intelligence")
    _edge("cascade_engine",  "next_best_message",     "realtime",  "Trigger decision engine")
    _edge("next_best_message", "db_decision",         "realtime",  "Write message decision")

    # ── Nightly pipeline (scheduled, 8 edges) ────────────────────────────
    _edge("shopify_sync_nightly", "activity_sync",         "scheduled", "Sync Shopify data nightly")
    _edge("activity_sync",        "ai_scoring",            "scheduled", "Feed activity data to scoring")
    _edge("ai_scoring",           "customer_intelligence", "scheduled", "Score feeds intelligence")
    _edge("customer_intelligence","deliverability_scoring", "scheduled", "Intelligence feeds deliverability")
    _edge("deliverability_scoring","next_best_message",    "scheduled", "Deliverability informs decisions")
    _edge("next_best_message",    "campaign_planner",      "scheduled", "Decisions feed campaign planning")
    _edge("campaign_planner",     "knowledge_scraper",     "scheduled", "Planner triggers knowledge scrape")
    _edge("knowledge_scraper",    "profit_engine",         "scheduled", "Knowledge feeds profit optimization")

    # ── Learning pipeline (scheduled + feedback) ──────────────────────────
    _edge("outcome_tracker",     "learning_engine",     "scheduled", "Outcomes feed learning")
    _edge("learning_engine",     "strategy_optimizer",  "scheduled", "Learning refines strategy")
    _edge("strategy_optimizer",  "next_best_message",   "feedback",  "Optimized strategy improves decisions")
    _edge("strategy_optimizer",  "template_perf",       "feedback",  "Optimized strategy refines templates")

    # ── Execution (continuous, 8 edges) ───────────────────────────────────
    _edge("next_best_message", "flow_processor",     "continuous", "Decisions trigger flow enrollments")
    _edge("flow_processor",    "condition_engine",    "continuous", "Evaluate flow step conditions")
    _edge("condition_engine",  "email_renderer",      "continuous", "Render email from template")
    _edge("email_renderer",    "delivery_queue",      "continuous", "Queue rendered email for delivery")
    _edge("campaign_sender",   "email_renderer",      "continuous", "Campaign sends through renderer")
    _edge("delivery_queue",    "suppression_check",   "continuous", "Check suppression before send")
    _edge("suppression_check", "warmup_engine",       "continuous", "Apply warmup rate limits")
    _edge("warmup_engine",     "delivery_engine",     "continuous", "Deliver within warmup limits")
    _edge("delivery_engine",   "amazon_ses",          "continuous", "Send via Amazon SES API")

    # ── Content (realtime, 7 edges) ───────────────────────────────────────
    _edge("campaign_planner",    "suggested_campaigns",  "realtime",  "AI suggests campaign ideas")
    _edge("suggested_campaigns", "template_studio",      "realtime",  "Suggested campaigns use templates")
    _edge("template_studio",     "block_registry",       "realtime",  "Templates use content blocks")
    _edge("block_registry",      "email_renderer",       "realtime",  "Blocks feed into renderer")
    _edge("ai_content_gen",      "ai_provider",          "realtime",  "Content gen calls AI provider")
    _edge("ai_provider",         "openrouter_llm",       "realtime",  "AI provider calls OpenRouter")
    _edge("template_studio",     "ai_content_gen",       "realtime",  "Templates use AI content generation")
    _edge("template_studio",     "template_candidates",  "realtime",  "Studio produces template candidates")
    _edge("template_perf",       "template_studio",      "feedback",  "Performance data refines templates")

    # ── Tracking (realtime, 7 edges) ──────────────────────────────────────
    _edge("amazon_ses",      "open_tracker",       "realtime",  "SES open notification")
    _edge("open_tracker",    "db_campaign_email",  "realtime",  "Record campaign email open")
    _edge("open_tracker",    "db_flow_email",      "realtime",  "Record flow email open")
    _edge("amazon_ses",      "click_tracker",      "realtime",  "SES click notification")
    _edge("click_tracker",   "db_campaign_email",  "realtime",  "Record campaign email click")
    _edge("open_tracker",    "action_ledger",      "realtime",  "Log open action")
    _edge("click_tracker",   "action_ledger",      "realtime",  "Log click action")
    _edge("bounce_handler",  "action_ledger",      "realtime",  "Log bounce action")
    _edge("delivery_engine", "action_ledger",      "realtime",  "Log delivery action")

    # ── Database writes (realtime, 12 edges) ──────────────────────────────
    _edge("identity_resolution", "db_contact",        "realtime",  "Create/update contact record")
    _edge("shopify_enrichment",  "db_profile",        "realtime",  "Write enriched profile")
    _edge("ai_scoring",          "db_score",          "realtime",  "Write contact score")
    _edge("customer_intelligence","db_profile",       "realtime",  "Update profile intelligence")
    _edge("next_best_message",   "db_decision",       "realtime",  "Write message decision")
    _edge("flow_processor",      "db_enrollment",     "realtime",  "Track flow enrollment")
    _edge("delivery_engine",     "db_campaign_email",  "realtime",  "Record campaign email sent")
    _edge("delivery_engine",     "db_flow_email",      "realtime",  "Record flow email sent")
    _edge("wh_order",            "db_shopify_order",   "realtime",  "Store Shopify order")
    _edge("delivery_queue",      "db_delivery_queue",  "realtime",  "Queue delivery record")
    _edge("warmup_engine",       "db_warmup_config",   "realtime",  "Update warmup state")
    _edge("bounce_handler",      "db_bounce_log",      "realtime",  "Log bounce event")

    return edges
