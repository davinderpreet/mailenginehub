"""
ai_engine.py — Autonomous AI Email Marketing Engine
Scores contacts nightly, asks Claude to plan what to send, and executes sends.
No human involvement required.
"""

import os, json, time, logging
from datetime import datetime, timedelta
from peewee import fn

load_dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(load_dotenv_path)
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _get_anthropic_client():
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    return anthropic.Anthropic(api_key=api_key)


# ─────────────────────────────────
#  AI EMAIL GENERATION (Phase G)
# ─────────────────────────────────

BRAND_CONTEXT = """You are the email copywriter for LDAS Electronics (ldas.ca / ldas-electronics.com),
a Canadian electronics store specializing in trucking electronics, dash cams, headsets, CB radios,
and accessories for professional drivers and fleet operators. Our tone is friendly, knowledgeable,
and helpful — like a fellow trucker who knows their tech. We are not pushy or corporate.

Store URL: https://ldas-electronics.com
Brand name: LDAS Electronics"""

EMAIL_PURPOSES = {
    "browse_abandonment": "The customer viewed products but did not purchase. Gently remind them about what they were looking at and encourage them to come back.",
    "cart_abandonment": "The customer added items to their cart but did not complete checkout. Create urgency without being pushy. Mention the specific items if known.",
    "winback": "The customer has not purchased in a while and may be churning. Win them back with a personal touch. Mention what they previously bought or showed interest in.",
    "upsell": "The customer recently purchased. Suggest complementary products based on what they bought. Be helpful, not salesy.",
    "welcome": "New subscriber who has not purchased yet. Introduce the store, mention what they have been browsing if known. Make them feel valued.",
    "re_engagement": "The customer used to be active but has gone quiet. Remind them why they signed up. Reference their interests if known.",
    "loyalty_reward": "A loyal, high-value customer. Thank them, make them feel special. Mention their purchase history to show you know them.",
    "high_intent": "The customer has high engagement (many page views, product views, searches) but has not bought. They are clearly interested — help them decide.",
}


def generate_personalized_email(email, purpose, extra_context=""):
    """
    Generate a unique, personalized email for a specific customer using Claude.
    Returns premium HTML email with product images and discount codes.

    Args:
        email: Customer email address
        purpose: One of EMAIL_PURPOSES keys
        extra_context: Optional extra info

    Returns:
        dict: {subject, preheader, body_text, body_html, reasoning} or None on error
    """
    import sys; sys.path.insert(0, '/var/www/mailengine')
    from database import Contact, CustomerProfile, init_db
    init_db()

    contact = Contact.get_or_none(Contact.email == email)
    if not contact:
        logger.warning("AI email gen: contact not found for %s", email)
        return None

    profile = CustomerProfile.get_or_none(CustomerProfile.email == email)

    # Build the customer context
    customer_info = []
    name = ((contact.first_name or '') + ' ' + (contact.last_name or '')).strip()
    first_name = contact.first_name or ""
    customer_info.append("Name: " + (name or "Unknown"))
    customer_info.append("Email: " + email)

    if profile:
        if profile.profile_summary:
            customer_info.append("Profile: " + profile.profile_summary)
        if profile.total_orders > 0:
            customer_info.append("Orders: %d, Total spent: $%.2f, AOV: $%.2f" % (profile.total_orders, profile.total_spent, profile.avg_order_value))
        if profile.last_order_at:
            customer_info.append("Last order: " + profile.last_order_at.strftime('%B %d, %Y'))
        if profile.days_since_last_order and profile.days_since_last_order < 999:
            customer_info.append("Days since last order: %d" % profile.days_since_last_order)
        if profile.churn_risk > 0:
            if profile.churn_risk < 1.0:
                risk_label = "low"
            elif profile.churn_risk < 1.5:
                risk_label = "medium"
            elif profile.churn_risk < 2.0:
                risk_label = "high"
            else:
                risk_label = "critical"
            customer_info.append("Churn risk: %s (%.1f)" % (risk_label, profile.churn_risk))
        if profile.predicted_ltv > 0:
            customer_info.append("Predicted lifetime value: $%.0f" % profile.predicted_ltv)
        if profile.last_viewed_product:
            customer_info.append("Last viewed product: " + profile.last_viewed_product)
        if profile.website_engagement_score > 0:
            customer_info.append("Website engagement: %d/100" % profile.website_engagement_score)
        if profile.total_product_views > 0:
            customer_info.append("Product views: %d" % profile.total_product_views)
        if profile.price_tier and profile.price_tier != "unknown":
            customer_info.append("Price preference: " + profile.price_tier)
        if profile.top_products and profile.top_products != "[]":
            try:
                prods = json.loads(profile.top_products)
                if prods:
                    customer_info.append("Previously bought: " + ", ".join(prods[:5]))
            except:
                pass
        if profile.product_recommendations and profile.product_recommendations != "[]":
            try:
                recs = json.loads(profile.product_recommendations)
                if recs:
                    customer_info.append("Recommended products: " + ", ".join(recs[:5]))
            except:
                pass
        if profile.city:
            loc = profile.city
            if profile.province:
                loc += ", " + profile.province
            customer_info.append("Location: " + loc)

    customer_context = "\n".join(customer_info)
    purpose_desc = EMAIL_PURPOSES.get(purpose, "Send a relevant, helpful email to this customer.")

    if extra_context:
        purpose_desc += "\n\nAdditional context: " + extra_context

    prompt = BRAND_CONTEXT + "\n\nCUSTOMER PROFILE:\n" + customer_context
    prompt += "\n\nEMAIL PURPOSE: " + purpose + "\n" + purpose_desc
    prompt += """

INSTRUCTIONS:
- Write a short, personal email for this specific customer
- Use the customer's first name if available (otherwise say "Hey there")
- Reference specific products, browsing behavior, or purchase history from their profile
- Keep it warm and conversational — like a helpful friend, not a corporation
- The email should feel like it was written specifically for THIS person
- Do NOT use generic filler phrases like "valued customer" or "exclusive offer"

Return ONLY valid JSON (no markdown, no code blocks) with this structure:
{
  "subject": "the email subject line (short, personal, compelling)",
  "preheader": "inbox preview text (different from subject, max 80 chars)",
  "hero_headline": "big headline shown at top of email (short, punchy, personal)",
  "hero_subheadline": "smaller text below headline (optional context)",
  "body_paragraphs": ["paragraph 1", "paragraph 2", "paragraph 3 (optional)"],
  "cta_text": "call-to-action button text (e.g., 'Shop Now', 'Complete Your Order')",
  "cta_url": "https://ldas-electronics.com or specific product/collection URL",
  "urgency_message": "urgency text shown in amber bar (optional, leave empty if not applicable)",
  "reasoning": "1 sentence explaining your strategy for this email"
}"""

    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)

        # ── Resolve product images ──
        products = []
        try:
            from shopify_products import get_products_for_email, get_popular_products
            # Get products relevant to this customer
            product_refs = []
            if profile:
                # Use recommendations first
                if profile.product_recommendations and profile.product_recommendations != "[]":
                    try:
                        recs = json.loads(profile.product_recommendations)
                        product_refs.extend(recs[:4])
                    except:
                        pass
                # Fall back to top products
                if not product_refs and profile.top_products and profile.top_products != "[]":
                    try:
                        tops = json.loads(profile.top_products)
                        product_refs.extend(tops[:4])
                    except:
                        pass
                # Fall back to last viewed
                if not product_refs and profile.last_viewed_product:
                    product_refs.append(profile.last_viewed_product)

            if product_refs:
                products = get_products_for_email(product_refs, limit=4)

            # If still no products, use popular ones
            if not products:
                products = get_popular_products(limit=4)
        except Exception as prod_err:
            logger.warning("Could not load product images: %s", prod_err)

        # ── Generate or retrieve discount code ──
        discount_display = None
        try:
            from discount_engine import get_or_create_discount, get_discount_display
            discount_info = get_or_create_discount(email, purpose)
            if discount_info:
                discount_display = get_discount_display(discount_info)
        except Exception as disc_err:
            logger.warning("Could not generate discount code: %s", disc_err)

        # ── Render premium HTML template ──
        from email_templates import render_email

        content_dict = {
            "hero_headline": result.get("hero_headline", result.get("subject", "")),
            "hero_subheadline": result.get("hero_subheadline", ""),
            "body_paragraphs": result.get("body_paragraphs", [result.get("body_text", "")]),
            "cta_text": result.get("cta_text", "Shop Now"),
            "cta_url": result.get("cta_url", "https://ldas-electronics.com"),
            "urgency_message": result.get("urgency_message", ""),
            "preheader": result.get("preheader", result.get("subject", "")),
        }

        body_html = render_email(purpose, content_dict, products, discount_display)

        # Build plain text version from paragraphs
        body_text = "\n\n".join(result.get("body_paragraphs", []))

        output = {
            "subject": result.get("subject", "A message from LDAS Electronics"),
            "preheader": result.get("preheader", ""),
            "body_text": body_text,
            "body_html": body_html,
            "reasoning": result.get("reasoning", ""),
        }

        # Store in AIGeneratedEmail table
        try:
            from database import AIGeneratedEmail
            AIGeneratedEmail.create(
                email=email,
                contact=contact,
                purpose=purpose,
                subject=output["subject"],
                body_text=output["body_text"],
                body_html=output["body_html"],
                reasoning=output["reasoning"],
                profile_snapshot=customer_context,
                generated_at=datetime.now(),
            )
        except Exception as store_err:
            logger.warning("Could not store AI email: %s", store_err)

        logger.info("AI email generated: %s (%s) — %s [products=%d, discount=%s]",
                     email, purpose, output['subject'][:50], len(products),
                     discount_display['code'] if discount_display else 'none')
        return output

    except json.JSONDecodeError as e:
        logger.error("AI email JSON parse error for %s: %s", email, e)
        return None
    except Exception as e:
        logger.error("AI email generation failed for %s: %s", email, e)
        return None


# ─────────────────────────────────
#  PHASE 1 — CONTACT SCORING
# ─────────────────────────────────



def score_single_contact(contact_id):
    """Compute RFM + engagement score for a single contact. Returns the segment or None on error."""
    from database import (Contact, CampaignEmail, FlowEmail,
                          ContactScore, init_db)
    init_db()

    try:
        contact = Contact.get_by_id(contact_id)
    except Contact.DoesNotExist:
        return None

    now = datetime.now()
    try:
        # Recency: days since last open (campaign or flow)
        last_open = None
        ce = (CampaignEmail.select(CampaignEmail.opened_at)
              .where(CampaignEmail.contact == contact, CampaignEmail.opened == True)
              .order_by(CampaignEmail.opened_at.desc()).first())
        if ce and ce.opened_at:
            last_open = ce.opened_at

        fe = (FlowEmail.select(FlowEmail.opened_at)
              .where(FlowEmail.contact == contact, FlowEmail.opened == True)
              .order_by(FlowEmail.opened_at.desc()).first())
        if fe and fe.opened_at:
            if last_open is None or fe.opened_at > last_open:
                last_open = fe.opened_at

        if last_open:
            recency_days = max(0, (now - last_open).days)
        else:
            recency_days = max(0, (now - contact.created_at).days)

        # Frequency: open rate across all emails received
        emails_received  = CampaignEmail.select().where(CampaignEmail.contact == contact).count()
        emails_received += FlowEmail.select().where(FlowEmail.contact == contact).count()
        emails_opened    = CampaignEmail.select().where(CampaignEmail.contact == contact, CampaignEmail.opened == True).count()
        emails_opened   += FlowEmail.select().where(FlowEmail.contact == contact, FlowEmail.opened == True).count()
        frequency_rate   = emails_opened / max(emails_received, 1)

        # Monetary — refresh from ShopifyOrders if available
        try:
            from database import ShopifyOrder
            _order_total = (ShopifyOrder
                            .select(fn.SUM(ShopifyOrder.total_price))
                            .where(ShopifyOrder.contact == contact)
                            .scalar()) or 0.0
            if float(_order_total) > float(contact.total_spent or 0):
                contact.total_spent = float(_order_total)
                contact.save()
            monetary_value = float(contact.total_spent or 0)
        except Exception:
            try:
                monetary_value = float(contact.total_spent or 0)
            except (ValueError, TypeError):
                monetary_value = 0.0

        # Engagement score 0–100
        recency_score    = max(0, min(100, 100 - recency_days))
        frequency_score  = frequency_rate * 100
        monetary_score   = min(100, monetary_value / 5.0)
        engagement_score = int(recency_score * 0.4 + frequency_score * 0.4 + monetary_score * 0.2)

        # RFM segment
        days_since_created = (now - contact.created_at).days
        if days_since_created <= 30:
            segment = "new"
        elif engagement_score >= 75:
            segment = "champion"
        elif engagement_score >= 55:
            segment = "loyal"
        elif recency_days > 180:
            segment = "lapsed"
        elif engagement_score >= 35 and recency_days <= 90:
            segment = "potential"
        else:
            segment = "at_risk"

        ContactScore.insert(
            contact_id=contact.id,
            rfm_segment=segment,
            recency_days=recency_days,
            frequency_rate=round(frequency_rate, 4),
            monetary_value=round(monetary_value, 2),
            engagement_score=engagement_score,
            last_scored_at=now,
        ).on_conflict(
            conflict_target=[ContactScore.contact],
            update={
                ContactScore.rfm_segment:     segment,
                ContactScore.recency_days:     recency_days,
                ContactScore.frequency_rate:   round(frequency_rate, 4),
                ContactScore.monetary_value:   round(monetary_value, 2),
                ContactScore.engagement_score: engagement_score,
                ContactScore.last_scored_at:   now,
            }
        ).execute()
        return segment

    except Exception as e:
        logger.error(f"Error scoring contact {contact_id}: {e}")
        return None


def score_all_contacts():
    """Compute RFM + engagement score for every subscribed contact."""
    from database import Contact, init_db
    init_db()

    contacts = list(Contact.select(Contact.id).where(Contact.subscribed == True))
    updated = 0
    for contact in contacts:
        result = score_single_contact(contact.id)
        if result is not None:
            updated += 1

    logger.info(f"[AI Engine] Scored {updated} contacts")
    return updated


# ─────────────────────────────────
#  PHASE 2 — AI PLAN GENERATION
# ─────────────────────────────────

def _build_context():
    from database import (Contact, EmailTemplate, ContactScore,
                          AIMarketingPlan, AIDecisionLog)

    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")

    segments = {}
    for seg in ["champion", "loyal", "potential", "at_risk", "lapsed", "new"]:
        scores = list(ContactScore.select().where(ContactScore.rfm_segment == seg))
        if not scores:
            segments[seg] = {"count": 0, "avg_spent": 0.0, "avg_open_rate": 0.0}
            continue
        avg_spent = sum(s.monetary_value for s in scores) / len(scores)
        avg_open  = sum(s.frequency_rate for s in scores) / len(scores)
        segments[seg] = {
            "count":         len(scores),
            "avg_spent":     round(avg_spent, 2),
            "avg_open_rate": round(avg_open * 100, 1),
        }

    templates = [
        {"id": t.id, "name": t.name, "subject": t.subject}
        for t in EmailTemplate.select()
    ]

    # Inject learned template performance data if available
    template_performance = {}
    try:
        from strategy_optimizer import get_template_recommendations
        for seg in ["champion", "loyal", "potential", "at_risk", "lapsed", "new"]:
            recs = get_template_recommendations(seg)
            if recs:
                template_performance[seg] = recs[:3]  # Top 3 per segment
    except Exception:
        pass  # Learning module not available yet — skip

    week_ago     = now - timedelta(days=7)
    sent_last_7  = AIDecisionLog.select().where(
        AIDecisionLog.status == "sent",
        AIDecisionLog.sent_at >= week_ago
    ).count()

    yesterday    = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_plan    = AIMarketingPlan.get_or_none(AIMarketingPlan.plan_date == yesterday)
    yesterday_summary = prev_plan.ai_summary if prev_plan else "No plan ran yesterday."

    recent_segments = set()
    recent_plans = (AIMarketingPlan.select()
                    .where(AIMarketingPlan.created_at >= week_ago,
                           AIMarketingPlan.status == "done"))
    for p in recent_plans:
        try:
            for a in json.loads(p.plan_json):
                recent_segments.add(a.get("segment", ""))
        except Exception:
            pass

    return {
        "today":                       today,
        "segments":                    segments,
        "available_templates":         templates,
        "emails_sent_last_7_days":     sent_last_7,
        "segments_emailed_last_7_days": list(recent_segments),
        "yesterday_summary":           yesterday_summary,
        "daily_send_limit":            180,
        "template_performance":        template_performance,
    }


def generate_daily_plan():
    """Ask Claude to create today's email marketing plan. Returns AIMarketingPlan id."""
    if os.getenv("AI_ENGINE_ENABLED", "false").lower() != "true":
        logger.warning("[AI Engine] Blocked — AI_ENGINE_ENABLED is not true")
        return None
    from database import AIMarketingPlan, init_db
    init_db()

    today    = datetime.now().strftime("%Y-%m-%d")
    existing = AIMarketingPlan.get_or_none(AIMarketingPlan.plan_date == today)
    if existing and existing.status in ("executing", "done"):
        logger.info(f"[AI Engine] Plan for {today} already exists ({existing.status})")
        return existing.id

    context = _build_context()

    system_prompt = (
        "You are an autonomous email marketing AI for LDAS Electronics, a Shopify electronics retailer. "
        "Your job: decide which email campaigns to send today to maximise engagement and revenue.\n\n"
        "Rules:\n"
        "- Be conservative. Do not send to the same segment more than once per week.\n"
        "- Prefer lapsed/at_risk contacts for win-back campaigns.\n"
        "- Champions and loyal customers respond to VIP/exclusive messaging.\n"
        "- New contacts should receive welcome/introductory messaging.\n"
        "- Return ONLY a valid JSON array. No explanation text, no markdown fences.\n"
        "- If template_performance data is provided, prefer templates marked 'high' confidence with higher revenue_per_send.\n"
        "- If nothing should be sent today, return an empty array: []\n\n"
        "JSON format:\n"
        '[{"segment":"lapsed","template_id":3,'
        '"subject_override":"{{first_name}}, we miss you","reason":"one sentence","max_sends":100}]'
    )

    user_message = f"Today's data:\n{json.dumps(context, indent=2)}\n\nWhat should we send today?"

    actions  = []
    summary  = ""
    raw      = ""

    try:
        client   = _get_anthropic_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        actions = json.loads(raw)
        if actions:
            summary = " | ".join(
                f"{a['segment']} ({a.get('max_sends','all')}): {a.get('reason','')}"
                for a in actions
            )
        else:
            summary = "AI decided no emails should be sent today."

    except json.JSONDecodeError as e:
        logger.error(f"[AI Engine] Invalid JSON from Claude: {e} | raw={raw[:300]}")
        summary = f"Error: invalid JSON from Claude — {e}"
    except Exception as e:
        logger.error(f"[AI Engine] Plan generation failed: {e}")
        summary = f"Error: {e}"

    if existing:
        existing.plan_json  = json.dumps(actions)
        existing.status     = "pending"
        existing.ai_summary = summary
        existing.save()
        return existing.id

    plan = AIMarketingPlan.create(
        plan_date=today,
        plan_json=json.dumps(actions),
        status="pending",
        ai_summary=summary,
    )
    logger.info(f"[AI Engine] Plan {plan.id} for {today}: {len(actions)} actions")
    return plan.id


# ─────────────────────────────────
#  PHASE 3 — PLAN EXECUTION
# ─────────────────────────────────

def execute_plan(plan_id):
    """Execute an AIMarketingPlan — send emails to matching contacts."""
    from database import (Contact, EmailTemplate, ContactScore,
                          AIMarketingPlan, AIDecisionLog, CampaignEmail, init_db)
    from email_sender import send_campaign_email
    init_db()

    plan = AIMarketingPlan.get_by_id(plan_id)
    if plan.status == "done":
        return 0

    try:
        actions = json.loads(plan.plan_json)
    except Exception:
        plan.status = "error"
        plan.save()
        return 0

    if not actions:
        plan.status = "done"
        plan.save()
        return 0

    plan.status = "executing"
    plan.save()

    from_email    = os.getenv("DEFAULT_FROM_EMAIL", "news@news.ldaselectronics.com")
    from_name     = "LDAS Electronics"
    base_url      = os.getenv("BASE_URL", "https://mailenginehub.com")
    sandbox_mode  = os.getenv("SES_SANDBOX_MODE", "false").lower() == "true"
    sandbox_email = os.getenv("SES_SANDBOX_TEST_EMAIL", "")
    now           = datetime.now()
    cutoff_3d     = now - timedelta(days=3)
    DAILY_CAP     = 180 if not sandbox_mode else 5
    total_sent    = 0

    if sandbox_mode:
        logger.info(f"[AI Engine] SANDBOX MODE — emails redirected to {sandbox_email}")

    # Build set of recently-emailed contact IDs (last 3 days)
    recently_emailed = set()
    for r in AIDecisionLog.select(AIDecisionLog.contact_id).where(
            AIDecisionLog.status == "sent", AIDecisionLog.sent_at >= cutoff_3d):
        recently_emailed.add(r.contact_id)
    for r in CampaignEmail.select(CampaignEmail.contact_id).where(
            CampaignEmail.status == "sent", CampaignEmail.created_at >= cutoff_3d):
        recently_emailed.add(r.contact_id)

    for action in actions:
        if total_sent >= DAILY_CAP:
            break

        segment     = action.get("segment", "")
        template_id = action.get("template_id")
        subject_tpl = action.get("subject_override", "")
        max_sends   = action.get("max_sends")

        try:
            template = EmailTemplate.get_by_id(template_id)
        except Exception:
            logger.error(f"[AI Engine] Template {template_id} not found, skipping")
            continue

        contacts_in_seg = list(
            Contact.select()
            .join(ContactScore)
            .where(ContactScore.rfm_segment == segment, Contact.subscribed == True)
        )

        sent_this_action = 0
        for contact in contacts_in_seg:
            if total_sent >= DAILY_CAP:
                break
            if max_sends is not None and sent_this_action >= max_sends:
                break

            if contact.id in recently_emailed:
                AIDecisionLog.create(
                    plan=plan, contact=contact, template_id=template_id,
                    segment=segment, subject_used="", status="skipped",
                )
                continue

            first_name = contact.first_name or "there"
            subject    = (subject_tpl or template.subject)
            subject    = subject.replace("{{first_name}}", first_name)
            subject    = subject.replace("{{city}}", contact.city or "")
            subject    = subject.replace("{{total_orders}}", str(contact.total_orders))

            unsubscribe_url = f"{base_url}/contacts/unsubscribe/{contact.email}"
            html = template.html_body
            html = html.replace("{{first_name}}", first_name)
            html = html.replace("{{email}}", contact.email)
            html = html.replace("{{unsubscribe_url}}", unsubscribe_url)

            to_email_actual = sandbox_email if sandbox_mode else contact.email
            ok, err = send_campaign_email(
                to_email=to_email_actual,
                to_name=contact.full_name,
                from_email=from_email,
                from_name=from_name,
                subject=subject,
                html_body=html,
            )

            status = "sent" if ok else "failed"
            AIDecisionLog.create(
                plan=plan, contact=contact, template_id=template_id,
                segment=segment, subject_used=subject,
                status=status, sent_at=now if ok else None,
            )

            if ok:
                recently_emailed.add(contact.id)
                sent_this_action += 1
                total_sent       += 1
                time.sleep(1.0)

    plan.total_sends = total_sent
    plan.status      = "done"
    plan.save()
    logger.info(f"[AI Engine] Plan {plan_id} complete — {total_sent} emails sent")
    return total_sent


# ─────────────────────────────────
#  TEMPLATE PERFORMANCE
# ─────────────────────────────────

def update_template_performance():
    """Compute open/click rates for all templates that have been sent via campaigns."""
    from database import TemplatePerformance, EmailTemplate, Campaign, CampaignEmail

    # Find all distinct template_ids used in campaigns
    template_ids = (
        Campaign
        .select(Campaign.template_id)
        .distinct()
        .tuples()
    )

    for (template_id,) in template_ids:
        # Verify template exists
        try:
            template = EmailTemplate.get_by_id(template_id)
        except EmailTemplate.DoesNotExist:
            continue

        # Get all campaign IDs that used this template
        campaign_ids = [
            c.id for c in
            Campaign.select(Campaign.id).where(Campaign.template_id == template_id)
        ]
        if not campaign_ids:
            continue

        # Count sends, opens, clicks across all campaigns for this template
        base_q = CampaignEmail.select().where(
            CampaignEmail.campaign_id.in_(campaign_ids)
        )
        sends = base_q.where(CampaignEmail.status == "sent").count()
        if sends == 0:
            continue

        opens = base_q.where(
            CampaignEmail.status == "sent",
            CampaignEmail.opened == True,  # noqa: E712
        ).count()
        clicks = base_q.where(
            CampaignEmail.status == "sent",
            CampaignEmail.clicked == True,  # noqa: E712
        ).count()

        open_rate = round(opens / sends, 4) if sends else 0.0
        click_rate = round(clicks / sends, 4) if sends else 0.0

        # Upsert TemplatePerformance row
        from datetime import datetime as dt
        perf, created = TemplatePerformance.get_or_create(
            template=template,
            defaults={
                "sends": sends,
                "opens": opens,
                "clicks": clicks,
                "open_rate": open_rate,
                "click_rate": click_rate,
                "last_computed": dt.now(),
            },
        )
        if not created:
            perf.sends = sends
            perf.opens = opens
            perf.clicks = clicks
            perf.open_rate = open_rate
            perf.click_rate = click_rate
            perf.last_computed = dt.now()
            perf.save()

    logger.info("[AI Engine] Template performance update complete")


# ─────────────────────────────────
#  SCHEDULER ENTRY POINTS
# ─────────────────────────────────

def run_nightly_scoring():
    """APScheduler job — runs at 1am."""
    try:
        n = score_all_contacts()
        logger.info(f"[AI Engine] Nightly scoring done: {n} contacts")
        update_template_performance()
        logger.info("[AI Engine] Template performance updated")
    except Exception as e:
        logger.error(f"[AI Engine] Scoring failed: {e}")


def run_nightly_plan():
    """APScheduler job — runs at 2am."""
    try:
        plan_id = generate_daily_plan()
        execute_plan(plan_id)
    except Exception as e:
        logger.error(f"[AI Engine] Nightly plan failed: {e}")
