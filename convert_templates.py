"""
convert_templates.py — One-time migration: convert 15 seed templates to block format.

Reads each seed template by name, builds an equivalent blocks_json array,
sets template_format='blocks' and template_family. Keeps html_body intact as fallback.

Idempotent — skips templates already in blocks format.

Usage:
    python convert_templates.py
"""

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db, EmailTemplate, init_db


# ═══════════════════════════════════════════════════════════════
# BLOCK DEFINITIONS PER TEMPLATE
# ═══════════════════════════════════════════════════════════════

CONVERSIONS = [
    # ── Welcome Series ──────────────────────────────────────
    {
        "name": "Welcome — Brand Intro + 5% Off",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Welcome to LDAS Electronics, {{first_name}}!",
                "subheadline": "Canada's trusted source for trucking electronics",
                "bg_color": "linear-gradient(135deg, #cffafe 0%, #a5f3fc 100%)",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "We're thrilled to have you. LDAS Electronics is Canada's trusted source for Bluetooth speakers, headsets, dash cams, and everyday electronics -- built for quality, priced for value.",
                "As a welcome gift, here's 5% off your first order:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "WELCOME5",
                "value_display": "5% Off",
                "display_text": "Your first order",
                "expires_text": "No minimum purchase",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Browse our store and find something you'll love:",
                "Questions? Just reply to this email -- we're real people and we love helping.",
            ]}},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Welcome — Bestsellers Showcase",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Our Customers' Top Picks",
                "subheadline": "Products people keep coming back for",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, here are the products people keep coming back for:",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Bestsellers",
                "columns": 2,
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Every product comes with free Canadian shipping on orders over $50.",
            ]}},
            {"block_type": "cta", "content": {
                "text": "Browse All Products",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Welcome — Social Proof",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Why Thousands Trust LDAS",
                "subheadline": "Real customers, real stories",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, don't just take our word for it -- hear from real customers:",
                '"Best Bluetooth speaker I\'ve owned. Survived a drop off my truck and still sounds perfect." -- Mike R., Ontario',
                '"The dash cam paid for itself the first month. Crystal clear footage, even at night." -- Sarah T., Alberta',
            ]}},
            {"block_type": "text", "content": {"paragraphs": [
                "What sets us apart: Canadian-owned, shipping from Ontario. 30-day hassle-free returns. Real human support. Products tested by real truckers and tradespeople.",
            ]}},
            {"block_type": "cta", "content": {
                "text": "Shop With Confidence",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Welcome — Last Chance 5% Off",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Your 5% Off Expires Soon",
                "subheadline": "Don't miss your welcome discount",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, just a friendly heads up -- your welcome discount is about to expire.",
                "If there's something you've been eyeing, now's the time:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "WELCOME5",
                "value_display": "5% Off",
                "display_text": "Use it before it's gone",
                "expires_text": "Expiring soon",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Remember: free shipping on orders over $50, and every order comes with our 30-day return guarantee.",
            ]}},
            {"block_type": "cta", "content": {
                "text": "Use My Discount",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },

    # ── Checkout Abandoned ──────────────────────────────────
    {
        "name": "Checkout Abandoned — Reminder",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "You Left Something Behind",
                "subheadline": "Your cart is waiting for you",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, looks like you started checking out but didn't finish. No worries -- your items are still waiting.",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Your Cart Items",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Complete Your Order",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Checkout Abandoned — Urgency",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Still Thinking It Over?",
                "subheadline": "Your cart items are selling fast",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, the items in your cart are popular and stock moves fast.",
                "We'd hate for you to miss out.",
            ]}},
            {"block_type": "urgency", "content": {
                "message": "These items are in high demand -- they may sell out soon!",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "Still In Your Cart",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Complete Your Order",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Checkout Abandoned — 10% Recovery",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Here's 10% Off to Seal the Deal",
                "subheadline": "We really want you to have these",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, we noticed you haven't completed your order yet.",
                "To make it a no-brainer, here's an exclusive 10% discount:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10",
                "value_display": "10% Off",
                "display_text": "Your abandoned cart",
                "expires_text": "Expires in 48 hours",
            }},
            {"block_type": "urgency", "content": {
                "message": "This code expires in 48 hours!",
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },

    # ── Post-Purchase ───────────────────────────────────────
    {
        "name": "Post-Purchase — Thank You",
        "family": "post_purchase",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Thanks for Your Order, {{first_name}}!",
                "subheadline": "We're packing it up now",
                "bg_color": "linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%)",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Your order is confirmed and we're getting it ready to ship. You'll receive a tracking number as soon as it's on its way.",
                "While you wait, here are some products that pair perfectly with your purchase:",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "You Might Also Like",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Track My Order",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Post-Purchase — Review Request",
        "family": "post_purchase",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "How's Your Purchase, {{first_name}}?",
                "subheadline": "We'd love to hear your thoughts",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "You've had your order for a bit now -- how's everything working out?",
                "Your review helps other customers make confident decisions, and it only takes a minute.",
            ]}},
            {"block_type": "cta", "content": {
                "text": "Leave a Review",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Post-Purchase — Loyalty Discount",
        "family": "post_purchase",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "A Special Thank You, {{first_name}}",
                "subheadline": "Loyalty deserves a reward",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "You're now part of the LDAS family, and we want to show our appreciation.",
                "Here's an exclusive loyalty discount for your next order:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "LOYAL10",
                "value_display": "10% Off",
                "display_text": "Your next order",
                "expires_text": "Valid for 30 days",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "New Arrivals",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Shop With My Discount",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },

    # ── Win-Back ────────────────────────────────────────────
    {
        "name": "Win-Back — We Miss You",
        "family": "winback",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "We Miss You, {{first_name}}!",
                "subheadline": "It's been a while since your last visit",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, it's been a while! We've added tons of new products since your last visit.",
                "Here's what's new:",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "New Since You Left",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "See What's New",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Win-Back — 10% Comeback Offer",
        "family": "winback",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Come Back for 10% Off",
                "subheadline": "An exclusive offer just for you",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, we'd love to see you back. Here's a special comeback offer:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "COMEBACK10",
                "value_display": "10% Off",
                "display_text": "Welcome back offer",
                "expires_text": "Expires in 7 days",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "Popular Right Now",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Shop With My Discount",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Win-Back — Final Push 15% Off",
        "family": "winback",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Last Chance: 15% Off Everything",
                "subheadline": "Our biggest offer -- just for you",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, this is our best offer yet. We really want you back.",
            ]}},
            {"block_type": "discount", "content": {
                "code": "COMEBACK15",
                "value_display": "15% Off",
                "display_text": "Everything in store",
                "expires_text": "48 hours only",
            }},
            {"block_type": "urgency", "content": {
                "message": "This is our final offer -- 15% off expires in 48 hours!",
            }},
            {"block_type": "cta", "content": {
                "text": "Claim 15% Off Now",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },

    # ── Browse Abandonment ──────────────────────────────────
    {
        "name": "Browse Abandon — Product Reminder",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Still Interested, {{first_name}}?",
                "subheadline": "The products you were browsing",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, we noticed you were checking out some great products. Here's a quick reminder:",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Products You Viewed",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Continue Shopping",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
    {
        "name": "Browse Abandon — Social Proof",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Others Are Loving These Products",
                "subheadline": "See what customers are saying",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, the products you were browsing have great reviews from other customers.",
                "Don't miss out -- these are some of our most popular items.",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Trending Products",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
                "color": "#063cff",
            }},
        ],
    },
]


def convert_all_seed_templates():
    """Convert all 15 seed templates to block format. Idempotent."""
    db.connect(reuse_if_open=True)
    converted = 0
    skipped = 0

    for conv in CONVERSIONS:
        name = conv["name"]
        try:
            tpl = EmailTemplate.get(EmailTemplate.name == name)
        except EmailTemplate.DoesNotExist:
            print("  [skip] Template not found: %s" % name)
            skipped += 1
            continue

        if tpl.template_format == "blocks":
            print("  [skip] Already blocks: %s" % name)
            skipped += 1
            continue

        tpl.template_format = "blocks"
        tpl.blocks_json = json.dumps(conv["blocks"])
        tpl.template_family = conv["family"]
        tpl.save()
        converted += 1
        print("  [converted] %s -> family=%s, %d blocks" % (name, conv["family"], len(conv["blocks"])))

    print("\nDone: %d converted, %d skipped" % (converted, skipped))
    return converted


def validate_all_conversions():
    """Validate all converted templates. Returns True if all pass."""
    from block_registry import validate_template
    db.connect(reuse_if_open=True)
    all_ok = True

    for conv in CONVERSIONS:
        name = conv["name"]
        blocks_json = json.dumps(conv["blocks"])
        family = conv["family"]
        warnings = validate_template(blocks_json, family=family)
        errors = [w for w in warnings if w.get("level") == "error"]
        if errors:
            print("  [FAIL] %s: %s" % (name, "; ".join(e["message"] for e in errors)))
            all_ok = False
        else:
            warns = [w for w in warnings if w.get("level") == "warning"]
            if warns:
                print("  [WARN] %s: %s" % (name, "; ".join(w["message"] for w in warns)))
            else:
                print("  [OK] %s" % name)

    return all_ok


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    init_db()

    print("=== Validating block definitions ===")
    valid = validate_all_conversions()

    if valid:
        print("\n=== Converting templates ===")
        convert_all_seed_templates()
    else:
        print("\n[ERROR] Validation failed — fix errors before converting.")
        sys.exit(1)
