"""
Profit & Inventory Brain -- Phase 2D
Adds commercial intelligence to every product and campaign decision.
Syncs cost/inventory from Shopify, estimates margins, scores profitability,
and determines promotion/discount eligibility.

Nightly at 4:45 AM, or on-demand: python3 profit_engine.py
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

# Estimated margins by product_type when Shopify cost data is unavailable
MARGIN_ESTIMATES = {
    "Bluetooth Headsets":   0.45,
    "Dash Cams":            0.35,
    "Phone Accessories":    0.55,
    "Speakers":             0.40,
    "Smart Home":           0.35,
    "Other Electronics":    0.40,
    "Cables & Adapters":    0.60,
    "Power Banks":          0.45,
    "Screen Protectors":    0.65,
    "Cases & Covers":       0.55,
}
DEFAULT_MARGIN = 0.40

# Category keyword mapping (same as customer_intelligence.py)
CATEGORY_KEYWORDS = {
    "Bluetooth Headsets": ["headset", "headphone", "earphone", "earbud", "bluetooth", "trucker", "wireless audio"],
    "Dash Cams":          ["dash cam", "dashcam", "dash camera", "car camera", "driving recorder"],
    "Phone Accessories":  ["phone", "case", "charger", "cable", "screen protector", "mount", "holder"],
    "Speakers":           ["speaker", "soundbar", "sound bar", "portable speaker", "bluetooth speaker"],
    "Smart Home":         ["smart", "alexa", "google home", "wifi", "iot", "smart plug", "smart light"],
    "Power Banks":        ["power bank", "battery pack", "portable charger"],
    "Cables & Adapters":  ["cable", "adapter", "usb", "hdmi", "converter", "dongle"],
    "Screen Protectors":  ["screen protector", "tempered glass", "film"],
    "Cases & Covers":     ["case", "cover", "sleeve", "pouch"],
}


def _infer_product_type(product_title, existing_type=""):
    """Infer product type from title if not set."""
    if existing_type and existing_type not in ("", "Unknown", "Other"):
        # Try to match existing type to our categories
        for cat in MARGIN_ESTIMATES:
            if cat.lower() in existing_type.lower():
                return cat
    title_lower = (product_title or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return category
    return "Other Electronics"


# ═══════════════════════════════════════════════════════════════
# Shopify API Sync
# ═══════════════════════════════════════════════════════════════

def sync_product_commercial_data():
    """Sync cost/inventory from Shopify API, populate ProductCommercial rows.
    Returns dict: {synced: int, with_cost: int, estimated: int, errors: int}
    """
    from database import (ProductCommercial, ShopifyOrderItem,
                          ProductImageCache, init_db)
    init_db()

    store_url = os.getenv("SHOPIFY_STORE_URL", "")
    access_token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

    # Collect all unique product_ids from orders + image cache
    product_ids = set()
    product_info = {}  # product_id -> {title, type, sku, price}

    for item in ShopifyOrderItem.select(
        ShopifyOrderItem.product_id,
        ShopifyOrderItem.product_title,
        ShopifyOrderItem.product_type,
        ShopifyOrderItem.sku,
        ShopifyOrderItem.unit_price
    ).distinct():
        pid = str(item.product_id)
        if pid and pid != "0" and pid != "None":
            product_ids.add(pid)
            product_info[pid] = {
                "title": item.product_title or "",
                "type": item.product_type or "",
                "sku": item.sku or "",
                "price": float(item.unit_price or 0),
            }

    for img in ProductImageCache.select():
        pid = str(img.product_id)
        if pid and pid != "0":
            product_ids.add(pid)
            if pid not in product_info:
                try:
                    _cached_price = float(img.price) if img.price else 0.0
                except (ValueError, TypeError):
                    _cached_price = 0.0
                product_info[pid] = {
                    "title": img.product_title or "",
                    "type": img.product_type or "",
                    "sku": "",
                    "price": _cached_price,
                }
            # Update price from cache if available (more current)
            if img.price:
                try:
                    _p = float(img.price)
                    if _p > 0:
                        product_info[pid]["price"] = _p
                except (ValueError, TypeError):
                    pass

    stats = {"synced": 0, "with_cost": 0, "estimated": 0, "errors": 0}

    for pid in product_ids:
        info = product_info.get(pid, {})
        title = info.get("title", "")
        product_type = _infer_product_type(title, info.get("type", ""))
        sku = info.get("sku", "")
        current_price = info.get("price", 0)
        cost_per_unit = None
        inventory_level = None
        inventory_location = ""
        margin_source = "estimated"
        compare_price = 0.0

        # Try Shopify API for cost + inventory
        if store_url and access_token:
            try:
                import requests
                headers = {"X-Shopify-Access-Token": access_token}
                resp = requests.get(
                    f"{store_url}/admin/api/2024-01/products/{pid}.json",
                    headers=headers, timeout=10
                )
                if resp.status_code == 200:
                    prod_data = resp.json().get("product", {})
                    variants = prod_data.get("variants", [])
                    if variants:
                        v = variants[0]
                        # Cost
                        if v.get("cost"):
                            try:
                                cost_per_unit = float(v["cost"])
                                margin_source = "shopify"
                                stats["with_cost"] += 1
                            except (ValueError, TypeError):
                                pass
                        # Inventory
                        inv_qty = v.get("inventory_quantity")
                        if inv_qty is not None:
                            try:
                                inventory_level = int(inv_qty)
                            except (ValueError, TypeError):
                                pass
                        # Price from Shopify (most current)
                        if v.get("price"):
                            try:
                                current_price = float(v["price"])
                            except (ValueError, TypeError):
                                pass
                        # Compare price
                        if v.get("compare_at_price"):
                            try:
                                compare_price = float(v["compare_at_price"])
                            except (ValueError, TypeError):
                                pass
                    # Product type from Shopify
                    if prod_data.get("product_type"):
                        product_type = _infer_product_type(title, prod_data["product_type"])
                elif resp.status_code == 404:
                    pass  # Product no longer exists in Shopify
                else:
                    stats["errors"] += 1
                time.sleep(0.5)  # Rate limit
            except Exception as e:
                logger.warning(f"Shopify API error for product {pid}: {e}")
                stats["errors"] += 1

        # Estimate cost if not from Shopify
        if cost_per_unit is None and current_price > 0:
            margin_est = MARGIN_ESTIMATES.get(product_type, DEFAULT_MARGIN)
            cost_per_unit = round(current_price * (1 - margin_est), 2)
            margin_source = "estimated"
            stats["estimated"] += 1

        # Compute margin
        margin_pct = None
        if cost_per_unit is not None and current_price > 0:
            margin_pct = round((current_price - cost_per_unit) / current_price * 100, 1)

        # Upsert
        try:
            ProductCommercial.insert(
                product_id=pid,
                product_title=title,
                sku=sku,
                product_type=product_type,
                current_price=current_price,
                compare_price=compare_price,
                cost_per_unit=cost_per_unit,
                margin_pct=margin_pct,
                margin_source=margin_source,
                inventory_level=inventory_level,
                inventory_location=inventory_location,
                last_synced=datetime.now(),
            ).on_conflict(
                conflict_target=[ProductCommercial.product_id],
                update={
                    ProductCommercial.product_title: title,
                    ProductCommercial.sku: sku,
                    ProductCommercial.product_type: product_type,
                    ProductCommercial.current_price: current_price,
                    ProductCommercial.compare_price: compare_price,
                    ProductCommercial.cost_per_unit: cost_per_unit,
                    ProductCommercial.margin_pct: margin_pct,
                    ProductCommercial.margin_source: margin_source,
                    ProductCommercial.inventory_level: inventory_level,
                    ProductCommercial.inventory_location: inventory_location,
                    ProductCommercial.last_synced: datetime.now(),
                }
            ).execute()
            stats["synced"] += 1
        except Exception as e:
            logger.error(f"Failed to upsert ProductCommercial for {pid}: {e}")
            stats["errors"] += 1

    print(f"[OK] Synced {stats['synced']} products "
          f"({stats['with_cost']} with Shopify cost, {stats['estimated']} estimated, "
          f"{stats['errors']} errors)")
    return stats


# ═══════════════════════════════════════════════════════════════
# Product Scoring
# ═══════════════════════════════════════════════════════════════

def compute_product_scores():
    """Compute derived commercial metrics for all products.
    Returns count of products scored.
    """
    from database import (ProductCommercial, ShopifyOrderItem, ShopifyOrder,
                          init_db)
    from peewee import fn
    init_db()

    now = datetime.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)
    count = 0

    for pc in ProductCommercial.select():
        pid = pc.product_id

        # ── Sales metrics ──
        # 30-day
        items_30d = (ShopifyOrderItem
            .select(
                fn.SUM(ShopifyOrderItem.quantity).alias("qty"),
                fn.SUM(ShopifyOrderItem.unit_price * ShopifyOrderItem.quantity).alias("rev"),
                fn.SUM(ShopifyOrderItem.total_discount).alias("disc"),
            )
            .join(ShopifyOrder, on=(ShopifyOrderItem.order == ShopifyOrder.id))
            .where(
                ShopifyOrderItem.product_id == pid,
                ShopifyOrder.ordered_at >= thirty_days_ago
            )
            .dicts()
            .first())

        units_30d = int(items_30d.get("qty") or 0) if items_30d else 0
        revenue_30d = float(items_30d.get("rev") or 0) if items_30d else 0

        # 90-day
        items_90d = (ShopifyOrderItem
            .select(
                fn.SUM(ShopifyOrderItem.quantity).alias("qty"),
                fn.SUM(ShopifyOrderItem.unit_price * ShopifyOrderItem.quantity).alias("rev"),
            )
            .join(ShopifyOrder, on=(ShopifyOrderItem.order == ShopifyOrder.id))
            .where(
                ShopifyOrderItem.product_id == pid,
                ShopifyOrder.ordered_at >= ninety_days_ago
            )
            .dicts()
            .first())

        units_90d = int(items_90d.get("qty") or 0) if items_90d else 0
        revenue_90d = float(items_90d.get("rev") or 0) if items_90d else 0

        # Return rate
        total_orders_for_product = (ShopifyOrderItem
            .select(fn.COUNT(ShopifyOrderItem.id).alias("cnt"))
            .join(ShopifyOrder, on=(ShopifyOrderItem.order == ShopifyOrder.id))
            .where(ShopifyOrderItem.product_id == pid)
            .dicts()
            .first())
        total_count = int(total_orders_for_product.get("cnt") or 0) if total_orders_for_product else 0

        refunded_count = (ShopifyOrderItem
            .select(fn.COUNT(ShopifyOrderItem.id).alias("cnt"))
            .join(ShopifyOrder, on=(ShopifyOrderItem.order == ShopifyOrder.id))
            .where(
                ShopifyOrderItem.product_id == pid,
                ShopifyOrder.financial_status.in_(["refunded", "partially_refunded"])
            )
            .dicts()
            .first())
        refund_count = int(refunded_count.get("cnt") or 0) if refunded_count else 0

        return_rate = round(refund_count / max(1, total_count) * 100, 1)

        # Average discount given
        disc_stats = (ShopifyOrderItem
            .select(
                fn.AVG(
                    ShopifyOrderItem.total_discount /
                    fn.MAX(ShopifyOrderItem.unit_price * ShopifyOrderItem.quantity, 0.01)
                    * 100
                ).alias("avg_disc")
            )
            .where(ShopifyOrderItem.product_id == pid)
            .dicts()
            .first())
        avg_discount = round(float(disc_stats.get("avg_disc") or 0), 1) if disc_stats else 0

        # ── Margin ──
        cost = pc.cost_per_unit
        price = pc.current_price or 0
        margin_source = pc.margin_source

        if cost is None and price > 0:
            ptype = pc.product_type or "Other Electronics"
            margin_est = MARGIN_ESTIMATES.get(ptype, DEFAULT_MARGIN)
            cost = round(price * (1 - margin_est), 2)
            margin_source = "estimated"

        margin_pct = None
        if cost is not None and price > 0:
            margin_pct = round((price - cost) / price * 100, 1)

        profit_30d = round(revenue_30d * margin_pct / 100, 2) if margin_pct else None
        profit_90d = round(revenue_90d * margin_pct / 100, 2) if margin_pct else None

        # ── Stock pressure ──
        inv = pc.inventory_level
        days_of_stock = None
        stock_pressure = "unknown"

        if inv is not None:
            if inv == 0:
                stock_pressure = "out_of_stock"
                days_of_stock = 0
            elif units_30d > 0:
                daily_rate = units_30d / 30
                days_of_stock = round(inv / daily_rate, 1)
                if days_of_stock <= 14:
                    stock_pressure = "critical"
                elif days_of_stock <= 30:
                    stock_pressure = "low"
                elif days_of_stock <= 90:
                    stock_pressure = "healthy"
                else:
                    stock_pressure = "overstocked"
            else:
                # Has inventory but no recent sales
                stock_pressure = "overstocked" if inv > 50 else "healthy"
                days_of_stock = 999

        # ── Promotion eligibility ──
        promo_eligible = True
        promo_reason = "Standard eligibility"
        max_discount_pct = max(0, (margin_pct or 40) - 15)

        if stock_pressure == "out_of_stock":
            promo_eligible = False
            promo_reason = "Out of stock -- do not promote"
        elif stock_pressure == "critical" and (margin_pct or 40) < 30:
            promo_eligible = False
            promo_reason = "Low stock, low margin -- preserve inventory"
        elif return_rate > 15:
            promo_eligible = False
            promo_reason = "High return rate -- fix product issues first"
        elif stock_pressure == "overstocked":
            promo_reason = "Overstocked -- push aggressively"
        elif margin_pct and margin_pct > 50:
            promo_reason = "High margin -- prioritize"
        elif margin_pct and margin_pct < 20:
            promo_reason = "Low margin -- discount-averse"

        # ── Profitability score ──
        prof_score = 40  # base
        if margin_pct is not None:
            if margin_pct >= 40:
                prof_score += 20
            elif margin_pct < 20:
                prof_score -= 20
        else:
            prof_score -= 20  # unknown margin

        if units_30d >= 10:
            prof_score += 15
        if stock_pressure in ("healthy", "overstocked"):
            prof_score += 10
        if return_rate < 5:
            prof_score += 10
        if stock_pressure in ("out_of_stock", "critical"):
            prof_score -= 15
        if return_rate > 10:
            prof_score -= 10
        if avg_discount > 15:
            prof_score -= 10

        prof_score = max(0, min(100, prof_score))

        # ── Save ──
        pc.units_sold_30d = units_30d
        pc.units_sold_90d = units_90d
        pc.revenue_30d = round(revenue_30d, 2)
        pc.revenue_90d = round(revenue_90d, 2)
        pc.profit_30d = profit_30d
        pc.profit_90d = profit_90d
        pc.return_rate = return_rate
        pc.avg_discount_given = avg_discount
        pc.cost_per_unit = cost
        pc.margin_pct = margin_pct
        pc.margin_source = margin_source
        pc.days_of_stock = days_of_stock
        pc.stock_pressure = stock_pressure
        pc.promotion_eligible = promo_eligible
        pc.promotion_reason = promo_reason
        pc.profitability_score = prof_score
        pc.last_computed = datetime.now()
        pc.save()
        count += 1

    print(f"[OK] Scored {count} products")
    return count


# ═══════════════════════════════════════════════════════════════
# Promotion & Discount Eligibility
# ═══════════════════════════════════════════════════════════════

def get_promotion_eligibility(product_id):
    """Check if a product should be actively promoted.
    Returns dict: {eligible: bool, reason: str, max_discount_pct: float}
    """
    from database import ProductCommercial, init_db
    init_db()

    try:
        pc = ProductCommercial.get(ProductCommercial.product_id == str(product_id))
        max_disc = max(0, (pc.margin_pct or 40) - 15)
        return {
            "eligible": pc.promotion_eligible,
            "reason": pc.promotion_reason,
            "max_discount_pct": round(max_disc, 1),
            "profitability_score": pc.profitability_score,
        }
    except ProductCommercial.DoesNotExist:
        return {
            "eligible": True,
            "reason": "Product not in commercial database",
            "max_discount_pct": 25.0,
            "profitability_score": 40,
        }


def get_customer_discount_eligibility(contact_id):
    """Determine if a customer should receive a discount.
    Returns dict: {should_discount: bool, max_pct: float, reason: str}
    """
    from database import CustomerProfile, ContactScore, init_db
    init_db()

    try:
        cp = CustomerProfile.get(CustomerProfile.contact == contact_id)
    except CustomerProfile.DoesNotExist:
        return {"should_discount": True, "max_pct": 10, "reason": "No profile -- standard eligibility"}

    # Rule 1: Premium full-price buyers
    if cp.price_tier == "premium" and (cp.discount_sensitivity or 0) < 0.2:
        return {"should_discount": False, "max_pct": 0, "reason": "Buys full price -- no discount needed"}

    # Rule 2: VIP with high engagement
    if cp.lifecycle_stage == "vip":
        eng = cp.website_engagement_score or 0
        if eng > 70:
            return {"should_discount": False, "max_pct": 0,
                    "reason": "Loyal VIP -- doesn't need discount incentive"}

    # Rule 3: At-risk discount responders
    if cp.lifecycle_stage in ("at_risk", "churned") and (cp.discount_sensitivity or 0) > 0.5:
        return {"should_discount": True, "max_pct": 15,
                "reason": "At risk, discount-responsive -- offer incentive"}

    # Rule 4: Engaged customers
    eng = cp.website_engagement_score or 0
    if eng > 50:
        return {"should_discount": True, "max_pct": 5,
                "reason": "Engaged -- small incentive sufficient"}

    # Default
    return {"should_discount": True, "max_pct": 10, "reason": "Standard discount eligibility"}


# ═══════════════════════════════════════════════════════════════
# Campaign Profit Forecast
# ═══════════════════════════════════════════════════════════════

CONVERSION_RATES = {
    "reorder_reminder": 0.05,
    "cross_sell":       0.03,
    "upsell":           0.025,
    "new_product":      0.02,
    "winback":          0.015,
    "education":        0.005,
    "loyalty_reward":   0.04,
    "discount_offer":   0.04,
}


def compute_campaign_profit_forecast(suggested_campaign_id):
    """Enrich a SuggestedCampaign with profit forecasts.
    Returns dict with margin_pct, profit, discount_cost, net_profit.
    """
    from database import (SuggestedCampaign, ProductCommercial,
                          CustomerProfile, init_db)
    init_db()

    try:
        sc = SuggestedCampaign.get_by_id(suggested_campaign_id)
    except SuggestedCampaign.DoesNotExist:
        return {"error": "Campaign not found"}

    # Load contact IDs
    contact_ids = json.loads(sc.eligible_contacts_json or "[]")[:200]

    # Aggregate top products across segment contacts
    product_counts = defaultdict(int)
    product_ids_found = set()
    for cid in contact_ids[:100]:  # Sample 100 for speed
        try:
            cp = CustomerProfile.get(CustomerProfile.contact == cid)
            prods = json.loads(cp.top_products or "[]")
            all_prods = json.loads(cp.all_products_bought or "[]")
            for p in prods:
                if isinstance(p, str):
                    product_counts[p] += 1
            for p in all_prods:
                if isinstance(p, dict) and p.get("product_id"):
                    product_ids_found.add(str(p["product_id"]))
        except (CustomerProfile.DoesNotExist, json.JSONDecodeError):
            pass

    # Load ProductCommercial for found products
    margins = []
    top_products = []
    margin_warnings = []

    for pc in ProductCommercial.select().where(
        ProductCommercial.product_id.in_(list(product_ids_found)[:50])
    ):
        if pc.margin_pct is not None:
            margins.append(pc.margin_pct)
        if pc.margin_pct and pc.margin_pct < 20:
            margin_warnings.append(f"{pc.product_title}: {pc.margin_pct}% margin")
        top_products.append({
            "title": pc.product_title,
            "price": pc.current_price,
            "margin_pct": pc.margin_pct,
            "promotion_eligible": pc.promotion_eligible,
        })

    # If we didn't find specific products, use category-level estimate
    if not margins:
        margins = [DEFAULT_MARGIN * 100]

    avg_margin = sum(margins) / len(margins) if margins else DEFAULT_MARGIN * 100
    predicted_profit = round(sc.predicted_revenue * avg_margin / 100, 2)

    # Discount cost estimate
    discount_cost = 0.0
    if sc.recommended_offer_type in ("percentage_off", "free_shipping"):
        conv_rate = CONVERSION_RATES.get(sc.campaign_type, 0.02)
        disc_pct = 0.10 if sc.recommended_offer_type == "percentage_off" else 0.05
        # Sample avg_order_value
        aovs = []
        for cid in contact_ids[:50]:
            try:
                cp = CustomerProfile.get(CustomerProfile.contact == cid)
                if cp.avg_order_value and cp.avg_order_value > 0:
                    aovs.append(cp.avg_order_value)
            except CustomerProfile.DoesNotExist:
                pass
        avg_aov = sum(aovs) / len(aovs) if aovs else 80
        discount_cost = round(sc.segment_size * conv_rate * avg_aov * disc_pct, 2)

    net_profit = round(predicted_profit - discount_cost, 2)

    margin_warning = "; ".join(margin_warnings[:3]) if margin_warnings else ""

    # Update SuggestedCampaign
    sc.predicted_margin_pct = round(avg_margin, 1)
    sc.predicted_profit = predicted_profit
    sc.discount_cost = discount_cost
    sc.net_profit = net_profit
    sc.top_products_json = json.dumps(top_products[:10])
    sc.margin_warning = margin_warning
    sc.save()

    return {
        "margin_pct": round(avg_margin, 1),
        "profit": predicted_profit,
        "discount_cost": discount_cost,
        "net_profit": net_profit,
        "margin_warning": margin_warning,
    }


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/mailengine")
    from database import init_db
    init_db()

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        result = sync_product_commercial_data()
    elif len(sys.argv) > 1 and sys.argv[1] == "score":
        count = compute_product_scores()
    else:
        result = sync_product_commercial_data()
        count = compute_product_scores()
        print(f"\nDone. Synced and scored all products.")
