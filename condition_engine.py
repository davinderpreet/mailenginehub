"""
condition_engine.py — Conditional Logic for Journey-Aware Email Templates

Phase 2 of the unified template system. Evaluates block-level conditions
against a contact's profile to determine which block variant to render.

Supported fields (from Contact + CustomerProfile):
  - lifecycle_stage: prospect|new_customer|active_buyer|loyal|vip|at_risk|churned|reactivated
  - customer_type:   browser|one_time|repeat|loyal|vip|discount_seeker|dormant
  - total_orders:    integer
  - total_spent:     float
  - days_since_last_order: integer
  - has_used_discount: boolean
  - tags:            comma-separated string on Contact

Supported operators:
  - eq:  equals (string or numeric)
  - neq: not equals
  - gt:  greater than (numeric)
  - lt:  less than (numeric)
  - in:  value is one of a list
  - contains: string contains substring (for tags)
  - not_contains: string does not contain substring

Usage:
    from condition_engine import evaluate_conditions, get_contact_context

    ctx = get_contact_context(contact)
    matched = evaluate_conditions(variant["conditions"], ctx)
"""

from database import Contact, CustomerProfile


def get_contact_context(contact):
    """
    Build a flat evaluation context dict from a Contact and its CustomerProfile.

    Args:
        contact: Contact model instance

    Returns:
        dict with all supported condition fields
    """
    ctx = {
        "lifecycle_stage": "unknown",
        "customer_type": "unknown",
        "total_orders": 0,
        "total_spent": 0.0,
        "days_since_last_order": 999,
        "has_used_discount": False,
        "tags": getattr(contact, "tags", "") or "",
    }

    if not contact:
        return ctx

    # Pull from Contact model directly
    ctx["tags"] = getattr(contact, "tags", "") or ""
    ctx["total_orders"] = getattr(contact, "total_orders", 0) or 0
    ctx["total_spent"] = float(getattr(contact, "total_spent", 0.0) or 0.0)

    # Pull from CustomerProfile if it exists
    try:
        profile = CustomerProfile.get(CustomerProfile.contact == contact)
        ctx["lifecycle_stage"] = profile.lifecycle_stage or "unknown"
        ctx["customer_type"] = profile.customer_type or "unknown"
        ctx["total_orders"] = max(ctx["total_orders"], profile.total_orders or 0)
        ctx["total_spent"] = max(ctx["total_spent"], float(profile.total_spent or 0.0))
        ctx["days_since_last_order"] = profile.days_since_last_order if profile.days_since_last_order is not None else 999
        ctx["has_used_discount"] = bool(profile.has_used_discount)
    except CustomerProfile.DoesNotExist:
        pass

    return ctx


def evaluate_conditions(conditions, context):
    """
    Evaluate a list of conditions against a contact context.
    All conditions must pass (AND logic) for the variant to match.

    Args:
        conditions: list of dicts, each with {"field", "op", "value"}
        context:    dict from get_contact_context()

    Returns:
        bool: True if ALL conditions pass
    """
    if not conditions:
        return True  # No conditions = always matches (default variant)

    for cond in conditions:
        field = cond.get("field", "")
        op = cond.get("op", "eq")
        expected = cond.get("value")

        actual = context.get(field)
        if actual is None:
            return False  # Unknown field → condition fails

        if not _evaluate_single(actual, op, expected):
            return False

    return True


def _evaluate_single(actual, op, expected):
    """
    Evaluate a single condition.

    Returns:
        bool: True if the condition passes
    """
    if op == "eq":
        return _coerce_compare(actual, expected) == 0

    elif op == "neq":
        return _coerce_compare(actual, expected) != 0

    elif op == "gt":
        try:
            return float(actual) > float(expected)
        except (ValueError, TypeError):
            return False

    elif op == "lt":
        try:
            return float(actual) < float(expected)
        except (ValueError, TypeError):
            return False

    elif op == "in":
        # expected should be a list
        if isinstance(expected, list):
            return str(actual).lower() in [str(v).lower() for v in expected]
        # Support comma-separated string
        if isinstance(expected, str):
            return str(actual).lower() in [v.strip().lower() for v in expected.split(",")]
        return False

    elif op == "contains":
        return str(expected).lower() in str(actual).lower()

    elif op == "not_contains":
        return str(expected).lower() not in str(actual).lower()

    else:
        # Unknown operator → fail safe
        return False


def _coerce_compare(actual, expected):
    """
    Compare two values. Try numeric first, then string.
    Returns: -1, 0, or 1 (like cmp)
    """
    # Boolean comparison
    if isinstance(actual, bool) or isinstance(expected, bool):
        a = _to_bool(actual)
        b = _to_bool(expected)
        return 0 if a == b else (1 if a else -1)

    # Try numeric
    try:
        a = float(actual)
        b = float(expected)
        if a == b:
            return 0
        return -1 if a < b else 1
    except (ValueError, TypeError):
        pass

    # String comparison (case-insensitive)
    a = str(actual).lower().strip()
    b = str(expected).lower().strip()
    if a == b:
        return 0
    return -1 if a < b else 1


def _to_bool(value):
    """Convert various representations to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).lower().strip()
    return s in ("true", "1", "yes", "on")


# =========================================================================
#  VARIANT RESOLUTION
# =========================================================================

def resolve_block_variants(block, context):
    """
    Given a block with optional variants, resolve which content to render.
    First-match-wins: iterate variants in order, return the first whose
    conditions all pass. If none match, return the block's default content.

    Args:
        block:   dict with "block_type", "content", and optional "variants"
        context: dict from get_contact_context()

    Returns:
        tuple: (resolved_content_dict, explain_dict)
            explain_dict has: block_type, matched_variant_index (int or None),
                              matched_conditions (list or None),
                              resolved_content_summary (str)
    """
    block_type = block.get("block_type", "")
    default_content = block.get("content", {})
    variants = block.get("variants", [])

    explain = {
        "block_type": block_type,
        "matched_variant_index": None,
        "matched_conditions": None,
        "resolved_content_summary": "default",
    }

    if not variants:
        return default_content, explain

    for i, variant in enumerate(variants):
        conditions = variant.get("conditions", [])
        if evaluate_conditions(conditions, context):
            variant_content = variant.get("content", {})
            # Merge: variant content overrides default content
            merged = dict(default_content)
            merged.update(variant_content)

            explain["matched_variant_index"] = i
            explain["matched_conditions"] = conditions
            explain["resolved_content_summary"] = _summarize_conditions(conditions)

            return merged, explain

    # No variant matched → use default
    return default_content, explain


def _summarize_conditions(conditions):
    """Create a human-readable summary of matched conditions."""
    if not conditions:
        return "default (no conditions)"

    parts = []
    for c in conditions:
        field = c.get("field", "?")
        op = c.get("op", "eq")
        value = c.get("value", "?")

        if op == "eq":
            parts.append("%s = %s" % (field, value))
        elif op == "neq":
            parts.append("%s ≠ %s" % (field, value))
        elif op == "gt":
            parts.append("%s > %s" % (field, value))
        elif op == "lt":
            parts.append("%s < %s" % (field, value))
        elif op == "in":
            if isinstance(value, list):
                parts.append("%s in [%s]" % (field, ", ".join(str(v) for v in value)))
            else:
                parts.append("%s in %s" % (field, value))
        elif op == "contains":
            parts.append("%s contains '%s'" % (field, value))
        elif op == "not_contains":
            parts.append("%s not contains '%s'" % (field, value))
        else:
            parts.append("%s %s %s" % (field, op, value))

    return " AND ".join(parts)


# =========================================================================
#  TEMPLATE FAMILY REGISTRY
# =========================================================================

TEMPLATE_FAMILIES = {
    "welcome": {
        "label": "Welcome Series",
        "description": "Onboarding flow for new subscribers",
        "allowed_blocks": ["hero", "text", "discount", "product_grid", "cta", "divider"],
        "recommended_order": ["hero", "text", "discount", "product_grid", "cta"],
        "required_blocks": ["hero", "text", "cta"],
        "max_blocks": 8,
    },
    "browse_recovery": {
        "label": "Browse Recovery",
        "description": "Re-engage contacts who viewed products but didn't add to cart",
        "allowed_blocks": ["hero", "text", "product_grid", "cta", "urgency", "divider"],
        "recommended_order": ["hero", "text", "product_grid", "cta"],
        "required_blocks": ["product_grid", "cta"],
        "max_blocks": 6,
    },
    "cart_recovery": {
        "label": "Cart Recovery",
        "description": "Recover abandoned shopping carts",
        "allowed_blocks": ["hero", "text", "product_grid", "discount", "cta", "urgency", "divider"],
        "recommended_order": ["hero", "text", "product_grid", "discount", "urgency", "cta"],
        "required_blocks": ["product_grid", "cta"],
        "max_blocks": 8,
    },
    "checkout_recovery": {
        "label": "Checkout Recovery",
        "description": "Recover abandoned checkouts (high intent)",
        "allowed_blocks": ["hero", "text", "product_grid", "discount", "cta", "urgency", "divider"],
        "recommended_order": ["hero", "text", "product_grid", "discount", "urgency", "cta"],
        "required_blocks": ["cta"],
        "max_blocks": 7,
    },
    "post_purchase": {
        "label": "Post Purchase",
        "description": "Thank you, cross-sell, and review request emails",
        "allowed_blocks": ["hero", "text", "product_grid", "discount", "cta", "divider"],
        "recommended_order": ["hero", "text", "product_grid", "cta"],
        "required_blocks": ["text", "cta"],
        "max_blocks": 7,
    },
    "winback": {
        "label": "Win-Back",
        "description": "Re-engage lapsed or at-risk customers",
        "allowed_blocks": ["hero", "text", "discount", "product_grid", "cta", "urgency", "divider"],
        "recommended_order": ["hero", "text", "discount", "product_grid", "urgency", "cta"],
        "required_blocks": ["text", "cta"],
        "max_blocks": 8,
    },
    "promo": {
        "label": "Promotional",
        "description": "Sales, flash deals, seasonal campaigns",
        "allowed_blocks": ["hero", "text", "discount", "product_grid", "cta", "urgency", "divider"],
        "recommended_order": ["hero", "discount", "product_grid", "urgency", "cta"],
        "required_blocks": ["cta"],
        "max_blocks": 10,
    },
}


def validate_family(blocks_json_str, family_key):
    """
    Validate a blocks template against its template family rules.

    Args:
        blocks_json_str: JSON string of block definitions
        family_key: key from TEMPLATE_FAMILIES (e.g. "welcome", "cart_recovery")

    Returns:
        list of dicts: [{"level": "error"|"warning", "message": "..."}]
    """
    import json
    warnings = []

    family = TEMPLATE_FAMILIES.get(family_key)
    if not family:
        return [{"level": "warning", "message": "Unknown template family '%s' -- skipping family validation" % family_key}]

    try:
        blocks = json.loads(blocks_json_str or "[]")
    except (json.JSONDecodeError, TypeError):
        return [{"level": "error", "message": "Invalid JSON in blocks definition"}]

    if not blocks:
        return [{"level": "error", "message": "Template has no blocks defined"}]

    block_types_present = [b.get("block_type", "") for b in blocks]

    # Check required blocks
    for required in family["required_blocks"]:
        if required not in block_types_present:
            warnings.append({
                "level": "error",
                "message": "%s family requires a '%s' block" % (family["label"], required)
            })

    # Check allowed blocks
    allowed = set(family["allowed_blocks"])
    for bt in block_types_present:
        if bt not in allowed:
            warnings.append({
                "level": "warning",
                "message": "Block type '%s' is not typical for %s templates" % (bt, family["label"])
            })

    # Check max blocks
    if len(blocks) > family["max_blocks"]:
        warnings.append({
            "level": "warning",
            "message": "%s templates should have at most %d blocks (has %d)" % (
                family["label"], family["max_blocks"], len(blocks))
        })

    # Check for duplicate CTA blocks
    cta_count = block_types_present.count("cta")
    if cta_count > 2:
        warnings.append({
            "level": "warning",
            "message": "Template has %d CTA buttons -- more than 2 can reduce click-through rates" % cta_count
        })

    # Variant warnings: check for unreachable variants (basic static analysis)
    for i, block in enumerate(blocks):
        variants = block.get("variants", [])
        if not variants:
            continue

        has_default = False
        for vi, variant in enumerate(variants):
            conditions = variant.get("conditions", [])
            if not conditions:
                has_default = True
                if vi < len(variants) - 1:
                    warnings.append({
                        "level": "warning",
                        "message": "Block %d: Variant %d has no conditions (always matches) but is not the last variant -- subsequent variants are unreachable" % (i + 1, vi + 1)
                    })

    return warnings
