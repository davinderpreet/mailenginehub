"""
convert_templates.py — One-time migration: convert 15 seed templates to block format.

Reads each seed template by name, builds an equivalent blocks_json array,
sets template_format='blocks' and template_family. Keeps html_body intact as fallback.

Idempotent — skips templates already in blocks format (unless --force).

Usage:
    python convert_templates.py           # Convert only html-format templates
    python convert_templates.py --force   # Re-convert ALL templates (resets blocks)
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
                "headline": "Welcome to the Family, {{first_name}}",
                "subheadline": "Canada's trusted source for trucking electronics",
            }},
            {"block_type": "text", "content": {
                "section_header": "Why LDAS?",
                "paragraphs": [
                    "We build rugged electronics for people who work hard -- Bluetooth speakers, dash cams, headsets, and everyday tech that survives the road.",
                    "As a welcome gift, here's 5% off your first order:",
                ],
            }},
            {"block_type": "discount", "content": {
                "code": "WELCOME5",
                "value_display": "5% Off",
                "display_text": "Your first order",
                "expires_text": "No minimum purchase",
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "Why LDAS",
                "icon_type": "check",
                "columns": 1,
                "items": [
                    "Rugged electronics built for the road",
                    "Canadian-owned, ships from Ontario",
                    "24/7 customer support",
                ],
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "shipping", "text": "Free Shipping Over $50"},
                    {"icon": "returns", "text": "30-Day Hassle-Free Returns"},
                    {"icon": "rating", "text": "4.8/5 Customer Rating"},
                    {"icon": "canadian", "text": "Canadian-Owned & Operated"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Welcome — Bestsellers Showcase",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Our Top Picks for You, {{first_name}}",
                "subheadline": "The products customers keep coming back for",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Here are the products our customers can't stop buying. See what all the buzz is about:",
            ]}},
            {"block_type": "product_hero", "content": {
                "section_title": "Customer Favourite",
                "cta_text": "Shop Now",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "More Bestsellers",
                "columns": 2,
            }},
            {"block_type": "driver_testimonial", "content": {
                "quote": "Best Bluetooth speaker I've owned. Survived months on the road without a scratch.",
                "author_name": "Mike R.",
                "author_role": "Long-haul driver",
                "rating": 5,
                "section_title": "What Customers Say",
            }},
            {"block_type": "cta", "content": {
                "text": "Browse All Products",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Welcome — Social Proof",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Thousands Trust LDAS, {{first_name}}",
                "subheadline": "Real customers, real results",
            }},
            {"block_type": "text", "content": {
                "section_header": "What Customers Say",
                "paragraphs": [
                    "Our products are tested by truckers, tradespeople, and everyday Canadians who need gear that works.",
                ],
            }},
            {"block_type": "why_choose_this", "content": {
                "section_title": "Why Customers Choose LDAS",
                "items": [
                    "Built tough for Canadian conditions",
                    "Fast, free shipping on orders over $50",
                    "30-day hassle-free returns on everything",
                ],
                "icon_style": "check",
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "rating", "text": "4.8/5 from 2,000+ Reviews"},
                    {"icon": "returns", "text": "30-Day Money-Back Guarantee"},
                    {"icon": "shipping", "text": "Free Canadian Shipping $50+"},
                    {"icon": "canadian", "text": "Canadian-Owned Since Day One"},
                ],
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "Top-Rated Products",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Shop With Confidence",
                "url": "https://ldas.ca",
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
                "Hey {{first_name}}, your welcome discount is about to expire. If you've been eyeing something, now's the time.",
            ]}},
            {"block_type": "discount", "content": {
                "code": "WELCOME5",
                "value_display": "5% Off",
                "display_text": "Use it before it's gone",
                "expires_text": "Expiring soon",
            }},
            {"block_type": "urgency", "content": {
                "message": "This welcome offer expires soon -- don't miss out!",
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "What You're Missing",
                "icon_type": "check",
                "columns": 1,
                "items": [
                    "Premium Bluetooth speakers and dash cams",
                    "Free shipping on orders over $50",
                    "4.8/5 average customer rating",
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Use My Discount",
                "url": "https://ldas.ca",
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
                "subheadline": "Your cart is saved and waiting",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, looks like you started checking out but didn't finish. No worries -- your items are still here.",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Your Cart Items",
                "columns": 2,
            }},
            {"block_type": "objection_handling", "content": {
                "section_title": "Quick Answers",
                "style": "qa",
                "items": [
                    {"objection": "What if it doesn't work for me?", "answer": "30-day hassle-free returns, no questions asked."},
                    {"objection": "How fast is shipping?", "answer": "Most orders arrive in 3-5 business days across Canada."},
                    {"objection": "Is my payment secure?", "answer": "256-bit SSL encryption on every transaction."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Checkout Abandoned — Urgency",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Still Thinking It Over?",
                "subheadline": "These items are selling fast",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, the items in your cart are popular and stock moves fast. We'd hate for you to miss out.",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Still In Your Cart",
                "columns": 2,
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "Why Buy from LDAS",
                "icon_type": "check",
                "columns": 1,
                "items": [
                    "Fast shipping -- most orders arrive in 3-5 days",
                    "30-day return guarantee",
                    "Secure, encrypted checkout",
                ],
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "shipping", "text": "Free Shipping Over $50"},
                    {"icon": "returns", "text": "30-Day Returns"},
                    {"icon": "rating", "text": "Secure Checkout"},
                    {"icon": "canadian", "text": "Ships from Ontario"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Checkout Abandoned — 10% Recovery",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Here's 10% Off Your Cart",
                "subheadline": "We really want you to have these",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, we noticed you haven't finished your order. Here's an exclusive 10% discount to make it easy:",
            ]}},
            {"block_type": "discount", "content": {
                "code": "SAVE10",
                "value_display": "10% Off",
                "display_text": "Your abandoned cart",
                "expires_text": "Expires in 48 hours",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "Your Cart Items",
                "columns": 2,
            }},
            {"block_type": "faq", "content": {
                "section_title": "Common Questions",
                "items": [
                    {"question": "Can I return it?", "answer": "Yes -- 30-day hassle-free returns on every order."},
                    {"question": "When will it arrive?", "answer": "Most Canadian orders arrive in 3-5 business days."},
                    {"question": "Is the discount stackable?", "answer": "This code works on any cart over $25."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca",
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
                "bg_color": "linear-gradient(135deg, #059669 0%, #047857 100%)",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Your order is confirmed and on its way soon. You'll get a tracking number once it ships.",
                "In the meantime, check out products that pair perfectly with your purchase:",
            ]}},
            {"block_type": "product_hero", "content": {
                "section_title": "Recommended for You",
                "cta_text": "Shop Now",
            }},
            {"block_type": "bundle_value", "content": {
                "section_title": "Pairs Perfectly With Your Order",
                "items": [],
                "bundle_price": "0.00",
                "savings_text": "Bundle & Save",
                "cta_text": "Shop the Bundle",
                "cta_url": "https://ldas.ca",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "You Might Also Like",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Track My Order",
                "url": "https://ldas.ca",
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
                "You've had your order for a bit now -- how's everything working out? Your review helps other customers make confident decisions.",
            ]}},
            {"block_type": "driver_testimonial", "content": {
                "quote": "Solid build quality. My LDAS speaker has been through rain, dust, and highway vibration -- still sounds great.",
                "author_name": "Jason T.",
                "author_role": "Fleet owner",
                "rating": 5,
                "section_title": "Others Love Their Purchase",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "You Might Also Like",
                "columns": 2,
            }},
            {"block_type": "cta", "content": {
                "text": "Leave a Review",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Post-Purchase — Loyalty Discount",
        "family": "post_purchase",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "A Thank You Gift, {{first_name}}",
                "subheadline": "Loyalty deserves a reward",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "You're part of the LDAS family now. Here's an exclusive loyalty discount on your next order:",
            ]}},
            {"block_type": "comparison_block", "content": {
                "section_title": "Recommended for You",
                "columns": 2,
            }},
            {"block_type": "discount", "content": {
                "code": "LOYAL10",
                "value_display": "10% Off",
                "display_text": "Your next order",
                "expires_text": "Valid for 30 days",
            }},
            {"block_type": "cta", "content": {
                "text": "Shop With My Discount",
                "url": "https://ldas.ca",
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
                "subheadline": "A lot has changed since your last visit",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, it's been a while! We've added great new products since you last visited.",
            ]}},
            {"block_type": "product_hero", "content": {
                "section_title": "New Arrival",
                "cta_text": "Shop Now",
            }},
            {"block_type": "best_seller_proof", "content": {
                "section_title": "What's Popular Right Now",
                "proof_line": "Our top sellers this month",
                "badge_text": "Best Seller",
                "show_rating": True,
            }},
            {"block_type": "cta", "content": {
                "text": "See What's New",
                "url": "https://ldas.ca",
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
            {"block_type": "driver_testimonial", "content": {
                "quote": "Came back after a year away and their new lineup is even better. The dash cam quality blew me away.",
                "author_name": "Sarah K.",
                "author_role": "Verified buyer",
                "rating": 5,
                "section_title": "Why Customers Come Back",
            }},
            {"block_type": "cta", "content": {
                "text": "Shop With My Discount",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Win-Back — Final Push 15% Off",
        "family": "winback",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Last Chance: 15% Off Everything",
                "subheadline": "Our biggest offer, just for you",
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
            {"block_type": "product_grid", "content": {
                "section_title": "Top Picks for You",
                "columns": 2,
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "What's Improved Since Your Last Visit",
                "icon_type": "check",
                "columns": 1,
                "items": [
                    "Expanded product lineup with new categories",
                    "Faster shipping -- most orders in 3-5 days",
                    "Even better return policy -- 30 days, no hassle",
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Claim 15% Off Now",
                "url": "https://ldas.ca",
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
            {"block_type": "product_hero", "content": {
                "section_title": "You Were Looking At",
                "cta_text": "View Product",
            }},
            {"block_type": "comparison_block", "content": {
                "section_title": "Similar Products",
                "columns": 2,
            }},
            {"block_type": "why_choose_this", "content": {
                "section_title": "Why LDAS Is the Right Choice",
                "items": [
                    "Built tough for Canadian conditions",
                    "Free shipping on orders over $50",
                    "30-day hassle-free returns",
                ],
                "icon_style": "check",
            }},
            {"block_type": "cta", "content": {
                "text": "Continue Shopping",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Browse Abandon — Social Proof",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Others Love These Products Too",
                "subheadline": "See what's trending right now",
            }},
            {"block_type": "text", "content": {"paragraphs": [
                "Hey {{first_name}}, the products you were browsing are customer favourites. Here's why people love them:",
            ]}},
            {"block_type": "product_grid", "content": {
                "section_title": "Trending Products",
                "columns": 2,
            }},
            {"block_type": "best_seller_proof", "content": {
                "section_title": "Customer Favourites",
                "proof_line": "Thousands of happy Canadian customers",
                "badge_text": "Top Rated",
                "show_rating": True,
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "rating", "text": "4.8/5 Average Rating"},
                    {"icon": "returns", "text": "30-Day Guarantee"},
                    {"icon": "shipping", "text": "Fast Canadian Shipping"},
                    {"icon": "canadian", "text": "Canadian-Owned"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
            }},
        ],
    },

    # ── Cart Abandonment (dedicated) ─────────────────────
    {
        "name": "Cart Abandon — Reassurance",
        "family": "cart_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Your Cart Is Waiting, {{first_name}}",
                "subheadline": "Everything is saved and ready to go",
            }},
            {"block_type": "product_grid", "content": {
                "section_title": "Still in Your Cart",
                "columns": 2,
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "returns", "text": "30-Day Hassle-Free Returns"},
                    {"icon": "shipping", "text": "Free Shipping Over $50"},
                    {"icon": "rating", "text": "Secure Checkout"},
                    {"icon": "canadian", "text": "Ships from Ontario"},
                ],
            }},
            {"block_type": "objection_handling", "content": {
                "section_title": "Still Have Questions?",
                "style": "qa",
                "items": [
                    {"objection": "What if it doesn't work for me?", "answer": "Easy 30-day returns, no questions asked."},
                    {"objection": "Is shipping really free?", "answer": "Free on all orders over $50 across Canada."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Cart Abandon — Urgency + FAQ",
        "family": "cart_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Don't Miss Out, {{first_name}}",
                "subheadline": "Your cart items are in demand",
            }},
            {"block_type": "product_hero", "content": {
                "section_title": "Your Top Pick",
                "cta_text": "View in Cart",
            }},
            {"block_type": "urgency", "content": {
                "message": "Cart items aren't reserved -- popular products can sell out!",
            }},
            {"block_type": "faq", "content": {
                "section_title": "Quick Answers",
                "items": [
                    {"question": "Can I return it?", "answer": "Yes -- 30-day easy returns on everything."},
                    {"question": "How fast does it ship?", "answer": "Most orders arrive in 3-5 business days."},
                    {"question": "Is my info safe?", "answer": "256-bit SSL encryption on every order."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Return to My Cart",
                "url": "https://ldas.ca",
            }},
        ],
    },
]


def convert_all_seed_templates(force=False):
    """Convert all 15 seed templates to block format.

    Args:
        force: If True, re-convert templates already in blocks format.
    """
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

        if tpl.template_format == "blocks" and not force:
            print("  [skip] Already blocks: %s" % name)
            skipped += 1
            continue

        # Force mode: reset to html first so we can re-convert
        if force and tpl.template_format == "blocks":
            tpl.template_format = "html"

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

    force = "--force" in sys.argv

    print("=== Validating block definitions ===")
    valid = validate_all_conversions()

    if valid:
        print("\n=== Converting templates%s ===" % (" (FORCE)" if force else ""))
        convert_all_seed_templates(force=force)
    else:
        print("\n[ERROR] Validation failed — fix errors before converting.")
        sys.exit(1)
