"""
am_runtime.py — Account Manager Decision & Execution Engine

Phase 4 of the MailEngineHub architecture rebuild.
Single entry point for building an AM decision package and executing it
(AI copy generation + template rendering).

Build order: Intelligence (Phase 1) -> Templates (Phase 2) -> Flows (Phase 3) -> AM (this, Phase 4)

Public API:
    decision = build_am_decision(contact, strategy, intelligence=None)
    if decision["should_act"]:
        result = execute_am_decision(contact, strategy, decision, template=None)
        if result["status"] == "rendered":
            send(result["html"], result["subject"])
"""

import copy
import json
import logging
import os
from datetime import datetime, timedelta

import intelligence_layer
import template_engine as te

# discount_engine imports requests at module level; we handle missing gracefully
try:
    import discount_engine
except ImportError:
    discount_engine = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ==================================================================
# Constants
# ==================================================================

AM_ACTION_TO_FAMILY = {
    "education":              ("AM: Education", "post_purchase"),
    "product_recommendation": ("AM: Product Recommendation", "promo"),
    "winback":                ("AM: Win-Back", "winback"),
    "reorder_reminder":       ("AM: Reorder Reminder", "post_purchase"),
    "loyalty":                ("AM: Loyalty", "post_purchase"),
    "cross_sell":             ("AM: Cross-Sell", "promo"),
}

AM_ACTION_TO_DISCOUNT_PURPOSE = {
    "winback": "winback",
    "cross_sell": "cross_sell",
    "reorder_reminder": "reorder_reminder",
    "loyalty": "loyalty_reward",
    "education": None,
    "product_recommendation": None,
}

MIN_PERFORMANCE_SAMPLE = 20
WAIT_SCORE_THRESHOLD = 0.3

# Normalization helpers
_TACTIC_TO_ACTIONS = {
    "retention":  ["reorder_reminder", "loyalty", "education"],
    "growth":     ["cross_sell", "product_recommendation", "education"],
    "winback":    ["winback", "education"],
    "nurture":    ["education", "product_recommendation"],
    "aggressive": ["cross_sell", "winback", "reorder_reminder", "product_recommendation", "loyalty", "education"],
}

_DEFAULT_ACTIONS = ["education", "product_recommendation", "reorder_reminder",
                    "winback", "loyalty", "cross_sell"]

_DEFAULT_CADENCE = {
    "min_gap_days": 5,
    "max_gap_days": 14,
    "preferred_gap_days": 7,
}

_DEFAULT_OFFER_POLICY = {
    "allow_discount": True,
    "max_discount_pct": 15,
    "free_shipping_ok": True,
}


# ==================================================================
# Helpers
# ==================================================================

def _safe_json(raw, default):
    """Parse JSON safely, return default on any failure."""
    if not raw:
        return default
    try:
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)
    except Exception:
        return default


def _get_openrouter_client():
    """Return an OpenAI-compatible client pointed at OpenRouter."""
    from openai import OpenAI
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


# ==================================================================
# Step 0 -- Normalize strategy
# ==================================================================

def _normalize_strategy(strategy_json_dict, current_phase=""):
    """Pure function: ensure strategy dict has all required runtime fields.

    Idempotent -- running twice gives the same result.

    Derives allowed_actions from phases[current_phase].tactic if missing.
    Fills cadence_policy, offer_policy, current_phase_name with defaults.
    """
    strat = copy.deepcopy(strategy_json_dict) if strategy_json_dict else {}

    # ── allowed_actions ──
    if not strat.get("allowed_actions"):
        # Try to extract from phases -> current_phase -> tactic
        phases = strat.get("phases", {})
        phase_data = phases.get(current_phase, {}) if current_phase else {}
        tactic = phase_data.get("tactic", "")
        if tactic and tactic in _TACTIC_TO_ACTIONS:
            strat["allowed_actions"] = list(_TACTIC_TO_ACTIONS[tactic])
        else:
            strat["allowed_actions"] = list(_DEFAULT_ACTIONS)

    # ── cadence_policy ──
    if not strat.get("cadence_policy"):
        strat["cadence_policy"] = dict(_DEFAULT_CADENCE)

    # ── offer_policy ──
    if not strat.get("offer_policy"):
        # Try to derive from discount_approach text
        approach = strat.get("discount_approach", "")
        if approach and "no discount" in approach.lower():
            strat["offer_policy"] = {
                "allow_discount": False,
                "max_discount_pct": 0,
                "free_shipping_ok": False,
            }
        else:
            strat["offer_policy"] = dict(_DEFAULT_OFFER_POLICY)

    # ── current_phase_name ──
    if not strat.get("current_phase_name"):
        strat["current_phase_name"] = current_phase or ""

    return strat


# ==================================================================
# Step 1 -- Check preconditions
# ==================================================================

def _check_preconditions(contact):
    """Check whether this contact is eligible for AM sends.

    Returns:
        ("ok", "") or ("skipped", reason)
    """
    from database import SuppressionEntry, ContactScore, FlowEnrollment

    # Unsubscribed
    if not getattr(contact, "subscribed", True):
        return ("skipped", "unsubscribed")

    # Suppressed
    try:
        suppressed = SuppressionEntry.get_or_none(
            SuppressionEntry.email == contact.email.lower()
        )
        if suppressed:
            return ("skipped", f"suppressed:{suppressed.reason}")
    except Exception:
        pass

    # Sunset score too high
    try:
        score = ContactScore.get_or_none(ContactScore.contact == contact.id)
        if score and score.sunset_score >= 85:
            return ("skipped", "sunset_suppressed")
    except Exception:
        pass

    # Active flow enrollment — don't overlap
    try:
        active = FlowEnrollment.select().where(
            FlowEnrollment.contact == contact.id,
            FlowEnrollment.status.in_(["active", "paused"]),
        ).first()
        if active:
            return ("skipped", "active_flow_enrollment")
    except Exception:
        pass

    return ("ok", "")


# ==================================================================
# Step 2 -- Check timing
# ==================================================================

def _check_timing(contact, intelligence):
    """AM always respects timing (no urgency bypass).

    Returns:
        ("ok", scheduled_at_datetime) or ("wait", wait_until_datetime)
    """
    now = datetime.now()

    # Consult intelligence layer timing gate
    try:
        result = intelligence_layer.should_contact_now(contact.id)
        can_send = result.get("can_send", True)
        next_available = result.get("next_available_at")

        if not can_send:
            wait_until = next_available or (now + timedelta(hours=24))
            return ("wait", wait_until)
    except Exception as exc:
        logger.warning("[am_runtime] timing gate error for contact %s: %s", contact.id, exc)

    # Compute preferred send time from intelligence
    timing = intelligence.get("timing", {}) if intelligence else {}
    preferred_hour = timing.get("preferred_hour", 10)

    target = now.replace(hour=preferred_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        # Passed today, schedule tomorrow
        target += timedelta(days=1)

    return ("ok", target)


# ==================================================================
# Step 3 -- Evaluate candidates
# ==================================================================

def _get_performance_boost(action_type, segment):
    """Look up ActionPerformance for this action+segment.

    Returns a small boost (0.05-0.15) if sample_size >= MIN_PERFORMANCE_SAMPLE
    and open_rate > 0.15. Otherwise 0.0.
    """
    from database import ActionPerformance

    try:
        perf = ActionPerformance.get_or_none(
            ActionPerformance.action_type == action_type,
            ActionPerformance.segment == segment,
        )
        if perf and perf.sample_size >= MIN_PERFORMANCE_SAMPLE and perf.open_rate > 0.15:
            # Scale boost from 0.05 to 0.15 based on open_rate (0.15 to 0.40)
            boost = min(0.15, max(0.05, (perf.open_rate - 0.15) * 0.4))
            return boost
    except Exception:
        pass
    return 0.0


def _evaluate_candidates(strategy_state, intel):
    """Score each allowed action 0.0-1.0 based on intelligence signals.

    Returns:
        (action_type, score, reasoning) if best >= WAIT_SCORE_THRESHOLD
        ("wait", best_score, reasoning) if all below threshold

    Also populates strategy_state["metadata"]["ranked_actions"].
    """
    allowed = strategy_state.get("allowed_actions", _DEFAULT_ACTIONS)
    scores_data = intel.get("scores", {})
    purchase = intel.get("purchase", {})
    classification = intel.get("classification", {})
    products_data = intel.get("next_products", {})
    segment = classification.get("rfm_segment", "new")

    scored = []

    for action in allowed:
        score = 0.0
        reason_parts = []

        if action == "reorder_reminder":
            reorder_lk = scores_data.get("reorder_likelihood", 0) / 100.0
            avg_cycle = purchase.get("avg_days_between_orders", 0)
            days_since = purchase.get("days_since_last_order", 999)
            cycle_ratio = (days_since / avg_cycle) if avg_cycle > 0 else 0.0
            cycle_ratio = min(cycle_ratio, 2.0)  # cap at 2x
            score = reorder_lk * 0.8 + (cycle_ratio / 2.0) * 0.2
            reason_parts.append(f"reorder_likelihood={reorder_lk:.2f}, cycle_ratio={cycle_ratio:.2f}")

        elif action == "cross_sell":
            # Only if unbought categories exist
            cross_sells = products_data.get("cross_sells", [])
            if not cross_sells:
                score = 0.0
                reason_parts.append("no cross-sell candidates")
            else:
                cat_strength = 0.5  # approximate category affinity
                intent = scores_data.get("intent", 0) / 100.0
                engagement = scores_data.get("engagement", 0) / 100.0
                score = cat_strength * 0.5 + intent * 0.3 + engagement * 0.2
                reason_parts.append(f"cross_sells={len(cross_sells)}, intent={intent:.2f}")

        elif action == "winback":
            churn_risk = scores_data.get("churn_risk", 0) / 100.0
            lifecycle = classification.get("lifecycle_stage", "")
            is_lapsed = 1.0 if lifecycle in ("lapsed", "dormant", "churned") else 0.0
            score = churn_risk * 0.7 + is_lapsed * 0.3
            reason_parts.append(f"churn_risk={churn_risk:.2f}, lapsed={is_lapsed}")

        elif action == "product_recommendation":
            intent = scores_data.get("intent", 0) / 100.0
            engagement = scores_data.get("engagement", 0) / 100.0
            score = intent * 0.5 + engagement * 0.3 + 0.2
            reason_parts.append(f"intent={intent:.2f}, engagement={engagement:.2f}")

        elif action == "loyalty":
            is_vip = 1.0 if segment in ("champion", "loyal", "vip") else 0.3
            disc_sens = scores_data.get("discount_sensitivity", 0.5)
            if isinstance(disc_sens, (int, float)):
                disc_sens = min(1.0, max(0.0, disc_sens))
            else:
                disc_sens = 0.5
            score = is_vip * 0.6 + (1 - disc_sens) * 0.4
            reason_parts.append(f"vip={is_vip}, disc_sens={disc_sens:.2f}")

        elif action == "education":
            score = 0.25
            reason_parts.append("base education score")

        # Apply performance boost
        boost = _get_performance_boost(action, segment)
        if boost > 0:
            score += boost
            reason_parts.append(f"perf_boost=+{boost:.2f}")

        score = min(1.0, score)
        scored.append((action, score, "; ".join(reason_parts)))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Store ranked_actions in metadata
    if "metadata" not in strategy_state:
        strategy_state["metadata"] = {}
    strategy_state["metadata"]["ranked_actions"] = [
        {"action": a, "score": s, "reasoning": r} for a, s, r in scored
    ]

    if not scored:
        return ("wait", 0.0, "no allowed actions")

    best_action, best_score, best_reason = scored[0]

    if best_score < WAIT_SCORE_THRESHOLD:
        return ("wait", best_score, f"best score {best_score:.2f} < threshold; {best_reason}")

    return (best_action, best_score, best_reason)


# ==================================================================
# Step 4 -- Resolve products
# ==================================================================

def _resolve_products(contact, action_type, intelligence):
    """Get recommended products from intelligence layer, filtered for OOS.

    Same pattern as flow_runtime._get_intelligence_products.
    Action-type prioritization: reorder->reorders first, cross_sell->cross_sells, etc.
    """
    from database import ProductImageCache, ProductCommercial

    try:
        intel_products = intelligence_layer.get_next_products(contact.id)
    except Exception as exc:
        logger.warning("[am_runtime] product resolution error for contact %s: %s", contact.id, exc)
        return []

    # Build out-of-stock set
    out_of_stock = set()
    try:
        for pc in ProductCommercial.select(ProductCommercial.product_title).where(
            ProductCommercial.stock_pressure == "out_of_stock"
        ):
            out_of_stock.add(pc.product_title)
    except Exception:
        pass

    # Extract candidates with action-type priority
    candidates = []
    seen = set()

    def _add(key, is_product=False):
        if key and key not in seen:
            seen.add(key)
            candidates.append((key, is_product))

    # Action-type prioritization
    if action_type == "reorder_reminder":
        for item in (intel_products.get("reorders") or []):
            _add(item.get("product_key"), is_product=True)
    elif action_type == "cross_sell":
        for item in (intel_products.get("cross_sells") or []):
            _add(item.get("target_key"), is_product=item.get("is_product", False))
    elif action_type == "winback":
        # Top pick + replacements for winback
        top_pick = intel_products.get("top_pick") or {}
        if top_pick.get("product_key"):
            _add(top_pick["product_key"], is_product=True)
    elif action_type == "product_recommendation":
        top_pick = intel_products.get("top_pick") or {}
        if top_pick.get("product_key"):
            _add(top_pick["product_key"], is_product=True)
        for item in (intel_products.get("upgrades") or []):
            if item.get("to_product"):
                _add(item["to_product"], is_product=True)
    elif action_type == "loyalty":
        top_pick = intel_products.get("top_pick") or {}
        if top_pick.get("product_key"):
            _add(top_pick["product_key"], is_product=True)

    # Fill remaining from all categories
    top_pick = intel_products.get("top_pick") or {}
    if top_pick.get("product_key"):
        _add(top_pick["product_key"], is_product=True)
    for item in (intel_products.get("replacements") or []):
        _add(item.get("product_key"), is_product=True)
    for item in (intel_products.get("reorders") or []):
        _add(item.get("product_key"), is_product=True)
    for key in ("cross_sells", "accessories"):
        for item in (intel_products.get(key) or []):
            _add(item.get("target_key"), is_product=item.get("is_product", False))

    if not candidates:
        return []

    # Resolve into concrete products
    products = []
    limit = 4
    for key, is_product in candidates[:limit * 2]:
        if len(products) >= limit:
            break

        matches = []
        if is_product:
            try:
                m = ProductImageCache.get_or_none(ProductImageCache.product_title == key)
                if m:
                    matches = [m]
            except Exception:
                pass

        if not matches:
            try:
                matches = list(ProductImageCache.select()
                               .where(ProductImageCache.product_type == key)
                               .limit(5))
            except Exception:
                pass

        for m in matches:
            if len(products) >= limit:
                break
            if m.product_title in out_of_stock:
                continue
            products.append({
                "product_title": m.product_title,
                "product_url": m.product_url or "https://ldas.ca",
                "image_url": m.image_url or "",
                "price": str(m.price or "0.00"),
            })

    return products


# ==================================================================
# Step 5 -- Resolve offer
# ==================================================================

def _resolve_offer(contact, action_type, products, strategy_state):
    """Determine whether to include a discount offer.

    Maps action_type -> discount purpose via AM_ACTION_TO_DISCOUNT_PURPOSE.
    Respects strategy offer_policy.
    """
    purpose = AM_ACTION_TO_DISCOUNT_PURPOSE.get(action_type)
    if purpose is None:
        return None

    offer_policy = strategy_state.get("offer_policy", _DEFAULT_OFFER_POLICY)
    if not offer_policy.get("allow_discount", True):
        return None

    # Ask intelligence layer
    try:
        product_keys = [p.get("product_title", "") for p in products] if products else None
        policy = intelligence_layer.get_discount_policy(contact.id, purpose, product_keys)

        if not policy.get("offer_discount", False):
            return None

        if discount_engine is None:
            return None

        # Create / retrieve the discount
        discount_info = discount_engine.get_or_create_discount(contact.email, purpose)
        display = discount_engine.get_discount_display(discount_info)
        return display

    except Exception as exc:
        logger.warning("[am_runtime] offer resolution error for contact %s: %s", contact.id, exc)
        return None


# ==================================================================
# Step 6 -- Suggest next action
# ==================================================================

def _suggest_next_action(strategy_state, decision):
    """Pick next action avoiding consecutive repeats.

    1. Get allowed_actions, remove what was just sent
    2. If decision has ranked_actions in metadata, prefer second-best
    3. Otherwise first in remaining list
    """
    allowed = list(strategy_state.get("allowed_actions", _DEFAULT_ACTIONS))
    just_sent = decision.get("action_type", "")

    # Ranked actions from evaluation
    ranked = decision.get("metadata", {}).get("ranked_actions", [])

    # Try second-best from ranked list
    for entry in ranked:
        action = entry.get("action", "")
        if action != just_sent and action in allowed:
            return action

    # Fallback: first allowed that isn't just_sent
    for action in allowed:
        if action != just_sent:
            return action

    # All are the same as just_sent, just return first
    return allowed[0] if allowed else "education"


# ==================================================================
# Main decision entry point
# ==================================================================

def build_am_decision(contact, strategy, intelligence=None):
    """Build a complete AM decision package for a contact.

    Args:
        contact: Contact instance
        strategy: strategy JSON dict or string
        intelligence: pre-fetched intel dict (optional, auto-fetched if None)

    Returns:
        dict with should_act, status, action_type, and full decision context
    """
    # Parse strategy
    strat_dict = _safe_json(strategy, {})
    current_phase = strat_dict.get("current_phase_name", "")
    strategy_state = _normalize_strategy(strat_dict, current_phase)

    base = {
        "should_act": False,
        "status": "wait",
        "action_type": "",
        "objective": "",
        "strategy_phase": current_phase,
        "template_family": "",
        "candidate_products": [],
        "offer_context": None,
        "scheduled_at": None,
        "expected_value": 0.0,
        "confidence": 0.0,
        "reasoning": "",
        "wait_until": None,
        "metadata": {},
    }

    # Step 1: preconditions
    status, reason = _check_preconditions(contact)
    if status == "skipped":
        base["status"] = "skipped"
        base["reasoning"] = reason
        return base

    # Step 2: fetch intelligence
    if intelligence is None:
        try:
            intelligence = intelligence_layer.get_contact_intelligence(contact.id)
        except Exception as exc:
            logger.warning("[am_runtime] intel fetch error for contact %s: %s", contact.id, exc)
            intelligence = {}

    # Step 3: timing
    timing_status, timing_value = _check_timing(contact, intelligence)
    if timing_status == "wait":
        base["status"] = "wait"
        base["wait_until"] = timing_value
        base["reasoning"] = "timing gate: wait until %s" % timing_value
        return base

    scheduled_at = timing_value  # datetime of preferred send time

    # Step 4: evaluate candidates
    eval_result = _evaluate_candidates(strategy_state, intelligence)
    action_type, score, eval_reasoning = eval_result

    if action_type == "wait":
        base["status"] = "wait"
        base["expected_value"] = score
        base["reasoning"] = eval_reasoning
        base["metadata"] = strategy_state.get("metadata", {})
        return base

    # Step 5: resolve products
    candidate_products = _resolve_products(contact, action_type, intelligence)

    # Step 6: resolve offer
    offer_context = _resolve_offer(contact, action_type, candidate_products, strategy_state)

    # Build family/objective from action type
    family_info = AM_ACTION_TO_FAMILY.get(action_type, ("AM: Unknown", "promo"))
    objective = family_info[0]
    template_family = family_info[1]

    # Metadata
    metadata = strategy_state.get("metadata", {})
    metadata["suggested_next"] = _suggest_next_action(strategy_state, {
        "action_type": action_type,
        "metadata": metadata,
    })

    return {
        "should_act": True,
        "status": "ready",
        "action_type": action_type,
        "objective": objective,
        "strategy_phase": current_phase,
        "template_family": template_family,
        "candidate_products": candidate_products,
        "offer_context": offer_context,
        "scheduled_at": scheduled_at,
        "expected_value": score,
        "confidence": min(1.0, score + 0.1),  # slight confidence premium
        "reasoning": eval_reasoning,
        "wait_until": None,
        "metadata": metadata,
    }


# ==================================================================
# AI copy generation
# ==================================================================

def _generate_ai_copy(contact, decision, intelligence):
    """Generate email copy via Claude (OpenRouter).

    Returns:
        dict: {hero_headline, hero_subheadline, paragraphs, cta_text, cta_url, tokens_used}
    """
    action_type = decision.get("action_type", "education")
    products = decision.get("candidate_products", [])
    offer = decision.get("offer_context")

    # Build intelligence brief
    intel_text = ""
    try:
        intel_text = intelligence_layer.format_intelligence_for_prompt(contact.id)
    except Exception:
        pass

    # Product context for prompt
    product_lines = []
    for p in products[:4]:
        product_lines.append(f"- {p.get('product_title', 'Product')} (${p.get('price', '0')})")
    product_text = "\n".join(product_lines) if product_lines else "No specific products."

    # Offer context
    offer_text = "No discount offer." if not offer else (
        f"Include offer: {offer.get('display_text', 'Special offer')}"
    )

    prompt = f"""You are writing a marketing email for LDAS Electronics (ldas.ca).
Action type: {action_type}
Customer: {contact.first_name or 'Customer'} ({contact.email})

{intel_text}

Products to feature:
{product_text}

{offer_text}

Write the email content as JSON with these exact keys:
- hero_headline: short punchy headline (max 8 words)
- hero_subheadline: supporting line (max 15 words)
- paragraphs: list of 1-3 short paragraph strings
- cta_text: call-to-action button text (max 4 words)
- cta_url: "https://ldas.ca" (or product URL if single product)

Return ONLY valid JSON, no markdown fences."""

    try:
        client = _get_openrouter_client()
        response = client.chat.completions.create(
            model="anthropic/claude-haiku-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        tokens_used = getattr(response.usage, "total_tokens", 0) if response.usage else 0

        # Parse JSON
        parsed = json.loads(raw)
        parsed["tokens_used"] = tokens_used
        return parsed

    except Exception as exc:
        logger.warning("[am_runtime] AI copy generation failed: %s", exc)
        # Fallback
        return {
            "hero_headline": "New from LDAS Electronics",
            "hero_subheadline": "Discover what's waiting for you",
            "paragraphs": ["We've got something special for you."],
            "cta_text": "Shop Now",
            "cta_url": "https://ldas.ca",
            "tokens_used": 0,
        }


# ==================================================================
# Apply AI overrides to template blocks
# ==================================================================

def _apply_ai_overrides(blocks_json, ai_content):
    """Deep-copy blocks and inject AI-generated text into matching block types.

    - hero block -> headline, subheadline
    - text block -> paragraphs
    - cta block  -> text, url
    """
    blocks = copy.deepcopy(blocks_json) if blocks_json else []

    for block in blocks:
        btype = block.get("type", "")

        if btype == "hero":
            if ai_content.get("hero_headline"):
                block["headline"] = ai_content["hero_headline"]
            if ai_content.get("hero_subheadline"):
                block["subheadline"] = ai_content["hero_subheadline"]

        elif btype == "text":
            paragraphs = ai_content.get("paragraphs", [])
            if paragraphs:
                block["text"] = "\n\n".join(paragraphs)

        elif btype == "cta":
            if ai_content.get("cta_text"):
                block["text"] = ai_content["cta_text"]
            if ai_content.get("cta_url"):
                block["url"] = ai_content["cta_url"]

    return blocks


# ==================================================================
# Execution entry point
# ==================================================================

def execute_am_decision(contact, strategy, decision, template=None):
    """Execute an AM decision: AI copy generation + template rendering.

    Args:
        contact: Contact instance
        strategy: strategy JSON dict or string
        decision: dict from build_am_decision
        template: optional EmailTemplate override

    Returns:
        dict: {status, subject, html, template_id, ai_tokens} or {status, reason}
    """
    from database import EmailTemplate

    action_type = decision.get("action_type", "")
    family_info = AM_ACTION_TO_FAMILY.get(action_type, ("AM: Unknown", "promo"))
    template_family = family_info[1]

    # Step 1: find template
    if template is None:
        try:
            # Look for template matching the objective name
            template = EmailTemplate.get_or_none(
                EmailTemplate.name == family_info[0]
            )
        except Exception:
            pass

    # Step 2: apply learning swap if available
    if template is not None:
        try:
            from database import ContactScore
            score = ContactScore.get_or_none(ContactScore.contact == contact.id)
            segment = score.rfm_segment if score else "new"
            from learning_context import get_best_template_for_family
            better = get_best_template_for_family(template_family, segment)
            if better and better != template.id:
                swap = EmailTemplate.get_or_none(EmailTemplate.id == better)
                if swap:
                    template = swap
        except Exception:
            pass

    if template is None:
        return {"status": "invalid", "reason": "no template found for action: %s" % action_type}

    # Step 3: generate AI copy
    ai_content = _generate_ai_copy(contact, decision, decision.get("_intelligence", {}))

    # Step 4: inject AI text into template blocks
    blocks_raw = getattr(template, "blocks_json", "[]") or "[]"
    blocks = _safe_json(blocks_raw, [])
    modified_blocks = _apply_ai_overrides(blocks, ai_content)

    # Step 5: create temp template object with modified blocks
    temp_template = copy.copy(template)
    temp_template.blocks_json = json.dumps(modified_blocks)

    # Step 6: build render contract and render
    try:
        contract = te.make_render_contract(
            template=temp_template,
            source_system="am",
            objective=decision.get("objective", ""),
            contact_id=contact.id,
            product_context=decision.get("candidate_products", []),
            offer_context=decision.get("offer_context"),
            mode="send",
        )
        result = te.render_email(contract)
    except Exception as exc:
        logger.warning("[am_runtime] render failed for contact %s: %s", contact.id, exc)
        return {"status": "invalid", "reason": "render error: %s" % str(exc)}

    # Step 7: check validity
    if not result:
        return {"status": "invalid", "reason": "render returned empty result"}

    html = result.get("html", "")
    subject = result.get("subject", "")

    # Check validation_report for errors
    report = result.get("validation_report", [])
    errors = [r for r in report if r.get("level") == "error"]
    if errors:
        return {"status": "invalid", "reason": "validation errors: %s" % errors[0].get("message", "")}

    if not html:
        return {"status": "invalid", "reason": "empty HTML after render"}

    return {
        "status": "rendered",
        "subject": subject,
        "html": html,
        "template_id": template.id,
        "ai_tokens": ai_content.get("tokens_used", 0),
    }
