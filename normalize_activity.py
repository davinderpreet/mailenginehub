"""
Normalization layer for CustomerActivity event_data.

Maps variant field names from different sources (pixel, Shopify webhooks,
Shopify sync, Omnisend) into canonical fields so detection functions never
miss events due to naming differences.

Every ingestion point calls normalize_event_data() before json.dumps().
"""

import copy


# ── Helper: first non-empty value from a dict for a list of keys ─────────

def _first(data, keys, default=""):
    """Return the first truthy value found for any key in keys."""
    for k in keys:
        v = data.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _extract_product_title_from_url(url):
    """Extract product slug from URL like /products/premium-headphones."""
    if not url or "/products/" not in url:
        return ""
    slug = url.split("/products/")[-1].split("?")[0].split("#")[0].strip("/")
    if not slug or "/" in slug:
        return ""
    return slug.replace("-", " ").strip().title()


def _extract_product_titles(items):
    """Given a list of line_item dicts, extract product titles."""
    titles = []
    for item in items:
        if isinstance(item, dict):
            t = (item.get("title") or item.get("name") or
                 item.get("product_title") or item.get("product_name") or "")
            if t:
                titles.append(t)
        elif isinstance(item, str) and item:
            titles.append(item)
    return titles


# ── Main normalization function ──────────────────────────────────────────

def normalize_event_data(event_type, raw_data):
    """Normalize variant field names into canonical fields.

    Args:
        event_type: CustomerActivity.event_type (e.g. 'viewed_product')
        raw_data:   dict of the original event_data payload

    Returns:
        dict with canonical top-level keys + 'raw_payload' preserving original
    """
    if not isinstance(raw_data, dict):
        return {"raw_payload": raw_data}

    out = copy.deepcopy(raw_data)

    # Preserve original under raw_payload (skip if already present = re-normalize)
    if "raw_payload" not in out:
        out["raw_payload"] = copy.deepcopy(raw_data)

    # ── viewed_product ───────────────────────────────────────────────
    if event_type == "viewed_product":
        # product_title
        title = _first(raw_data, ["product_title", "product_name", "title", "name"])
        if not title:
            title = _extract_product_title_from_url(raw_data.get("url", ""))
        if title:
            out["product_title"] = title

        # product_id
        pid = _first(raw_data, ["product_id", "variant_id", "id"])
        if pid:
            out["product_id"] = str(pid)

        # product_url
        url = _first(raw_data, ["url", "product_url", "href"])
        if url:
            out["product_url"] = url

        # product_image
        img = _first(raw_data, ["image_url", "image", "featured_image"])
        if img:
            out["product_image"] = img

    # ── abandoned_checkout / completed_checkout ──────────────────────
    elif event_type in ("abandoned_checkout", "completed_checkout"):
        # checkout_id
        cid = _first(raw_data, ["checkout_id", "checkout_token", "token", "id"])
        if cid:
            out["checkout_id"] = str(cid)

        # products — handle list of strings OR list of line_item dicts
        products = raw_data.get("products")
        if products and isinstance(products, list):
            out["products"] = _extract_product_titles(products)
        elif not products:
            items = raw_data.get("line_items") or raw_data.get("items") or []
            if items:
                out["products"] = _extract_product_titles(items)

        # total
        total = _first(raw_data, ["total", "total_price", "subtotal_price", "amount"])
        if total is not None and total != "":
            out["total"] = total

        # item_count
        ic = _first(raw_data, ["item_count", "items_count"])
        if ic:
            out["item_count"] = ic
        elif "products" in out and isinstance(out["products"], list):
            out["item_count"] = len(out["products"])

        # currency
        out.setdefault("currency", raw_data.get("currency", "CAD"))

    # ── placed_order ─────────────────────────────────────────────────
    elif event_type == "placed_order":
        # order_number
        on = _first(raw_data, ["order_number", "name", "order_name"])
        if on:
            out["order_number"] = on

        # order_total
        ot = _first(raw_data, ["order_total", "total_price", "total", "amount"])
        if ot is not None and ot != "":
            out["order_total"] = ot

    return out
