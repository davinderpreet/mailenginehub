"""
account_manager.py — AI Account Manager Engine
Per-contact AI strategist: builds 6-month plans, generates daily emails,
learns from human feedback, graduates to autonomous sending.
Runs nightly at 3:40 AM before NBM (4:00 AM).
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
    """Build a comprehensive text profile for a contact — used by the AI strategist."""
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

    # Customer profile (also used for location since Contact has no province field)
    profile = CustomerProfile.get_or_none(CustomerProfile.email == contact.email)
    if profile and profile.city:
        loc = profile.city
        if profile.province:
            loc += f", {profile.province}"
        lines.append(f"Location: {loc}")
    elif contact.city:
        lines.append(f"Location: {contact.city}")
    if profile:
        if profile.lifecycle_stage:
            lines.append(f"Lifecycle: {profile.lifecycle_stage}")
        if profile.total_orders > 0:
            lines.append(f"Orders: {profile.total_orders}, Total spent: ${profile.total_spent:.2f}, AOV: ${profile.avg_order_value:.2f}")
        if profile.last_order_at:
            lines.append(f"Last order: {profile.last_order_at.strftime('%B %d, %Y')}")
        if profile.days_since_last_order and profile.days_since_last_order < 999:
            lines.append(f"Days since last order: {profile.days_since_last_order}")
        if profile.churn_risk and profile.churn_risk > 0:
            risk = "low" if profile.churn_risk < 1.0 else "medium" if profile.churn_risk < 1.5 else "high" if profile.churn_risk < 2.0 else "critical"
            lines.append(f"Churn risk: {risk} ({profile.churn_risk:.1f})")
        if profile.predicted_ltv and profile.predicted_ltv > 0:
            lines.append(f"Predicted LTV: ${profile.predicted_ltv:.0f}")
        if profile.website_engagement_score and profile.website_engagement_score > 0:
            lines.append(f"Website engagement: {profile.website_engagement_score}/100")
        if profile.total_product_views and profile.total_product_views > 0:
            lines.append(f"Product views: {profile.total_product_views}")
        if profile.price_tier and profile.price_tier != "unknown":
            lines.append(f"Price preference: {profile.price_tier}")
        if profile.top_products and profile.top_products != "[]":
            try:
                prods = json.loads(profile.top_products)
                if prods:
                    lines.append(f"Previously bought: {', '.join(prods[:5])}")
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
            lines.append(f"Predicted next purchase: {profile.next_purchase_category}")
        if profile.avg_days_between_orders and profile.avg_days_between_orders > 0:
            lines.append(f"Reorder cycle: every {profile.avg_days_between_orders} days")
        if profile.reorder_likelihood and profile.reorder_likelihood > 0:
            lines.append(f"Reorder likelihood: {profile.reorder_likelihood}/100")
        if profile.intelligence_summary:
            lines.append(f"\nINTELLIGENCE BRIEF: {profile.intelligence_summary}")
        if profile.profile_summary:
            lines.append(f"Profile summary: {profile.profile_summary}")

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
#  MAIN NIGHTLY ENGINE
# ─────────────────────────────────

def run_account_manager():
    """
    Nightly AI Account Manager run.
    For each enrolled contact: review profile, update strategy, generate email if needed.
    """
    from database import (ContactStrategy, AMPendingReview, Contact,
                          SuppressionEntry, LearningConfig, DeliveryQueue,
                          ContactScore, FlowEnrollment, init_db)
    from delivery_engine import enqueue_email
    from ai_engine import generate_personalized_email
    from action_ledger import log_action
    init_db()

    # Master switch
    if LearningConfig.get_val("am_enabled", "false") != "true":
        logger.info("[AccountManager] Disabled — am_enabled is not true")
        return {"status": "disabled"}

    max_daily = int(LearningConfig.get_val("am_max_daily_contacts", "200"))

    # Pre-compute shared context ONCE
    business_context = gather_business_context()
    business_brief = _get_active_prompt("am_business_brief", DEFAULT_PROMPTS["am_business_brief"])
    cross_learnings = gather_cross_account_learnings()
    system_prompt = _get_active_prompt("am_system_prompt", DEFAULT_PROMPTS["am_system_prompt"])
    strategy_prompt = _get_active_prompt("am_strategy_prompt", DEFAULT_PROMPTS["am_strategy_prompt"])
    evaluation_prompt = _get_active_prompt("am_evaluation_prompt", DEFAULT_PROMPTS["am_evaluation_prompt"])
    email_gen_prompt = _get_active_prompt("am_email_generation_prompt", DEFAULT_PROMPTS["am_email_generation_prompt"])

    # Get enrolled contacts
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    strategies = (ContactStrategy.select(ContactStrategy, Contact)
                  .join(Contact)
                  .where(ContactStrategy.enrolled == True)
                  .limit(max_daily))

    processed = 0
    errors = 0
    emails_generated = 0
    waits = 0

    for cs in strategies:
        try:
            contact = cs.contact

            # Skip checks
            if not contact.subscribed:
                continue
            sup = SuppressionEntry.get_or_none(SuppressionEntry.email == contact.email)
            if sup:
                continue
            # Skip contacts with high sunset score (disengaged)
            cscore = ContactScore.get_or_none(ContactScore.contact == contact)
            if cscore and cscore.sunset_score and cscore.sunset_score >= 85:
                continue
            # Already reviewed today
            if cs.last_reviewed_at and cs.last_reviewed_at >= today_start:
                continue
            # Skip contacts still in active flows — flows handle them first
            active_flows = (FlowEnrollment.select()
                            .where(FlowEnrollment.contact == contact,
                                   FlowEnrollment.status.in_(["active", "paused"]))
                            .count())
            if active_flows > 0:
                logger.debug("[AccountManager] Skipping contact #%s — still in %d active flow(s)",
                             contact.id, active_flows)
                continue

            processed += 1

            # Gather full profile
            profile_text = gather_contact_profile(contact)

            # Build current strategy context
            strategy_data = {}
            try:
                strategy_data = json.loads(cs.strategy_json) if cs.strategy_json and cs.strategy_json != "{}" else {}
            except Exception:
                pass

            # Build rejection history
            rejection_history = []
            try:
                rejection_history = json.loads(cs.rejection_reasons) if cs.rejection_reasons and cs.rejection_reasons != "[]" else []
            except Exception:
                pass

            # Build the Claude prompt
            user_prompt = f"""CUSTOMER PROFILE:
{profile_text}

{business_context}

{business_brief}

{cross_learnings}

CURRENT STRATEGY (version {cs.strategy_version}):
{json.dumps(strategy_data, indent=2) if strategy_data else "No strategy yet — create an initial 6-month plan."}

Current phase: {cs.current_phase or "Not set"}
Phase number: {cs.current_phase_num}
Last reviewed: {cs.last_reviewed_at.strftime('%Y-%m-%d') if cs.last_reviewed_at else "Never"}

FEEDBACK HISTORY (learn from ALL of this — do NOT repeat mistakes):
- "type": "edit_feedback" = reviewer asked for specific changes (apply these corrections to ALL future emails)
- "type": absent or "rejection" = email was rejected entirely (avoid this approach)
{json.dumps(rejection_history[-10:], indent=2) if rejection_history else "No feedback yet."}

{strategy_prompt}

{evaluation_prompt}

Based on everything above, decide what to do TODAY for this contact.

Respond with ONLY valid JSON (do NOT include email content — emails are generated separately).
Keep strategy_update COMPACT — max 3-4 phases, each with name + goal + 1 tactic line. No verbose descriptions.

{{
  "action": "send_email" | "wait" | "update_strategy_only",
  "strategy_update": {{
    "phases": [
      {{"name": "Phase Name", "months": "1-2", "goal": "short goal", "tactic": "one-line tactic"}}
    ],
    "overall_goal": "one sentence"
  }} or null,
  "email_purpose": "education" | "product_recommendation" | "reorder_reminder" | "cart_recovery" | "winback" | "cross_sell" | "loyalty" | null,
  "email_brief": "1-sentence description of what the email should say" or null,
  "reasoning": "1-2 sentences explaining your decision",
  "phase_update": "phase name" or null,
  "next_action_date": "YYYY-MM-DD" or null,
  "next_action_type": "action type" or null
}}"""

            # Call Claude — strategy decision only (no email content)
            client = _get_anthropic_client()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )

            raw = response.content[0].text.strip()
            # Clean markdown code blocks if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            decision = json.loads(raw)
            action = decision.get("action", "wait")

            # Update strategy if provided
            if decision.get("strategy_update"):
                cs.strategy_json = json.dumps(decision["strategy_update"])
                cs.strategy_version += 1

            if decision.get("phase_update"):
                cs.current_phase = decision["phase_update"]

            if decision.get("next_action_date"):
                try:
                    cs.next_action_date = datetime.strptime(decision["next_action_date"], "%Y-%m-%d")
                except Exception:
                    pass

            if decision.get("next_action_type"):
                cs.next_action_type = decision["next_action_type"]

            cs.last_reviewed_at = datetime.now()
            cs.updated_at = datetime.now()

            if action == "send_email":
                # Generate full HTML using existing ai_engine — separate call
                purpose = decision.get("email_purpose") or cs.next_action_type or "education"
                email_brief = decision.get("email_brief", "")
                strategy_context = f"Phase: {cs.current_phase}. {decision.get('reasoning', '')}. Brief: {email_brief}"
                result = generate_personalized_email(
                    contact.email, purpose,
                    extra_context=strategy_context
                )

                if result:
                    if cs.autonomous:
                        # Autonomous — straight to delivery queue at optimal time
                        send_at = _get_optimal_send_time(contact)
                        _unsub = f"https://mailenginehub.com/unsubscribe?email={contact.email}"
                        ledger = log_action(contact, "auto", 0, "rendered", "RC_ACCOUNT_MANAGER",
                                            source_type="account_manager",
                                            subject=result["subject"],
                                            html=result["body_html"], priority=60,
                                            reason_detail=f"AM auto: {purpose}, scheduled {send_at.strftime('%H:%M')}")
                        enqueue_email(
                            contact=contact,
                            email_type="auto",
                            source_id=0,
                            enrollment_id=0,
                            step_id=0,
                            template_id=0,
                            from_name="LDAS Electronics",
                            from_email="hello@news.ldaselectronics.com",
                            subject=result["subject"],
                            html=result["body_html"],
                            unsubscribe_url=_unsub,
                            priority=60,
                            ledger_id=ledger.id if ledger else 0,
                            scheduled_at=send_at,
                        )
                        emails_generated += 1
                    else:
                        # Manual review — create pending review
                        AMPendingReview.create(
                            contact=contact,
                            strategy=cs,
                            subject=result["subject"],
                            preheader=result.get("preheader", ""),
                            body_html=result["body_html"],
                            reasoning=decision.get("reasoning", ""),
                            strategy_context=strategy_context,
                            status="pending",
                            action_type=purpose,
                            created_at=datetime.now()
                        )
                        emails_generated += 1
            else:
                waits += 1

            cs.save()

            # Rate limit
            time.sleep(0.5)

        except json.JSONDecodeError as e:
            logger.warning(f"[AccountManager] JSON parse error for contact {cs.contact.email}: {e}")
            errors += 1
        except Exception as e:
            logger.error(f"[AccountManager] Error processing contact {cs.contact.email}: {e}")
            errors += 1

            # Circuit breaker: if error rate > 20%, halt
            if processed > 10 and errors / processed > 0.2:
                logger.error("[AccountManager] Circuit breaker triggered — error rate > 20%. Halting.")
                break

    results = {
        "status": "completed",
        "processed": processed,
        "emails": emails_generated,
        "waits": waits,
        "errors": errors,
        "timestamp": datetime.now().isoformat()
    }
    logger.info(f"[AccountManager] Run complete: {results}")
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
    2. AM enrollment mode includes post-flow handover
    3. Contact isn't already enrolled in AM
    """
    from database import (FlowEnrollment, ContactStrategy, LearningConfig,
                          FlowEmail, init_db)
    init_db()

    # Only hand over if enrollment mode supports it
    mode = LearningConfig.get_val("am_enrollment_mode", "manual")
    if mode not in ("auto_post_flow", "auto_all"):
        return None

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

    logger.info("[AccountManager] Flow handover: contact #%s enrolled in AM after completing all flows "
                "(%d flow(s) in history)", contact.id, len(flow_history))
    return cs
