"""
shared_constants.py — Single source of truth for business knowledge.

Seeded knowledge that lives here:
  - Category keywords (product title → category mapping)
  - Upgrade ladders (price tiers per category)
  - Reorder cycles (replacement timing per product type)
  - Competitive positioning
  - Seasonal calendar
  - Value propositions
  - Margin estimates (fallback when Shopify cost data unavailable)

This file is imported by: customer_intelligence, profit_engine, data_enrichment,
shopify_enrichment, product_intelligence, intelligence_layer.

RULE: Nightly learned inference (co-purchase patterns, timing) must NOT overwrite
values defined here. Learned data lives in database tables with source='learned'
vs source='seeded'. See product_intelligence.py for enforcement.
"""

# ═══════════════════════════════════════════════════════════════
# Category Keywords — product title → category mapping
# ═══════════════════════════════════════════════════════════════
# Superset of all keywords previously duplicated across 4 files.
# Order matters within each list — more specific patterns first.

CATEGORY_KEYWORDS = {
    "Bluetooth Headsets": [
        "bluetooth headset", "trucker headset", "headset", "earpiece",
        "th11", "th-11", "g10", "g7", "g3", "geforce", "bluetooth head",
        "headphone", "earphone", "earbud", "wireless audio",
    ],
    "Dash Cams": [
        "dash cam", "dashcam", "dash-cam", "dash camera",
        "car camera", "driving recorder", "parking mode", "a20",
    ],
    "CB Radios": [
        "cb radio", "two-way radio", "2-way radio", "citizens band",
    ],
    "Mounts & Holders": [
        "mount", "holder", "phone mount", "tablet mount",
        "windshield mount", "suction cup",
    ],
    "Cables & Chargers": [
        "cable", "charger", "usb-c", "usb c", "usb", "adapter",
        "hdmi", "converter", "dongle", "charging", "power cable",
    ],
    "Ear Cushions & Parts": [
        "ear cushion", "ear pad", "ear cup", "replacement pad",
        "foam pad", "leatherette", "headband pad",
    ],
    "Phone Accessories": [
        "phone case", "screen protector", "tempered glass",
        "phone", "case", "cover", "sleeve", "pouch", "film",
    ],
    "Power Banks": [
        "power bank", "battery pack", "portable charger",
    ],
    "Speakers": [
        "speaker", "soundbar", "sound bar", "portable speaker",
        "bluetooth speaker", "audio",
    ],
    "Smart Home": [
        "smart", "wifi plug", "bulb", "alexa", "google home",
        "iot", "smart plug", "smart light", "automation",
    ],
    "Other Electronics": [],  # fallback — must be last
}

# Quick alias for categories that have alternate names
CATEGORY_ALIASES = {
    "Cables & Adapters": "Cables & Chargers",
    "Screen Protectors": "Phone Accessories",
    "Cases & Covers": "Phone Accessories",
}


def infer_category(product_title):
    """Match product title to a category using keyword matching.
    Returns category string.
    """
    t = (product_title or "").lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if cat == "Other Electronics":
            continue
        if any(kw in t for kw in kws):
            return cat
    return "Other Electronics"


# ═══════════════════════════════════════════════════════════════
# Product Aliases — canonical product identification
# ═══════════════════════════════════════════════════════════════
# Maps variant titles/abbreviations to canonical product keys.
# Used by product_intelligence.py to normalize product identity.
# Entries are (alias_lower → canonical_key).

PRODUCT_ALIASES = {
    # BlueParrott headsets
    "g7":                    "BlueParrott G7",
    "blueparrott g7":        "BlueParrott G7",
    "g7 headset":            "BlueParrott G7",
    "g10":                   "BlueParrott G10",
    "blueparrott g10":       "BlueParrott G10",
    "g10 headset":           "BlueParrott G10",
    "g3":                    "BlueParrott G3",
    "blueparrott g3":        "BlueParrott G3",
    "th11":                  "TH-11",
    "th-11":                 "TH-11",
    "geforce":               "GeForce Headset",
    # Common abbreviations
    "a20":                   "A20 Dash Cam",
}


def resolve_product_alias(title):
    """Resolve a product title to its canonical key, or return the original title."""
    if not title:
        return title
    lower = title.strip().lower()
    return PRODUCT_ALIASES.get(lower, title.strip())


# ═══════════════════════════════════════════════════════════════
# Upgrade Paths — price tier ladders per category
# ═══════════════════════════════════════════════════════════════
# Extracted from account_manager.py:490-493
# Each entry: (tier_name, price_low, price_high, upgrade_trigger_days)

UPGRADE_PATHS = {
    "Bluetooth Headsets": [
        ("Budget",  30,  50,  180),
        ("Mid",     50,  80,  180),
        ("Pro",     80,  150, 240),
        ("Premium", 150, 999, 365),
    ],
    "Dash Cams": [
        ("Single",   50,  100, 240),
        ("Dual",     100, 200, 365),
        ("Fleet",    200, 999, 540),
    ],
    "CB Radios": [
        ("Basic",  30,  60,  365),
        ("Pro",    60,  120, 365),
        ("Fleet",  120, 999, 540),
    ],
    "Mounts & Holders": [
        ("Standard", 15, 30, 180),
        ("Premium",  30, 50, 365),
    ],
}


# ═══════════════════════════════════════════════════════════════
# Reorder Cycles — replacement timing per product type
# ═══════════════════════════════════════════════════════════════
# Extracted from account_manager.py:497-501
# (typical_cycle_days, variance_days, reminder_offset_days, consumable)

REORDER_CYCLES = {
    "Ear Cushions & Parts":  (180,  30, 14, True),
    "Cables & Chargers":     (120,  30, 14, True),
    "Bluetooth Headsets":    (540,  60, 30, False),
    "Dash Cams":             (730,  90, 30, False),
    "CB Radios":             (730,  90, 30, False),
    "Mounts & Holders":      (365,  60, 14, False),
    "Phone Accessories":     (180,  30, 14, True),
    "Power Banks":           (365,  60, 14, False),
    "Speakers":              (730,  90, 30, False),
}


# ═══════════════════════════════════════════════════════════════
# Product Relationships — seeded cross-category knowledge
# ═══════════════════════════════════════════════════════════════
# (source_type, target_type, relationship_type, strength, typical_delay_days, reason)
# Also supports exact product-level relationships.

SEEDED_RELATIONSHIPS = [
    # Category-level relationships
    ("Bluetooth Headsets", "Ear Cushions & Parts", "replacement", 90, 180,
     "Ear cushions wear out after ~6 months of daily trucking use"),
    ("Bluetooth Headsets", "Dash Cams", "cross_sell", 60, None,
     "Truckers who invest in headsets often want dash cams for safety"),
    ("Bluetooth Headsets", "Mounts & Holders", "accessory", 70, None,
     "Phone mounts pair well with hands-free headsets"),
    ("Bluetooth Headsets", "Cables & Chargers", "accessory", 65, None,
     "Replacement charging cables for headsets"),
    ("Dash Cams", "Mounts & Holders", "accessory", 75, None,
     "Dash cams need proper mounting accessories"),
    ("Dash Cams", "Cables & Chargers", "accessory", 60, None,
     "Power cables and adapters for in-cab dash cam setup"),
    ("Dash Cams", "Bluetooth Headsets", "cross_sell", 55, None,
     "Safety-conscious drivers who use dash cams often want hands-free headsets"),
    ("CB Radios", "Bluetooth Headsets", "cross_sell", 50, None,
     "CB radio users often also need a Bluetooth headset for phone calls"),
    ("Mounts & Holders", "Cables & Chargers", "accessory", 60, None,
     "Charging cables complement phone mounting setups"),

    # Exact product-level relationships
    ("BlueParrott G7", "BlueParrott G10", "upgrade", 80, 180,
     "G10 is the natural upgrade from G7 with better noise cancellation"),
    ("BlueParrott G3", "BlueParrott G7", "upgrade", 85, 180,
     "G7 is the mid-tier upgrade from the budget G3"),
    ("BlueParrott G7", "Ear Cushions & Parts", "replacement", 95, 180,
     "G7 ear cushions need replacement every ~6 months"),
    ("BlueParrott G10", "Ear Cushions & Parts", "replacement", 95, 180,
     "G10 ear cushions need replacement every ~6 months"),
]


# ═══════════════════════════════════════════════════════════════
# Competitive Positioning
# ═══════════════════════════════════════════════════════════════
# Extracted from account_manager.py:503-507

COMPETITIVE_POSITIONING = {
    "Jabra": {
        "positioning": "More affordable, similar quality for trucking use",
        "key_diff": "LDAS specializes in trucking; Jabra is office-focused",
        "approach": "Highlight trucking expertise and value pricing",
    },
    "BlueParrott": {
        "positioning": "Better value, wider product range",
        "key_diff": "LDAS offers complementary accessories and bundles",
        "approach": "Emphasize ecosystem and Canadian support",
    },
    "Amazon Generics": {
        "positioning": "Specialization in trucking, expert support, Canadian warranty",
        "key_diff": "Generic brands lack driver-specific features and support",
        "approach": "Stress quality, warranty, and expertise",
    },
    "Poly/Plantronics": {
        "positioning": "More focused on driver needs, not office headsets",
        "key_diff": "Poly targets offices; LDAS targets truck cabs",
        "approach": "Highlight noise cancellation for road noise, not office chatter",
    },
}


# ═══════════════════════════════════════════════════════════════
# Seasonal Calendar
# ═══════════════════════════════════════════════════════════════
# Extracted from account_manager.py:509-514
# (month_start, month_end, key, label, themes, promo_appropriate)

SEASONAL_CALENDAR = [
    (1,  2,  "new_year_fleet",     "New Year Fleet Upgrades",
     ["fleet renewal", "budget reset", "new equipment"], True),
    (3,  4,  "spring_prep",        "Spring Driving Season Prep",
     ["road trip", "new equipment", "spring cleaning"], True),
    (5,  5,  "early_summer",       "Early Summer Deals",
     ["summer prep", "road trip gear"], False),
    (6,  8,  "summer_deals",       "Summer Road Trip Season",
     ["summer deals", "road trip", "hot weather gear"], True),
    (9,  10, "fall_prep",          "Fall & Pre-Winter Prep",
     ["fleet renewal", "winter prep", "cold weather gear"], True),
    (11, 11, "black_friday",       "Black Friday & Cyber Monday",
     ["holiday deals", "gift ideas", "biggest sale"], True),
    (12, 12, "holiday_gifts",      "Holiday Gift Season",
     ["gifts for drivers", "holiday specials", "year-end deals"], True),
]


def get_current_season():
    """Return the current seasonal context based on today's date."""
    from datetime import datetime
    month = datetime.now().month
    for start, end, key, label, themes, promo in SEASONAL_CALENDAR:
        if start <= month <= end:
            return {
                "key": key,
                "label": label,
                "themes": themes,
                "promo_appropriate": promo,
                "month": month,
            }
    # Fallback (should not happen)
    return {"key": "general", "label": "General", "themes": [], "promo_appropriate": False, "month": month}


# ═══════════════════════════════════════════════════════════════
# Value Propositions
# ═══════════════════════════════════════════════════════════════
# Extracted from account_manager.py:516-521

VALUE_PROPS = [
    "Canadian company, Canadian warranty and support",
    "Trucking-focused expertise (not generic electronics)",
    "Competitive pricing vs brand-name alternatives",
    "Fast shipping across Canada",
    "Product expertise — we know what works in a truck cab",
]


# ═══════════════════════════════════════════════════════════════
# Margin Estimates (fallback when Shopify cost unavailable)
# ═══════════════════════════════════════════════════════════════
# Moved from profit_engine.py

MARGIN_ESTIMATES = {
    "Bluetooth Headsets":    0.45,
    "Dash Cams":             0.35,
    "CB Radios":             0.40,
    "Mounts & Holders":      0.50,
    "Cables & Chargers":     0.60,
    "Ear Cushions & Parts":  0.55,
    "Phone Accessories":     0.55,
    "Power Banks":           0.45,
    "Speakers":              0.40,
    "Smart Home":            0.35,
    "Other Electronics":     0.40,
    # Legacy keys (mapped through CATEGORY_ALIASES)
    "Cables & Adapters":     0.60,
    "Screen Protectors":     0.65,
    "Cases & Covers":        0.55,
}
DEFAULT_MARGIN = 0.40


# ═══════════════════════════════════════════════════════════════
# Lifecycle & Customer Type Labels
# ═══════════════════════════════════════════════════════════════

LIFECYCLE_LABELS = {
    "prospect":      "Prospect",
    "new_customer":  "New Customer",
    "active_buyer":  "Active Buyer",
    "loyal":         "Loyal Customer",
    "vip":           "VIP",
    "at_risk":       "At Risk",
    "churned":       "Churned",
    "reactivated":   "Reactivated",
}

CUSTOMER_TYPE_LABELS = {
    "browser":         "Browser",
    "one_time":        "One-Time Buyer",
    "repeat":          "Repeat Buyer",
    "loyal":           "Loyal",
    "vip":             "VIP",
    "discount_seeker": "Discount Seeker",
    "dormant":         "Dormant",
}
