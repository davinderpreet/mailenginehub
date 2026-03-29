"""
flow_runtime.py — Centralized Flow Email Render & Decision Engine

Phase 3 of the MailEngineHub architecture rebuild.
Single entry point for building a fully-resolved email send package for any
flow enrollment step. Handles objective resolution, soft timing gates, product
resolution, offer resolution, template rendering, and subject personalization.

Build order: Intelligence Layer (Phase 1) → Templates (Phase 2) → Flows (this, Phase 3)

Public API:
    package = build_flow_send_package(enrollment, step, contact, flow,
                                      template=None, trigger_context=None)
    if package["status"] == "ready":
        send(package["html"], package["subject"])
"""

import json
import logging
from datetime import datetime

import intelligence_layer
import template_engine as te

# discount_engine imports requests at module level; we handle missing gracefully
try:
    import discount_engine
except ImportError:
    discount_engine = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Trigger → metadata maps
# ═══════════════════════════════════════════════════════════════

TRIGGER_URGENCY = {
    "checkout_abandoned": "urgent",
    "browse_abandonment": "urgent",
    "cart_abandonment": "urgent",
    "contact_created": "medium",
    "order_placed": "medium",
    "tag_added": "medium",
    "no_purchase_days": "lower",
}

TRIGGER_OBJECTIVE = {
    "checkout_abandoned": "checkout_recovery",
    "browse_abandonment": "browse_recovery",
    "cart_abandonment": "checkout_recovery",
    "contact_created": "welcome",
    "order_placed": "post_purchase",
    "no_purchase_days": "winback",
    "tag_added": "engagement",
}

TRIGGER_DISCOUNT_PURPOSE = {
    "checkout_abandoned": "cart_abandonment",
    "browse_abandonment": "browse_abandonment",
    "cart_abandonment": "cart_abandonment",
    "contact_created": "welcome",
    "order_placed": "post_purchase",
    "no_purchase_days": "winback",
    "tag_added": None,
}

# template_family → (objective, discount_purpose)
_FAMILY_OVERRIDES = {
    "welcome":            ("welcome",            "welcome"),
    "cart_recovery":      ("checkout_recovery",  "cart_abandonment"),
    "checkout_recovery":  ("checkout_recovery",  "cart_abandonment"),
    "browse_recovery":    ("browse_recovery",    "browse_abandonment"),
    "post_purchase":      ("post_purchase",       "post_purchase"),
    "winback":            ("winback",             "winback"),
    "high_intent_browse": ("browse_recovery",    "high_intent"),
    "promo":              ("engagement",          None),
}


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════

def _safe_json(raw, default):
    """Parse JSON safely, return default on any failure."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _make_package(status, reason, **kwargs):
    """Build a non-ready (deferred / suppressed / invalid) package dict."""
    base = {
        "status": status,
        "reason": reason,
        "objective": kwargs.get("objective", ""),
        "urgency": kwargs.get("urgency", ""),
        "candidate_products": kwargs.get("candidate_products", []),
        "offer_context": kwargs.get("offer_context", None),
        "render_result": kwargs.get("render_result", None),
        "subject": kwargs.get("subject", None),
        "html": kwargs.get("html", None),
        "priority": kwargs.get("priority", 50),
        "metadata": kwargs.get("metadata", {}),
    }
    return base


# ═══════════════════════════════════════════════════════════════
# Step 1 — Resolve objective
# ═══════════════════════════════════════════════════════════════

def _resolve_objective(flow, step, template):
    """Determine (objective, urgency, discount_purpose) for this send.

    Urgency always comes from the trigger type.
    Template family overrides objective + discount_purpose when set.
    Falls back to TRIGGER_OBJECTIVE / TRIGGER_DISCOUNT_PURPOSE.

    Returns:
        (objective, urgency, discount_purpose)
    """
    trigger = getattr(flow, "trigger_type", "") or ""
    urgency = TRIGGER_URGENCY.get(trigger, "medium")

    # Template-family override (most specific signal)
    family = getattr(template, "template_family", "") or ""
    if family and family in _FAMILY_OVERRIDES:
        objective, discount_purpose = _FAMILY_OVERRIDES[family]
        return objective, urgency, discount_purpose

    # Trigger-based defaults
    objective = TRIGGER_OBJECTIVE.get(trigger, "engagement")
    discount_purpose = TRIGGER_DISCOUNT_PURPOSE.get(trigger, None)
    return objective, urgency, discount_purpose


# ═══════════════════════════════════════════════════════════════
# Step 2 — Soft timing gate
# ═══════════════════════════════════════════════════════════════

def _check_soft_timing_gate(contact, urgency):
    """Check whether the contact should receive an email right now.

    - urgent:  skip timing gate entirely → always "ok"
    - medium:  only block on weekly_cap reasons; ignore too_soon
    - lower:   fully respect should_contact_now

    Returns:
        ("ok", None) — proceed
        ("deferred", next_available_at) — defer sending
    """
    if urgency == "urgent":
        return "ok", None

    try:
        result = intelligence_layer.should_contact_now(contact.id)
        can_send = result.get("can_send", True)
        reason = result.get("reason", "")
        next_available_at = result.get("next_available_at")

        if can_send:
            return "ok", None

        if urgency == "medium":
            # Respect weekly_cap; ignore too_soon
            if reason.startswith("weekly_cap"):
                return "deferred", next_available_at
            return "ok", None

        # urgency == "lower" — fully respect the gate
        return "deferred", next_available_at

    except Exception as exc:
        logger.warning("[flow_runtime] timing gate error for contact %s: %s", contact.id, exc)
        return "ok", None


# ═══════════════════════════════════════════════════════════════
# Step 3 — Product resolution helpers
# ═══════════════════════════════════════════════════════════════

def _normalize_cart_items(raw_items, contact_email=None):
    """Normalize Shopify cart line items to standard product dict format.

    Shopify format: [{title, quantity, price, image_url, product_url, ...}]
    Standard format: {product_title, product_url, image_url, price}

    Enriches image_url / product_url from ProductImageCache when missing.
    """
    from database import ProductImageCache

    products = []
    for item in raw_items:
        if not item:
            continue
        title = item.get("title") or item.get("product_title") or ""
        if not title:
            continue

        # Try to enrich from cache by title match
        cached = ProductImageCache.get_or_none(ProductImageCache.product_title == title)

        product_url = item.get("product_url") or (cached.product_url if cached else "") or "https://ldas.ca"
        image_url = item.get("image_url") or (cached.image_url if cached else "") or ""
        price = item.get("price") or (cached.price if cached else "0.00") or "0.00"

        products.append({
            "product_title": title,
            "product_url": product_url,
            "image_url": image_url,
            "price": str(price),
        })
    return products


def _resolve_cart_from_db(contact):
    """Resolve cart items from the most recent AbandonedCheckout in the DB."""
    from database import AbandonedCheckout

    checkout = (AbandonedCheckout.select()
                .where(AbandonedCheckout.contact == contact.id,
                       AbandonedCheckout.recovered == False)
                .order_by(AbandonedCheckout.created_at.desc())
                .first())
    if not checkout:
        return [], None

    items = _safe_json(checkout.line_items_json, [])
    normalized = _normalize_cart_items(items)
    checkout_url = checkout.checkout_url or "https://ldas.ca/checkout"
    return normalized, checkout_url


def _resolve_viewed_from_db(contact, limit=5):
    """Resolve recently-viewed products from CustomerActivity."""
    from database import CustomerActivity

    activities = (CustomerActivity.select()
                  .where(CustomerActivity.contact == contact.id,
                         CustomerActivity.event_type == "viewed_product")
                  .order_by(CustomerActivity.occurred_at.desc())
                  .limit(limit))

    products = []
    seen_titles = set()
    for act in activities:
        data = _safe_json(act.event_data, {})
        title = data.get("product_title") or data.get("title") or ""
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        products.append({
            "product_title": title,
            "product_url": data.get("url") or data.get("product_url") or "https://ldas.ca",
            "image_url": data.get("image_url") or "",
            "price": str(data.get("price") or "0.00"),
        })
    return products


def _get_intelligence_products(contact, limit=4):
    """Get recommended products from intelligence_layer.

    Resolves intelligence schema keys (product_key, target_key, to_product)
    into concrete ProductImageCache-backed product dicts, matching the same
    resolution pattern used by template_engine._intelligence_to_products().
    """
    try:
        intel = intelligence_layer.get_next_products(contact.id)
    except Exception as exc:
        logger.warning("[flow_runtime] intelligence products error for contact %s: %s", contact.id, exc)
        return []

    # Extract candidate keys from intelligence output (ordered by priority)
    # Each entry: (key_string, is_product_level)
    candidates = []
    seen = set()

    def _add(key, is_product=False):
        if key and key not in seen:
            seen.add(key)
            candidates.append((key, is_product))

    # top_pick first (highest priority)
    top_pick = intel.get("top_pick") or {}
    if top_pick.get("product_key"):
        _add(top_pick["product_key"], is_product=True)

    # replacements: product_key is a specific product title
    for item in (intel.get("replacements") or []):
        _add(item.get("product_key"), is_product=True)

    # reorders: product_key is a specific product title
    for item in (intel.get("reorders") or []):
        _add(item.get("product_key"), is_product=True)

    # cross_sells / accessories: target_key
    for key in ("cross_sells", "accessories"):
        for item in (intel.get(key) or []):
            is_prod = item.get("is_product", False)
            _add(item.get("target_key"), is_product=is_prod)

    # upgrades: to_product when present, otherwise category-level fallback
    for item in (intel.get("upgrades") or []):
        if item.get("to_product"):
            _add(item["to_product"], is_product=True)
        elif item.get("category"):
            _add(item["category"], is_product=False)

    if not candidates:
        return []

    # Resolve candidate keys into concrete ProductImageCache-backed dicts
    # Filter out-of-stock via ProductCommercial (mirrors template_engine)
    products = []
    try:
        from database import ProductImageCache, ProductCommercial
    except ImportError:
        logger.warning("[flow_runtime] ProductImageCache/ProductCommercial not available")
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

    for key, is_product in candidates[:limit * 2]:  # check extra candidates
        if len(products) >= limit:
            break

        matches = []
        if is_product:
            try:
                m = ProductImageCache.get_or_none(
                    ProductImageCache.product_title == key
                )
                if m:
                    matches = [m]
            except Exception:
                pass

        if not matches:
            # Category-level or product not found: find all by product_type
            try:
                matches = list(ProductImageCache.select()
                               .where(ProductImageCache.product_type == key)
                               .limit(5))
            except Exception:
                pass

        if not matches:
            logger.debug("[flow_runtime] No ProductImageCache match for key=%s is_product=%s", key, is_product)
            continue

        # Pick the first in-stock match
        picked = None
        for m in matches:
            if m.product_title in out_of_stock:
                logger.debug("[flow_runtime] Skipping out-of-stock product: %s", m.product_title)
                continue
            picked = m
            break

        if not picked:
            continue

        products.append({
            "product_title": picked.product_title,
            "product_url": picked.product_url or "",
            "image_url": picked.image_url or "",
            "price": str(picked.price or "0.00"),
        })

    if not products and candidates:
        logger.info("[flow_runtime] Intelligence recommended %d candidates but no concrete products resolved",
                    len(candidates))

    return products


def _legacy_product_fallback(contact):
    """Legacy fallback: CustomerProfile.top_products + ProductImageCache enrichment."""
    from database import CustomerProfile, ProductImageCache

    products = []
    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact.id)
    if not profile:
        return products

    top = _safe_json(profile.top_products, [])
    for title in top[:4]:
        if not title:
            continue
        cached = ProductImageCache.get_or_none(ProductImageCache.product_title == title)
        products.append({
            "product_title": title,
            "product_url": (cached.product_url if cached else "") or "https://ldas.ca",
            "image_url": (cached.image_url if cached else "") or "",
            "price": str((cached.price if cached else "") or "0.00"),
        })
    return products


def _resolve_products(contact, flow, enrollment, trigger_context):
    """Resolve the candidate product list using the priority chain.

    Priority:
    1. trigger_context cart_items / viewed_products (if provided)
    2. DB resolution (AbandonedCheckout / CustomerActivity viewed_product)
    3. intelligence_layer.get_next_products()
    4. Legacy fallback (CustomerProfile.top_products + ProductImageCache)

    Returns list of standard product dicts: {product_title, product_url, image_url, price}
    """
    trigger = getattr(flow, "trigger_type", "") or ""
    ctx = trigger_context or {}

    # ── 1. Trigger context ──
    if trigger in ("checkout_abandoned", "cart_abandonment"):
        cart_items = ctx.get("cart_items") or ctx.get("line_items") or []
        if cart_items:
            return _normalize_cart_items(cart_items)

    if trigger == "browse_abandonment":
        viewed = ctx.get("viewed_products") or []
        if viewed:
            return _normalize_cart_items(viewed)  # same normalization

    # ── 2. DB resolution ──
    if trigger in ("checkout_abandoned", "cart_abandonment"):
        products, _ = _resolve_cart_from_db(contact)
        if products:
            return products

    if trigger == "browse_abandonment":
        products = _resolve_viewed_from_db(contact)
        if len(products) >= 1:
            # Supplement with intelligence if < 2
            if len(products) < 2:
                extra = _get_intelligence_products(contact, limit=2)
                seen = {p["product_title"] for p in products}
                for p in extra:
                    if p["product_title"] not in seen:
                        products.append(p)
            return products

    # ── 3. Intelligence layer ──
    intel_products = _get_intelligence_products(contact)
    if intel_products:
        return intel_products

    # ── 4. Legacy fallback ──
    return _legacy_product_fallback(contact)


# ═══════════════════════════════════════════════════════════════
# Step 4 — Offer resolution
# ═══════════════════════════════════════════════════════════════

def _resolve_offer(contact, discount_purpose, candidate_products):
    """Resolve discount offer for this send.

    Returns offer_context dict (from get_discount_display) or None.
    """
    if discount_purpose is None:
        return None

    try:
        product_ids = [p.get("product_title") for p in (candidate_products or []) if p.get("product_title")]
        policy = intelligence_layer.get_discount_policy(contact.id, discount_purpose, product_ids)

        if not policy.get("offer_discount", False):
            return None

        discount_info = discount_engine.get_or_create_discount(contact.email, discount_purpose)
        if not discount_info:
            return None

        display = discount_engine.get_discount_display(discount_info)
        return display or discount_info

    except Exception as exc:
        logger.warning("[flow_runtime] offer resolution error for contact %s: %s", contact.id, exc)
        return None


# ═══════════════════════════════════════════════════════════════
# Step 5 — Legacy token context
# ═══════════════════════════════════════════════════════════════

def _build_legacy_token_context(contact, flow, trigger_context, candidate_products, offer_context):
    """Build a {{token}}: value dict for all 17 legacy tokens.

    Tokens resolved:
        {{first_name}}, {{last_name}}, {{email}}
        {{discount_code}}, {{checkout_url}}
        {{cart_items}}, {{recently_browsed_html}}, {{top_products_html}}
        {{last_viewed_product}}
        {{total_orders}}, {{total_spent}}
        {{rfm_segment}}, {{lifecycle_stage}}, {{customer_type}}
        {{top_category}}, {{days_since_purchase}}, {{intent_level}}
        {{unsubscribe_url}}
    """
    from database import CustomerProfile, ContactScore, AbandonedCheckout, CustomerActivity

    ctx = trigger_context or {}

    # ── Contact basics ──
    first_name = getattr(contact, "first_name", "") or ""
    last_name = getattr(contact, "last_name", "") or ""
    email = getattr(contact, "email", "") or ""

    # ── Offer ──
    discount_code = ""
    if offer_context:
        discount_code = offer_context.get("code") or ""

    # ── Checkout / cart ──
    checkout_url = ctx.get("checkout_url") or ctx.get("recover_url") or ""
    cart_html = ""

    # Build cart items HTML from trigger_context
    raw_items = ctx.get("cart_items") or ctx.get("line_items") or []
    if raw_items:
        for item in raw_items:
            title = item.get("title") or item.get("product_title") or ""
            qty = item.get("quantity") or 1
            price = item.get("price") or "0.00"
            if title:
                cart_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; %s x%s &mdash; $%s</p>' % (
                    title, qty, price
                )

    # Fall back to DB if no trigger_context cart / checkout_url
    if not checkout_url or not cart_html:
        checkout = (AbandonedCheckout.select()
                    .where(AbandonedCheckout.contact == contact.id,
                           AbandonedCheckout.recovered == False)
                    .order_by(AbandonedCheckout.created_at.desc())
                    .first())
        if checkout:
            if not checkout_url:
                checkout_url = checkout.checkout_url or "https://ldas.ca/checkout"
            if not cart_html:
                db_items = _safe_json(checkout.line_items_json, [])
                for item in db_items:
                    title = item.get("title") or item.get("product_title") or ""
                    qty = item.get("quantity") or 1
                    price = item.get("price") or "0.00"
                    if title:
                        cart_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; %s x%s &mdash; $%s</p>' % (
                            title, qty, price
                        )

    if not checkout_url:
        checkout_url = "https://ldas.ca/checkout"

    # ── Profile data ──
    profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact.id)
    score = ContactScore.get_or_none(ContactScore.contact == contact.id)

    total_orders = 0
    total_spent = "0.00"
    lifecycle_stage = "unknown"
    customer_type = "unknown"
    last_viewed_product = "one of our popular items"
    top_category = ""
    days_since_purchase = "a while"
    intent_level = "low"
    rfm_segment = "new"

    if profile:
        total_orders = profile.total_orders or 0
        total_spent = "%.2f" % (profile.total_spent or 0.0)
        lifecycle_stage = profile.lifecycle_stage or "unknown"
        customer_type = profile.customer_type or "unknown"
        last_viewed_product = profile.last_viewed_product or "one of our popular items"

        # Top category from category_affinity_json
        affinities = _safe_json(profile.category_affinity_json, {})
        if affinities:
            top_category = max(affinities, key=lambda k: affinities[k])

        # Days since purchase
        if profile.last_order_at:
            days = (datetime.now() - profile.last_order_at).days if not hasattr(profile.last_order_at, 'tzinfo') or not profile.last_order_at.tzinfo else (datetime.now() - profile.last_order_at.replace(tzinfo=None)).days
            days_since_purchase = str(days)

        # Intent level
        intent = profile.intent_score or 0
        if intent >= 70:
            intent_level = "high"
        elif intent >= 35:
            intent_level = "medium"
        else:
            intent_level = "low"

    if score:
        rfm_segment = score.rfm_segment or "new"

    # ── Recently browsed HTML ──
    recently_browsed_html = ""
    browsed_activities = (CustomerActivity.select()
                          .where(CustomerActivity.contact == contact.id,
                                 CustomerActivity.event_type == "viewed_product")
                          .order_by(CustomerActivity.occurred_at.desc())
                          .limit(5))
    for act in browsed_activities:
        data = _safe_json(act.event_data, {})
        title = data.get("product_title") or data.get("title") or ""
        price = data.get("price") or "0.00"
        if title:
            recently_browsed_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; <strong>%s</strong> &mdash; $%s</p>' % (
                title, price
            )

    # ── Top products HTML ──
    top_products_html = ""
    if profile:
        top_prods = _safe_json(profile.top_products, [])
        for title in top_prods[:4]:
            if title:
                top_products_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; <strong>%s</strong></p>' % title

    return {
        "{{first_name}}": first_name,
        "{{last_name}}": last_name,
        "{{email}}": email,
        "{{discount_code}}": discount_code,
        "{{cart_items}}": cart_html,
        "{{checkout_url}}": checkout_url,
        "{{last_viewed_product}}": last_viewed_product,
        "{{recently_browsed_html}}": recently_browsed_html,
        "{{top_products_html}}": top_products_html,
        "{{total_orders}}": str(total_orders),
        "{{total_spent}}": total_spent,
        "{{rfm_segment}}": rfm_segment,
        "{{lifecycle_stage}}": lifecycle_stage,
        "{{customer_type}}": customer_type,
        "{{top_category}}": top_category,
        "{{days_since_purchase}}": days_since_purchase,
        "{{intent_level}}": intent_level,
        "{{unsubscribe_url}}": "",
    }


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════

def build_flow_send_package(enrollment, step, contact, flow,
                            template=None, trigger_context=None):
    """Build a fully-resolved email send package for a flow enrollment step.

    Pipeline:
        1. Resolve template
        2. Resolve objective / urgency / discount_purpose
        3. Soft timing gate — return deferred if blocked
        4. Resolve candidate products
        5. Resolve offer / discount
        6. Render (blocks or legacy HTML)
        7. Personalize subject
        8. Return package dict

    Args:
        enrollment: FlowEnrollment instance
        step: FlowStep instance
        contact: Contact instance
        flow: Flow instance
        template: EmailTemplate (optional — defaults to step.template)
        trigger_context: dict with {cart_items, viewed_products, checkout_url, ...}

    Returns:
        dict with keys: status, reason, objective, urgency, candidate_products,
                        offer_context, render_result, subject, html, priority, metadata
    """
    from delivery_engine import get_priority_for_trigger
    import email_shell

    trigger = getattr(flow, "trigger_type", "") or ""
    priority = get_priority_for_trigger(trigger)

    # ── 1. Resolve template ──
    if template is None:
        template = step.template

    if template is None:
        return _make_package(
            "invalid", "no_template",
            priority=priority,
        )

    # ── 2. Resolve objective ──
    objective, urgency, discount_purpose = _resolve_objective(flow, step, template)

    # ── 3. Soft timing gate ──
    gate_status, next_available_at = _check_soft_timing_gate(contact, urgency)
    if gate_status == "deferred":
        return _make_package(
            "deferred", "timing_gate",
            objective=objective,
            urgency=urgency,
            priority=priority,
            metadata={"next_available_at": next_available_at},
        )

    # ── 4. Resolve products ──
    candidate_products = _resolve_products(contact, flow, enrollment, trigger_context)

    # ── 5. Resolve offer ──
    offer_context = _resolve_offer(contact, discount_purpose, candidate_products)

    # ── 6. Render ──
    template_format = getattr(template, "template_format", "html") or "html"
    shell_version = getattr(template, "shell_version", 1) or 1
    render_result = None
    html = None
    subject = None
    issues = []

    if template_format == "blocks":
        contract = te.make_render_contract(
            template=template,
            source_system="flow",
            objective=objective,
            contact_id=contact.id,
            product_context=candidate_products if candidate_products else None,
            offer_context=offer_context,
            mode="send",
        )
        render_result = te.render_email(contract)
        if not render_result.get("is_valid", False):
            issues = render_result.get("errors", [])
            return _make_package(
                "invalid", "render_failed",
                objective=objective,
                urgency=urgency,
                candidate_products=candidate_products,
                offer_context=offer_context,
                render_result=render_result,
                priority=priority,
                metadata={"errors": issues},
            )
        html = render_result.get("html", "")
        subject = render_result.get("subject", "")

    else:
        # Legacy HTML path
        html_body = getattr(template, "html_body", "") or ""

        # Build token context
        token_ctx = _build_legacy_token_context(
            contact, flow, trigger_context, candidate_products, offer_context
        )

        # Apply tokens
        rendered_html = html_body
        for token, value in token_ctx.items():
            rendered_html = rendered_html.replace(token, value or "")

        # Wrap in email shell if shell_version >= 1
        if shell_version >= 1:
            preview_text = getattr(template, "preview_text", "") or ""
            unsubscribe_url = ""  # caller resolves with _make_unsubscribe_url
            try:
                rendered_html = email_shell.wrap_email(
                    rendered_html,
                    preview_text=preview_text,
                    unsubscribe_url=unsubscribe_url,
                )
            except Exception as exc:
                logger.warning("[flow_runtime] email_shell.wrap_email failed: %s", exc)

        # Validate
        family = getattr(template, "template_family", None) or objective
        validation = te.validate_rendered_email(
            html=rendered_html,
            subject=getattr(template, "subject", "") or "",
            preview_text=getattr(template, "preview_text", "") or "",
            offer_context=offer_context,
            products=candidate_products,
            mode="send",
            family=family,
        )
        errors = [v for v in validation if v["level"] == "error"]

        render_result = {
            "is_valid": len(errors) == 0,
            "html": rendered_html,
            "subject": getattr(template, "subject", "") or "",
            "errors": errors,
            "warnings": [v for v in validation if v["level"] == "warning"],
            "validation_report": validation,
        }

        if not render_result["is_valid"]:
            return _make_package(
                "invalid", "render_failed",
                objective=objective,
                urgency=urgency,
                candidate_products=candidate_products,
                offer_context=offer_context,
                render_result=render_result,
                priority=priority,
                metadata={"errors": errors},
            )

        html = rendered_html
        subject = getattr(template, "subject", "") or ""

    # ── 7. Subject personalization ──
    subject_line = getattr(step, "subject_override", "") or ""
    if not subject_line:
        subject_line = subject or getattr(template, "subject", "") or ""

    # Token substitutions in subject
    first_name = getattr(contact, "first_name", "") or ""
    last_viewed = ""
    total_orders_str = ""

    # Pull from token_ctx if already built (legacy path); else query
    if template_format != "blocks":
        # token_ctx is already computed above
        last_viewed = token_ctx.get("{{last_viewed_product}}", "")
        total_orders_str = token_ctx.get("{{total_orders}}", "")
    else:
        try:
            from database import CustomerProfile
            profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact.id)
            last_viewed = (profile.last_viewed_product if profile else "") or "one of our popular items"
            total_orders_str = str(profile.total_orders if profile else 0)
        except Exception:
            last_viewed = "one of our popular items"
            total_orders_str = "0"

    subject_line = subject_line.replace("{{first_name}}", first_name)
    subject_line = subject_line.replace("{{last_viewed_product}}", last_viewed)
    subject_line = subject_line.replace("{{total_orders}}", total_orders_str)

    # ── 8. Return ready package ──
    return {
        "status": "ready",
        "reason": "ok",
        "objective": objective,
        "urgency": urgency,
        "candidate_products": candidate_products,
        "offer_context": offer_context,
        "render_result": render_result,
        "subject": subject_line,
        "html": html,
        "priority": priority,
        "metadata": {},
    }
