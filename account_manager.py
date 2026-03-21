"""
account_manager.py — AI Account Manager Engine
Per-contact AI strategist: builds 6-month plans, generates daily emails,
learns from human feedback, graduates to autonomous sending.
Runs nightly at 3:40 AM before NBM (4:00 AM).
"""

import os, json, time, logging, random, re
from datetime import datetime, timedelta
from peewee import fn, OperationalError

load_dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(load_dotenv_path)
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _retry_db_op(fn_call, max_retries=5, base_delay=0.5):
    """Retry a DB operation with exponential backoff on SQLite lock errors."""
    for attempt in range(max_retries + 1):
        try:
            return fn_call()
        except OperationalError as e:
            if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                logger.debug("[AccountManager] DB locked, retry %d/%d in %.1fs", attempt + 1, max_retries, delay)
                time.sleep(delay)
            else:
                raise


def _parse_claude_json(raw_text):
    """Robustly extract JSON from Claude responses, handling markdown fences and trailing text."""
    text = raw_text.strip()

    # Strip markdown code fences
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try direct parse (happy path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first { and its matching } using brace-depth counting
    brace_start = text.find('{')
    if brace_start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[brace_start:], brace_start):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break

    raise json.JSONDecodeError(
        "Could not extract JSON from Claude response (%d chars)" % len(raw_text),
        raw_text, 0
    )


def _get_anthropic_client():
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    return anthropic.Anthropic(api_key=api_key)


# ─────────────────────────────────
#  FLOW TAG HELPERS
# ─────────────────────────────────

def _slugify_flow_name(flow_name):
    """Convert flow name to a tag-safe slug: 'Welcome Series' -> 'welcome_series'."""
    import re
    slug = flow_name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    return slug.strip('_')


def add_flow_tag(contact, flow_name, status):
    """Add a flow tracking tag to a contact. e.g. flow:welcome_series:active.
    Removes any existing tag for this flow before adding the new one."""
    slug = _slugify_flow_name(flow_name)
    prefix = f"flow:{slug}:"

    # Parse existing tags
    existing = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]

    # Remove any existing tag for this flow (any status)
    existing = [t for t in existing if not t.startswith(prefix)]

    # Add the new status tag
    existing.append(f"flow:{slug}:{status}")

    contact.tags = ",".join(existing)
    contact.save()
    logger.debug("[FlowTag] Contact #%s: set flow:%s:%s", contact.id, slug, status)


def remove_flow_tag(contact, flow_name):
    """Remove all flow tags for a specific flow from a contact."""
    slug = _slugify_flow_name(flow_name)
    prefix = f"flow:{slug}:"

    existing = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
    existing = [t for t in existing if not t.startswith(prefix)]

    contact.tags = ",".join(existing)
    contact.save()


def _get_active_prompt(prompt_key, default=""):
    """Get the active version of an editable prompt, falling back to default."""
    from database import PromptVersion
    try:
        pv = (PromptVersion
              .select()
              .where(PromptVersion.prompt_key == prompt_key,
                     PromptVersion.is_active == True)
              .order_by(PromptVersion.version.desc())
              .first())
        return pv.content if pv else default
    except Exception:
        return default


def gather_contact_profile(contact):
    """Build a comprehensive text profile for a contact — used by the AI strategist.
    Includes ALL available intelligence for deep personalization."""
    from database import (CustomerProfile, ContactScore, CustomerActivity,
                          AutoEmail, CampaignEmail, AbandonedCheckout,
                          ProductImageCache, ProductCommercial,
                          CompetitorProduct, ShopifyOrder, ShopifyOrderItem,
                          FlowEnrollment, FlowEmail)

    lines = []
    lines.append(f"Name: {(contact.first_name or '')} {(contact.last_name or '')}".strip())
    lines.append(f"Email: {contact.email}")
    if contact.tags:
        lines.append(f"Tags: {contact.tags}")
    lines.append(f"Source: {contact.source or 'unknown'}")
    lines.append(f"Subscribed since: {contact.created_at.strftime('%B %d, %Y') if contact.created_at else 'unknown'}")

    # Customer profile
    profile = CustomerProfile.get_or_none(CustomerProfile.email == contact.email)
    if profile and profile.city:
        loc = profile.city
        if profile.province:
            loc += f", {profile.province}"
        lines.append(f"Location: {loc}")
    elif contact.city:
        lines.append(f"Location: {contact.city}")
    if profile:
        # ── Core classification ──
        if profile.lifecycle_stage:
            lines.append(f"Lifecycle: {profile.lifecycle_stage}")
        if profile.customer_type:
            lines.append(f"Customer type: {profile.customer_type}")

        # ── Purchase history ──
        if profile.total_orders > 0:
            lines.append(f"Orders: {profile.total_orders}, Total spent: ${profile.total_spent:.2f}, AOV: ${profile.avg_order_value:.2f}")
            if profile.first_order_at:
                lines.append(f"First order: {profile.first_order_at.strftime('%B %d, %Y')}")
        if profile.last_order_at:
            lines.append(f"Last order: {profile.last_order_at.strftime('%B %d, %Y')}")
        if profile.days_since_last_order and profile.days_since_last_order < 999:
            lines.append(f"Days since last order: {profile.days_since_last_order}")

        # ── Predictive scores ──
        if profile.intent_score and profile.intent_score > 0:
            lines.append(f"Purchase intent: {profile.intent_score}/100 (confidence: {profile.confidence_intent or 0}/100)")
        if profile.churn_risk and profile.churn_risk > 0:
            risk = "low" if profile.churn_risk < 1.0 else "medium" if profile.churn_risk < 1.5 else "high" if profile.churn_risk < 2.0 else "critical"
            lines.append(f"Churn risk: {risk} ({profile.churn_risk:.1f}, confidence: {profile.confidence_churn or 0}/100)")
        if profile.reorder_likelihood and profile.reorder_likelihood > 0:
            lines.append(f"Reorder likelihood: {profile.reorder_likelihood}/100 (confidence: {profile.confidence_reorder or 0}/100)")
        if profile.predicted_ltv and profile.predicted_ltv > 0:
            lines.append(f"Predicted LTV: ${profile.predicted_ltv:.0f}")

        # ── Engagement ──
        if profile.website_engagement_score and profile.website_engagement_score > 0:
            lines.append(f"Website engagement: {profile.website_engagement_score}/100")
        if profile.total_product_views and profile.total_product_views > 0:
            lines.append(f"Product views: {profile.total_product_views}")
        if profile.last_active_at:
            days_since_active = (datetime.now() - profile.last_active_at).days
            lines.append(f"Last website visit: {days_since_active} days ago ({profile.last_active_at.strftime('%b %d')})")
        if profile.last_viewed_product:
            lines.append(f"Last viewed product: {profile.last_viewed_product}")

        # ── Price & discount behavior ──
        if profile.price_tier and profile.price_tier != "unknown":
            lines.append(f"Price preference: {profile.price_tier}")
        if profile.has_used_discount:
            sensitivity = int((profile.discount_sensitivity or 0) * 100)
            lines.append(f"Discount sensitive: YES ({sensitivity}% of orders used codes, confidence: {profile.confidence_discount or 0}/100)")
        elif profile.total_orders > 0:
            lines.append(f"Discount sensitive: NO (never used a discount code)")

        # ── Product preferences ──
        if profile.top_products and profile.top_products != "[]":
            try:
                prods = json.loads(profile.top_products)
                if prods:
                    lines.append(f"Previously bought: {', '.join(prods[:5])}")
            except Exception:
                pass
        if profile.all_products_bought and profile.all_products_bought != "[]":
            try:
                all_prods = json.loads(profile.all_products_bought)
                if all_prods and len(all_prods) > 0:
                    total_items = sum(p.get("qty", 1) for p in all_prods) if isinstance(all_prods[0], dict) else len(all_prods)
                    lines.append(f"Total items purchased: {total_items}")
            except Exception:
                pass
        if profile.product_recommendations and profile.product_recommendations != "[]":
            try:
                recs = json.loads(profile.product_recommendations)
                if recs:
                    lines.append(f"Recommended products: {', '.join(recs[:5])}")
            except Exception:
                pass
        if profile.category_affinity_json:
            lines.append(f"Category affinities: {profile.category_affinity_json}")
        if profile.next_purchase_category:
            lines.append(f"Predicted next purchase: {profile.next_purchase_category} (confidence: {profile.confidence_category or 0}/100)")
        if profile.avg_days_between_orders and profile.avg_days_between_orders > 0:
            lines.append(f"Reorder cycle: every {profile.avg_days_between_orders} days")
            if profile.days_since_last_order and profile.days_since_last_order < 999:
                days_until_due = max(0, profile.avg_days_between_orders - profile.days_since_last_order)
                if days_until_due == 0:
                    lines.append(f"Reorder status: OVERDUE by {profile.days_since_last_order - profile.avg_days_between_orders} days")
                else:
                    lines.append(f"Reorder status: {days_until_due} days until due")

        # ── Send preferences ──
        if profile.preferred_send_hour and profile.preferred_send_hour >= 0:
            dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            dow = dow_names.get(profile.preferred_send_dow, "any day") if profile.preferred_send_dow >= 0 else "any day"
            lines.append(f"Best send time: {profile.preferred_send_hour}:00 on {dow} (confidence: {profile.confidence_send_window or 0}/100)")
        if profile.channel_preference:
            lines.append(f"Channel: {profile.channel_preference}")

        # ── Abandoned checkouts ──
        if profile.checkout_abandonment_count and profile.checkout_abandonment_count > 0:
            lines.append(f"Abandoned checkouts: {profile.checkout_abandonment_count}")

        # ── Intelligence summary (the rich narrative from customer_intelligence.py) ──
        if profile.intelligence_summary:
            lines.append(f"\nINTELLIGENCE BRIEF: {profile.intelligence_summary}")

    # ── Email engagement ──
    if contact.last_open_at:
        days_since_open = (datetime.now() - contact.last_open_at).days
        lines.append(f"\nLast email opened: {days_since_open} days ago")
    if contact.last_click_at:
        days_since_click = (datetime.now() - contact.last_click_at).days
        lines.append(f"Last email clicked: {days_since_click} days ago")
    lines.append(f"Emails received (7d): {contact.emails_received_7d or 0}, (30d): {contact.emails_received_30d or 0}")
    lines.append(f"Fatigue score: {contact.fatigue_score or 0}/100, Spam risk: {contact.spam_risk_score or 0}/100")

    # Contact score (RFM)
    score = ContactScore.get_or_none(ContactScore.contact == contact)
    if score:
        lines.append(f"\nRFM Segment: {score.rfm_segment or 'unscored'}")
        if score.engagement_score:
            lines.append(f"Engagement score: {score.engagement_score}")
        if score.sunset_score and score.sunset_score > 0:
            lines.append(f"Sunset risk: {score.sunset_score}/100")

    # Recent browsing activity (last 30 days)
    thirty_days_ago = datetime.now() - timedelta(days=30)
    activities = (CustomerActivity.select()
                  .where(CustomerActivity.contact == contact,
                         CustomerActivity.occurred_at >= thirty_days_ago)
                  .order_by(CustomerActivity.occurred_at.desc())
                  .limit(20))
    if activities:
        act_lines = []
        for a in activities:
            detail = ""
            try:
                ed = json.loads(a.event_data) if a.event_data else {}
                detail = ed.get("product_title") or ed.get("page_title") or ed.get("url", "")
            except Exception:
                pass
            act_lines.append(f"  {a.occurred_at.strftime('%b %d')}: {a.event_type} — {detail}")
        lines.append(f"\nRecent Activity (last 30 days):\n" + "\n".join(act_lines))

    # Email history (last 10 emails)
    emails_sent = (AutoEmail.select()
                   .where(AutoEmail.contact == contact)
                   .order_by(AutoEmail.sent_at.desc())
                   .limit(10))
    if emails_sent:
        em_lines = []
        for e in emails_sent:
            status_parts = [e.status or "sent"]
            if e.opened:
                status_parts.append("opened")
            if e.clicked:
                status_parts.append("clicked")
            em_lines.append(f"  {e.sent_at.strftime('%b %d')}: {' | '.join(status_parts)} — {e.subject or ''}")
        lines.append(f"\nEmail History:\n" + "\n".join(em_lines))

    # Abandoned checkouts
    checkout = (AbandonedCheckout.select()
                .where(AbandonedCheckout.email == contact.email)
                .order_by(AbandonedCheckout.created_at.desc())
                .first())
    if checkout:
        lines.append(f"\nAbandoned Checkout: ${checkout.total_price or 0:.2f} on {checkout.created_at.strftime('%b %d, %Y')}")

    # Order details (last 5)
    orders = (ShopifyOrder.select()
              .where(ShopifyOrder.email == contact.email)
              .order_by(ShopifyOrder.created_at.desc())
              .limit(5))
    if orders:
        ord_lines = []
        for o in orders:
            items = ShopifyOrderItem.select().where(ShopifyOrderItem.order == o)
            item_names = [i.product_title for i in items]
            ord_lines.append(f"  {o.created_at.strftime('%b %d, %Y')}: ${o.order_total:.2f} — {', '.join(item_names[:3])}")
        lines.append(f"\nOrder History:\n" + "\n".join(ord_lines))

    # Flow history — what automated sequences this contact went through
    flow_enrollments = (FlowEnrollment.select()
                        .where(FlowEnrollment.contact == contact)
                        .order_by(FlowEnrollment.enrolled_at.desc())
                        .limit(10))
    if flow_enrollments:
        flow_lines = []
        for fe in flow_enrollments:
            try:
                flow_name = fe.flow.name
            except Exception:
                flow_name = "Unknown"
            emails_in_flow = (FlowEmail.select()
                              .where(FlowEmail.enrollment == fe,
                                     FlowEmail.status == "sent")
                              .count())
            enrolled_date = fe.enrolled_at.strftime("%b %d, %Y") if fe.enrolled_at else "?"
            flow_lines.append(f"  {flow_name}: {fe.status} (enrolled {enrolled_date}, {emails_in_flow} emails sent)")
        lines.append(f"\nFlow History (automated sequences before Account Manager):\n" + "\n".join(flow_lines))

    return "\n".join(lines)


def gather_business_context():
    """Build business context: product catalog + competitor intelligence."""
    from database import ProductImageCache, ProductCommercial, CompetitorProduct

    lines = ["=== LDAS ELECTRONICS PRODUCT CATALOG ==="]

    # Products with commercial data
    products = ProductImageCache.select().limit(50)
    for p in products:
        try:
            price_val = float(p.price) if p.price else 0
        except (ValueError, TypeError):
            price_val = 0
        line = f"- {p.product_title}: ${price_val:.2f}"
        comm = ProductCommercial.get_or_none(ProductCommercial.product_id == p.product_id)
        if comm:
            if comm.margin_pct:
                line += f" (margin: {comm.margin_pct:.0f}%)"
            if comm.promotion_eligible:
                line += " [PROMO ELIGIBLE]"
            if comm.inventory_level:
                line += f" stock: {comm.inventory_level}"
        lines.append(line)

    # Competitor data
    competitors = CompetitorProduct.select().limit(30)
    if competitors:
        lines.append("\n=== COMPETITOR INTELLIGENCE ===")
        for c in competitors:
            line = f"- {c.brand} {c.product_name}"
            if c.price:
                line += f": ${c.price:.2f}"
            if c.comparison_summary:
                line += f" — {c.comparison_summary}"
            lines.append(line)

    return "\n".join(lines)


def gather_cross_account_learnings():
    """Aggregate patterns from all contacts for the AI to learn from."""
    from database import (OutcomeLog, ActionPerformance,
                          TemplatePerformance, AMPendingReview)

    lines = ["=== CROSS-ACCOUNT LEARNINGS ==="]

    # Action type performance
    try:
        perfs = ActionPerformance.select().where(ActionPerformance.total_sent > 5)
        for p in perfs:
            open_rate = (p.total_opened / p.total_sent * 100) if p.total_sent > 0 else 0
            click_rate = (p.total_clicked / p.total_sent * 100) if p.total_sent > 0 else 0
            conv_rate = (p.total_converted / p.total_sent * 100) if p.total_sent > 0 else 0
            lines.append(f"- {p.action_type}: {open_rate:.0f}% open, {click_rate:.0f}% click, {conv_rate:.0f}% conversion (n={p.total_sent})")
    except Exception:
        pass

    # Approval/rejection patterns from AMPendingReview
    try:
        total_approved = AMPendingReview.select().where(AMPendingReview.status == "approved").count()
        total_rejected = AMPendingReview.select().where(AMPendingReview.status == "rejected").count()
        if total_approved + total_rejected > 0:
            approval_rate = total_approved / (total_approved + total_rejected) * 100
            lines.append(f"\nApproval rate: {approval_rate:.0f}% ({total_approved} approved, {total_rejected} rejected)")

        # Recent rejection reasons
        recent_rejections = (AMPendingReview.select()
                             .where(AMPendingReview.status == "rejected",
                                    AMPendingReview.reviewer_notes != "")
                             .order_by(AMPendingReview.reviewed_at.desc())
                             .limit(10))
        if recent_rejections:
            lines.append("Recent rejection reasons:")
            for r in recent_rejections:
                lines.append(f"  - {r.reviewer_notes}")
    except Exception:
        pass

    return "\n".join(lines)


# ─────────────────────────────────
#  DEFAULT PROMPTS
# ─────────────────────────────────

DEFAULT_PROMPTS = {
    "am_system_prompt": """You are a senior marketing strategist and account manager for LDAS Electronics (ldas.ca), a Canadian retailer of trucking electronics — headsets, dash cams, CB radios, mounts, chargers, and accessories for professional truck drivers and fleet operators.

You manage individual customer accounts. Your job is to build a 6-month marketing strategy for each customer and execute it day by day. You think like a business owner who deeply understands the trucking accessories market.

Your goals:
1. Convert browsers into first-time buyers
2. Increase repeat purchases and AOV from existing customers
3. Build loyalty and reduce churn
4. Educate customers about products to drive informed purchases

You are NOT a generic email bot. You are a strategic marketer who:
- Understands that a trucker who bought a headset will need ear cushion replacements in ~6 months
- Knows that fleet operators buy in bulk and need different messaging than individual drivers
- Recognizes that price-sensitive customers need education before discounts
- Understands seasonal patterns (fleet renewals, Black Friday, summer deals)
- Knows when to wait and do nothing vs when to reach out

ALL URLs must use the domain ldas.ca. NEVER use ldas-electronics.com.""",

    "am_business_brief": """=== LDAS ELECTRONICS BUSINESS BRIEF ===

TARGET MARKET: Professional truck drivers, fleet operators, and long-haul drivers across Canada and the US.

PRODUCT CATEGORIES & UPGRADE PATHS:
- Headsets: Budget ($30-50) -> Mid ($50-80) -> Pro ($80-150) -> Premium/ANC ($150+)
- Dash Cams: Single ($50-100) -> Dual ($100-200) -> Fleet GPS+Cam ($200+)
- CB Radios: Basic ($30-60) -> Pro ($60-120) -> Fleet ($120+)
- Mounts & Holders: $15-50, high reorder frequency
- Cables & Chargers: $10-30, consumable — reorder every 4-6 months
- Accessories: Ear cushions (~6mo replacement), antenna, cases

REORDER CYCLES:
- Ear cushions: ~6 months
- Cables/chargers: ~4 months
- Headsets: ~18 months
- Dash cams: ~24 months

COMPETITIVE POSITIONING:
- vs Jabra: We're more affordable, similar quality for trucking use
- vs BlueParrott: We offer better value, wider product range
- vs Amazon generics: We specialize in trucking, offer expert support, Canadian warranty
- vs Poly/Plantronics: We're more focused on driver needs, not office headsets

SEASONAL PATTERNS:
- Jan-Feb: New year fleet upgrades, budget resets
- Mar-Apr: Spring driving season prep
- Jun-Aug: Summer deals, road trip season
- Sep-Oct: Pre-winter prep, fleet renewals
- Nov-Dec: Black Friday, holiday gifts for drivers

VALUE PROPS:
- Canadian company, Canadian warranty and support
- Trucking-focused expertise (not generic electronics)
- Competitive pricing vs brand-name alternatives
- Fast shipping across Canada
- Product expertise — we know what works in a truck cab""",

    "am_strategy_prompt": """Given the customer's full profile, create or update their 6-month marketing strategy.

Structure your strategy as phases:
- Each phase has a name, duration (in months), goal, and specific tactics
- Tactics are concrete email types/content, not vague actions
- Consider the customer's lifecycle stage, purchase history, and browsing behavior
- Factor in reorder cycles for products they've bought
- If they're a new browser, start with education before selling
- If they're a repeat buyer, focus on loyalty and cross-sell
- If they're at risk of churning, prioritize re-engagement

The strategy should feel like a real account manager's plan, not a generic funnel.""",

    "am_email_generation_prompt": """Write a SHORT, scannable email. Nobody reads long paragraphs in marketing emails.

Rules:
- 2-3 short paragraphs, each 1-2 sentences (under 30 words each)
- Use the customer's first name if available
- Reference specific products, browsing, or purchase history
- Warm and conversational — like a helpful trucker friend, not a corporation
- NO generic filler: "valued customer", "exclusive offer", "limited time"
- For education emails: share useful tips, NO product pitches or discounts
- For discount emails: make the offer feel personal, not mass-blast
- ALL URLs use ldas.ca domain""",

    "am_learning_prompt": """Learn from the human reviewer's feedback. When an email is:
- APPROVED: The approach, tone, timing, and content were good. Remember what worked.
- REJECTED with notes: Understand WHY it was rejected. Common reasons:
  - "Too pushy" = back off on selling, use education instead
  - "Too soon" = increase wait time before next email
  - "Wrong product" = re-examine category affinity and browsing data
  - "Generic" = needs more personalization from profile data
  - "Bad timing" = check customer's engagement patterns
- EDITED: The idea was right but execution needed tweaks. Note the specific changes.

Apply these learnings to ALL future emails, not just this contact.""",

    "am_evaluation_prompt": """Decide whether today is an action day for this contact.

Consider:
- When was the last email sent? (minimum 3 days between emails, 5-7 preferred)
- Is there a reason to reach out now? (new browsing, approaching reorder cycle, abandoned cart)
- Is the customer in a "wait" phase of their strategy?
- Has the customer shown any new activity?
- Would reaching out now feel natural or forced?

If nothing has changed and there's no strategic reason to act, respond with "wait".
Doing nothing is often the best marketing decision."""
}


def seed_default_prompts():
    """Seed the PromptVersion table with default prompts if they don't exist."""
    from database import PromptVersion, init_db
    init_db()

    for key, content in DEFAULT_PROMPTS.items():
        existing = PromptVersion.get_or_none(PromptVersion.prompt_key == key)
        if not existing:
            PromptVersion.create(
                prompt_key=key,
                version=1,
                content=content,
                change_note="Initial default prompt",
                is_active=True,
                created_at=datetime.now()
            )
            logger.info(f"Seeded default prompt: {key}")


# ─────────────────────────────────
#  AM BLOCK TEMPLATES (guardrails)
# ─────────────────────────────────

# Template structures per email purpose — blocks define layout, AI fills content
AM_TEMPLATES = {
    "education": {
        "name": "AM: Education",
        "family": "post_purchase",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "cta", "content": {"text": "Learn More", "url": "https://ldas.ca"}},
        ]
    },
    "product_recommendation": {
        "name": "AM: Product Recommendation",
        "family": "promo",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "product_grid", "content": {"section_title": "Picked for You", "columns": 2}},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}},
        ]
    },
    "winback": {
        "name": "AM: Win-Back",
        "family": "winback",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "discount", "content": {"code": "", "value_display": "", "display_text": "", "expires_text": ""}},
            {"block_type": "cta", "content": {"text": "Come Back & Save", "url": "https://ldas.ca"}},
        ]
    },
    "reorder_reminder": {
        "name": "AM: Reorder Reminder",
        "family": "post_purchase",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "product_hero", "content": {"section_title": "Time to Restock", "cta_text": "Reorder Now"}},
            {"block_type": "cta", "content": {"text": "Shop Now", "url": "https://ldas.ca"}},
        ]
    },
    "loyalty": {
        "name": "AM: Loyalty",
        "family": "post_purchase",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "cta", "content": {"text": "See What's New", "url": "https://ldas.ca/collections/new"}},
        ]
    },
    "cross_sell": {
        "name": "AM: Cross-Sell",
        "family": "promo",
        "subject": "{{ai_subject}}",
        "blocks": [
            {"block_type": "hero", "content": {"headline": "", "subheadline": ""}},
            {"block_type": "text", "content": {"paragraphs": []}},
            {"block_type": "product_grid", "content": {"section_title": "Goes Great With Your Gear", "columns": 2}},
            {"block_type": "cta", "content": {"text": "Browse Accessories", "url": "https://ldas.ca"}},
        ]
    },
}


def seed_am_templates():
    """Create AM block templates if they don't exist."""
    from database import EmailTemplate, init_db
    init_db()

    for purpose, tpl in AM_TEMPLATES.items():
        existing = EmailTemplate.get_or_none(EmailTemplate.name == tpl["name"])
        if not existing:
            EmailTemplate.create(
                name=tpl["name"],
                subject=tpl["subject"],
                preview_text="",
                html_body="",
                template_format="blocks",
                blocks_json=json.dumps(tpl["blocks"]),
                template_family=tpl["family"],
                ai_enabled=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            logger.info("Seeded AM template: %s", tpl["name"])


def generate_am_email_from_template(contact, purpose, strategy_context=""):
    """
    Generate a personalized email using AM block templates as guardrails.
    AI generates only the text content; template defines the structure.

    Returns: {subject, preheader, body_html, template_id} or None
    """
    from database import EmailTemplate, init_db
    from block_registry import render_template_blocks
    from discount_engine import get_or_create_discount, get_discount_display
    init_db()

    # Map purpose to template name
    tpl_info = AM_TEMPLATES.get(purpose)
    if not tpl_info:
        # Fallback to education for unknown purposes
        tpl_info = AM_TEMPLATES["education"]
        purpose = "education"

    template = EmailTemplate.get_or_none(EmailTemplate.name == tpl_info["name"])
    if not template:
        logger.warning("[AM] Template '%s' not found, seeding...", tpl_info["name"])
        seed_am_templates()
        template = EmailTemplate.get_or_none(EmailTemplate.name == tpl_info["name"])
        if not template:
            logger.error("[AM] Could not create template '%s'", tpl_info["name"])
            return None

    # Gather contact profile for AI prompt
    profile_text = gather_contact_profile(contact)

    # Build AI prompt — ask Claude ONLY for text content, not structure
    email_gen_prompt = _get_active_prompt("am_email_generation_prompt",
                                          DEFAULT_PROMPTS["am_email_generation_prompt"])

    # Describe what blocks the template has so AI knows what to fill
    block_types = [b["block_type"] for b in json.loads(template.blocks_json)]
    has_products = "product_grid" in block_types or "product_hero" in block_types
    has_discount = "discount" in block_types

    prompt = """CUSTOMER PROFILE:
%s

STRATEGY CONTEXT: %s

%s

Generate the EMAIL CONTENT for this %s email. The template has these blocks: %s

Respond with ONLY valid JSON:
{
  "subject": "email subject line (under 50 chars, personal, no generic filler)",
  "preheader": "inbox preview text (max 80 chars)",
  "hero_headline": "big headline (max 8 words, punchy, personal)",
  "hero_subheadline": "smaller text below headline (max 15 words)",
  "paragraphs": ["short paragraph 1 (1-2 sentences, under 30 words)", "short paragraph 2 (1-2 sentences, under 30 words)"],
  "cta_text": "button text (2-4 words)",
  "cta_url": "https://ldas.ca or https://ldas.ca/collections/... or https://ldas.ca/products/..."
}

ALL URLs must use ldas.ca domain. Use the customer's first name if available.""" % (
        profile_text, strategy_context, email_gen_prompt, purpose,
        " → ".join(block_types)
    )

    # Call Claude
    client = _get_anthropic_client()
    system_prompt = _get_active_prompt("am_system_prompt", DEFAULT_PROMPTS["am_system_prompt"])

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    content = _parse_claude_json(raw)

    # Deep-copy template blocks and inject AI content
    import copy
    blocks = copy.deepcopy(json.loads(template.blocks_json))

    for block in blocks:
        bt = block["block_type"]
        if bt == "hero":
            block["content"]["headline"] = content.get("hero_headline", "")
            block["content"]["subheadline"] = content.get("hero_subheadline", "")
        elif bt == "text":
            block["content"]["paragraphs"] = content.get("paragraphs", [])
        elif bt == "cta":
            block["content"]["text"] = content.get("cta_text", block["content"].get("text", "Shop Now"))
            block["content"]["url"] = content.get("cta_url", block["content"].get("url", "https://ldas.ca"))
        # product_grid and discount blocks are filled by render_template_blocks() automatically

    # Create a temporary template-like object for rendering
    class _TempTemplate:
        pass
    render_tpl = _TempTemplate()
    render_tpl.blocks_json = json.dumps(blocks)
    render_tpl.template_format = "blocks"
    render_tpl.preview_text = content.get("preheader", "")
    render_tpl.subject = content.get("subject", "")

    # Resolve discount if template has a discount block
    discount_display = None
    if has_discount:
        discount_info = get_or_create_discount(contact.email, purpose)
        if discount_info:
            discount_display = get_discount_display(discount_info)

    # Render to full HTML via the block registry
    html = render_template_blocks(render_tpl, contact, products=[], discount=discount_display)

    # Replace unsubscribe placeholder
    _unsub = "https://mailenginehub.com/unsubscribe?email=%s" % contact.email
    html = html.replace("{{unsubscribe_url}}", _unsub)

    return {
        "subject": content.get("subject", ""),
        "preheader": content.get("preheader", ""),
        "body_html": html,
        "template_id": template.id,
    }


# ─────────────────────────────────
#  MAIN NIGHTLY ENGINE
# ─────────────────────────────────

def run_account_manager():
    """
    Lightweight nightly AI Account Manager.
    Only processes contacts whose next_action_date <= today.
    Generates email via Claude, queues for review, advances to next action date.
    Strategies are pre-populated by bootstrap — no per-contact re-strategizing.
    """
    from database import (ContactStrategy, AMPendingReview, Contact,
                          SuppressionEntry, LearningConfig, DeliveryQueue,
                          ContactScore, FlowEnrollment, init_db)
    from delivery_engine import enqueue_email
    from action_ledger import log_action
    init_db()

    # Master switch
    if LearningConfig.get_val("am_enabled", "false") != "true":
        logger.info("[AccountManager] Disabled — am_enabled is not true")
        return {"status": "disabled"}

    max_daily = int(LearningConfig.get_val("am_max_daily_contacts", "500"))

    # Get only contacts due today or overdue
    today_end = datetime.now().replace(hour=23, minute=59, second=59)
    strategies = (ContactStrategy.select(ContactStrategy, Contact)
                  .join(Contact)
                  .where(
                      ContactStrategy.enrolled == True,
                      ContactStrategy.next_action_date.is_null(False),
                      ContactStrategy.next_action_date <= today_end,
                  )
                  .order_by(ContactStrategy.next_action_date.asc())
                  .limit(max_daily))

    total_due = strategies.count()
    logger.info("[AccountManager] %d contacts due today (limit %d)", total_due, max_daily)

    processed = 0
    db_errors = 0
    api_errors = 0
    fatal_errors = 0
    emails_generated = 0
    skipped = 0

    for cs in strategies:
        try:
            contact = cs.contact

            # Skip checks
            if not contact.subscribed:
                skipped += 1
                continue
            sup = SuppressionEntry.get_or_none(SuppressionEntry.email == contact.email)
            if sup:
                skipped += 1
                continue
            cscore = ContactScore.get_or_none(ContactScore.contact == contact)
            if cscore and cscore.sunset_score and cscore.sunset_score >= 85:
                skipped += 1
                continue
            # Skip contacts still in active flows
            active_flows = (FlowEnrollment.select()
                            .where(FlowEnrollment.contact == contact,
                                   FlowEnrollment.status.in_(["active", "paused"]))
                            .count())
            if active_flows > 0:
                skipped += 1
                continue

            processed += 1

            # Use the pre-populated strategy to determine email purpose
            purpose = cs.next_action_type or "education"
            strategy_data = {}
            try:
                strategy_data = json.loads(cs.strategy_json) if cs.strategy_json and cs.strategy_json != "{}" else {}
            except Exception:
                pass

            strategy_context = "Phase: %s. Strategy: %s" % (
                cs.current_phase or "unknown",
                strategy_data.get("overall_goal", "")
            )

            # Generate email via block template + Claude (one API call per contact)
            api_start = time.time()
            result = generate_am_email_from_template(contact, purpose, strategy_context)
            api_elapsed = time.time() - api_start

            if result:
                if cs.autonomous:
                    send_at = _get_optimal_send_time(contact)
                    _unsub = "https://mailenginehub.com/unsubscribe?email=%s" % contact.email
                    ledger = _retry_db_op(lambda: log_action(
                        contact, "auto", 0, "rendered", "RC_ACCOUNT_MANAGER",
                        source_type="account_manager",
                        subject=result["subject"],
                        html=result["body_html"], priority=60,
                        reason_detail="AM auto: %s, scheduled %s" % (purpose, send_at.strftime('%H:%M'))
                    ))
                    _tpl_id = result.get("template_id", 0)
                    _retry_db_op(lambda: enqueue_email(
                        contact=contact,
                        email_type="auto",
                        source_id=0, enrollment_id=0, step_id=0, template_id=_tpl_id,
                        from_name="LDAS Electronics",
                        from_email="hello@news.ldaselectronics.com",
                        subject=result["subject"],
                        html=result["body_html"],
                        unsubscribe_url=_unsub,
                        priority=60,
                        ledger_id=ledger.id if ledger else 0,
                        scheduled_at=send_at,
                    ))
                    emails_generated += 1
                else:
                    _retry_db_op(lambda: AMPendingReview.create(
                        contact=contact,
                        strategy=cs,
                        subject=result["subject"],
                        preheader=result.get("preheader", ""),
                        body_html=result["body_html"],
                        reasoning=strategy_context,
                        strategy_context=strategy_context,
                        status="pending",
                        action_type=purpose,
                        created_at=datetime.now()
                    ))
                    emails_generated += 1

            # Advance next_action_date (7-14 days based on strategy phase)
            next_gap = 10  # default
            phases = strategy_data.get("phases", [])
            for i, ph in enumerate(phases):
                if ph.get("name") == cs.current_phase and i + 1 < len(phases):
                    # Move to next phase
                    cs.current_phase = phases[i + 1]["name"]
                    cs.current_phase_num = i + 2
                    next_gap = 14
                    break
            else:
                next_gap = random.randint(7, 14)

            cs.next_action_date = datetime.now() + timedelta(days=next_gap)
            cs.last_reviewed_at = datetime.now()
            cs.updated_at = datetime.now()
            _retry_db_op(lambda: cs.save())

            # Adaptive pacing
            sleep_time = max(0.2, 1.0 - api_elapsed)
            time.sleep(sleep_time)

        except Exception as e:
            err_str = str(e).lower()
            if "locked" in err_str or "busy" in err_str:
                db_errors += 1
                logger.warning("[AccountManager] DB lock for %s (after retries)", cs.contact.email)
            elif "overloaded" in err_str or "rate" in err_str or "529" in err_str:
                api_errors += 1
                logger.warning("[AccountManager] API error for %s: %s", cs.contact.email, e)
                time.sleep(5)
            else:
                fatal_errors += 1
                logger.error("[AccountManager] Error for %s: %s", cs.contact.email, e)

            # Circuit breaker: only fatal errors trigger halt
            if processed > 10 and fatal_errors / processed > 0.2:
                logger.error("[AccountManager] Circuit breaker: fatal error rate > 20%%. Halting.")
                break
            if api_errors >= 5:
                logger.warning("[AccountManager] API cooldown — sleeping 60s")
                time.sleep(60)
                api_errors = 0

    results = {
        "status": "completed",
        "total_due": total_due,
        "processed": processed,
        "skipped": skipped,
        "emails": emails_generated,
        "db_errors": db_errors,
        "api_errors": api_errors,
        "fatal_errors": fatal_errors,
        "timestamp": datetime.now().isoformat()
    }
    logger.info("[AccountManager] Run complete: %s", results)
    return results


# ─────────────────────────────────
#  OPTIMAL SEND TIME
# ─────────────────────────────────

def _get_optimal_send_time(contact):
    """Calculate the next optimal send datetime for a contact based on their profile."""
    from database import CustomerProfile
    profile = CustomerProfile.get_or_none(CustomerProfile.email == contact.email)

    send_hour = -1
    if profile and profile.preferred_send_hour >= 0:
        send_hour = profile.preferred_send_hour

    if send_hour < 0:
        send_hour = 10  # Default: 10 AM if no preference known

    now = datetime.now()
    # Build target datetime for today at the preferred hour
    target = now.replace(hour=send_hour, minute=0, second=0, microsecond=0)

    # If that time already passed today, schedule for tomorrow
    if target <= now:
        target += timedelta(days=1)

    return target


# ─────────────────────────────────
#  APPROVAL / REJECTION / EDIT
# ─────────────────────────────────

def approve_email(pending_id):
    """Approve a pending email — move it to DeliveryQueue."""
    from database import AMPendingReview, ContactStrategy, DeliveryQueue, Contact, init_db
    from delivery_engine import enqueue_email
    from action_ledger import log_action
    init_db()

    pe = AMPendingReview.get_or_none(AMPendingReview.id == pending_id)
    if not pe or pe.status != "pending":
        return False

    contact = pe.contact

    # Use edited version if available
    subject = pe.edited_subject if pe.edited_subject else pe.subject
    html = pe.edited_html if pe.edited_html else pe.body_html

    # Schedule at the contact's optimal send time
    send_at = _get_optimal_send_time(contact)

    _unsub = f"https://mailenginehub.com/unsubscribe?email={contact.email}"
    ledger = log_action(contact, "auto", 0, "rendered", "RC_ACCOUNT_MANAGER",
                        source_type="account_manager",
                        subject=subject,
                        html=html, priority=60,
                        reason_detail=f"AM approved: {pe.action_type}, scheduled for {send_at.strftime('%H:%M')}")

    enqueue_email(
        contact=contact,
        email_type="auto",
        source_id=0,
        enrollment_id=0,
        step_id=0,
        template_id=0,
        from_name="LDAS Electronics",
        from_email="hello@news.ldaselectronics.com",
        subject=subject,
        html=html,
        unsubscribe_url=_unsub,
        priority=60,
        ledger_id=ledger.id if ledger else 0,
        scheduled_at=send_at,
    )

    pe.status = "approved"
    pe.reviewed_at = datetime.now()
    pe.send_at = send_at
    pe.save()

    # Update strategy confidence
    cs = pe.strategy
    cs.total_approved += 1
    _recalculate_confidence(cs)
    cs.save()

    return True


def reject_email(pending_id, reason=""):
    """Reject a pending email — log the feedback."""
    from database import AMPendingReview, ContactStrategy, init_db
    init_db()

    pe = AMPendingReview.get_or_none(AMPendingReview.id == pending_id)
    if not pe or pe.status != "pending":
        return False

    pe.status = "rejected"
    pe.reviewer_notes = reason
    pe.reviewed_at = datetime.now()
    pe.save()

    # Log rejection reason to strategy
    cs = pe.strategy
    cs.total_rejected += 1
    try:
        reasons = json.loads(cs.rejection_reasons) if cs.rejection_reasons != "[]" else []
    except Exception:
        reasons = []
    reasons.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "action_type": pe.action_type,
        "reason": reason,
        "subject": pe.subject
    })
    # Keep last 20 rejection reasons
    cs.rejection_reasons = json.dumps(reasons[-20:])
    _recalculate_confidence(cs)
    cs.save()

    return True


def regenerate_email(pending_id, feedback):
    """Regenerate an email with reviewer feedback via Claude."""
    from database import AMPendingReview, init_db
    from ai_engine import generate_personalized_email
    init_db()

    pe = AMPendingReview.get_or_none(AMPendingReview.id == pending_id)
    if not pe:
        return None

    contact = pe.contact
    purpose = pe.action_type
    extra_context = f"{pe.strategy_context}\n\nREVIEWER FEEDBACK (must incorporate): {feedback}\n\nPrevious subject that was rejected/edited: {pe.subject}"

    result = generate_personalized_email(contact.email, purpose, extra_context=extra_context)
    if result:
        pe.edited_subject = result["subject"]
        pe.edited_html = result["body_html"]
        pe.status = "pending"  # Reset to pending for re-review
        pe.save()

        # Track the edit AND log feedback so AI learns from it
        cs = pe.strategy
        cs.total_edited += 1
        try:
            reasons = json.loads(cs.rejection_reasons) if cs.rejection_reasons and cs.rejection_reasons != "[]" else []
        except Exception:
            reasons = []
        reasons.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "action_type": pe.action_type,
            "type": "edit_feedback",
            "reason": feedback,
            "original_subject": pe.subject
        })
        cs.rejection_reasons = json.dumps(reasons[-20:])
        cs.save()

    return result


def _recalculate_confidence(cs):
    """Recalculate confidence score based on rolling last 30 decisions."""
    from database import AMPendingReview

    recent = (AMPendingReview.select()
              .where(AMPendingReview.strategy == cs,
                     AMPendingReview.status != "pending")
              .order_by(AMPendingReview.reviewed_at.desc())
              .limit(30))

    score = 0  # Start at 0, build up from decisions
    for pe in recent:
        if pe.status == "approved":
            score += 3
        elif pe.status == "edited":
            score += 1
        elif pe.status == "rejected":
            score -= 5

    cs.confidence_score = max(0, min(100, score))


def enroll_contact(contact_id):
    """Enroll a contact in the AI Account Manager."""
    from database import Contact, ContactStrategy, init_db
    init_db()

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return None

    cs, created = ContactStrategy.get_or_create(
        contact=contact,
        defaults={
            "enrolled": True,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
    )
    if not created:
        cs.enrolled = True
        cs.updated_at = datetime.now()
        cs.save()

    return cs


def unenroll_contact(contact_id):
    """Remove a contact from the AI Account Manager."""
    from database import ContactStrategy, init_db
    init_db()

    cs = ContactStrategy.get_or_none(ContactStrategy.contact == contact_id)
    if cs:
        cs.enrolled = False
        cs.updated_at = datetime.now()
        cs.save()
    return cs


def maybe_handover_from_flow(contact):
    """Auto-enroll a contact in Account Manager if they have no more active flows.

    Called when a flow enrollment completes or is cancelled. Checks:
    1. Contact has zero remaining active/paused flow enrollments
    2. Contact isn't already enrolled in AM
    Tags contact with 'am_managed' on handover.
    """
    from database import (FlowEnrollment, ContactStrategy, LearningConfig,
                          FlowEmail, init_db)
    init_db()

    # Check if contact still has active/paused flows
    remaining = (FlowEnrollment.select()
                 .where(FlowEnrollment.contact == contact,
                        FlowEnrollment.status.in_(["active", "paused"]))
                 .count())
    if remaining > 0:
        return None  # Still in flows — not ready for handover

    # Check if already enrolled
    existing = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
    if existing and existing.enrolled:
        return None  # Already managed by AM

    # Build flow graduation summary for the AI strategist
    flow_history = []
    completed_flows = (FlowEnrollment.select()
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status.in_(["completed", "cancelled"]))
                       .order_by(FlowEnrollment.enrolled_at.desc())
                       .limit(10))
    for fe in completed_flows:
        try:
            flow_name = fe.flow.name
        except Exception:
            flow_name = "Unknown"
        # Count emails sent in this flow
        emails_in_flow = (FlowEmail.select()
                          .where(FlowEmail.enrollment == fe,
                                 FlowEmail.status == "sent")
                          .count())
        flow_history.append({
            "flow": flow_name,
            "status": fe.status,
            "enrolled": fe.enrolled_at.strftime("%Y-%m-%d") if fe.enrolled_at else "",
            "emails_sent": emails_in_flow
        })

    # Enroll in AM with flow context
    import json
    cs, created = ContactStrategy.get_or_create(
        contact=contact,
        defaults={
            "enrolled": True,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
    )
    if not created:
        cs.enrolled = True
        cs.updated_at = datetime.now()

    # Store flow graduation context so AI strategist knows the backstory
    try:
        existing_strategy = json.loads(cs.strategy_json) if cs.strategy_json and cs.strategy_json != "{}" else {}
    except Exception:
        existing_strategy = {}
    existing_strategy["flow_graduation"] = {
        "graduated_at": datetime.now().strftime("%Y-%m-%d"),
        "completed_flows": flow_history
    }
    cs.strategy_json = json.dumps(existing_strategy)
    cs.save()

    # Tag contact as AM-managed
    existing_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
    if "am_managed" not in existing_tags:
        existing_tags.append("am_managed")
        contact.tags = ",".join(existing_tags)
        contact.save()

    logger.info("[AccountManager] Flow handover: contact #%s enrolled in AM after completing all flows "
                "(%d flow(s) in history)", contact.id, len(flow_history))
    return cs
