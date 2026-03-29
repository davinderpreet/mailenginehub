"""
product_intelligence.py — Product relationship computation & seeding.

Answers questions like:
  - Customer bought G7, what should we recommend next?
  - What accessories fit this product?
  - What's the upgrade path?
  - When should we pitch a replacement?

Two data sources:
  1. Seeded knowledge (shared_constants.py) — human-curated, authoritative
  2. Learned knowledge (nightly co-purchase analysis) — additive, never overwrites seeded

Primary truth source for purchase history: ShopifyOrder + ShopifyOrderItem
(not CustomerProfile.all_products_bought, which is a cached summary).

Run nightly at 4:50 AM after profit_engine, or on-demand.
"""

import json
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Seeding — populate tables from shared_constants
# ═══════════════════════════════════════════════════════════════

def seed_product_intelligence():
    """Populate ProductRelationship, ProductUpgradePath, ReorderCycle, ProductAlias
    from shared_constants.py seeded data. Idempotent — skips existing seeded rows."""
    from database import (ProductRelationship, ProductUpgradePath, ReorderCycle,
                          ProductAlias, init_db)
    from shared_constants import (SEEDED_RELATIONSHIPS, UPGRADE_PATHS,
                                  REORDER_CYCLES, PRODUCT_ALIASES,
                                  CATEGORY_KEYWORDS, infer_category)
    init_db()

    created = {"relationships": 0, "upgrades": 0, "cycles": 0, "aliases": 0}

    # ── Product Relationships ──
    for row in SEEDED_RELATIONSHIPS:
        source, target, rel_type, strength, delay, reason = row
        # Determine if source/target are exact products or categories
        source_is_product = source not in CATEGORY_KEYWORDS
        target_is_product = target not in CATEGORY_KEYWORDS
        try:
            ProductRelationship.get_or_create(
                source_key=source,
                target_key=target,
                relationship_type=rel_type,
                defaults={
                    "source_is_product": source_is_product,
                    "target_is_product": target_is_product,
                    "strength": strength,
                    "typical_delay_days": delay,
                    "reason": reason,
                    "source_origin": "seeded",
                    "is_active": True,
                }
            )
            created["relationships"] += 1
        except Exception as e:
            logger.debug("Seed relationship skip: %s", e)

    # ── Upgrade Paths ──
    for category, tiers in UPGRADE_PATHS.items():
        for tier_order_idx, (tier_name, low, high, trigger_days) in enumerate(tiers, 1):
            try:
                ProductUpgradePath.get_or_create(
                    category=category,
                    tier_order=tier_order_idx,
                    defaults={
                        "tier_name": tier_name,
                        "price_range_low": low,
                        "price_range_high": high,
                        "upgrade_trigger_days": trigger_days,
                        "source_origin": "seeded",
                    }
                )
                created["upgrades"] += 1
            except Exception as e:
                logger.debug("Seed upgrade skip: %s", e)

    # ── Reorder Cycles ──
    for product_type, (cycle_days, variance, offset, consumable) in REORDER_CYCLES.items():
        is_product = product_type not in CATEGORY_KEYWORDS
        try:
            ReorderCycle.get_or_create(
                product_key=product_type,
                defaults={
                    "is_product": is_product,
                    "typical_cycle_days": cycle_days,
                    "cycle_variance_days": variance,
                    "reminder_offset_days": offset,
                    "consumable": consumable,
                    "source_origin": "seeded",
                }
            )
            created["cycles"] += 1
        except Exception as e:
            logger.debug("Seed cycle skip: %s", e)

    # ── Product Aliases ──
    for alias_lower, canonical in PRODUCT_ALIASES.items():
        try:
            ProductAlias.get_or_create(
                alias=alias_lower,
                defaults={
                    "canonical_title": canonical,
                    "category": infer_category(canonical),
                    "source": "seeded",
                }
            )
            created["aliases"] += 1
        except Exception as e:
            logger.debug("Seed alias skip: %s", e)

    logger.info("[ProductIntelligence] Seeded: %s", created)
    return created


# ═══════════════════════════════════════════════════════════════
# Alias Resolution
# ═══════════════════════════════════════════════════════════════

def resolve_product(title):
    """Resolve a product title to its canonical form.
    Checks ProductAlias table first, then shared_constants.PRODUCT_ALIASES,
    then returns the original title stripped.
    """
    if not title:
        return title or ""
    from database import ProductAlias
    stripped = title.strip()
    lower = stripped.lower()

    # DB lookup (covers seeded + learned aliases)
    try:
        alias_row = ProductAlias.get_or_none(ProductAlias.alias == lower)
        if alias_row:
            return alias_row.canonical_title
    except Exception:
        pass

    # Fallback to in-memory constants
    from shared_constants import resolve_product_alias
    return resolve_product_alias(stripped)


# ═══════════════════════════════════════════════════════════════
# Purchase History (source of truth: ShopifyOrder + ShopifyOrderItem)
# ═══════════════════════════════════════════════════════════════

def get_contact_purchase_history(contact_id):
    """Return structured purchase history from ShopifyOrder + ShopifyOrderItem.
    This is the primary truth source — NOT CustomerProfile.all_products_bought.

    Returns: {
        "orders": [{"order_id", "ordered_at", "total", "items": [{"title", "canonical", "category", "qty", "price"}]}],
        "products_bought": {"canonical_title": {"category", "total_qty", "first_bought", "last_bought", "total_spent"}},
        "categories_bought": {"category": {"total_qty", "total_spent"}},
        "total_orders": int,
        "first_order_at": datetime|None,
        "last_order_at": datetime|None,
    }
    """
    from database import Contact, ShopifyOrder, ShopifyOrderItem
    from shared_constants import infer_category

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return _empty_purchase_history()

    orders = list(
        ShopifyOrder.select()
        .where(ShopifyOrder.email == contact.email.lower())
        .order_by(ShopifyOrder.ordered_at.desc())
    )

    if not orders:
        return _empty_purchase_history()

    result_orders = []
    products_bought = {}
    categories_bought = defaultdict(lambda: {"total_qty": 0, "total_spent": 0.0})

    for order in orders:
        items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order == order.id))
        order_items = []

        for item in items:
            canonical = resolve_product(item.product_title)
            # Use item's product_type if available, otherwise infer
            category = item.product_type if item.product_type and item.product_type not in ("", "Unknown") else infer_category(canonical)

            order_items.append({
                "title": item.product_title,
                "canonical": canonical,
                "category": category,
                "product_id": item.product_id,
                "qty": item.quantity,
                "price": item.unit_price,
            })

            # Aggregate product history
            if canonical not in products_bought:
                products_bought[canonical] = {
                    "category": category,
                    "product_id": item.product_id,
                    "total_qty": 0,
                    "first_bought": order.ordered_at,
                    "last_bought": order.ordered_at,
                    "total_spent": 0.0,
                }
            pb = products_bought[canonical]
            pb["total_qty"] += item.quantity
            pb["total_spent"] += item.unit_price * item.quantity
            if order.ordered_at and (pb["first_bought"] is None or order.ordered_at < pb["first_bought"]):
                pb["first_bought"] = order.ordered_at
            if order.ordered_at and (pb["last_bought"] is None or order.ordered_at > pb["last_bought"]):
                pb["last_bought"] = order.ordered_at

            # Aggregate category history
            categories_bought[category]["total_qty"] += item.quantity
            categories_bought[category]["total_spent"] += item.unit_price * item.quantity

        result_orders.append({
            "order_id": order.shopify_order_id,
            "ordered_at": order.ordered_at,
            "total": order.order_total,
            "items": order_items,
        })

    return {
        "orders": result_orders,
        "products_bought": products_bought,
        "categories_bought": dict(categories_bought),
        "total_orders": len(orders),
        "first_order_at": orders[-1].ordered_at if orders else None,
        "last_order_at": orders[0].ordered_at if orders else None,
    }


def _empty_purchase_history():
    return {
        "orders": [],
        "products_bought": {},
        "categories_bought": {},
        "total_orders": 0,
        "first_order_at": None,
        "last_order_at": None,
    }


# ═══════════════════════════════════════════════════════════════
# Core Query: What should we recommend for this contact?
# ═══════════════════════════════════════════════════════════════

def get_customer_product_context(contact_id):
    """Given a contact's purchase history, return actionable product recommendations.

    Returns: {
        "replacements": [{"product_key", "category", "due_in_days", "cycle_days", "reason"}],
        "upgrades": [{"from_product", "from_tier", "to_tier", "category", "ready": bool, "days_owned"}],
        "cross_sells": [{"target_key", "strength", "reason", "is_product": bool}],
        "accessories": [{"target_key", "strength", "reason", "is_product": bool}],
        "reorders": [{"product_key", "category", "days_since", "cycle_days", "due": bool}],
        "top_pick": {"action", "product_key", "reason"} or None,
    }
    """
    from database import ProductRelationship, ProductUpgradePath, ReorderCycle, ProductCommercial
    from shared_constants import infer_category

    history = get_contact_purchase_history(contact_id)
    now = datetime.now()

    replacements = []
    upgrades = []
    cross_sells = []
    accessories = []
    reorders = []

    if not history["products_bought"]:
        return {
            "replacements": [], "upgrades": [], "cross_sells": [],
            "accessories": [], "reorders": [], "top_pick": None,
        }

    # Categories the customer has bought from
    bought_categories = set(history["categories_bought"].keys())

    # ── Check relationships for each product and category bought ──
    bought_keys = set()
    for prod_title, info in history["products_bought"].items():
        bought_keys.add(prod_title)
        bought_keys.add(info["category"])

    # Query all relevant relationships (both product-level and category-level)
    try:
        rels = list(ProductRelationship.select().where(
            ProductRelationship.source_key.in_(list(bought_keys)),
            ProductRelationship.is_active == True,
        ).order_by(ProductRelationship.strength.desc()))
    except Exception:
        rels = []

    # Filter out-of-stock targets using ProductCommercial
    in_stock_cache = {}

    def _is_available(product_key):
        if product_key in in_stock_cache:
            return in_stock_cache[product_key]
        try:
            # Check if it's a category (always "available")
            from shared_constants import CATEGORY_KEYWORDS
            if product_key in CATEGORY_KEYWORDS:
                in_stock_cache[product_key] = True
                return True
            # Check ProductCommercial for exact products
            pc = ProductCommercial.get_or_none(
                ProductCommercial.product_title == product_key
            )
            available = pc is None or pc.stock_pressure != "out_of_stock"
            in_stock_cache[product_key] = available
            return available
        except Exception:
            return True

    for rel in rels:
        if not _is_available(rel.target_key):
            continue

        entry = {
            "target_key": rel.target_key,
            "strength": rel.strength,
            "reason": rel.reason,
            "is_product": rel.target_is_product,
        }

        if rel.relationship_type == "replacement":
            # Calculate when replacement is due
            source_info = history["products_bought"].get(rel.source_key)
            if not source_info:
                # Category-level: use most recent purchase in that category
                for pt, pi in history["products_bought"].items():
                    if pi["category"] == rel.source_key:
                        if source_info is None or (pi["last_bought"] and (source_info is None or pi["last_bought"] > source_info.get("last_bought"))):
                            source_info = pi
            if source_info and source_info.get("last_bought"):
                days_since = (now - source_info["last_bought"]).days
                cycle = rel.typical_delay_days or 180
                due_in = cycle - days_since
                replacements.append({
                    "product_key": rel.target_key,
                    "category": rel.target_key if not rel.target_is_product else infer_category(rel.target_key),
                    "due_in_days": due_in,
                    "cycle_days": cycle,
                    "reason": rel.reason,
                })

        elif rel.relationship_type == "upgrade":
            source_info = history["products_bought"].get(rel.source_key)
            if source_info and source_info.get("last_bought"):
                days_owned = (now - source_info["last_bought"]).days
                # Check if upgrade trigger met
                trigger = rel.typical_delay_days or 180
                upgrades.append({
                    "from_product": rel.source_key,
                    "to_product": rel.target_key,
                    "from_tier": "",
                    "to_tier": "",
                    "category": infer_category(rel.target_key),
                    "ready": days_owned >= trigger,
                    "days_owned": days_owned,
                })

        elif rel.relationship_type == "cross_sell":
            # Don't cross-sell categories they already buy from
            target_cat = rel.target_key if not rel.target_is_product else infer_category(rel.target_key)
            if target_cat not in bought_categories or rel.target_is_product:
                cross_sells.append(entry)

        elif rel.relationship_type == "accessory":
            accessories.append(entry)

    # ── Category-level upgrade paths ──
    for prod_title, info in history["products_bought"].items():
        cat = info["category"]
        price = info["total_spent"] / max(1, info["total_qty"])  # avg price paid

        try:
            tiers = list(ProductUpgradePath.select()
                         .where(ProductUpgradePath.category == cat)
                         .order_by(ProductUpgradePath.tier_order))
        except Exception:
            tiers = []

        if not tiers:
            continue

        # Find current tier
        current_tier = None
        for t in tiers:
            if t.price_range_low <= price <= t.price_range_high:
                current_tier = t
                break

        if current_tier is None:
            # Below lowest tier or above highest
            continue

        # Find next tier
        next_tier = None
        for t in tiers:
            if t.tier_order == current_tier.tier_order + 1:
                next_tier = t
                break

        if next_tier and info.get("last_bought"):
            days_owned = (now - info["last_bought"]).days
            # Only add if not already captured by product-level upgrade
            already_has = any(u["category"] == cat for u in upgrades)
            if not already_has:
                upgrades.append({
                    "from_product": prod_title,
                    "to_product": "",  # category-level, no specific product
                    "from_tier": current_tier.tier_name,
                    "to_tier": next_tier.tier_name,
                    "category": cat,
                    "ready": days_owned >= current_tier.upgrade_trigger_days,
                    "days_owned": days_owned,
                })

    # ── Reorder cycles ──
    for prod_title, info in history["products_bought"].items():
        cat = info["category"]

        # Check product-level cycle first, then category-level
        try:
            cycle_row = (ReorderCycle.get_or_none(ReorderCycle.product_key == prod_title) or
                         ReorderCycle.get_or_none(ReorderCycle.product_key == cat))
        except Exception:
            cycle_row = None

        if cycle_row and info.get("last_bought"):
            days_since = (now - info["last_bought"]).days
            # Use learned cycle if available and has sufficient data, else seeded
            effective_cycle = cycle_row.typical_cycle_days
            if cycle_row.learned_cycle_days and cycle_row.learned_sample_size >= 10:
                effective_cycle = cycle_row.learned_cycle_days

            due = days_since >= (effective_cycle - cycle_row.reminder_offset_days)
            reorders.append({
                "product_key": prod_title,
                "category": cat,
                "days_since": days_since,
                "cycle_days": effective_cycle,
                "due": due,
                "consumable": cycle_row.consumable,
            })

    # ── Sort and pick top recommendation ──
    replacements.sort(key=lambda x: x["due_in_days"])
    upgrades.sort(key=lambda x: (not x["ready"], -x["days_owned"]))
    cross_sells.sort(key=lambda x: -x["strength"])
    accessories.sort(key=lambda x: -x["strength"])
    reorders.sort(key=lambda x: (-int(x["due"]), -x["days_since"]))

    top_pick = _pick_top_recommendation(replacements, upgrades, cross_sells, reorders)

    return {
        "replacements": replacements[:5],
        "upgrades": upgrades[:3],
        "cross_sells": cross_sells[:5],
        "accessories": accessories[:5],
        "reorders": reorders[:5],
        "top_pick": top_pick,
    }


def _pick_top_recommendation(replacements, upgrades, cross_sells, reorders):
    """Select the single best recommendation across all types."""
    # Priority: replacement due soon > reorder due > upgrade ready > cross-sell
    for r in replacements:
        if r["due_in_days"] <= 14:
            return {"action": "replacement", "product_key": r["product_key"], "reason": r["reason"]}

    for r in reorders:
        if r["due"]:
            return {"action": "reorder", "product_key": r["product_key"],
                    "reason": f"Last purchased {r['days_since']} days ago (cycle: {r['cycle_days']} days)"}

    for u in upgrades:
        if u["ready"]:
            reason = f"Upgrade from {u['from_tier']} to {u['to_tier']}" if u["from_tier"] else f"Upgrade from {u['from_product']}"
            return {"action": "upgrade", "product_key": u.get("to_product") or u["category"],
                    "reason": reason}

    if cross_sells:
        return {"action": "cross_sell", "product_key": cross_sells[0]["target_key"],
                "reason": cross_sells[0]["reason"]}

    return None


# ═══════════════════════════════════════════════════════════════
# Learned Intelligence — nightly co-purchase analysis
# ═══════════════════════════════════════════════════════════════

def compute_learned_relationships():
    """Analyze ShopifyOrderItem co-purchase patterns to discover new relationships
    and strengthen/weaken existing learned rows.

    RULE: Never overwrites source_origin='seeded' rows. Only creates/updates 'learned' rows.
    """
    from database import (ShopifyOrder, ShopifyOrderItem, ProductRelationship, init_db)
    from shared_constants import infer_category

    init_db()
    logger.info("[ProductIntelligence] Computing learned relationships...")

    # Get all orders from last 180 days for co-purchase analysis
    cutoff = datetime.now() - timedelta(days=180)
    orders = list(ShopifyOrder.select().where(ShopifyOrder.ordered_at >= cutoff))

    # Count how often product categories (and exact products) are bought together
    co_purchases = Counter()  # (source_key, target_key) -> count
    customer_orders = defaultdict(list)  # email -> list of (canonical_title, category)

    for order in orders:
        items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order == order.id))
        products_in_order = []
        for item in items:
            canonical = resolve_product(item.product_title)
            category = item.product_type if item.product_type and item.product_type not in ("", "Unknown") else infer_category(canonical)
            products_in_order.append((canonical, category))

        # Cross-product within same order (co-purchase signal)
        for i, (prod_a, cat_a) in enumerate(products_in_order):
            for j, (prod_b, cat_b) in enumerate(products_in_order):
                if i >= j:
                    continue
                if cat_a != cat_b:
                    # Category-level cross-sell signal
                    co_purchases[(cat_a, cat_b)] += 1
                    co_purchases[(cat_b, cat_a)] += 1
                # Product-level signal (only if different products)
                if prod_a != prod_b:
                    co_purchases[(prod_a, prod_b)] += 1
                    co_purchases[(prod_b, prod_a)] += 1

        # Track per-customer for sequential analysis
        if order.email:
            customer_orders[order.email.lower()].extend(products_in_order)

    # Create/update learned relationships for pairs with 3+ co-purchases
    created = 0
    updated = 0
    from shared_constants import CATEGORY_KEYWORDS

    for (source, target), count in co_purchases.items():
        if count < 3:
            continue

        source_is_product = source not in CATEGORY_KEYWORDS
        target_is_product = target not in CATEGORY_KEYWORDS

        try:
            existing = ProductRelationship.get_or_none(
                ProductRelationship.source_key == source,
                ProductRelationship.target_key == target,
                ProductRelationship.relationship_type == "cross_sell",
            )

            if existing:
                if existing.source_origin == "seeded":
                    # Never overwrite seeded — but update evidence count
                    existing.evidence_count = count
                    existing.save()
                else:
                    # Update learned row
                    existing.strength = min(90, 30 + count * 5)
                    existing.evidence_count = count
                    existing.save()
                    updated += 1
            else:
                # Create new learned relationship
                ProductRelationship.create(
                    source_key=source,
                    target_key=target,
                    source_is_product=source_is_product,
                    target_is_product=target_is_product,
                    relationship_type="cross_sell",
                    strength=min(90, 30 + count * 5),
                    reason=f"Co-purchased {count} times in last 180 days",
                    source_origin="learned",
                    evidence_count=count,
                    is_active=True,
                )
                created += 1
        except Exception as e:
            logger.debug("Learned relationship error: %s", e)

    logger.info("[ProductIntelligence] Learned relationships: %d created, %d updated", created, updated)
    return {"created": created, "updated": updated}


def compute_learned_reorder_cycles():
    """Analyze repeat purchases to learn actual reorder timing.
    Updates ReorderCycle.learned_cycle_days without touching seeded values."""
    from database import ShopifyOrder, ShopifyOrderItem, ReorderCycle, init_db
    from shared_constants import infer_category

    init_db()
    logger.info("[ProductIntelligence] Computing learned reorder cycles...")

    # Group purchases by (email, canonical_product) with timestamps
    purchase_times = defaultdict(list)  # (email, canonical) -> [ordered_at, ...]

    orders = list(ShopifyOrder.select().order_by(ShopifyOrder.ordered_at))
    for order in orders:
        if not order.email or not order.ordered_at:
            continue
        items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order == order.id))
        for item in items:
            canonical = resolve_product(item.product_title)
            purchase_times[(order.email.lower(), canonical)].append(order.ordered_at)

    # Compute average gap for products with 2+ purchases by same customer
    product_gaps = defaultdict(list)  # canonical -> [gap_days, ...]

    for (email, canonical), timestamps in purchase_times.items():
        if len(timestamps) < 2:
            continue
        timestamps.sort()
        for i in range(1, len(timestamps)):
            gap = (timestamps[i] - timestamps[i - 1]).days
            if 7 <= gap <= 1095:  # Ignore same-week and 3yr+ gaps
                product_gaps[canonical].append(gap)

    # Also aggregate by category
    category_gaps = defaultdict(list)
    for canonical, gaps in product_gaps.items():
        cat = infer_category(canonical)
        category_gaps[cat].extend(gaps)

    # Update ReorderCycle with learned data
    updated = 0
    for product_key, gaps in {**product_gaps, **category_gaps}.items():
        if len(gaps) < 3:
            continue
        avg_gap = int(sum(gaps) / len(gaps))

        try:
            cycle = ReorderCycle.get_or_none(ReorderCycle.product_key == product_key)
            if cycle:
                cycle.learned_cycle_days = avg_gap
                cycle.learned_sample_size = len(gaps)
                cycle.save()
                updated += 1
            # Don't create new ReorderCycle rows from learned data alone
            # (requires human review to add new product types)
        except Exception as e:
            logger.debug("Learned cycle error: %s", e)

    logger.info("[ProductIntelligence] Updated %d reorder cycles with learned data", updated)
    return {"updated": updated}


# ═══════════════════════════════════════════════════════════════
# Nightly Entry Point
# ═══════════════════════════════════════════════════════════════

def run_nightly():
    """Full nightly product intelligence pipeline.
    1. Ensure seeded data exists (idempotent)
    2. Compute learned relationships from co-purchase patterns
    3. Compute learned reorder cycles from repeat purchases
    """
    logger.info("[ProductIntelligence] Starting nightly run...")
    seed_product_intelligence()
    compute_learned_relationships()
    compute_learned_reorder_cycles()
    logger.info("[ProductIntelligence] Nightly run complete")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/mailengine")
    from database import init_db
    init_db()

    if "--seed" in sys.argv:
        print("Seeding product intelligence...")
        result = seed_product_intelligence()
        print(f"Done: {result}")
    else:
        print("Running full product intelligence pipeline...")
        run_nightly()
        print("Done.")
