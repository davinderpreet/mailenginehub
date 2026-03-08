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
    total_spent  = CharField(default="0.00")  # total_spent string e.g. "149.99"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    class Meta:
        table_name = "contacts"


class EmailTemplate(BaseModel):
    name         = CharField()
    subject      = CharField()
    preview_text = CharField(default="")
    html_body    = TextField()
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
    status       = CharField(default="active")  # active | completed | cancelled

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
        ("total_spent",   "VARCHAR(50) DEFAULT '0.00'"),
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


def init_db():
    db.connect(reuse_if_open=True)
    db.create_tables(
        [Contact, EmailTemplate, Campaign, CampaignEmail, WarmupConfig, WarmupLog,
         Flow, FlowStep, FlowEnrollment, FlowEmail, AgentMessage,
         ContactScore, AIMarketingPlan, AIDecisionLog,
         OmnisendOrder, OmnisendOrderItem, CustomerProfile,
         ShopifyOrder, ShopifyOrderItem, ShopifyCustomer,
         CustomerActivity, PendingTrigger, AIGeneratedEmail,
         ProductImageCache, GeneratedDiscount],
        safe=True
    )
    _migrate_contact_columns()
    _migrate_activity_fields()
    _seed_example_templates()
    _seed_starter_flows()
    print("[OK] Database ready (email_platform.db)")


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
    status          = CharField(default="pending")  # pending, enrolled, dismissed
    enrolled_at     = DateTimeField(null=True)

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
