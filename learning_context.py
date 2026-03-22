"""
learning_context.py — Shared utility for learning data access.
Used by Flows (app.py) and Account Manager (account_manager.py) to make
data-driven decisions based on nightly learning outputs.

No business logic here — just clean data access with safe fallbacks.
"""

import json
import logging

logger = logging.getLogger(__name__)


def get_learning_context(contact_id):
    """Return a dict of all learning data for a contact.

    Returns:
        dict with keys: segment, engagement_score, optimal_gap_hours,
        sunset_score, churn_risk, top_templates, top_actions
    """
    from database import ContactScore, ContactStrategy, CustomerProfile

    ctx = {
        "segment": "new",
        "engagement_score": 50,
        "optimal_gap_hours": 48.0,
        "sunset_score": 0,
        "churn_risk": 0,
        "top_templates": [],
        "top_actions": [],
        "rejection_summary": "",
    }

    # ContactScore data
    try:
        cs = ContactScore.get(ContactScore.contact == contact_id)
        ctx["segment"] = cs.rfm_segment or "new"
        ctx["engagement_score"] = cs.engagement_score or 50
        ctx["optimal_gap_hours"] = cs.optimal_gap_hours or 48.0
        ctx["sunset_score"] = cs.sunset_score or 0
    except Exception:
        pass

    # Churn risk from CustomerProfile
    try:
        cp = CustomerProfile.get(CustomerProfile.contact == contact_id)
        ctx["churn_risk"] = cp.churn_risk_score or 0
    except Exception:
        pass

    # Top templates for this segment
    try:
        from strategy_optimizer import get_template_recommendations
        ctx["top_templates"] = get_template_recommendations(ctx["segment"])[:3]
    except Exception:
        pass

    # Top actions for this segment
    try:
        ctx["top_actions"] = get_top_actions_for_segment(ctx["segment"])
    except Exception:
        pass

    # Rejection patterns from AM
    try:
        cs_strat = ContactStrategy.get_or_none(ContactStrategy.contact == contact_id)
        if cs_strat and cs_strat.rejection_reasons:
            ctx["rejection_summary"] = analyze_rejection_patterns(cs_strat.rejection_reasons)
    except Exception:
        pass

    return ctx


def get_top_actions_for_segment(segment, limit=3):
    """Return top action types for a segment ranked by conversion_rate.

    Returns:
        list of dicts: [{action_type, conversion_rate, open_rate, sample_size}, ...]
    """
    from database import ActionPerformance

    results = []
    try:
        rows = (ActionPerformance.select()
                .where(ActionPerformance.segment == segment,
                       ActionPerformance.sample_size >= 20)
                .order_by(ActionPerformance.conversion_rate.desc())
                .limit(limit))
        for r in rows:
            results.append({
                "action_type": r.action_type,
                "conversion_rate": round(r.conversion_rate * 100, 1),
                "open_rate": round(r.open_rate * 100, 1),
                "sample_size": r.sample_size,
            })
    except Exception:
        pass

    return results


def get_best_template_for_family(family, segment):
    """Return the best template ID for a family+segment combo.

    Queries TemplateSegmentPerformance for templates in the given family,
    ranked by open_rate for the given segment.

    Args:
        family: Template family string (e.g., 'welcome', 'cart_recovery')
        segment: RFM segment string (e.g., 'champion', 'new')

    Returns:
        int template_id or None (caller should use default)
    """
    from database import TemplateSegmentPerformance, EmailTemplate
    from learning_config import get_learning_phase

    # Only swap templates in active learning phase
    phase = get_learning_phase()
    if phase != "active":
        return None

    try:
        # Find all templates in this family
        family_templates = list(
            EmailTemplate.select(EmailTemplate.id)
            .where(EmailTemplate.family == family)
        )
        if len(family_templates) <= 1:
            return None  # No alternatives to pick from

        template_ids = [t.id for t in family_templates]

        # Get performance for these templates in this segment
        best = (TemplateSegmentPerformance.select()
                .where(TemplateSegmentPerformance.template.in_(template_ids),
                       TemplateSegmentPerformance.segment == segment,
                       TemplateSegmentPerformance.sample_size >= 30)
                .order_by(TemplateSegmentPerformance.open_rate.desc())
                .first())

        if best:
            return best.template_id

    except Exception as e:
        logger.debug("[LearningContext] Template lookup error: %s", e)

    return None


def analyze_rejection_patterns(rejection_reasons_json):
    """Parse AM rejection reasons and return a 1-line summary.

    Args:
        rejection_reasons_json: JSON string (array of reason strings)

    Returns:
        str like "Common feedback: too salesy (3x), wrong product (2x)"
    """
    try:
        reasons = json.loads(rejection_reasons_json) if isinstance(rejection_reasons_json, str) else rejection_reasons_json
        if not reasons:
            return ""

        # Keyword frequency counting
        keywords = {
            "too salesy": 0, "salesy": 0, "pushy": 0,
            "wrong product": 0, "irrelevant": 0,
            "too long": 0, "verbose": 0,
            "tone": 0, "casual": 0, "formal": 0,
            "discount": 0, "price": 0,
            "timing": 0, "too soon": 0, "too frequent": 0,
            "generic": 0, "impersonal": 0,
        }

        for reason in reasons:
            lower = reason.lower() if isinstance(reason, str) else ""
            for kw in keywords:
                if kw in lower:
                    keywords[kw] += 1

        # Merge related keywords
        merged = {}
        merged["too salesy"] = keywords["too salesy"] + keywords["salesy"] + keywords["pushy"]
        merged["wrong product"] = keywords["wrong product"] + keywords["irrelevant"]
        merged["too long"] = keywords["too long"] + keywords["verbose"]
        merged["wrong tone"] = keywords["tone"] + keywords["casual"] + keywords["formal"]
        merged["discount issues"] = keywords["discount"] + keywords["price"]
        merged["too frequent"] = keywords["timing"] + keywords["too soon"] + keywords["too frequent"]
        merged["too generic"] = keywords["generic"] + keywords["impersonal"]

        # Filter to patterns that appeared 2+ times
        patterns = [(k, v) for k, v in merged.items() if v >= 2]
        patterns.sort(key=lambda x: -x[1])

        if not patterns:
            # Just return the last 3 raw reasons
            recent = reasons[-3:] if len(reasons) > 3 else reasons
            return "Recent feedback: " + " | ".join(str(r) for r in recent)

        parts = ["%s (%dx)" % (k, v) for k, v in patterns[:3]]
        return "Common feedback: " + ", ".join(parts)

    except Exception:
        return ""


def build_learning_prompt_section(contact_id, segment=None):
    """Build a text block for Claude's AM prompt with learning insights.

    Returns:
        str — ready to inject into the Claude prompt, or "" if no data
    """
    ctx = get_learning_context(contact_id)
    if segment:
        ctx["segment"] = segment

    lines = ["[LEARNING INSIGHTS]"]

    # Top actions for this segment
    if ctx["top_actions"]:
        lines.append("Best actions for %s customers:" % ctx["segment"])
        for a in ctx["top_actions"]:
            lines.append("  - %s: %.1f%% conversion, %.1f%% opens (n=%d)" % (
                a["action_type"], a["conversion_rate"], a["open_rate"], a["sample_size"]))

    # Top templates for this segment
    if ctx["top_templates"]:
        lines.append("Top templates for %s customers:" % ctx["segment"])
        for t in ctx["top_templates"]:
            lines.append("  - %s: %.1f%% opens, %.2f%% conversion, $%.2f rev/send (%s confidence)" % (
                t.get("name", "template_%d" % t.get("template_id", 0)),
                t.get("open_rate", 0) * 100,
                t.get("conversion_rate", 0) * 100,
                t.get("revenue_per_send", 0),
                t.get("confidence", "learning")))

    # Rejection patterns
    if ctx["rejection_summary"]:
        lines.append("[PAST FEEDBACK] %s" % ctx["rejection_summary"])
        lines.append("Adjust your writing based on this feedback.")

    # Engagement level
    lines.append("Contact engagement: %d/100 | Segment: %s | Sunset risk: %d/100" % (
        ctx["engagement_score"], ctx["segment"], ctx["sunset_score"]))

    if len(lines) <= 1:
        return ""  # No meaningful data

    return "\n".join(lines)
