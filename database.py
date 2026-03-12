"""
Database Models — SQLite via Peewee ORM
No external database server needed.
"""

from peewee import *
from datetime import datetime
import os

# Always store the database in the same folder as this file
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_PATH  = os.path.join(_BASE_DIR, "email_platform.db")

db = SqliteDatabase(_DB_PATH, pragmas={"foreign_keys": 1})


class BaseModel(Model):
    class Meta:
        database = db


class Contact(BaseModel):
    email       = CharField(unique=True, index=True)
    first_name  = CharField(default="")
    last_name   = CharField(default="")
    phone       = CharField(default="")
    tags        = CharField(default="")       # comma-separated: "vip,repeat-buyer"
    source      = CharField(default="manual") # shopify | csv_import | manual
    subscribed  = BooleanField(default=True)  # Email subscription
    sms_consent = BooleanField(default=False) # SMS marketing consent
    created_at  = DateTimeField(default=datetime.now)
    # Shopify-enriched fields
    shopify_id   = CharField(default="")      # Shopify customer ID
    city         = CharField(default="")      # from default_address.city
    country      = CharField(default="")      # from default_address.country_code
    total_orders = IntegerField(default=0)    # orders_count from Shopify
    total_spent  = FloatField(default=0.0)    # total_spent as float e.g. 149.99
    # ── Deliverability fields (Phase I) ──
    fatigue_score       = IntegerField(default=0)        # 0-100
    spam_risk_score     = IntegerField(default=0)        # 0-100
    suppression_reason  = CharField(default="")          # hard_bounce | complaint | invalid | manual | fatigue
    suppression_source  = CharField(default="")          # ses_notification | import_validation | admin | system
    suppression_until   = DateTimeField(null=True)       # temporary suppression expiry
    last_open_at        = DateTimeField(null=True)
    last_click_at       = DateTimeField(null=True)
    emails_received_7d  = IntegerField(default=0)
    emails_received_30d = IntegerField(default=0)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    @property
    def is_suppressed(self):
        """True if contact is currently suppressed (permanent or temporary)."""
        if not self.suppression_reason:
            return False
        if self.suppression_until and self.suppression_until < datetime.now():
            return False  # Temporary suppression expired
        return True

    class Meta:
        table_name = "contacts"


class EmailTemplate(BaseModel):
    name         = CharField()
    subject      = CharField()
    preview_text = CharField(default="")
    html_body    = TextField()
    shell_version = IntegerField(default=1)
    created_at   = DateTimeField(default=datetime.now)
    updated_at   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "email_templates"


class Campaign(BaseModel):
    name           = CharField()
    from_name      = CharField()
    from_email     = CharField()
    reply_to       = CharField(default="")
    template_id    = IntegerField()
    segment_filter = CharField(default="all")  # "all" | tag name
    status         = CharField(default="draft") # draft | sending | sent
    created_at     = DateTimeField(default=datetime.now)
    sent_at        = DateTimeField(null=True)

    class Meta:
        table_name = "campaigns"


class CampaignEmail(BaseModel):
    campaign   = ForeignKeyField(Campaign, backref="emails")
    contact    = ForeignKeyField(Contact,  backref="campaign_emails")
    status     = CharField(default="pending")  # pending | sent | failed | bounced
    error_msg  = TextField(default="")
    opened     = BooleanField(default=False)
    opened_at  = DateTimeField(null=True)
    clicked    = BooleanField(default=False)
    clicked_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "campaign_emails"


class WarmupConfig(BaseModel):
    """Singleton row (id=1) — controls the active warmup state and checklist."""
    is_active          = BooleanField(default=False)
    warmup_started_at  = DateTimeField(null=True)
    current_phase      = IntegerField(default=1)    # 1–8
    emails_sent_today  = IntegerField(default=0)
    last_reset_date    = CharField(default="")      # YYYY-MM-DD
    # Pre-send checklist (manually confirmed by user)
    check_spf          = BooleanField(default=False)
    check_dkim         = BooleanField(default=False)
    check_dmarc        = BooleanField(default=False)
    check_sandbox      = BooleanField(default=False)  # SES production access granted
    check_list_cleaned = BooleanField(default=False)
    check_subdomain    = BooleanField(default=False)  # Sending from mail.domain.com

    class Meta:
        table_name = "warmup_config"


class WarmupLog(BaseModel):
    """One row per calendar day — used for the 14-day trend chart."""
    log_date       = CharField(unique=True)  # YYYY-MM-DD
    phase          = IntegerField(default=1)
    daily_limit    = IntegerField(default=0)
    emails_sent    = IntegerField(default=0)
    emails_opened  = IntegerField(default=0)
    emails_bounced = IntegerField(default=0)

    class Meta:
        table_name = "warmup_log"


class Flow(BaseModel):
    """An automation flow definition."""
    name          = CharField()
    description   = CharField(default="")
    trigger_type  = CharField()   # contact_created | tag_added | no_purchase_days | manual
    trigger_value = CharField(default="")  # tag name for tag_added; days as string for no_purchase_days
    is_active     = BooleanField(default=False)
    priority      = IntegerField(default=5)  # 1=highest, 10=lowest
    created_at    = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "flows"


class FlowStep(BaseModel):
    """One email step within a flow."""
    flow             = ForeignKeyField(Flow, backref="steps")
    step_order       = IntegerField()           # 1, 2, 3 ...
    delay_hours      = IntegerField(default=0)  # hours to wait before sending this step
    template         = ForeignKeyField(EmailTemplate)
    from_name        = CharField(default="")
    from_email       = CharField(default="")    # blank = use DEFAULT_FROM_EMAIL
    subject_override = CharField(default="")    # blank = use template subject

    class Meta:
        table_name = "flow_steps"


class FlowEnrollment(BaseModel):
    """Tracks one contact's journey through a flow."""
    flow         = ForeignKeyField(Flow, backref="enrollments")
    contact      = ForeignKeyField(Contact, backref="flow_enrollments")
    current_step = IntegerField(default=1)
    enrolled_at  = DateTimeField(default=datetime.now)
    next_send_at = DateTimeField()
    status       = CharField(default="active")  # active | completed | cancelled | paused
    paused_by_flow = IntegerField(default=0)  # flow_id that caused pause, 0=not paused

    class Meta:
        table_name      = "flow_enrollments"
        indexes         = ((("flow_id", "contact_id"), True),)  # unique per flow+contact


class FlowEmail(BaseModel):
    """One email sent as part of a flow — for tracking opens."""
    enrollment = ForeignKeyField(FlowEnrollment, backref="emails")
    step       = ForeignKeyField(FlowStep)
    contact    = ForeignKeyField(Contact)
    status     = CharField(default="sent")  # sent | failed
    sent_at    = DateTimeField(default=datetime.now)
    opened     = BooleanField(default=False)
    opened_at  = DateTimeField(null=True)

    class Meta:
        table_name = "flow_emails"


class AbandonedCheckout(BaseModel):
    """Shopify checkout that was started but not completed."""
    shopify_checkout_id = CharField(unique=True, index=True)
    email               = CharField(index=True, default="")
    contact             = ForeignKeyField(Contact, null=True, backref="abandoned_checkouts")
    checkout_url        = CharField(default="")
    total_price         = FloatField(default=0.0)
    currency            = CharField(default="CAD")
    line_items_json     = TextField(default="[]")   # JSON: [{title, quantity, price, image_url}]
    recovered           = BooleanField(default=False)
    recovered_at        = DateTimeField(null=True)
    abandoned_at        = DateTimeField(null=True)   # when Shopify created the checkout
    enrolled_in_flow    = BooleanField(default=False)
    created_at          = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "abandoned_checkouts"


class AgentMessage(BaseModel):
    """Persistent IT Agent conversation history."""
    role       = CharField()              # 'user' | 'assistant'
    content    = TextField(default="")
    tool_calls = TextField(default="[]")  # JSON array of tool call logs
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "agent_messages"


def get_warmup_config():
    """Return the singleton WarmupConfig row, creating it if it doesn't exist."""
    config, _ = WarmupConfig.get_or_create(id=1)
    return config


def _migrate_contact_columns():
    """Safely add new columns to contacts table without dropping existing data."""
    new_cols = [
        ("shopify_id",    "VARCHAR(255) DEFAULT ''"),
        ("city",          "VARCHAR(255) DEFAULT ''"),
        ("country",       "VARCHAR(255) DEFAULT ''"),
        ("total_orders",  "INTEGER DEFAULT 0"),
        ("total_spent",   "REAL DEFAULT 0.0"),
        ("sms_consent",   "INTEGER DEFAULT 0"),
    ]
    cursor = db.execute_sql("PRAGMA table_info(contacts)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in new_cols:
        if col_name not in existing:
            db.execute_sql(f"ALTER TABLE contacts ADD COLUMN {col_name} {col_def}")



# ─────────────────────────────────────────────────────────────────────────────
# Customer Data Enrichment Models
# ─────────────────────────────────────────────────────────────────────────────

class OmnisendOrder(BaseModel):
    """Order record pulled from Omnisend API."""
    contact          = ForeignKeyField(Contact, backref="orders", null=True)
    email            = CharField(index=True)
    order_id         = CharField(unique=True)
    order_number     = IntegerField(default=0)
    order_total      = FloatField(default=0.0)    # in dollars (already /100)
    currency         = CharField(default="CAD")
    payment_status   = CharField(default="")
    fulfillment_status = CharField(default="")
    discount_code    = CharField(default="")
    discount_amount  = FloatField(default=0.0)
    shipping_city    = CharField(default="")
    shipping_province = CharField(default="")
    ordered_at       = DateTimeField(null=True)
    created_at       = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "omnisend_orders"


class OmnisendOrderItem(BaseModel):
    """Individual product line item within an order."""
    order         = ForeignKeyField(OmnisendOrder, backref="items")
    product_id    = CharField(default="")
    product_title = CharField(default="")
    variant_title = CharField(default="")
    sku           = CharField(default="")
    quantity      = IntegerField(default=1)
    unit_price    = FloatField(default=0.0)    # in dollars
    discount      = FloatField(default=0.0)
    vendor        = CharField(default="")

    class Meta:
        table_name = "omnisend_order_items"


class CustomerProfile(BaseModel):
    """Enriched customer profile — computed nightly from order history."""
    contact              = ForeignKeyField(Contact, unique=True, backref="profile")
    email                = CharField(index=True)

    # Purchase history
    total_orders         = IntegerField(default=0)
    total_spent          = FloatField(default=0.0)
    avg_order_value      = FloatField(default=0.0)
    first_order_at       = DateTimeField(null=True)
    last_order_at        = DateTimeField(null=True)
    days_since_last_order = IntegerField(default=999)
    avg_days_between_orders = FloatField(default=0.0)  # purchase frequency

    # Product preferences
    top_products         = TextField(default="[]")   # JSON list of product titles
    top_categories       = TextField(default="[]")   # JSON list of inferred categories
    all_products_bought  = TextField(default="[]")   # JSON full purchase history

    # Behavioural signals
    price_tier           = CharField(default="unknown")  # budget/mid/premium
    has_used_discount    = BooleanField(default=False)
    discount_sensitivity = FloatField(default=0.0)  # % of orders with discount
    total_items_bought   = IntegerField(default=0)

    # Geography
    city                 = CharField(default="")
    province             = CharField(default="")

    # AI-ready summary (updated nightly)
    profile_summary      = TextField(default="")  # plain-English for Claude
    last_computed_at     = DateTimeField(default=datetime.now)

    # Activity enrichment fields (added via migration — must match ALTER TABLE in _migrate_activity_fields)
    checkout_abandonment_count = IntegerField(default=0)
    last_active_at             = DateTimeField(null=True)
    total_page_views           = IntegerField(default=0)
    total_product_views        = IntegerField(default=0)
    website_engagement_score   = IntegerField(default=0)
    last_viewed_product        = CharField(max_length=500, default="")

    # Predictive intelligence (Phase G)
    churn_risk                 = FloatField(default=0.0)   # 0=safe, 1=overdue, 2+=churned
    predicted_next_order_date  = DateTimeField(null=True)
    predicted_ltv              = FloatField(default=0.0)   # predicted 3-year lifetime value
    product_recommendations    = TextField(default="[]")   # JSON list of recommended product titles

    # ── Customer Intelligence (Phase 2A) ──────────────────────
    # Core classification
    lifecycle_stage          = CharField(default="unknown")     # prospect|new_customer|active_buyer|loyal|vip|at_risk|churned|reactivated
    customer_type            = CharField(default="unknown")     # browser|one_time|repeat|loyal|vip|discount_seeker|dormant

    # Scores (all 0-100)
    intent_score             = IntegerField(default=0)          # purchase intent 0-100
    reorder_likelihood       = IntegerField(default=0)          # 0-100
    category_affinity_json   = TextField(default="{}")          # JSON: {"Bluetooth Headsets": 82, ...}
    next_purchase_category   = CharField(default="")            # predicted next category

    # Engagement & timing
    preferred_send_hour      = IntegerField(default=-1)         # 0-23 or -1 (unknown)
    preferred_send_dow       = IntegerField(default=-1)         # 0=Mon..6=Sun or -1
    channel_preference       = CharField(default="email")       # email|sms|both

    # Confidence scores (0-100)
    confidence_lifecycle     = IntegerField(default=0)
    confidence_intent        = IntegerField(default=0)
    confidence_reorder       = IntegerField(default=0)
    confidence_category      = IntegerField(default=0)
    confidence_send_window   = IntegerField(default=0)
    confidence_channel       = IntegerField(default=0)
    confidence_discount      = IntegerField(default=0)

    # Churn risk normalized
    churn_risk_score         = IntegerField(default=0)          # 0-100 normalized
    confidence_churn         = IntegerField(default=0)

    # Metadata
    intelligence_summary     = TextField(default="")            # plain-English for Claude
    last_intelligence_at     = DateTimeField(null=True)

    class Meta:
        table_name = "customer_profiles"


class ShopifyOrder(BaseModel):
    """Order pulled directly from Shopify — source of truth for all purchases."""
    contact            = ForeignKeyField(Contact, null=True, backref="shopify_orders")
    shopify_order_id   = CharField(unique=True)
    order_number       = IntegerField(default=0)
    email              = CharField(index=True, default="")
    first_name         = CharField(default="")
    last_name          = CharField(default="")
    order_total        = FloatField(default=0.0)
    subtotal           = FloatField(default=0.0)
    total_tax          = FloatField(default=0.0)
    total_discounts    = FloatField(default=0.0)
    currency           = CharField(default="CAD")
    financial_status   = CharField(default="")
    fulfillment_status = CharField(default="")
    discount_codes     = CharField(default="")
    shipping_city      = CharField(default="")
    shipping_province  = CharField(default="")
    source_name        = CharField(default="web")
    tags               = CharField(default="")
    ordered_at         = DateTimeField(null=True)
    created_at         = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "shopify_orders"


class ShopifyOrderItem(BaseModel):
    """Line item within a Shopify order."""
    order            = ForeignKeyField(ShopifyOrder, backref="items")
    shopify_line_id  = CharField(default="")
    product_id       = CharField(default="")
    variant_id       = CharField(default="")
    product_title    = CharField(default="")
    variant_title    = CharField(default="")
    sku              = CharField(default="")
    quantity         = IntegerField(default=1)
    unit_price       = FloatField(default=0.0)
    total_discount   = FloatField(default=0.0)
    vendor           = CharField(default="")
    product_type     = CharField(default="")

    class Meta:
        table_name = "shopify_order_items"


class ShopifyCustomer(BaseModel):
    """Customer record from Shopify — enriches Contact profiles."""
    contact          = ForeignKeyField(Contact, null=True, unique=True, backref="shopify_customer")
    shopify_id       = CharField(unique=True)
    email            = CharField(index=True, default="")
    first_name       = CharField(default="")
    last_name        = CharField(default="")
    phone            = CharField(default="")
    orders_count     = IntegerField(default=0)
    total_spent      = FloatField(default=0.0)
    tags             = CharField(default="")
    city             = CharField(default="")
    province         = CharField(default="")
    country          = CharField(default="")
    accepts_marketing = BooleanField(default=False)
    shopify_created_at = DateTimeField(null=True)
    last_order_at    = DateTimeField(null=True)
    last_synced_at   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "shopify_customers"



class CustomerActivity(BaseModel):
    """Single customer activity event from any source."""
    contact     = ForeignKeyField(Contact, null=True, backref="activities")
    email       = CharField(index=True, default="")
    event_type  = CharField(default="")   # viewed_product|viewed_page|started_checkout|abandoned_checkout|completed_checkout|placed_order|email_activity|pixel_event
    event_data  = TextField(default="{}") # JSON: product_title, url, page_title, order_number, amount, etc.
    source      = CharField(default="")   # shopify_checkout|shopify_order|omnisend|pixel|email_campaign
    source_ref  = CharField(default="", index=True)  # external ID for dedup
    session_id  = CharField(default="")   # group events in a browsing session
    occurred_at = DateTimeField(default=datetime.now, index=True)
    created_at  = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "customer_activity"



def _migrate_activity_fields():
    """Add activity-derived fields to customer_profiles and customer_activity table."""
    # New CustomerProfile columns
    new_cols = [
        ("customer_profiles", "checkout_abandonment_count", "INTEGER DEFAULT 0"),
        ("customer_profiles", "last_active_at",            "DATETIME"),
        ("customer_profiles", "total_page_views",           "INTEGER DEFAULT 0"),
        ("customer_profiles", "total_product_views",        "INTEGER DEFAULT 0"),
        ("customer_profiles", "website_engagement_score",   "INTEGER DEFAULT 0"),
        ("customer_profiles", "last_viewed_product",        "VARCHAR(500) DEFAULT ''"),
    ]
    for table, col_name, col_def in new_cols:
        try:
            cursor = db.execute_sql(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            if col_name not in existing:
                db.execute_sql(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                print(f"[migrate] Added {col_name} to {table}")
        except Exception as e:
            print(f"[migrate] Skipped {col_name}: {e}")

class ProductImageCache(BaseModel):
    """Cache of Shopify product images for use in emails."""
    product_id    = CharField(unique=True, index=True)
    product_title = CharField(default="")
    image_url     = CharField(default="")
    product_url   = CharField(default="")
    price         = CharField(default="0.00")
    compare_price = CharField(default="")
    product_type  = CharField(default="")
    handle        = CharField(default="")
    last_synced   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "product_image_cache"


class GeneratedDiscount(BaseModel):
    """Tracks Shopify discount codes generated for email campaigns."""
    contact         = ForeignKeyField(Contact, backref="discounts", null=True)
    email           = CharField(index=True)
    code            = CharField(unique=True, index=True)
    purpose         = CharField()
    discount_type   = CharField(default="percentage")
    value           = CharField(default="5")
    shopify_price_rule_id = CharField(default="")
    shopify_discount_id   = CharField(default="")
    expires_at      = DateTimeField(null=True)
    used            = BooleanField(default=False)
    used_at         = DateTimeField(null=True)
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "generated_discounts"



class SuppressionEntry(BaseModel):
    """Email addresses that should never be sent to (hard bounces, complaints, invalid)."""
    email       = CharField(unique=True, index=True)
    reason      = CharField(default="")        # hard_bounce | complaint | invalid | manual
    source      = CharField(default="")        # ses_notification | import_validation | admin
    detail      = TextField(default="")        # bounce type, diagnostic code, etc.
    created_at  = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "suppression_list"


class BounceLog(BaseModel):
    """Log of every bounce/complaint event from SES SNS notifications."""
    email            = CharField(index=True)
    event_type       = CharField(default="")        # Bounce | Complaint
    sub_type         = CharField(default="")        # Permanent | Transient | abuse | not-spam
    diagnostic       = TextField(default="")
    campaign_id      = IntegerField(default=0)
    timestamp        = DateTimeField(default=datetime.now)
    # ── Attribution fields (Phase I completion) ──
    recipient_domain = CharField(default="")        # gmail.com, yahoo.com, etc.
    template_id      = IntegerField(default=0)
    subject_family   = CharField(default="")        # first ~50 chars of campaign subject
    ses_message_id   = CharField(default="")        # SES MessageId for dedup

    class Meta:
        table_name = "bounce_log"



class PreflightLog(BaseModel):
    """Stores the result of campaign preflight checks."""
    campaign_id = IntegerField()
    overall     = CharField()       # PASS | WARN | BLOCK
    checks_json = TextField()       # JSON array of check results
    created_at  = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "preflight_log"



def _migrate_flow_priority():
    """Add priority column to flows table."""
    cursor = db.execute_sql("PRAGMA table_info(flows)")
    existing = {row[1] for row in cursor.fetchall()}
    if "priority" not in existing:
        db.execute_sql("ALTER TABLE flows ADD COLUMN priority INTEGER DEFAULT 5")
        print("  [migrate] Added priority to flows")
        # Set smart defaults based on trigger type
        for trigger, prio in [("checkout_abandoned", 1), ("browse_abandonment", 2),
                              ("order_placed", 3), ("contact_created", 4), ("no_purchase_days", 5)]:
            db.execute_sql("UPDATE flows SET priority = ? WHERE trigger_type = ?", (prio, trigger))
        print("  [migrate] Set default flow priorities")


def _migrate_flow_enrollment_pause():
    """Add paused_by_flow column to flow_enrollments table."""
    cursor = db.execute_sql("PRAGMA table_info(flow_enrollments)")
    existing = {row[1] for row in cursor.fetchall()}
    if "paused_by_flow" not in existing:
        db.execute_sql("ALTER TABLE flow_enrollments ADD COLUMN paused_by_flow INTEGER DEFAULT 0")
        print("  [migrate] Added paused_by_flow to flow_enrollments")


def _migrate_total_spent_to_float():
    """Convert total_spent from string to float."""
    try:
        db.execute_sql("""
            UPDATE contacts SET total_spent = CAST(
                CASE WHEN total_spent IS NULL OR total_spent = '' THEN '0'
                     ELSE total_spent END AS REAL
            ) WHERE typeof(total_spent) = 'text'
        """)
        affected = db.execute_sql("SELECT changes()").fetchone()[0]
        if affected > 0:
            print("  [migrate] Converted %d total_spent values from string to float" % affected)
    except Exception as e:
        print("  [migrate] total_spent conversion: %s" % e)


def _migrate_system_config():
    """Ensure the singleton SystemConfig row exists."""
    try:
        get_system_config()
    except Exception:
        pass


def _migrate_pending_trigger_fields():
    """Add processed_at column to pending_triggers table."""
    cursor = db.execute_sql("PRAGMA table_info(pending_triggers)")
    existing = {row[1] for row in cursor.fetchall()}
    if "processed_at" not in existing:
        db.execute_sql("ALTER TABLE pending_triggers ADD COLUMN processed_at DATETIME")


def init_db():
    db.connect(reuse_if_open=True)
    # Enable WAL mode for concurrent reads/writes (real-time pipeline)
    try:
        db.execute_sql('PRAGMA journal_mode=WAL')
        db.execute_sql('PRAGMA busy_timeout=10000')
    except Exception:
        pass
    db.create_tables(
        [Contact, EmailTemplate, Campaign, CampaignEmail, WarmupConfig, WarmupLog,
         Flow, FlowStep, FlowEnrollment, FlowEmail, AbandonedCheckout, AgentMessage,
         ContactScore, AIMarketingPlan, AIDecisionLog,
         OmnisendOrder, OmnisendOrderItem, CustomerProfile,
         ShopifyOrder, ShopifyOrderItem, ShopifyCustomer,
         CustomerActivity, PendingTrigger, AIGeneratedEmail,
         ProductImageCache, GeneratedDiscount,
         SuppressionEntry, BounceLog,
         PreflightLog,
         MessageDecision, MessageDecisionHistory,
         SuggestedCampaign, OpportunityScanLog, ProductCommercial,
         SystemConfig, ActionLedger, DeliveryQueue],
        safe=True
    )
    _migrate_contact_columns()
    _migrate_activity_fields()
    _migrate_deliverability_fields()
    _migrate_bounce_log_fields()
    _migrate_intelligence_fields()
    _migrate_message_decision_tables()
    _migrate_flow_priority()
    _migrate_flow_enrollment_pause()
    _migrate_total_spent_to_float()
    _migrate_system_config()
    _migrate_pending_trigger_fields()
    _seed_example_templates()
    _seed_starter_flows()
    print("[OK] Database ready (email_platform.db)")



def _migrate_deliverability_fields():
    """Add Phase I deliverability columns to contacts table."""
    new_cols = [
        ("fatigue_score",       "INTEGER DEFAULT 0"),
        ("spam_risk_score",     "INTEGER DEFAULT 0"),
        ("suppression_reason",  "VARCHAR(50) DEFAULT ''"),
        ("suppression_source",  "VARCHAR(50) DEFAULT ''"),
        ("suppression_until",   "DATETIME"),
        ("last_open_at",        "DATETIME"),
        ("last_click_at",       "DATETIME"),
        ("emails_received_7d",  "INTEGER DEFAULT 0"),
        ("emails_received_30d", "INTEGER DEFAULT 0"),
    ]
    cursor = db.execute_sql("PRAGMA table_info(contacts)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in new_cols:
        if col_name not in existing:
            db.execute_sql(f"ALTER TABLE contacts ADD COLUMN {col_name} {col_def}")
            print(f"  [migrate] Added {col_name} to contacts")


def _migrate_bounce_log_fields():
    """Add attribution columns to bounce_log table."""
    new_cols = [
        ("recipient_domain", "VARCHAR(100) DEFAULT ''"),
        ("template_id",      "INTEGER DEFAULT 0"),
        ("subject_family",   "VARCHAR(100) DEFAULT ''"),
        ("ses_message_id",   "VARCHAR(100) DEFAULT ''"),
    ]
    cursor = db.execute_sql("PRAGMA table_info(bounce_log)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in new_cols:
        if col_name not in existing:
            db.execute_sql(f"ALTER TABLE bounce_log ADD COLUMN {col_name} {col_def}")
            print(f"  [migrate] Added {col_name} to bounce_log")


def get_bounce_stats_by_domain(days=30):
    """Return bounce/complaint stats grouped by recipient domain."""
    cutoff = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor = db.execute_sql("""
            SELECT recipient_domain,
                   SUM(CASE WHEN event_type = 'Bounce' THEN 1 ELSE 0 END) as bounces,
                   SUM(CASE WHEN event_type = 'Complaint' THEN 1 ELSE 0 END) as complaints,
                   COUNT(*) as total
            FROM bounce_log
            WHERE timestamp >= ? AND recipient_domain != ''
            GROUP BY recipient_domain
            ORDER BY total DESC
            LIMIT 20
        """, (cutoff,))
        cols = ["domain", "bounces", "complaints", "total"]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception:
        return []


def get_bounce_stats_by_template(days=30):
    """Return bounce/complaint stats grouped by template."""
    cutoff = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor = db.execute_sql("""
            SELECT template_id, subject_family,
                   SUM(CASE WHEN event_type = 'Bounce' THEN 1 ELSE 0 END) as bounces,
                   SUM(CASE WHEN event_type = 'Complaint' THEN 1 ELSE 0 END) as complaints,
                   COUNT(*) as total
            FROM bounce_log
            WHERE timestamp >= ? AND template_id > 0
            GROUP BY template_id
            ORDER BY total DESC
        """, (cutoff,))
        cols = ["template_id", "subject_family", "bounces", "complaints", "total"]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception:
        return []



def _migrate_intelligence_fields():
    """Add Phase 2A intelligence columns to customer_profiles table."""
    new_cols = [
        ("lifecycle_stage",       "VARCHAR(40) DEFAULT 'unknown'"),
        ("customer_type",         "VARCHAR(40) DEFAULT 'unknown'"),
        ("intent_score",          "INTEGER DEFAULT 0"),
        ("reorder_likelihood",    "INTEGER DEFAULT 0"),
        ("category_affinity_json","TEXT DEFAULT '{}'"),
        ("next_purchase_category","VARCHAR(100) DEFAULT ''"),
        ("preferred_send_hour",   "INTEGER DEFAULT -1"),
        ("preferred_send_dow",    "INTEGER DEFAULT -1"),
        ("channel_preference",    "VARCHAR(20) DEFAULT 'email'"),
        ("confidence_lifecycle",  "INTEGER DEFAULT 0"),
        ("confidence_intent",     "INTEGER DEFAULT 0"),
        ("confidence_reorder",    "INTEGER DEFAULT 0"),
        ("confidence_category",   "INTEGER DEFAULT 0"),
        ("confidence_send_window","INTEGER DEFAULT 0"),
        ("confidence_channel",    "INTEGER DEFAULT 0"),
        ("confidence_discount",   "INTEGER DEFAULT 0"),
        ("churn_risk_score",      "INTEGER DEFAULT 0"),
        ("confidence_churn",      "INTEGER DEFAULT 0"),
        ("intelligence_summary",  "TEXT DEFAULT ''"),
        ("last_intelligence_at",  "DATETIME"),
    ]
    cursor = db.execute_sql("PRAGMA table_info(customer_profiles)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in new_cols:
        if col_name not in existing:
            db.execute_sql(f"ALTER TABLE customer_profiles ADD COLUMN {col_name} {col_def}")
            print(f"  [migrate] Added {col_name} to customer_profiles")



def _migrate_message_decision_tables():
    """Ensure Phase 2B message_decisions and message_decision_history tables are up to date."""
    # Tables created by db.create_tables(); this handles future column additions
    for table_name in ["message_decisions", "message_decision_history"]:
        try:
            cursor = db.execute_sql(f"PRAGMA table_info({table_name})")
            existing = {row[1] for row in cursor.fetchall()}
            if existing:
                print(f"  [migrate] {table_name}: {len(existing)} columns OK")
        except Exception as e:
            print(f"  [migrate] {table_name}: {e}")


def _seed_example_templates():
    """Add starter templates if none exist."""
    if EmailTemplate.select().count() > 0:
        return

    EmailTemplate.create(
        name="Welcome Email",
        subject="Welcome to our store, {{first_name}}! 🎉",
        preview_text="Thanks for joining us — here's a little something for you.",
        html_body="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        <!-- Header -->
        <tr><td style="background:#6366f1;padding:40px;text-align:center;">
          <h1 style="color:#ffffff;margin:0;font-size:28px;">Welcome, {{first_name}}! 👋</h1>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:40px;">
          <p style="color:#333;font-size:16px;line-height:1.6;">Hi {{first_name}},</p>
          <p style="color:#333;font-size:16px;line-height:1.6;">Thanks for joining us! We're thrilled to have you as part of our community.</p>
          <p style="color:#333;font-size:16px;line-height:1.6;">Here's what you can expect from us:</p>
          <ul style="color:#333;font-size:16px;line-height:2;">
            <li>Exclusive deals and early access to sales</li>
            <li>New product announcements</li>
            <li>Tips and guides from our team</li>
          </ul>
          <div style="text-align:center;margin:30px 0;">
            <a href="#" style="background:#6366f1;color:#fff;padding:14px 32px;border-radius:6px;text-decoration:none;font-size:16px;font-weight:bold;">Shop Now →</a>
          </div>
        </td></tr>
        <!-- Footer -->
        <tr><td style="background:#f9f9f9;padding:20px;text-align:center;border-top:1px solid #eee;">
          <p style="color:#999;font-size:12px;margin:0;">You're receiving this because you signed up at our store.</p>
          <p style="color:#999;font-size:12px;margin:5px 0 0;">
            <a href="{{unsubscribe_url}}" style="color:#999;">Unsubscribe</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    )

    EmailTemplate.create(
        name="Promotional Sale",
        subject="🔥 Big Sale — Up to 40% off, {{first_name}}!",
        preview_text="Limited time. Don't miss out.",
        html_body="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#fff8f0;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff8f0;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        <tr><td style="background:#ef4444;padding:40px;text-align:center;">
          <p style="color:#ffe4e4;font-size:14px;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase;">LIMITED TIME OFFER</p>
          <h1 style="color:#ffffff;margin:0;font-size:48px;font-weight:900;">40% OFF</h1>
          <p style="color:#ffffff;font-size:20px;margin:8px 0 0;">Everything in store, {{first_name}}!</p>
        </td></tr>
        <tr><td style="padding:40px;text-align:center;">
          <p style="color:#333;font-size:16px;line-height:1.6;">Don't miss out — this sale ends soon. Use code <strong>SAVE40</strong> at checkout.</p>
          <div style="margin:30px 0;">
            <a href="#" style="background:#ef4444;color:#fff;padding:16px 40px;border-radius:6px;text-decoration:none;font-size:18px;font-weight:bold;">Shop The Sale →</a>
          </div>
          <p style="color:#999;font-size:13px;">Offer valid for 48 hours only. Cannot be combined with other offers.</p>
        </td></tr>
        <tr><td style="background:#f9f9f9;padding:20px;text-align:center;border-top:1px solid #eee;">
          <p style="color:#999;font-size:12px;margin:0;">
            <a href="{{unsubscribe_url}}" style="color:#999;">Unsubscribe</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    )

    EmailTemplate.create(
        name="Win-Back (Lapsed Customers)",
        subject="{{first_name}}, we miss you! Here's 20% off to come back 💙",
        preview_text="It's been a while — we have something for you.",
        html_body="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f4ff;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4ff;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        <tr><td style="background:#3b82f6;padding:40px;text-align:center;">
          <h1 style="color:#ffffff;margin:0;font-size:32px;">We Miss You, {{first_name}} 💙</h1>
        </td></tr>
        <tr><td style="padding:40px;text-align:center;">
          <p style="color:#333;font-size:16px;line-height:1.6;">It's been a while since your last visit, and we wanted to reach out with a little something special.</p>
          <div style="background:#f0f4ff;border-radius:8px;padding:24px;margin:24px 0;">
            <p style="color:#3b82f6;font-size:14px;font-weight:bold;letter-spacing:2px;margin:0 0 8px;text-transform:uppercase;">Your Exclusive Code</p>
            <p style="color:#333;font-size:36px;font-weight:900;margin:0;letter-spacing:4px;">COMEBACK20</p>
            <p style="color:#666;font-size:14px;margin:8px 0 0;">20% off your next order</p>
          </div>
          <a href="#" style="background:#3b82f6;color:#fff;padding:14px 32px;border-radius:6px;text-decoration:none;font-size:16px;font-weight:bold;">Redeem Now →</a>
        </td></tr>
        <tr><td style="background:#f9f9f9;padding:20px;text-align:center;border-top:1px solid #eee;">
          <p style="color:#999;font-size:12px;margin:0;">
            <a href="{{unsubscribe_url}}" style="color:#999;">Unsubscribe</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    )

    print("[OK] 3 starter templates added.")


def _seed_starter_flows():
    """Add starter flows if none exist."""
    if Flow.select().count() > 0:
        return

    # Welcome Series — fires when a new contact is created
    welcome = Flow.create(
        name="Welcome Series",
        description="Greet new subscribers with a warm welcome email right away.",
        trigger_type="contact_created",
        trigger_value="",
        is_active=False,
    )
    welcome_tmpl = EmailTemplate.get_or_none(EmailTemplate.name == "Welcome Email")
    if welcome_tmpl:
        FlowStep.create(
            flow=welcome,
            step_order=1,
            delay_hours=0,
            template=welcome_tmpl,
            from_name="",
            from_email="",
            subject_override="",
        )

    # Win-Back — fires when a Shopify contact hasn't purchased in 90 days
    winback_tmpl = EmailTemplate.get_or_none(EmailTemplate.name == "Win-Back (Lapsed Customers)")
    winback = Flow.create(
        name="Win-Back",
        description="Re-engage customers who haven't bought in 90 days.",
        trigger_type="no_purchase_days",
        trigger_value="90",
        is_active=False,
    )
    if winback_tmpl:
        FlowStep.create(
            flow=winback,
            step_order=1,
            delay_hours=0,
            template=winback_tmpl,
            from_name="",
            from_email="",
            subject_override="",
        )

    print("[OK] 2 starter flows added.")


# ─────────────────────────────────
#  AI ENGINE MODELS
# ─────────────────────────────────

class ContactScore(BaseModel):
    """RFM + engagement score per contact, updated nightly."""
    contact          = ForeignKeyField(Contact, unique=True, backref="score")
    rfm_segment      = CharField(default="new")   # champion|loyal|potential|at_risk|lapsed|new
    recency_days     = IntegerField(default=999)  # days since last open or purchase
    frequency_rate   = FloatField(default=0.0)    # opens / emails_received (0.0–1.0)
    monetary_value   = FloatField(default=0.0)    # total_spent as float
    engagement_score = IntegerField(default=0)    # 0–100 composite
    last_scored_at   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "contact_scores"



class PendingTrigger(BaseModel):
    """Detected behavioural triggers queued for action (no sends until SES production)."""
    email           = CharField(index=True)
    contact         = ForeignKeyField(Contact, null=True, backref="pending_triggers")
    trigger_type    = CharField()  # browse_abandonment, cart_abandonment, churn_risk_high, etc.
    trigger_data    = TextField(default="{}")  # JSON: product details, checkout items, risk score
    detected_at     = DateTimeField()
    status          = CharField(default="pending")  # pending | processed | skipped | skipped_stale | skipped_duplicate | skipped_no_flow | failed
    enrolled_at     = DateTimeField(null=True)
    processed_at    = DateTimeField(null=True)  # When status changed from pending

    class Meta:
        table_name = "pending_triggers"


class AIGeneratedEmail(BaseModel):
    """AI-generated personalized email content (preview before sending)."""
    email           = CharField(index=True)
    contact         = ForeignKeyField(Contact, null=True, backref="ai_emails")
    purpose         = CharField()  # browse_abandonment, winback, upsell, welcome, etc.
    subject         = TextField(default="")
    body_text       = TextField(default="")
    body_html       = TextField(default="")
    reasoning       = TextField(default="")  # why Claude chose this content
    profile_snapshot = TextField(default="")  # the profile_summary at generation time
    generated_at    = DateTimeField()
    sent            = BooleanField(default=False)
    sent_at         = DateTimeField(null=True)

    class Meta:
        table_name = "ai_generated_emails"



class AIMarketingPlan(BaseModel):
    """One AI-generated marketing plan per day."""
    plan_date    = CharField(unique=True)         # YYYY-MM-DD
    plan_json    = TextField(default="[]")        # JSON list of actions
    total_sends  = IntegerField(default=0)
    status       = CharField(default="pending")  # pending|executing|done|error
    ai_summary   = TextField(default="")         # plain-English reasoning from Claude
    created_at   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "ai_marketing_plans"


class AIDecisionLog(BaseModel):
    """One row per email decision made by the AI engine."""
    plan         = ForeignKeyField(AIMarketingPlan, backref="decisions")
    contact      = ForeignKeyField(Contact, backref="ai_decisions")
    template_id  = IntegerField()
    segment      = CharField()
    subject_used = CharField(default="")
    status       = CharField(default="pending")  # sent|failed|skipped
    sent_at      = DateTimeField(null=True)
    created_at   = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "ai_decision_log"


class MessageDecision(BaseModel):
    """Current next-best-action decision per contact. Upserted nightly by Phase 2B engine."""
    contact              = ForeignKeyField(Contact, unique=True, backref="message_decision")
    email                = CharField(index=True, default="")
    action_type          = CharField(default="wait")
    action_score         = IntegerField(default=0)
    action_reason        = TextField(default="")
    action_email_purpose = CharField(default="")
    ranked_actions_json  = TextField(default="[]")
    rejections_json      = TextField(default="[]")
    lifecycle_stage      = CharField(default="")
    fatigue_score        = IntegerField(default=0)
    emails_received_7d   = IntegerField(default=0)
    churn_risk_score     = IntegerField(default=0)
    intent_score         = IntegerField(default=0)
    reorder_likelihood   = IntegerField(default=0)
    discount_sensitivity = FloatField(default=0.0)
    days_since_last_order = IntegerField(default=999)
    suppression_active   = BooleanField(default=False)
    risk_level           = CharField(default="low")
    suppression_reason   = CharField(default="")
    decided_at           = DateTimeField(default=datetime.now)
    expires_at           = DateTimeField(null=True)

    class Meta:
        table_name = "message_decisions"


class MessageDecisionHistory(BaseModel):
    """Append-only audit log of every decision. Never overwritten."""
    contact              = ForeignKeyField(Contact, backref="decision_history")
    email                = CharField(index=True, default="")
    decision_date        = CharField(index=True, default="")
    action_type          = CharField(default="wait")
    action_score         = IntegerField(default=0)
    action_reason        = TextField(default="")
    action_email_purpose = CharField(default="")
    ranked_actions_json  = TextField(default="[]")
    rejections_json      = TextField(default="[]")
    was_executed         = BooleanField(default=False)
    executed_at          = DateTimeField(null=True)
    lifecycle_stage      = CharField(default="")
    fatigue_score        = IntegerField(default=0)
    churn_risk_score     = IntegerField(default=0)
    intent_score         = IntegerField(default=0)
    reorder_likelihood   = IntegerField(default=0)
    decided_at           = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "message_decision_history"

# ═══════════════════════════════════════════════════════════════
# Phase 2C+2D: Campaign Planner & Profit Brain
# ═══════════════════════════════════════════════════════════════

class SuggestedCampaign(BaseModel):
    """Ranked daily campaign opportunities generated by the campaign planner."""
    scan_date                = CharField(index=True, default="")
    campaign_type            = CharField(default="")
    campaign_name            = CharField(default="")
    target_description       = TextField(default="")
    segment_size             = IntegerField(default=0)
    eligible_contacts_json   = TextField(default="[]")
    quality_score            = IntegerField(default=0)
    urgency                  = CharField(default="medium")
    recommended_send_window  = IntegerField(default=-1)
    recommended_channel      = CharField(default="email")
    recommended_offer_type   = CharField(default="none")
    predicted_revenue        = FloatField(default=0.0)
    predicted_conversions    = IntegerField(default=0)
    predicted_complaint_risk = FloatField(default=0.0)
    safe_send_volume         = IntegerField(default=0)
    preflight_status         = CharField(default="PASS")
    preflight_warnings_json  = TextField(default="[]")
    brief_text               = TextField(default="")
    status                   = CharField(default="suggested")
    accepted_at              = DateTimeField(null=True)
    executed_at              = DateTimeField(null=True)
    metrics_json             = TextField(default="{}")
    predicted_margin_pct     = FloatField(default=0.0)
    predicted_profit         = FloatField(default=0.0)
    discount_cost            = FloatField(default=0.0)
    net_profit               = FloatField(default=0.0)
    top_products_json        = TextField(default="[]")
    margin_warning           = CharField(default="")
    deliverability_risk_score = IntegerField(default=0)
    created_at               = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "suggested_campaigns"


class OpportunityScanLog(BaseModel):
    """Audit log of opportunity scans."""
    scan_date               = CharField(index=True, default="")
    opportunities_found     = IntegerField(default=0)
    total_eligible_contacts = IntegerField(default=0)
    scan_duration_seconds   = FloatField(default=0.0)
    created_at              = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "opportunity_scan_log"


class ProductCommercial(BaseModel):
    """Commercial intelligence per product -- margins, inventory, promotion eligibility."""
    product_id          = CharField(unique=True, index=True)
    product_title       = CharField(default="")
    sku                 = CharField(default="")
    product_type        = CharField(default="")
    current_price       = FloatField(default=0.0)
    compare_price       = FloatField(default=0.0)
    cost_per_unit       = FloatField(null=True)
    margin_pct          = FloatField(null=True)
    margin_source       = CharField(default="estimated")
    inventory_level     = IntegerField(null=True)
    inventory_location  = CharField(default="")
    days_of_stock       = FloatField(null=True)
    stock_pressure      = CharField(default="unknown")
    units_sold_30d      = IntegerField(default=0)
    units_sold_90d      = IntegerField(default=0)
    revenue_30d         = FloatField(default=0.0)
    revenue_90d         = FloatField(default=0.0)
    profit_30d          = FloatField(null=True)
    profit_90d          = FloatField(null=True)
    return_rate         = FloatField(default=0.0)
    avg_discount_given  = FloatField(default=0.0)
    promotion_eligible  = BooleanField(default=True)
    promotion_reason    = CharField(default="")
    profitability_score = IntegerField(default=0)
    last_synced         = DateTimeField(null=True)
    last_computed       = DateTimeField(null=True)

    class Meta:
        table_name = "product_commercial"


# ═══════════════════════════════════════════════════════════════
# Shadow Mode, Action Ledger & Delivery Queue
# ═══════════════════════════════════════════════════════════════

class SystemConfig(BaseModel):
    """Singleton (id=1). Global delivery settings."""
    delivery_mode = CharField(default="shadow")   # live | shadow | sandbox
    updated_at    = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "system_config"


class ActionLedger(BaseModel):
    """Append-only audit log. One row per automation decision or send attempt.
    Status lifecycle: detected → qualified → suppressed → queued → rendered → sent → shadowed → failed
    """
    # Identity
    contact       = ForeignKeyField(Contact, null=True, backref="ledger_entries", on_delete="SET NULL")
    email         = CharField(default="", index=True)

    # Source
    trigger_type  = CharField(default="")         # flow | campaign | ai_plan
    source_type   = CharField(default="")         # human-readable: flow name or campaign name
    source_id     = IntegerField(default=0)       # Flow.id or Campaign.id
    enrollment_id = IntegerField(default=0)       # FlowEnrollment.id (0 for campaigns)
    step_id       = IntegerField(default=0)       # FlowStep.id (0 for campaigns)

    # Status
    status        = CharField(default="detected", index=True)
    reason_code   = CharField(default="", index=True)
    # Reason codes: warmup_limit | cooldown_active | duplicate_trigger | unsubscribed
    # | bounced | suppressed_entry | no_step_found | no_template | no_content
    # | no_flow_match | ses_error | sandbox_mode | ok
    reason_detail = TextField(default="")

    # Email content (populated at render stage, stored for shadow review)
    template_id   = IntegerField(default=0)
    subject       = CharField(default="")
    preview_text  = CharField(default="")
    generated_html = TextField(default="")
    ses_message_id = CharField(default="")

    # Priority (lower number = higher priority)
    priority      = IntegerField(default=50)

    # Timestamps
    created_at    = DateTimeField(default=datetime.now, index=True)

    class Meta:
        table_name = "action_ledger"


class DeliveryQueue(BaseModel):
    """Mutable work queue. Items are enqueued, then drained by the queue processor.
    Status lifecycle: queued → sending → sent → failed → shadowed → cancelled
    """
    # Identity
    contact       = ForeignKeyField(Contact, null=True, backref="delivery_queue", on_delete="SET NULL")
    email         = CharField(default="", index=True)

    # Source
    email_type    = CharField(default="")          # flow | campaign
    source_id     = IntegerField(default=0)        # Flow.id or Campaign.id
    enrollment_id = IntegerField(default=0)
    step_id       = IntegerField(default=0)

    # Content (fully rendered, ready to send)
    template_id   = IntegerField(default=0)
    from_name     = CharField(default="")
    from_email    = CharField(default="")
    subject       = CharField(default="")
    html          = TextField(default="")
    unsubscribe_url = CharField(default="")

    # Delivery control
    priority      = IntegerField(default=50, index=True)
    status        = CharField(default="queued", index=True)
    error_msg     = TextField(default="")

    # Links
    ledger_id     = IntegerField(default=0)        # ActionLedger.id
    campaign_id   = IntegerField(default=0)        # for CampaignEmail backward compat

    # Timestamps
    created_at    = DateTimeField(default=datetime.now, index=True)
    sent_at       = DateTimeField(null=True)

    class Meta:
        table_name = "delivery_queue"


def get_system_config():
    """Return the singleton SystemConfig row (creates it if missing)."""
    cfg, _ = SystemConfig.get_or_create(id=1, defaults={
        "delivery_mode": "shadow",
        "updated_at": datetime.now(),
    })
    return cfg

