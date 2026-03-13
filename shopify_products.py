"""
shopify_products.py — Fetch and cache Shopify product images for email templates.

Usage:
    python shopify_products.py          # Full sync (backfill all products)
    python shopify_products.py --check  # Just show cached product count
"""

import sys
sys.path.insert(0, '/var/www/mailengine')

import os
import time
import requests
from datetime import datetime
from database import (ProductImageCache, ShopifyOrderItem, init_db)

init_db()

STORE_URL = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2024-01"
STOREFRONT_DOMAIN = "ldas.ca"


def _shopify_get(endpoint):
    """Make a GET request to Shopify Admin API."""
    url = "%s/admin/api/%s/%s" % (STORE_URL, API_VERSION, endpoint)
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    return resp


def sync_product_images():
    """
    Fetch product images from Shopify for all products in our order history.
    Stores in ProductImageCache table.
    """
    if not STORE_URL or not ACCESS_TOKEN:
        print("ERROR: SHOPIFY_STORE_URL or SHOPIFY_ACCESS_TOKEN not set")
        return 0

    # Get unique product IDs from order items
    product_ids = set()
    title_to_id = {}
    for item in ShopifyOrderItem.select(
        ShopifyOrderItem.product_id, ShopifyOrderItem.product_title
    ).distinct():
        pid = str(item.product_id).strip() if item.product_id else ""
        title = (item.product_title or "").strip()
        if pid and pid != "0" and pid != "None":
            product_ids.add(pid)
            if title:
                title_to_id[title] = pid

    print("Found %d unique product IDs from order history" % len(product_ids))

    synced = 0
    errors = 0

    for pid in sorted(product_ids):
        # Skip if recently synced (within 24 hours)
        existing = ProductImageCache.get_or_none(ProductImageCache.product_id == pid)
        if existing and existing.last_synced:
            age_hours = (datetime.now() - existing.last_synced).total_seconds() / 3600
            if age_hours < 24:
                print("  SKIP %s (synced %.0f hrs ago)" % (pid, age_hours))
                continue

        try:
            resp = _shopify_get("products/%s.json" % pid)
            if resp.status_code == 404:
                print("  SKIP %s (product deleted from Shopify)" % pid)
                continue
            if resp.status_code != 200:
                print("  ERROR %s (status %d)" % (pid, resp.status_code))
                errors += 1
                continue

            product = resp.json().get("product", {})
            title = product.get("title", "")
            handle = product.get("handle", "")
            product_type = product.get("product_type", "")

            # Get first image
            images = product.get("images", [])
            image_url = images[0]["src"] if images else ""

            # Get price from first variant
            variants = product.get("variants", [])
            price = variants[0].get("price", "0.00") if variants else "0.00"
            compare_price = variants[0].get("compare_at_price", "") if variants else ""
            if compare_price is None:
                compare_price = ""

            # Build storefront URL
            product_url = "https://%s/products/%s" % (STOREFRONT_DOMAIN, handle) if handle else ""

            # Upsert
            if existing:
                existing.product_title = title
                existing.image_url = image_url
                existing.product_url = product_url
                existing.price = price
                existing.compare_price = compare_price or ""
                existing.product_type = product_type
                existing.handle = handle
                existing.last_synced = datetime.now()
                existing.save()
            else:
                ProductImageCache.create(
                    product_id=pid,
                    product_title=title,
                    image_url=image_url,
                    product_url=product_url,
                    price=price,
                    compare_price=compare_price or "",
                    product_type=product_type,
                    handle=handle,
                    last_synced=datetime.now(),
                )

            synced += 1
            print("  OK %s — %s ($%s) [img: %s]" % (
                pid, title[:50], price,
                "yes" if image_url else "NO IMAGE"
            ))

        except Exception as e:
            print("  ERROR %s: %s" % (pid, e))
            errors += 1

        # Rate limit: 0.5s between calls
        time.sleep(0.5)

    print("\nSync complete: %d synced, %d errors" % (synced, errors))
    print("Total cached products: %d" % ProductImageCache.select().count())
    return synced


def get_products_for_email(titles_or_ids, limit=4):
    """
    Given product titles or IDs, return product data for email templates.

    Args:
        titles_or_ids: list of product titles (strings) or product IDs
        limit: max products to return

    Returns:
        list of dicts: [{title, image_url, price, product_url, compare_price}, ...]
    """
    results = []
    seen = set()

    for ref in titles_or_ids[:limit * 2]:  # Try more to fill up to limit
        if len(results) >= limit:
            break

        ref_str = str(ref).strip()
        if not ref_str:
            continue

        # Try by product_id first
        cached = ProductImageCache.get_or_none(ProductImageCache.product_id == ref_str)

        # Try by title match
        if not cached:
            cached = ProductImageCache.get_or_none(
                ProductImageCache.product_title == ref_str
            )

        # Try partial title match
        if not cached:
            try:
                # Search for title containing the reference
                cached = (ProductImageCache.select()
                    .where(ProductImageCache.product_title.contains(ref_str[:30]))
                    .first())
            except:
                pass

        # Try reverse: look up product_id from ShopifyOrderItem by title
        if not cached:
            try:
                item = (ShopifyOrderItem.select()
                    .where(ShopifyOrderItem.product_title == ref_str)
                    .first())
                if item and item.product_id:
                    cached = ProductImageCache.get_or_none(
                        ProductImageCache.product_id == str(item.product_id)
                    )
            except:
                pass

        if cached and cached.product_id not in seen:
            seen.add(cached.product_id)
            results.append({
                "title": cached.product_title,
                "image_url": cached.image_url,
                "price": cached.price,
                "product_url": cached.product_url,
                "compare_price": cached.compare_price or "",
            })

    return results


def get_popular_products(limit=4):
    """Get the most-ordered products with images for fallback recommendations."""
    from collections import Counter
    from database import ShopifyOrderItem

    # Count orders per product
    counter = Counter()
    for item in ShopifyOrderItem.select(
        ShopifyOrderItem.product_id, ShopifyOrderItem.product_title
    ):
        pid = str(item.product_id).strip() if item.product_id else ""
        if pid and pid != "0":
            counter[pid] += 1

    # Get top products that have cached images
    results = []
    for pid, count in counter.most_common(limit * 2):
        if len(results) >= limit:
            break
        cached = ProductImageCache.get_or_none(ProductImageCache.product_id == pid)
        if cached and cached.image_url:
            results.append({
                "title": cached.product_title,
                "image_url": cached.image_url,
                "price": cached.price,
                "product_url": cached.product_url,
                "compare_price": cached.compare_price or "",
                "order_count": count,
            })

    return results


# ── CLI ──────────────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/var/www/mailengine/.env")

    # Re-read env after loading
    STORE_URL = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
    ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

    if "--check" in sys.argv:
        count = ProductImageCache.select().count()
        print("Cached products: %d" % count)
        for p in ProductImageCache.select().limit(5):
            print("  %s — $%s — img: %s" % (
                p.product_title[:50], p.price,
                "yes" if p.image_url else "no"
            ))
    else:
        print("=== Shopify Product Image Sync ===")
        print("Store: %s" % STORE_URL)
        sync_product_images()
