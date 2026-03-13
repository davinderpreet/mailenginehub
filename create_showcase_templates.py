"""
create_showcase_templates.py -- Create 8 module showcase templates in the DB.
Run on VPS after deploying updated block_registry.py.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db, EmailTemplate, init_db
init_db()

SHOWCASE_TEMPLATES = [
    {
        "name": "Module: Driver Testimonial",
        "subject": "What Truckers Say About LDAS",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Trusted by Truckers Across Canada",
                "subheadline": "Real reviews from real drivers",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Our products are built for the road. But don't take our word for it -- hear from drivers who use LDAS gear every day."],
            }},
            {"block_type": "driver_testimonial", "content": {
                "quote": "Bought the G3 for my truck. Crystal clear calls, 20+ hours easy. Best headset I've owned.",
                "author_name": "Mike R.",
                "author_role": "OTR driver, Alberta",
                "rating": 5,
                "product_name": "LDAS G3 Headset",
                "section_title": "What Customers Say",
            }},
            {"block_type": "driver_testimonial", "content": {
                "quote": "Compared three headsets before buying this one. Won on comfort and battery life.",
                "author_name": "Sarah K.",
                "author_role": "Fleet dispatcher, Ontario",
                "rating": 5,
                "section_title": "Another Happy Customer",
            }},
            {"block_type": "cta", "content": {
                "text": "Browse Bestsellers",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Product Comparison",
        "subject": "Which Headset Is Right for You?",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Still Deciding, {{first_name}}?",
                "subheadline": "Compare your options side by side",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Not sure which product is right for you? Here's a quick comparison to help you decide."],
            }},
            {"block_type": "comparison", "content": {
                "section_title": "Which One's Right for You?",
                "columns": 2,
                "highlight_index": 0,
                "cta_text": "View Details",
            }},
            {"block_type": "cta", "content": {
                "text": "See All Products",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Why Choose This",
        "subject": "Why Truckers Choose the G3",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Built for the Long Haul",
                "subheadline": "See why the G3 is our #1 seller",
            }},
            {"block_type": "product_hero", "content": {
                "section_title": "Featured Product",
                "cta_text": "Shop Now",
            }},
            {"block_type": "why_choose_this", "content": {
                "section_title": "Why You'll Love It",
                "items": [
                    "24-hour battery -- a full shift without charging",
                    "Dual-mic noise cancelling for highway calls",
                    "Pairs with 2 devices at once",
                    "30-day return guarantee if it's not right",
                ],
                "icon_style": "check",
            }},
            {"block_type": "cta", "content": {
                "text": "Add to Cart",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Objection Handling",
        "subject": "Quick Answers Before You Buy",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Almost There, {{first_name}}",
                "subheadline": "Your order is saved and waiting",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["We know buying online can feel uncertain. Here are answers to the most common questions."],
            }},
            {"block_type": "objection_handling", "content": {
                "section_title": "Before You Decide",
                "style": "qa",
                "items": [
                    {"objection": "Is my payment info safe?", "answer": "256-bit encrypted checkout. We never store card details."},
                    {"objection": "What if the total was higher than expected?", "answer": "Free shipping on orders over $50. No hidden fees, ever."},
                    {"objection": "What if I change my mind?", "answer": "30-day hassle-free returns. We even cover return shipping."},
                ],
            }},
            {"block_type": "objection_handling", "content": {
                "section_title": "Common Concerns",
                "style": "statement",
                "items": [
                    {"objection": "It might not work for me", "answer": "30-day returns. Zero risk."},
                    {"objection": "Shipping takes forever", "answer": "3-5 days. Ships from Ontario."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete My Order",
                "url": "https://ldas.ca/cart",
            }},
        ],
    },
    {
        "name": "Module: Trust Reassurance (Variants)",
        "subject": "Shop With Confidence",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Welcome to LDAS, {{first_name}}",
                "subheadline": "Canada's trusted electronics for the road",
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "package", "text": "Free Shipping Over $50"},
                    {"icon": "shield", "text": "30-Day Hassle-Free Returns"},
                    {"icon": "star", "text": "4.8/5 from 2,000+ Reviews"},
                    {"icon": "maple", "text": "Canadian-Owned & Operated"},
                ],
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Your purchase is protected:"],
            }},
            {"block_type": "trust_reassurance", "content": {
                "items": [
                    {"icon": "lock", "text": "Encrypted Secure Checkout"},
                    {"icon": "shield", "text": "30-Day Money-Back Guarantee"},
                    {"icon": "heart", "text": "All Major Cards Accepted"},
                    {"icon": "package", "text": "Free Shipping Over $50"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Start Shopping",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Module: Bundle Value",
        "subject": "Better Together -- Save on a Bundle",
        "family": "post_purchase",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Complete Your Setup, {{first_name}}",
                "subheadline": "These products pair perfectly together",
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Customers who bought your product also grabbed these. Bundle them and save."],
            }},
            {"block_type": "bundle_value", "content": {
                "section_title": "Better Together",
                "bundle_price": "149.99",
                "savings_text": "Save $20 on the set",
                "cta_text": "Shop the Bundle",
                "cta_url": "https://ldas.ca/collections/all",
                "items": [],
            }},
            {"block_type": "cta", "content": {
                "text": "See All Accessories",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Best Seller Proof",
        "subject": "Our Most Popular Products Right Now",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "What's Trending, {{first_name}}",
                "subheadline": "See what customers are buying right now",
            }},
            {"block_type": "best_seller_proof", "content": {
                "section_title": "Customer Favourites",
                "badge_text": "Best Seller",
                "proof_line": "200+ sold this month",
                "show_rating": True,
            }},
            {"block_type": "cta", "content": {
                "text": "Browse All Products",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Feature Highlights",
        "subject": "Why LDAS -- Built for the Road",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Why Choose LDAS, {{first_name}}",
                "subheadline": "Gear that works as hard as you do",
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "Why LDAS",
                "icon_type": "check",
                "columns": 1,
                "items": [
                    "Free shipping on orders over $50",
                    "30-day hassle-free returns",
                    "Ships fast from Ontario",
                    "4.8/5 from 2,000+ reviews",
                    "Canadian-owned since day one",
                ],
            }},
            {"block_type": "text", "content": {
                "paragraphs": ["Here's what's under the hood:"],
            }},
            {"block_type": "feature_highlights", "content": {
                "section_title": "G3 Headset Specs",
                "icon_type": "arrow",
                "columns": 2,
                "items": [
                    "24-hour talk time",
                    "CVC 8.0 noise cancelling",
                    "Bluetooth 5.2 dual connect",
                    "40g ultralight build",
                    "USB-C fast charge",
                    "IPX4 sweat resistant",
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "See All Products",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    # ---- Content-Rich Module Showcases ----
    {
        "name": "Module: Competitor Comparison",
        "subject": "See How LDAS Stacks Up",
        "family": "high_intent_browse",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "LDAS vs. The Competition",
                "subheadline": "See why truckers choose LDAS Electronics",
            }},
            {"block_type": "competitor_comparison", "content": {
                "competitors": ["Generic Brand"],
                "section_title": "How We Compare",
                "rows": [
                    {"feature": "Battery Life", "ldas": True, "competitors": [False]},
                    {"feature": "Dual-Mic Noise Cancelling", "ldas": True, "competitors": [False]},
                    {"feature": "2-Year Warranty", "ldas": True, "competitors": [False]},
                    {"feature": "Ships from Canada", "ldas": True, "competitors": [False]},
                    {"feature": "30-Day Free Returns", "ldas": True, "competitors": [False]},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Shop LDAS",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Spec Table",
        "subject": "G3 Headset — Full Specs",
        "family": "high_intent_browse",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "G3 Pro Headset Specs",
                "subheadline": "Everything you need to know before you buy",
            }},
            {"block_type": "spec_table", "content": {
                "section_title": "Technical Specifications",
                "product_name": "G3 Pro Bluetooth Headset",
                "rows": [
                    {"label": "Battery Life", "value": "24 hours talk time"},
                    {"label": "Bluetooth", "value": "5.2 dual connect"},
                    {"label": "Noise Cancelling", "value": "CVC 8.0 dual-mic"},
                    {"label": "Weight", "value": "40g ultralight"},
                    {"label": "Charging", "value": "USB-C fast charge"},
                    {"label": "Water Resistance", "value": "IPX4 rated"},
                    {"label": "Range", "value": "10m / 33ft"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "View Product",
                "url": "https://ldas.ca/products/g3-headset",
            }},
        ],
    },
    {
        "name": "Module: Stat Callout",
        "subject": "LDAS By the Numbers",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Why Truckers Trust LDAS",
                "subheadline": "The numbers speak for themselves",
            }},
            {"block_type": "stat_callout", "content": {
                "stats": [
                    {"value": "2,000+", "label": "Five-Star Reviews"},
                    {"value": "24hrs", "label": "Battery Life"},
                    {"value": "30-Day", "label": "Free Returns"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Shop Now",
                "url": "https://ldas.ca",
            }},
        ],
    },
    {
        "name": "Module: What's Included",
        "subject": "Everything in the Box",
        "family": "high_intent_browse",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "What's in the Box",
                "subheadline": "Everything you need to get started",
            }},
            {"block_type": "whats_included", "content": {
                "section_title": "Your G3 Kit Includes",
                "items": [
                    "G3 Pro Bluetooth Headset",
                    "USB-C charging cable",
                    "3 ear tip sizes (S/M/L)",
                    "Carrying pouch",
                    "Quick start guide",
                    "2-year warranty card",
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Order Yours",
                "url": "https://ldas.ca/products/g3-headset",
            }},
        ],
    },
    {
        "name": "Module: FAQ",
        "subject": "Your Questions, Answered",
        "family": "checkout_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Got Questions?",
                "subheadline": "Here are the answers our customers ask most",
            }},
            {"block_type": "faq", "content": {
                "section_title": "Common Questions",
                "items": [
                    {"question": "How long does shipping take?", "answer": "Most orders arrive in 3-5 business days. We ship from Ontario."},
                    {"question": "What if it doesn't fit?", "answer": "30-day hassle-free returns. We even cover return shipping."},
                    {"question": "Is my payment secure?", "answer": "256-bit encrypted checkout. We never store your card details."},
                    {"question": "Do you ship to the US?", "answer": "Yes! We ship across Canada and the continental United States."},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Complete Your Order",
                "url": "https://ldas.ca/cart",
            }},
        ],
    },
    {
        "name": "Module: Use Case Match",
        "subject": "Find Your Perfect Match",
        "family": "browse_recovery",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Which One Fits Your Life?",
                "subheadline": "We have the right gear for every driver",
            }},
            {"block_type": "use_case_match", "content": {
                "section_title": "Find Your Match",
                "cases": [
                    {"persona": "Long-Haul Trucker", "description": "24-hour battery, noise cancelling for highway calls, lightweight all-day comfort", "product_name": "G3 Pro Headset", "cta_url": "https://ldas.ca/products/g3-headset"},
                    {"persona": "Fleet Manager", "description": "Dual-device connect, crystal clear calls, durable build for daily use", "product_name": "TH11 Headset", "cta_url": "https://ldas.ca/products/th11-headset"},
                    {"persona": "Owner-Operator", "description": "Night vision dash cam, loop recording, installs in 5 minutes", "product_name": "DC200 Dash Cam", "cta_url": "https://ldas.ca/products/dc200-dashcam"},
                ],
            }},
            {"block_type": "cta", "content": {
                "text": "Browse All Products",
                "url": "https://ldas.ca/collections/all",
            }},
        ],
    },
    {
        "name": "Module: Brand Story",
        "subject": "The LDAS Story",
        "family": "welcome",
        "blocks": [
            {"block_type": "hero", "content": {
                "headline": "Our Story",
                "subheadline": "Canadian-owned. Trucker-tested. Built for the road.",
            }},
            {"block_type": "brand_story", "content": {
                "headline": "Built for Canadian Truckers",
                "body": "LDAS Electronics started with one goal: give truckers gear that actually lasts. Every product we sell is tested on the road by real drivers before it hits our shelves. We ship fast from Ontario, stand behind everything with 30-day returns, and our support team knows trucking because they live it.",
                "variant": "mission",
                "cta_text": "See Our Products",
                "cta_url": "https://ldas.ca",
            }},
            {"block_type": "brand_story", "content": {
                "headline": "Gear That Goes the Distance",
                "body": "From Bluetooth headsets built for 24-hour shifts to dash cams that capture every mile, our products are designed for life on the road. Over 2,000 five-star reviews from drivers across Canada and the US.",
                "variant": "heritage",
            }},
            {"block_type": "cta", "content": {
                "text": "Shop LDAS",
                "url": "https://ldas.ca",
            }},
        ],
    },
]


def create_showcase_templates():
    db.connect(reuse_if_open=True)
    created = 0
    updated = 0

    for tmpl in SHOWCASE_TEMPLATES:
        name = tmpl["name"]
        blocks_json = json.dumps(tmpl["blocks"])
        try:
            existing = EmailTemplate.get(EmailTemplate.name == name)
            existing.subject = tmpl["subject"]
            existing.template_format = "blocks"
            existing.blocks_json = blocks_json
            existing.template_family = tmpl["family"]
            existing.save()
            updated += 1
            print("  [updated] %s (%d blocks)" % (name, len(tmpl["blocks"])))
        except EmailTemplate.DoesNotExist:
            EmailTemplate.create(
                name=name,
                subject=tmpl["subject"],
                template_format="blocks",
                blocks_json=blocks_json,
                template_family=tmpl["family"],
                html_body="",
                preview_text="Module showcase template",
            )
            created += 1
            print("  [created] %s (%d blocks)" % (name, len(tmpl["blocks"])))

    print("\nDone: %d created, %d updated" % (created, updated))


if __name__ == "__main__":
    create_showcase_templates()
