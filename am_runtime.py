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
    "education":              ("AM: Education", "promo"),
    "product_recommendation": ("AM: Product Recommendation", "promo"),
    "winback":                ("AM: Win-Back", "winback"),
    "reorder_reminder":       ("AM: Reorder Reminder", "promo"),
    "loyalty":                ("AM: Loyalty", "promo"),
    "cross_sell":             ("AM: Cross-Sell", "promo"),
}

AM_ACTION_GUIDANCE = {
    "education": {
        "purpose": "Teach the customer how to get more value from products they already own",
        "tone": "Helpful, expert, like a knowledgeable friend sharing tips",
        "emphasize": "Usage tips, setup guides, maintenance advice, hidden features for THEIR specific product",
        "avoid": "Do NOT pitch new products. Do NOT include product recommendations. This email is about HELPING, not selling. Focus entirely on the product(s) they already own.",
    },
    "product_recommendation": {
        "purpose": "Introduce products hand-picked based on their purchase history",
        "tone": "Enthusiastic, genuine — a trusted advisor who knows their preferences",
        "emphasize": "Why THESE products fit THIS customer. Reference their past purchase.",
        "avoid": "Never say 'customers like you'. Be specific about what they bought.",
    },
    "cross_sell": {
        "purpose": "Suggest complementary accessories for products they already own",
        "tone": "Practical, solution-oriented — 'complete your setup'",
        "emphasize": "How these pair with what they own. Compatibility and convenience.",
        "avoid": "Do NOT recommend products from unrelated categories.",
    },
    "reorder_reminder": {
        "purpose": "Remind them it's time to replace or restock consumable items",
        "tone": "Gentle, helpful — 'just checking in'",
        "emphasize": "How long since purchase, typical replacement timing",
        "avoid": "Do NOT introduce new products. Focus on RE-ordering what they know.",
    },
    "loyalty": {
        "purpose": "Thank them for being a customer and make them feel valued",
        "tone": "Warm, appreciative, exclusive",
        "emphasize": "Their loyalty, how much they mean to the business, new items to explore",
        "avoid": "Do NOT be transactional. Lead with gratitude, not discounts.",
    },
    "winback": {
        "purpose": "Re-engage a customer who hasn't purchased in a while",
        "tone": "Warm, no-pressure — 'we miss you' without guilt",
        "emphasize": "What's new since their last visit, fresh products",
        "avoid": "Do NOT guilt-trip. Keep it casual and inviting.",
    },
}

# ── Layer 2: Per-action prompt configs with named output slots ──────
# Each action type gets a focused system role and purpose-specific output slots.
# Named slots (tip_paragraph, compatibility_paragraph, etc.) prime the model's
# direction better than generic "paragraphs" + prose rules.
AM_ACTION_PROMPTS = {
    "education": {
        "system_role": "You write helpful product tips emails for LDAS Electronics (ldas.ca). Focus ONLY on the customer's owned product — usage tips, setup, maintenance, hidden features.",
        "output_slots": {
            "hero_headline": "Short headline about getting more from their product (max 8 words)",
            "hero_subheadline": "Supporting line about tips/value (max 15 words)",
            "tip_paragraph": "One paragraph with a specific, practical usage tip for the featured product. Do not mention any other products.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["reorder", "restock", "replace your", "upgrade to", "new arrival",
                           "check out our", "don't miss", "limited time", "days ago",
                           "time to replace", "running low", "we miss you"],
        "required_signals": ["tip", "usage", "how to", "get more", "your"],
    },
    "product_recommendation": {
        "system_role": "You write product recommendation emails for LDAS Electronics (ldas.ca). Introduce the listed products as hand-picked matches for this customer.",
        "output_slots": {
            "hero_headline": "Short headline about curated picks (max 8 words)",
            "hero_subheadline": "Supporting line (max 15 words)",
            "recommendation_paragraph": "One paragraph explaining why THESE specific products are a great fit. Reference what they already own.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["reorder", "restock", "time to replace", "days ago",
                           "it's been.*since", "running low", "we miss you",
                           "loyalty", "thank you for being"],
        "required_signals": [],
    },
    "cross_sell": {
        "system_role": "You write cross-sell emails for LDAS Electronics (ldas.ca). Show how the listed accessories complement what the customer already owns.",
        "output_slots": {
            "hero_headline": "Short headline about completing their setup (max 8 words)",
            "hero_subheadline": "Supporting line about compatibility (max 15 words)",
            "compatibility_paragraph": "One paragraph about how these products pair with what they own. Focus on compatibility and convenience.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["reorder", "restock", "time to replace", "days ago",
                           "it's been.*since", "running low", "we miss you",
                           "loyalty", "ear cushion"],
        "required_signals": [],
    },
    "reorder_reminder": {
        "system_role": "You write gentle reorder reminder emails for LDAS Electronics (ldas.ca). Use ONLY the replacement timing data provided.",
        "output_slots": {
            "hero_headline": "Short headline about restocking (max 8 words)",
            "hero_subheadline": "Supporting line (max 15 words)",
            "reorder_paragraph": "One paragraph about why it might be time to restock, referencing ONLY the timing data provided in the customer context.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["we miss you", "new arrival", "upgrade to",
                           "loyalty", "thank you for being"],
        "required_signals": [],
    },
    "loyalty": {
        "system_role": "You write loyalty/thank-you emails for LDAS Electronics (ldas.ca). Express genuine gratitude. Do NOT pitch products or be transactional.",
        "output_slots": {
            "hero_headline": "Short headline expressing appreciation (max 8 words)",
            "hero_subheadline": "Supporting line about their value (max 15 words)",
            "gratitude_paragraph": "One heartfelt paragraph thanking them for their loyalty. Reference their order history stats if provided. Do NOT pitch products.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["reorder", "restock", "replace", "days ago",
                           "it's been.*since", "running low", "we miss you",
                           "don't miss", "limited time", "upgrade to"],
        "required_signals": ["thank", "appreciate", "value", "loyal", "gratitude", "mean"],
    },
    "winback": {
        "system_role": "You write warm re-engagement emails for LDAS Electronics (ldas.ca). Invite the customer back with a no-pressure, casual tone. Mention what's new.",
        "output_slots": {
            "hero_headline": "Short warm headline about reconnecting (max 8 words)",
            "hero_subheadline": "Supporting line (max 15 words)",
            "winback_paragraph": "One warm paragraph inviting them back. Mention the listed products as things that are new/popular. No guilt-tripping.",
            "cta_text": "CTA button text (max 4 words)",
        },
        "banned_phrases": ["reorder", "restock", "time to replace",
                           "loyalty", "thank you for being", "ear cushion",
                           "replacement pad"],
        "required_signals": [],
    },
}


# ── Layer 3: Structural output validation ──────────────────────────
# Deterministic checks AFTER AI generation, BEFORE template injection.
# No AI involved — pure string matching.

import re as _re

_TIMELINE_PATTERN = _re.compile(
    r'\b\d+\s*(?:days?|weeks?|months?)\s*(?:ago|since|left|remaining)\b'
    r'|\bexpires?\s+in\b'
    r'|\bcountdown\b'
    r'|\bonly\s+\d+\s+left\b',
    _re.IGNORECASE
)

_BRAND_WORDS = frozenset({"ldas", "electronics", "canadian", "canada", "trucker"})


def _validate_ai_copy(ai_output, action_type, candidate_products):
    """Structural validation of AI-generated copy. Returns (ok, violations).

    ok: bool — True if copy is safe to use
    violations: list of str — human-readable reasons for failure
    """
    config = AM_ACTION_PROMPTS.get(action_type)
    if not config:
        return True, []

    # Flatten all text from AI output into one string for scanning
    parts = []
    for key in ("hero_headline", "hero_subheadline", "cta_text"):
        v = ai_output.get(key, "")
        if v:
            parts.append(str(v))
    # Handle both named slots and generic paragraphs
    for key, val in ai_output.items():
        if key.endswith("_paragraph") or key == "paragraphs":
            if isinstance(val, list):
                parts.extend(str(p) for p in val)
            elif isinstance(val, str):
                parts.append(val)
    text = " ".join(parts).strip()
    text_lower = text.lower()

    violations = []

    # ── Check 1: Banned phrases ──
    for phrase in config.get("banned_phrases", []):
        if ".*" in phrase:
            # Regex pattern
            if _re.search(phrase, text_lower):
                violations.append("banned_phrase: '%s'" % phrase)
        else:
            if phrase.lower() in text_lower:
                violations.append("banned_phrase: '%s'" % phrase)

    # ── Check 2: Timeline fabrication (all types except reorder_reminder) ──
    if action_type != "reorder_reminder":
        timeline_matches = _TIMELINE_PATTERN.findall(text)
        if timeline_matches:
            violations.append("timeline_fabrication: %s" % timeline_matches)

    # ── Check 3: Required signals (at least one must be present) ──
    required = config.get("required_signals", [])
    if required:
        found_any = any(sig.lower() in text_lower for sig in required)
        if not found_any:
            violations.append("missing_required_signal: need one of %s" % required)

    # ── Check 4: Unlisted product mentions ──
    # Only scan paragraph text (not headlines/CTA which are naturally title-cased).
    # Look for product-name patterns: phrases containing model numbers (TH11, A20)
    # or "LDAS" brand prefix followed by a product name not in our candidate list.
    para_text = ""
    for key, val in ai_output.items():
        if key.endswith("_paragraph") or key == "paragraphs":
            if isinstance(val, list):
                para_text += " ".join(str(p) for p in val)
            elif isinstance(val, str):
                para_text += " " + val

    if para_text and candidate_products:
        # Build allowlist from candidate product titles
        allow_titles_lower = set()
        for p in candidate_products:
            allow_titles_lower.add(p.get("product_title", "").lower())

        # Find "LDAS <product name>" mentions in paragraph text
        ldas_mentions = _re.findall(r'LDAS\s+[\w\s\-]+?(?=[\.,;!?\)]|$)', para_text)
        for mention in ldas_mentions:
            mention_clean = mention.strip().rstrip(".,;!?)")
            # Check if this LDAS product is in our candidate list
            if not any(mention_clean.lower() in t for t in allow_titles_lower):
                violations.append("unlisted_product: '%s'" % mention_clean)

    return (len(violations) == 0), violations


def _normalize_ai_slots(ai_output, action_type):
    """Map action-specific named slots to the standard format expected by _apply_ai_overrides().

    Named slots like tip_paragraph, compatibility_paragraph, etc.
    get mapped to the standard 'paragraphs' list.
    """
    result = {
        "hero_headline": ai_output.get("hero_headline", ""),
        "hero_subheadline": ai_output.get("hero_subheadline", ""),
        "cta_text": ai_output.get("cta_text", ""),
        "cta_url": ai_output.get("cta_url", ""),
        "token_usage": ai_output.get("token_usage", {}),
        "copy_source": ai_output.get("copy_source", "ai"),
    }

    # Collect paragraphs: check for named slots first, then fall back to generic
    paragraphs = []
    for key, val in ai_output.items():
        if key.endswith("_paragraph") and val:
            if isinstance(val, list):
                paragraphs.extend(val)
            else:
                paragraphs.append(str(val))

    if not paragraphs:
        # Fall back to generic paragraphs key
        raw = ai_output.get("paragraphs", [])
        if isinstance(raw, list):
            paragraphs = [str(p) for p in raw]
        elif isinstance(raw, str):
            paragraphs = [raw]

    result["paragraphs"] = paragraphs
    return result


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


# Known generic fallback strings — used by quality gate to detect low-quality output
_GENERIC_HEADLINES = frozenset({
    "New from LDAS Electronics",
    "Discover what's waiting for you",
    "We've got something special for you.",
})


def check_am_quality(result, decision, test_mode=False):
    """Check whether an AM render is good enough to send/review.

    Returns:
        ("pass", "") — acceptable for production
        ("blocked", reason) — not acceptable, must not send

    copy_source flow:
        "ai"        → AI copy passed validation, injected into blocks. PASS.
        "seed"      → AI copy failed validation, seed template preserved. PASS (seed is pre-vetted).
        "api_error"  → API call failed, seed template preserved. PASS (seed is pre-vetted).
        "fallback"  → Legacy generic fallback. BLOCKED (generic copy must never reach customers).
    """
    if test_mode:
        return ("pass", "")

    copy_source = result.get("copy_source", "")

    # Block 1: Legacy generic fallback must never reach real customers
    if copy_source == "fallback":
        return ("blocked", "fallback_copy_not_allowed_in_production: %s" % result.get("fallback_reason", ""))

    # Block 2: API errors with seed fallback are acceptable — seed templates are pre-vetted
    # Block 3: Validation-failed with seed fallback are acceptable — seed templates are pre-vetted
    # (Both "seed" and "api_error" use the action-specific seed template, which is safe)

    # Block 4: Detect generic content that slipped through
    html = result.get("html", "")
    for generic in _GENERIC_HEADLINES:
        if generic in html:
            return ("blocked", "generic_content_detected: '%s'" % generic)

    return ("pass", "")


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
        # Supports both phase shapes:
        #   - list of objects: [{"name": "Phase 1", "tactic": "education"}, ...]
        #   - dict keyed by name: {"Phase 1": {"tactic": "education"}, ...}
        phases = strat.get("phases", {})
        phase_data = {}

        if isinstance(phases, list):
            # List-based (standard repo format): find matching phase by name
            if current_phase:
                for ph in phases:
                    if isinstance(ph, dict) and ph.get("name") == current_phase:
                        phase_data = ph
                        break
            # Fallback: use first phase if no match or no current_phase
            if not phase_data and phases:
                first = phases[0]
                if isinstance(first, dict):
                    phase_data = first
        elif isinstance(phases, dict):
            # Dict-based: look up by key
            phase_data = phases.get(current_phase, {}) if current_phase else {}
            if not phase_data and phases:
                phase_data = next(iter(phases.values()), {})

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
    raw_hour = timing.get("preferred_hour")

    # Sanitize: must be an int in 0..23; fall back to 10 (mid-morning)
    _DEFAULT_SEND_HOUR = 10
    try:
        preferred_hour = int(raw_hour) if raw_hour is not None else _DEFAULT_SEND_HOUR
    except (TypeError, ValueError):
        preferred_hour = _DEFAULT_SEND_HOUR
    if not (0 <= preferred_hour <= 23):
        preferred_hour = max(0, min(23, preferred_hour))

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

def _find_products_by_category(key, limit=5):
    """Resolve a category name like 'Dash Cams' to actual ProductImageCache rows.

    Tries three strategies:
      1. Exact product_type match
      2. Contains match (e.g. "Dash Cam" matches "Dash Cam Hardwire Kit")
      3. CATEGORY_KEYWORDS title search (e.g. "dash cam" keyword in product_title)
    """
    from database import ProductImageCache
    from shared_constants import CATEGORY_KEYWORDS

    # Strategy 1: Exact product_type match
    try:
        matches = list(ProductImageCache.select()
                       .where(ProductImageCache.product_type == key).limit(limit))
        if matches:
            return matches
    except Exception:
        pass

    # Strategy 2: Contains match (strip trailing 's' for singular form)
    search_term = key.rstrip('s') if key.endswith('s') else key
    try:
        matches = list(ProductImageCache.select()
                       .where(ProductImageCache.product_type.contains(search_term)).limit(limit))
        if matches:
            return matches
    except Exception:
        pass

    # Strategy 3: Use CATEGORY_KEYWORDS to search product titles by keyword
    keywords = CATEGORY_KEYWORDS.get(key, [])
    for kw in keywords[:3]:
        try:
            matches = list(ProductImageCache.select()
                           .where(ProductImageCache.product_title.contains(kw)).limit(limit))
            if matches:
                return matches
        except Exception:
            pass

    return []


def _resolve_products(contact, action_type, intelligence):
    """Get recommended products from intelligence layer, filtered for OOS.

    Same pattern as flow_runtime._get_intelligence_products.
    Action-type prioritization: reorder->reorders first, cross_sell->cross_sells, etc.
    Excludes products the customer already owns.
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

    # Build owned-product set (exclude from recommendations)
    owned_titles = set()
    try:
        from product_intelligence import get_contact_purchase_history
        history = get_contact_purchase_history(contact.id)
        owned_titles = set(history.get("products_bought", {}).keys())
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
    if action_type == "education":
        # Education features products they ALREADY OWN (for tips/usage content)
        for title in list(owned_titles)[:2]:
            _add(title, is_product=True)
    elif action_type == "reorder_reminder":
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

    # Fill remaining from all categories — only for action types that need product recs.
    # Education shows owned products (tips), loyalty/reorder should not pad with unrelated products.
    if action_type not in ("education", "loyalty", "reorder_reminder"):
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
    # Education: show owned products (for tips), skip owned-product filter
    skip_owned_filter = (action_type == "education")
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
            # Use smart category resolution (contains + keyword strategies)
            try:
                matches = _find_products_by_category(key, limit=5)
            except Exception:
                pass

        for m in matches:
            if len(products) >= limit:
                break
            if m.product_title in out_of_stock:
                continue
            if not skip_owned_filter and m.product_title in owned_titles:
                continue  # Don't recommend products they already own
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

def _extract_strategy(strategy):
    """Extract strategy dict + current_phase from various input types.

    Handles:
        - ContactStrategy model instance → parse .strategy_json, use .current_phase
        - dict → use directly
        - JSON string → parse
        - None/empty → return ({}, "")
    """
    # ContactStrategy model instance (has strategy_json attribute)
    if hasattr(strategy, 'strategy_json'):
        raw = strategy.strategy_json
        current_phase = getattr(strategy, 'current_phase', '') or ''
        strat_dict = _safe_json(raw, {})
        return strat_dict, current_phase

    # Plain dict
    if isinstance(strategy, dict):
        return strategy, strategy.get("current_phase_name", "")

    # JSON string
    strat_dict = _safe_json(strategy, {})
    return strat_dict, strat_dict.get("current_phase_name", "")


def build_am_decision(contact, strategy, intelligence=None):
    """Build a complete AM decision package for a contact.

    Args:
        contact: Contact instance
        strategy: ContactStrategy model, strategy JSON dict, or JSON string
        intelligence: pre-fetched intel dict (optional, auto-fetched if None)

    Returns:
        dict with should_act, status, action_type, and full decision context
    """
    # Parse strategy — supports model instance, dict, or string
    strat_dict, current_phase = _extract_strategy(strategy)
    strategy_state = _normalize_strategy(strat_dict, current_phase)

    base = {
        "should_act": False,
        "status": "wait",
        "action_type": "",
        "objective": "",
        "strategy_phase": strategy_state.get("current_phase_name", "") or current_phase,
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
        "strategy_phase": strategy_state.get("current_phase_name", "") or current_phase,
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


def _infer_action_from_template(template):
    """Best-effort reverse lookup from AM template name/family to action type."""
    if template is None:
        return ""

    template_name = getattr(template, "name", "") or ""
    template_family = getattr(template, "template_family", "") or ""

    for action_type, family_info in AM_ACTION_TO_FAMILY.items():
        if template_name and template_name == family_info[0]:
            return action_type
        if template_family and template_family == family_info[1]:
            return action_type

    return ""


def restore_am_decision(raw_decision, strategy=None, template=None):
    """Rehydrate a stored AM decision without re-deciding strategy.

    Used by review-time regeneration so we preserve the original action,
    products, offer, and template context instead of building a new decision.
    """
    stored = _safe_json(raw_decision, {}) if not isinstance(raw_decision, dict) else copy.deepcopy(raw_decision)
    strat_dict, current_phase = _extract_strategy(strategy)
    strategy_state = _normalize_strategy(strat_dict, current_phase)

    inferred_action = stored.get("action_type") or _infer_action_from_template(template)
    family_info = AM_ACTION_TO_FAMILY.get(
        inferred_action,
        ("AM: Unknown", getattr(template, "template_family", "") or "promo"),
    )

    metadata = stored.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    decision = {
        "should_act": stored.get("should_act", bool(inferred_action)),
        "status": stored.get("status", "ready" if inferred_action else "invalid"),
        "action_type": inferred_action,
        "objective": stored.get("objective") or family_info[0],
        "strategy_phase": stored.get("strategy_phase") or strategy_state.get("current_phase_name", "") or current_phase,
        "template_family": stored.get("template_family") or getattr(template, "template_family", "") or family_info[1],
        "candidate_products": list(stored.get("candidate_products") or []),
        "offer_context": stored.get("offer_context"),
        "scheduled_at": stored.get("scheduled_at"),
        "expected_value": stored.get("expected_value", 0.0),
        "confidence": stored.get("confidence", 0.0),
        "reasoning": stored.get("reasoning", ""),
        "wait_until": stored.get("wait_until"),
        "metadata": metadata,
    }

    if decision["action_type"] and "suggested_next" not in decision["metadata"]:
        decision["metadata"]["suggested_next"] = _suggest_next_action(
            strategy_state,
            {"action_type": decision["action_type"], "metadata": decision["metadata"]},
        )

    return decision


# ==================================================================
# AI copy generation
# ==================================================================

def _get_openrouter_client():
    """Create an OpenAI client configured for OpenRouter.

    Raises RuntimeError with a clear message if the openai package is not installed.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "openai package not installed — run 'pip install openai' on the VPS. "
            "AM AI copy generation requires this package for OpenRouter access."
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in environment")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


def _generate_ai_copy(contact, decision, intelligence, reviewer_feedback=""):
    """Generate email copy via AI with 3-layer architecture.

    Layer 1: Action-scoped intelligence brief (input filtering)
    Layer 2: Per-action prompt template with named output slots
    Layer 3: Structural output validation (banned phrases, timeline detection)

    Returns:
        dict: {hero_headline, hero_subheadline, paragraphs, cta_text, cta_url,
               token_usage, copy_source, validation_violations}
        copy_source values:
            "ai"   — passed all validation, safe to inject
            "seed" — AI output failed validation, use seed template instead
            "api_error" — API call failed, use seed template instead
    """
    action_type = decision.get("action_type", "education")
    products = decision.get("candidate_products", [])
    offer = decision.get("offer_context")

    # ── Layer 1: Action-scoped intelligence ──
    intel_text = ""
    try:
        intel_text = intelligence_layer.format_intelligence_for_action(contact.id, action_type)
    except Exception:
        pass

    # Product context for prompt
    product_lines = []
    for p in products[:4]:
        product_lines.append("- %s ($%s)" % (p.get("product_title", "Product"), p.get("price", "0")))
    product_text = "\n".join(product_lines) if product_lines else "No specific products."

    # Offer context
    offer_text = "No discount offer." if not offer else (
        "Include offer: %s" % offer.get("display_text", "Special offer"))

    _ACTION_CTA_DEFAULTS = {
        "education": "https://ldas.ca/blogs/news",
        "product_recommendation": "https://ldas.ca/collections/all",
        "cross_sell": "https://ldas.ca/collections/accessories",
        "reorder_reminder": None,
        "loyalty": "https://ldas.ca/collections/new",
        "winback": "https://ldas.ca/collections/new",
    }
    default_cta_url = _ACTION_CTA_DEFAULTS.get(action_type) or "https://ldas.ca"
    if default_cta_url == "https://ldas.ca" and products and products[0].get("product_url"):
        default_cta_url = products[0]["product_url"]

    feedback_text = ""
    if reviewer_feedback:
        feedback_text = (
            "\nReviewer feedback to incorporate:\n%s\n"
            "Keep the same action, product set, and offer. Improve only the wording and emphasis.\n"
        ) % reviewer_feedback

    # ── Layer 2: Per-action prompt with named slots ──
    config = AM_ACTION_PROMPTS.get(action_type, {})
    system_role = config.get("system_role", "You write marketing emails for LDAS Electronics (ldas.ca).")
    output_slots = config.get("output_slots", {
        "hero_headline": "Short punchy headline (max 8 words)",
        "hero_subheadline": "Supporting line (max 15 words)",
        "paragraphs": "List of 1-2 short paragraphs",
        "cta_text": "CTA button text (max 4 words)",
    })

    # Build slot schema for prompt
    slot_lines = []
    for key, desc in output_slots.items():
        slot_lines.append('- "%s": %s' % (key, desc))
    slot_lines.append('- "cta_url": "%s"' % default_cta_url)
    slot_schema = "\n".join(slot_lines)

    prompt = """{system_role}

Customer: {name}

{intel_text}

Products to feature:
{product_text}

{offer_text}
{feedback_text}
Write the email content as JSON with ONLY these keys:
{slot_schema}

ONLY mention products listed above. Return ONLY valid JSON.""".format(
        system_role=system_role,
        name=contact.first_name or "Customer",
        intel_text=intel_text,
        product_text=product_text,
        offer_text=offer_text,
        feedback_text=feedback_text,
        slot_schema=slot_schema,
    )

    try:
        raw = None
        input_tokens = output_tokens = total_tokens = 0

        # Try OpenRouter first (preferred: openai/gpt-4o-mini)
        try:
            client = _get_openrouter_client()
            response = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content.strip()
            usage = response.usage
            input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
            if not total_tokens:
                total_tokens = input_tokens + output_tokens
        except Exception as openrouter_exc:
            logger.info("[am_runtime] OpenRouter unavailable (%s), trying ai_provider fallback", openrouter_exc)
            try:
                from ai_provider import get_provider
                provider = get_provider()
                raw = provider.complete(system_prompt="", user_prompt=prompt, max_tokens=1000)
            except Exception as provider_exc:
                raise RuntimeError("Both OpenRouter and ai_provider failed: %s / %s" % (openrouter_exc, provider_exc))

        # Parse JSON
        parsed = json.loads(raw)
        parsed["token_usage"] = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }

        # ── Layer 3: Structural validation ──
        ok, violations = _validate_ai_copy(parsed, action_type, products)
        if ok:
            parsed["copy_source"] = "ai"
            parsed["validation_violations"] = []
        else:
            logger.warning("[am_runtime] AI copy validation FAILED for %s/%s: %s",
                           contact.id, action_type, violations)
            parsed["copy_source"] = "seed"
            parsed["validation_violations"] = violations

        # Normalize named slots to standard format
        return _normalize_ai_slots(parsed, action_type)

    except Exception as exc:
        logger.warning("[am_runtime] AI copy generation failed: %s", exc)
        # API failure — signal to use seed template (NOT generic fallback)
        return {
            "hero_headline": "",
            "hero_subheadline": "",
            "paragraphs": [],
            "cta_text": "",
            "cta_url": "",
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "copy_source": "api_error",
            "api_error_reason": str(exc),
            "validation_violations": [],
        }


# ==================================================================
# Apply AI overrides to template blocks
# ==================================================================

def _apply_ai_overrides(blocks_json, ai_content):
    """Deep-copy blocks and inject AI-generated text into matching block types.

    When copy_source is "fallback" (AI unavailable), skip overrides entirely
    to preserve the seed template's purpose-specific content. The seed content
    (e.g. "You're One of Our Best" for loyalty) is always better than the
    generic fallback ("New from LDAS Electronics").

    Block format uses "block_type" key, with data nested under "content".
    - hero block -> content.headline, content.subheadline
    - text block -> content.paragraphs (list) or content.text
    - cta block  -> content.text, content.url
    """
    blocks = copy.deepcopy(blocks_json) if blocks_json else []

    # Preserve seed template when AI copy is not usable.
    # "seed" = validation failed, "api_error" = API call failed, "fallback" = legacy fallback.
    # In all cases the pre-vetted seed template content is better than bad AI copy.
    if ai_content.get("copy_source") in ("fallback", "seed", "api_error"):
        return blocks

    for block in blocks:
        btype = block.get("block_type", "")
        content = block.get("content", {})

        if btype == "hero":
            if ai_content.get("hero_headline"):
                content["headline"] = ai_content["hero_headline"]
            if ai_content.get("hero_subheadline"):
                content["subheadline"] = ai_content["hero_subheadline"]
            block["content"] = content

        elif btype == "text":
            paragraphs = ai_content.get("paragraphs", [])
            if paragraphs:
                content["paragraphs"] = paragraphs
                content["text"] = "\n\n".join(paragraphs)
            block["content"] = content

        elif btype == "cta":
            if ai_content.get("cta_text"):
                content["text"] = ai_content["cta_text"]
            # Do NOT override CTA URL — seed template URLs are authoritative
            block["content"] = content

    return blocks


# ==================================================================
# Execution entry point
# ==================================================================

def execute_am_decision(contact, strategy, decision, template=None, reviewer_feedback="", test_mode=False):
    """Execute an AM decision: AI copy generation + template rendering.

    Args:
        contact: Contact instance
        strategy: strategy JSON dict or string
        decision: dict from build_am_decision
        template: optional EmailTemplate override (locked — no learning swap)
        reviewer_feedback: optional text from human review
        test_mode: when True, subjects are prefixed with [AM TEST]

    Returns:
        dict: {status, subject, html, template_id, ai_tokens, copy_source, fallback_reason}
              or {status, reason}
    """
    from database import EmailTemplate

    action_type = decision.get("action_type", "")
    family_info = AM_ACTION_TO_FAMILY.get(action_type, ("AM: Unknown", "promo"))
    template_family = family_info[1]

    # Track whether template was explicitly provided (locked for review regeneration)
    _template_locked = template is not None

    # Step 1: find template
    if template is None and decision.get("template_id"):
        try:
            template = EmailTemplate.get_or_none(EmailTemplate.id == decision["template_id"])
        except Exception:
            template = None

    if template is None:
        try:
            # Look for template matching the objective name
            template = EmailTemplate.get_or_none(
                EmailTemplate.name == family_info[0]
            )
        except Exception:
            pass

    # Step 2: apply learning swap if available
    # ONLY for fresh nightly/autonomous decisions — NOT when template was explicitly locked
    # (review regeneration passes template explicitly to honor AMPendingReview.template_id)
    if template is not None and not _template_locked:
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
    ai_content = _generate_ai_copy(
        contact,
        decision,
        decision.get("_intelligence", {}),
        reviewer_feedback=reviewer_feedback,
    )

    # Step 4: inject AI text into template blocks
    blocks_raw = getattr(template, "blocks_json", "[]") or "[]"
    blocks = _safe_json(blocks_raw, [])
    modified_blocks = _apply_ai_overrides(blocks, ai_content)

    # Step 5: create temp template object with modified blocks
    temp_template = copy.copy(template)
    temp_template.blocks_json = json.dumps(modified_blocks)

    # Step 5b: enrich products with descriptions and compare_price
    enriched_products = decision.get("candidate_products", [])
    try:
        from flow_runtime import enrich_products_for_merch
        enriched_products = enrich_products_for_merch(enriched_products)
    except Exception as exc:
        logger.debug("[am_runtime] product enrichment skipped: %s", exc)

    # Step 6: build render contract and render
    try:
        contract = te.make_render_contract(
            template=temp_template,
            source_system="am",
            objective=decision.get("objective", ""),
            contact_id=contact.id,
            product_context=enriched_products,
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
    preview_text = result.get("preview_text", "")

    # Check validation_report for errors
    report = result.get("validation_report", [])
    errors = [r for r in report if r.get("level") == "error"]
    if errors:
        return {"status": "invalid", "reason": "validation errors: %s" % errors[0].get("message", "")}

    if not html:
        return {"status": "invalid", "reason": "empty HTML after render"}

    # Test mode: prefix subject so it's clearly identifiable
    if test_mode:
        subject = "[AM TEST] " + subject

    return {
        "status": "rendered",
        "subject": subject,
        "preheader": preview_text,
        "html": html,
        "template_id": template.id,
        "ai_tokens": ai_content.get("token_usage", {"input": 0, "output": 0, "total": 0}),
        "copy_source": ai_content.get("copy_source", "unknown"),
        "fallback_reason": ai_content.get("fallback_reason", ""),
        "api_error_reason": ai_content.get("api_error_reason", ""),
        "validation_violations": ai_content.get("validation_violations", []),
    }
