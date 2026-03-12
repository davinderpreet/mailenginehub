"""
condition_engine.py — Conditional Logic for Journey-Aware Email Templates

Phase 2 of the unified template system. Evaluates block-level conditions
against a contact's profile to determine which block variant to render.

CONDITION SCHEMA CONTRACT (frozen — builder/storage/preview all use this):
─────────────────────────────────────────────────────────────────────────

Each condition is a dict with exactly 3 keys:
    {"field": "<field_key>", "op": "<operator>", "value": <value>}

FIELDS (CONDITION_FIELDS):
    Field                  | Type    | Source           | Allowed values
    ───────────────────────┼─────────┼──────────────────┼─────────────────────────────
    lifecycle_stage        | str     | CustomerProfile  | prospect, new_customer, active_buyer, loyal, vip, at_risk, churned, reactivated, unknown
    customer_type          | str     | CustomerProfile  | browser, one_time, repeat, loyal, vip, discount_seeker, dormant, unknown
    total_orders           | int     | Contact/Profile  | >= 0
    total_spent            | float   | Contact/Profile  | >= 0.0
    days_since_last_order  | int     | CustomerProfile  | >= 0 (999 = never ordered)
    has_used_discount      | bool    | CustomerProfile  | true / false
    tags                   | str     | Contact          | comma-separated tag string

OPERATORS (CONDITION_OPERATORS):
    Operator       | Accepts types         | Value format         | Behaviour
    ───────────────┼───────────────────────┼──────────────────────┼───────────────────
    eq             | str, int, float, bool | single value         | Equals (case-insensitive for strings)
    neq            | str, int, float, bool | single value         | Not equals
    gt             | int, float            | numeric              | Greater than
    lt             | int, float            | numeric              | Less than
    in             | str                   | list or csv string   | Value is one of the set
    contains       | str                   | substring            | Field contains substring (for tags)
    not_contains   | str                   | substring            | Field does not contain substring

VARIANT SCHEMA:
    Each block may have an optional "variants" list:
    {
        "block_type": "hero",
        "content": { ... default content ... },
        "variants": [
            {
                "conditions": [ {"field": "...", "op": "...", "value": ...} ],
                "content": { ... override fields ... }
            }
        ]
    }

    Resolution: first-match-wins. Variant content merges over default content.
    Empty conditions list = unconditional match (use as last fallback only).

EXPLAINABILITY PAYLOAD (frozen — preview/debug output):
    Each block produces an explain dict with this exact shape:
    {
        "block_index":              int,     # 0-based position in blocks array
        "block_type":               str,     # e.g. "hero", "text", "cta"
        "matched_variant_index":    int|None,# index into variants[] or None if default
        "matched_conditions":       list|None,# conditions list that matched, or None
        "resolved_content_summary": str      # human-readable: "default", "no variants",
                                             #   "lifecycle_stage = vip AND total_orders > 5",
                                             #   "default (no contact context)", etc.
    }

    The preview route (?explain=1) wraps these in:
    {
        "html":            str,
        "explain":         [explain_dict, ...],
        "contact_context": dict|absent,   # only if contact_id provided
        "contact_info":    dict|absent,   # only if contact_id provided
        "warnings":        [warn_dict, ...]|absent  # only if validate=1
    }

Usage:
    from condition_engine import evaluate_conditions, get_contact_context
    from condition_engine import CONDITION_FIELDS, CONDITION_OPERATORS

    ctx = get_contact_context(contact)
    matched = evaluate_conditions(variant["conditions"], ctx)
"""

from database import Contact, CustomerProfile


# =========================================================================
#  AUTHORITATIVE SCHEMA — single source of truth for conditions
#  Builder, validator, evaluator, and preview all reference these dicts.
# =========================================================================

CONDITION_FIELDS = {
    "lifecycle_stage": {
        "type": "str",
        "label": "Lifecycle Stage",
        "source": "CustomerProfile",
        "allowed_values": [
            "prospect", "new_customer", "active_buyer", "loyal",
            "vip", "at_risk", "churned", "reactivated", "unknown",
        ],
        "allowed_ops": ["eq", "neq", "in"],
        "default": "unknown",
    },
    "customer_type": {
        "type": "str",
        "label": "Customer Type",
        "source": "CustomerProfile",
        "allowed_values": [
            "browser", "one_time", "repeat", "loyal",
            "vip", "discount_seeker", "dormant", "unknown",
        ],
        "allowed_ops": ["eq", "neq", "in"],
        "default": "unknown",
    },
    "total_orders": {
        "type": "int",
        "label": "Total Orders",
        "source": "Contact + CustomerProfile",
        "allowed_values": None,  # any int >= 0
        "allowed_ops": ["eq", "neq", "gt", "lt"],
        "default": 0,
    },
    "total_spent": {
        "type": "float",
        "label": "Total Spent ($)",
        "source": "Contact + CustomerProfile",
        "allowed_values": None,  # any float >= 0
        "allowed_ops": ["eq", "neq", "gt", "lt"],
        "default": 0.0,
    },
    "days_since_last_order": {
        "type": "int",
        "label": "Days Since Last Order",
        "source": "CustomerProfile",
        "allowed_values": None,  # any int >= 0; 999 = never ordered
        "allowed_ops": ["eq", "neq", "gt", "lt"],
        "default": 999,
    },
    "has_used_discount": {
        "type": "bool",
        "label": "Has Used Discount",
        "source": "CustomerProfile",
        "allowed_values": [True, False],
        "allowed_ops": ["eq", "neq"],
        "default": False,
    },
    "tags": {
        "type": "str",
        "label": "Contact Tags",
        "source": "Contact",
        "allowed_values": None,  # free-form comma-separated string
        "allowed_ops": ["contains", "not_contains", "eq"],
        "default": "",
    },
}

CONDITION_OPERATORS = {
    "eq": {
        "label": "equals",
        "accepts_types": ["str", "int", "float", "bool"],
        "value_format": "single",
    },
    "neq": {
        "label": "not equals",
        "accepts_types": ["str", "int", "float", "bool"],
        "value_format": "single",
    },
    "gt": {
        "label": "greater than",
        "accepts_types": ["int", "float"],
        "value_format": "numeric",
    },
    "lt": {
        "label": "less than",
        "accepts_types": ["int", "float"],
        "value_format": "numeric",
    },
    "in": {
        "label": "is one of",
        "accepts_types": ["str"],
        "value_format": "list_or_csv",
    },
    "contains": {
        "label": "contains",
        "accepts_types": ["str"],
        "value_format": "substring",
    },
    "not_contains": {
        "label": "does not contain",
        "accepts_types": ["str"],
        "value_format": "substring",
    },
}

# Frozen explain payload keys — must always be present in every explain dict
EXPLAIN_PAYLOAD_KEYS = frozenset([
    "block_index",
    "block_type",
    "matched_variant_index",
    "matched_conditions",
    "resolved_content_summary",
])


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

def _make_explain(block_index, block_type, variant_index=None, conditions=None, summary="default"):
    """
    Build a frozen-shape explain dict. All explain payloads MUST go through this
    function to guarantee the contract defined by EXPLAIN_PAYLOAD_KEYS.
    """
    return {
        "block_index": block_index,
        "block_type": block_type,
        "matched_variant_index": variant_index,
        "matched_conditions": conditions,
        "resolved_content_summary": summary,
    }


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
            explain_dict always has exactly the keys in EXPLAIN_PAYLOAD_KEYS.
    """
    block_type = block.get("block_type", "")
    default_content = block.get("content", {})
    variants = block.get("variants", [])

    if not variants:
        return default_content, _make_explain(
            block_index=0, block_type=block_type, summary="default"
        )

    for i, variant in enumerate(variants):
        conditions = variant.get("conditions", [])
        if evaluate_conditions(conditions, context):
            variant_content = variant.get("content", {})
            # Merge: variant content overrides default content
            merged = dict(default_content)
            merged.update(variant_content)

            return merged, _make_explain(
                block_index=0,
                block_type=block_type,
                variant_index=i,
                conditions=conditions,
                summary=_summarize_conditions(conditions),
            )

    # No variant matched → use default
    return default_content, _make_explain(
        block_index=0, block_type=block_type, summary="default"
    )


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


def validate_condition(condition, block_num=0, variant_num=0, cond_num=0):
    """
    Validate a single condition dict against the authoritative schema.
    Returns list of warning dicts. Uses CONDITION_FIELDS and CONDITION_OPERATORS
    as the single source of truth.

    Args:
        condition: dict with {"field", "op", "value"}
        block_num, variant_num, cond_num: for error message context (1-based)

    Returns:
        list of dicts: [{"level": "error"|"warning", "message": "..."}]
    """
    warnings = []
    prefix = "Block %d variant %d condition %d" % (block_num, variant_num, cond_num)

    field = condition.get("field", "")
    op = condition.get("op", "")
    value = condition.get("value")

    # Missing keys
    if not field:
        warnings.append({"level": "error", "message": "%s: missing 'field'" % prefix})
        return warnings
    if not op:
        warnings.append({"level": "error", "message": "%s: missing 'op'" % prefix})
        return warnings

    # Unknown field
    field_def = CONDITION_FIELDS.get(field)
    if not field_def:
        warnings.append({
            "level": "error",
            "message": "%s: unknown field '%s' (valid: %s)" % (
                prefix, field, ", ".join(sorted(CONDITION_FIELDS.keys())))
        })
        return warnings

    # Unknown operator
    op_def = CONDITION_OPERATORS.get(op)
    if not op_def:
        warnings.append({
            "level": "error",
            "message": "%s: unknown operator '%s' (valid: %s)" % (
                prefix, op, ", ".join(sorted(CONDITION_OPERATORS.keys())))
        })
        return warnings

    # Operator not allowed for this field
    if op not in field_def["allowed_ops"]:
        warnings.append({
            "level": "error",
            "message": "%s: operator '%s' is not valid for field '%s' (allowed: %s)" % (
                prefix, op, field, ", ".join(field_def["allowed_ops"]))
        })

    # Value type checks
    field_type = field_def["type"]
    op_accepts = op_def["accepts_types"]
    if field_type not in op_accepts:
        warnings.append({
            "level": "error",
            "message": "%s: field '%s' (type %s) is incompatible with operator '%s' (accepts %s)" % (
                prefix, field, field_type, op, ", ".join(op_accepts))
        })

    # Value format checks
    if value is None and op not in ("eq", "neq"):
        warnings.append({
            "level": "error",
            "message": "%s: 'value' is required for operator '%s'" % (prefix, op)
        })

    if op in ("gt", "lt"):
        try:
            float(value)
        except (ValueError, TypeError):
            warnings.append({
                "level": "error",
                "message": "%s: operator '%s' requires a numeric value (got %r)" % (prefix, op, value)
            })

    if op == "in" and not isinstance(value, (list, str)):
        warnings.append({
            "level": "error",
            "message": "%s: operator 'in' requires a list or comma-separated string (got %s)" % (
                prefix, type(value).__name__)
        })

    # Enum value check for fields with allowed_values
    if field_def["allowed_values"] is not None and op == "eq":
        if value not in field_def["allowed_values"] and str(value).lower() not in [str(v).lower() for v in field_def["allowed_values"]]:
            warnings.append({
                "level": "warning",
                "message": "%s: value %r is not a known value for '%s' (known: %s)" % (
                    prefix, value, field, ", ".join(str(v) for v in field_def["allowed_values"]))
            })

    if field_def["allowed_values"] is not None and op == "in":
        check_values = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        known = [str(v).lower() for v in field_def["allowed_values"]]
        for v in check_values:
            if str(v).lower() not in known:
                warnings.append({
                    "level": "warning",
                    "message": "%s: value '%s' is not a known value for '%s'" % (prefix, v, field)
                })

    return warnings


def validate_family(blocks_json_str, family_key):
    """
    Validate a blocks template against its template family rules.
    STRICT enforcement — disallowed blocks are errors, not warnings.
    Phase 3 AI authoring cannot bypass these constraints.

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
        return [{"level": "error", "message": "Unknown template family '%s' -- cannot validate (valid: %s)" % (
            family_key, ", ".join(sorted(TEMPLATE_FAMILIES.keys())))}]

    try:
        blocks = json.loads(blocks_json_str or "[]")
    except (json.JSONDecodeError, TypeError):
        return [{"level": "error", "message": "Invalid JSON in blocks definition"}]

    if not blocks:
        return [{"level": "error", "message": "Template has no blocks defined"}]

    block_types_present = [b.get("block_type", "") for b in blocks]

    # Check required blocks — ERROR level
    for required in family["required_blocks"]:
        if required not in block_types_present:
            warnings.append({
                "level": "error",
                "message": "%s family requires a '%s' block" % (family["label"], required)
            })

    # Check allowed blocks — STRICT: disallowed = ERROR (not warning)
    # Phase 3 AI content cannot inject blocks outside the family's allowed set
    allowed = set(family["allowed_blocks"])
    for bt in block_types_present:
        if bt not in allowed:
            warnings.append({
                "level": "error",
                "message": "Block type '%s' is not allowed in %s templates (allowed: %s)" % (
                    bt, family["label"], ", ".join(sorted(allowed)))
            })

    # Check max blocks — ERROR level (strict cap)
    if len(blocks) > family["max_blocks"]:
        warnings.append({
            "level": "error",
            "message": "%s templates cannot exceed %d blocks (has %d)" % (
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

        for vi, variant in enumerate(variants):
            conditions = variant.get("conditions", [])
            if not conditions and vi < len(variants) - 1:
                warnings.append({
                    "level": "warning",
                    "message": "Block %d: Variant %d has no conditions (always matches) but is not the last variant -- subsequent variants are unreachable" % (i + 1, vi + 1)
                })

    return warnings


def enforce_family_constraints(blocks, family_key):
    """
    HARD enforcement gate for Phase 3 AI authoring.
    Returns (is_valid, error_list). If is_valid is False, the template
    MUST NOT be saved or sent. This function is the final guard —
    validate_family() is advisory, this is a hard stop.

    Args:
        blocks: list of block dicts (already parsed, not JSON string)
        family_key: template family key

    Returns:
        tuple: (bool, list_of_error_strings)
    """
    family = TEMPLATE_FAMILIES.get(family_key)
    if not family:
        return False, ["Unknown template family '%s'" % family_key]

    errors = []
    allowed = set(family["allowed_blocks"])

    for i, block in enumerate(blocks):
        bt = block.get("block_type", "")
        if bt not in allowed:
            errors.append("Block %d: type '%s' is not allowed in %s family" % (i + 1, bt, family_key))

    if len(blocks) > family["max_blocks"]:
        errors.append("Exceeds max blocks (%d > %d) for %s family" % (
            len(blocks), family["max_blocks"], family_key))

    for required in family["required_blocks"]:
        if required not in [b.get("block_type", "") for b in blocks]:
            errors.append("Missing required block '%s' for %s family" % (required, family_key))

    return (len(errors) == 0, errors)
