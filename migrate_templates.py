"""
Migrate all 18 production templates from html to blocks format.
Run on VPS: python migrate_templates.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env", override=True)

from database import *
init_db()

# G10 product data (real Shopify CDN)
G10 = {
    "title": "LDAS G10 Bluetooth Headset",
    "image_url": "https://cdn.shopify.com/s/files/1/0690/4076/7280/files/Mainimage4.png?v=1762714055",
    "price": "65.99",
    "product_url": "https://ldas.ca/products/ldas-bluetooth-headset-g10",
    "short_description": "40-hour battery, dual-mic noise cancellation for drivers.",
}

# TH11 product data (real Shopify CDN)
TH11 = {
    "title": "LDAS Trucker Bluetooth Headset TH11",
    "image_url": "https://cdn.shopify.com/s/files/1/0690/4076/7280/files/Image_for_comparision_table.png?v=1764968886",
    "price": "89.99",
    "product_url": "https://ldas.ca/products/ldas-trucker-bluetooth-headset-th11",
    "short_description": "36-hour battery, dual-mic noise cancellation built for truckers.",
}

LDAS_URL = "https://ldas.ca"
BLUE = "#063cff"

# Map of template_id -> (family, blocks)
TEMPLATES = {
    # ── Welcome Series ──
    1: ("welcome", [
        {"block_type": "hero", "content": {"headline": "Welcome to LDAS", "subheadline": "The G10 brings crystal clear calls to every drive"}},
        {"block_type": "text", "content": {"paragraphs": ["Welcome to LDAS. Canadian-built gear for drivers who demand reliability.", "Your G10 delivers 40 hours of talk time and elite noise cancellation. Stay connected from Alberta to Ontario."]}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Featured Product"}},
        {"block_type": "driver_testimonial", "content": {"quote": "Survived brutal Alberta winter. G10 never missed a frame.", "author_name": "Chris B.", "author_title": "Long-haul driver, Calgary", "product": "G10"}},
        {"block_type": "cta", "content": {"text": "Get the G10", "url": LDAS_URL + "/products/ldas-bluetooth-headset-g10", "color": BLUE, "secondary_text": "Browse All Gear", "secondary_url": LDAS_URL + "/collections/all"}},
    ]),
    4: ("welcome", [
        {"block_type": "hero", "content": {"headline": "Welcome to LDAS", "subheadline": "Canadian-built Bluetooth gear for the road"}},
        {"block_type": "text", "content": {"paragraphs": ["Thanks for joining the LDAS family. We build headsets that survive Canadian highways.", "Here's 5% off your first order to get started."]}},
        {"block_type": "discount", "content": {"code": "", "value_display": "", "display_text": "", "expires_text": ""}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Our Bestseller"}},
        {"block_type": "cta", "content": {"text": "Shop Now", "url": LDAS_URL, "color": BLUE, "secondary_text": "View All Products", "secondary_url": LDAS_URL + "/collections/all"}},
    ]),
    5: ("welcome", [
        {"block_type": "hero", "content": {"headline": "Meet Our Bestsellers", "subheadline": "Trusted by truckers across Canada"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Top Pick: G10"}},
        {"block_type": "product_hero", "content": {**TH11, "section_title": "Pro Choice: TH11"}},
        {"block_type": "driver_testimonial", "content": {"quote": "Crystal-clear calls on Teams and Zoom. The TH11 handles everything.", "author_name": "A.J.", "author_title": "Project Manager, Vancouver", "product": "TH11"}},
        {"block_type": "cta", "content": {"text": "Browse Collection", "url": LDAS_URL + "/collections/all", "color": BLUE}},
    ]),
    6: ("welcome", [
        {"block_type": "hero", "content": {"headline": "Why Drivers Choose LDAS", "subheadline": "Real reviews from real Canadian drivers"}},
        {"block_type": "driver_testimonial", "content": {"quote": "Battery Life is Great and Sound quality is excellent. The TH11 lasts me full shift and then some.", "author_name": "Gary", "author_title": "Canada", "product": "TH11"}},
        {"block_type": "driver_testimonial", "content": {"quote": "These are the best headsets I have used for trucking. Clear sound, comfortable fit.", "author_name": "Laurie Hegg", "author_title": "Long-Haul Trucker, Texas", "product": "TH11"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Join 5,000+ Drivers"}},
        {"block_type": "cta", "content": {"text": "Shop LDAS", "url": LDAS_URL, "color": BLUE, "secondary_text": "Read More Reviews", "secondary_url": LDAS_URL + "/blogs/news"}},
    ]),
    7: ("welcome", [
        {"block_type": "hero", "content": {"headline": "Last Chance: 5% Off", "subheadline": "Your welcome offer expires soon"}},
        {"block_type": "text", "content": {"paragraphs": ["We noticed you haven't used your welcome discount yet.", "Don't miss out on 5% off any LDAS product. This is your last reminder."]}},
        {"block_type": "discount", "content": {"code": "", "value_display": "", "display_text": "", "expires_text": ""}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Most Popular"}},
        {"block_type": "urgency", "content": {"message": "Offer expires at midnight. Don't miss out."}},
        {"block_type": "cta", "content": {"text": "Claim Your Discount", "url": LDAS_URL, "color": BLUE}},
    ]),

    # ── Promotional ──
    2: ("promotional", [
        {"block_type": "hero", "content": {"headline": "G10. Cut The Noise.", "subheadline": "40-hour battery. ENC dual-mic clarity."}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Featured Product"}},
        {"block_type": "feature_highlights", "content": {"items": ["Qualcomm 3020 Bluetooth 5.0 chip", "ENC dual microphones silence road noise", "Forty hour talk time with case", "Triple charging options for any trip", "Built tough for Canadian highways"], "section_title": "Why G10 Wins", "icon_type": "check", "columns": 2}},
        {"block_type": "urgency", "content": {"message": "Sale ends tonight. Secure your G10 now."}},
        {"block_type": "cta", "content": {"text": "Get the G10", "url": LDAS_URL + "/products/ldas-bluetooth-headset-g10", "color": BLUE, "secondary_text": "View Specs", "secondary_url": LDAS_URL + "/products/ldas-bluetooth-headset-g10"}},
    ]),

    # ── Checkout Abandoned ──
    8: ("checkout_recovery", [
        {"block_type": "hero", "content": {"headline": "You're Almost There", "subheadline": "Your order is waiting to ship"}},
        {"block_type": "text", "content": {"paragraphs": ["Looks like you left something behind at checkout.", "Your items are reserved but won't last forever."]}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "In Your Cart"}},
        {"block_type": "trust_reassurance", "content": {"items": [{"icon": "package", "text": "Free Shipping on $50+"}, {"icon": "shield", "text": "30-Day Easy Returns"}, {"icon": "maple", "text": "Canadian Owned & Operated"}, {"icon": "star", "text": "4.8/5 Customer Rating"}]}},
        {"block_type": "cta", "content": {"text": "Complete Checkout", "url": LDAS_URL + "/checkout", "color": BLUE}},
    ]),
    9: ("checkout_recovery", [
        {"block_type": "hero", "content": {"headline": "Don't Miss Out", "subheadline": "Your cart expires soon"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Your Cart"}},
        {"block_type": "urgency", "content": {"message": "Stock is limited. Complete your order before it sells out."}},
        {"block_type": "trust_reassurance", "content": {"items": [{"icon": "truck", "text": "Express Shipping Canada Wide"}, {"icon": "shield", "text": "30 Day Money Back Guarantee"}, {"icon": "package", "text": "Free Shipping On Orders $50+"}, {"icon": "maple", "text": "Proudly Canadian Owned"}]}},
        {"block_type": "cta", "content": {"text": "Complete Your Order", "url": LDAS_URL + "/checkout", "color": BLUE}},
    ]),
    10: ("checkout_recovery", [
        {"block_type": "hero", "content": {"headline": "Here's 10% Off", "subheadline": "We saved your cart and sweetened the deal"}},
        {"block_type": "discount", "content": {"code": "SAVE10", "value_display": "10% OFF", "display_text": "Complete your order today", "expires_text": "24 hours only"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Still In Your Cart"}},
        {"block_type": "trust_reassurance", "content": {"items": [{"icon": "package", "text": "Free Shipping on $50+"}, {"icon": "shield", "text": "30-Day Returns"}, {"icon": "maple", "text": "Canadian Owned"}, {"icon": "star", "text": "4.8/5 Rating"}]}},
        {"block_type": "cta", "content": {"text": "Claim 10% Off", "url": LDAS_URL + "/checkout", "color": BLUE}},
    ]),

    # ── Post-Purchase ──
    11: ("post_purchase", [
        {"block_type": "hero", "content": {"headline": "Thanks for Your Order!", "subheadline": "Your LDAS gear is on its way"}},
        {"block_type": "text", "content": {"paragraphs": ["We're packing your order now. You'll get a tracking email within 24 hours.", "In the meantime, here's how to get the most from your new headset."]}},
        {"block_type": "feature_highlights", "content": {"items": ["Charge fully before first use (2 hours)", "Download the LDAS app for firmware updates", "Pair via Bluetooth Settings on your phone", "Adjust ENC levels in the app for best calls"], "section_title": "Quick Start Tips", "icon_type": "arrow", "columns": 1}},
        {"block_type": "cta", "content": {"text": "Track Your Order", "url": LDAS_URL + "/account", "color": BLUE, "secondary_text": "Visit Support", "secondary_url": LDAS_URL + "/pages/support"}},
    ]),
    12: ("post_purchase", [
        {"block_type": "hero", "content": {"headline": "How's Your LDAS Gear?", "subheadline": "We'd love to hear from you"}},
        {"block_type": "text", "content": {"paragraphs": ["You've had your headset for a few days now. How's it working on the road?", "A quick review helps other drivers make the right choice."]}},
        {"block_type": "driver_testimonial", "content": {"quote": "I was skeptical at the price point but the G10 outperforms my old Plantronics.", "author_name": "Mike D.", "author_title": "Owner-operator, Manitoba", "product": "G10"}},
        {"block_type": "cta", "content": {"text": "Leave a Review", "url": "https://www.amazon.ca/review/create-review?asin=B0BRJX5PFM", "color": BLUE, "secondary_text": "Shop More Gear", "secondary_url": LDAS_URL + "/collections/all"}},
    ]),
    13: ("post_purchase", [
        {"block_type": "hero", "content": {"headline": "You've Earned This", "subheadline": "A thank-you discount just for you"}},
        {"block_type": "text", "content": {"paragraphs": ["As a valued LDAS customer, here's an exclusive loyalty discount on your next order.", "Whether you need a backup headset or want to try the TH11, this one's for you."]}},
        {"block_type": "discount", "content": {"code": "LOYAL10", "value_display": "10% OFF", "display_text": "Your loyalty reward", "expires_text": "30 days to use"}},
        {"block_type": "product_hero", "content": {**TH11, "section_title": "Upgrade to TH11"}},
        {"block_type": "cta", "content": {"text": "Shop with Discount", "url": LDAS_URL, "color": BLUE}},
    ]),

    # ── Win-Back ──
    3: ("winback", [
        {"block_type": "hero", "content": {"headline": "Come Back To Quality", "subheadline": "Pro headsets without the premium price"}},
        {"block_type": "text", "content": {"paragraphs": ["You tried others. Canada chooses LDAS.", "The TH11 delivers 36-hour battery and dual-mic noise cancellation. Built for the road."]}},
        {"block_type": "discount", "content": {"code": "RETURN20", "value_display": "SAVE 20%", "display_text": "Ready to roll again?", "expires_text": "72 hours only. Single use only"}},
        {"block_type": "product_hero", "content": {**TH11, "section_title": "Featured Product"}},
        {"block_type": "urgency", "content": {"message": "Last chance. Offer expires at midnight."}},
        {"block_type": "cta", "content": {"text": "Shop LDAS", "url": LDAS_URL, "color": BLUE, "secondary_text": "View Deals", "secondary_url": LDAS_URL + "/collections/all"}},
    ]),
    14: ("winback", [
        {"block_type": "hero", "content": {"headline": "We Miss You", "subheadline": "It's been a while since your last order"}},
        {"block_type": "text", "content": {"paragraphs": ["A lot has changed at LDAS since you last visited.", "We've launched new products, improved battery life, and upgraded noise cancellation across the line."]}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "New: G10 Headset"}},
        {"block_type": "driver_testimonial", "content": {"quote": "Upgraded from my old V5.2 to the G10. Night and day difference on calls.", "author_name": "Steve R.", "author_title": "Fleet manager, Toronto", "product": "G10"}},
        {"block_type": "cta", "content": {"text": "See What's New", "url": LDAS_URL + "/collections/all", "color": BLUE}},
    ]),
    15: ("winback", [
        {"block_type": "hero", "content": {"headline": "10% Off to Come Back", "subheadline": "We want to earn your business again"}},
        {"block_type": "discount", "content": {"code": "COMEBACK10", "value_display": "10% OFF", "display_text": "Your comeback offer", "expires_text": "5 days to use"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Our Bestseller"}},
        {"block_type": "trust_reassurance", "content": {"items": [{"icon": "package", "text": "Free Shipping on $50+"}, {"icon": "shield", "text": "30-Day Returns"}, {"icon": "maple", "text": "Canadian Owned"}, {"icon": "star", "text": "4.8/5 Rating"}]}},
        {"block_type": "cta", "content": {"text": "Claim 10% Off", "url": LDAS_URL, "color": BLUE}},
    ]),
    16: ("winback", [
        {"block_type": "hero", "content": {"headline": "Final Offer: 15% Off", "subheadline": "This is your last chance"}},
        {"block_type": "text", "content": {"paragraphs": ["We've reached out a few times but haven't heard back.", "This is our best and final offer — 15% off anything in the store."]}},
        {"block_type": "discount", "content": {"code": "FINAL15", "value_display": "15% OFF", "display_text": "Last chance", "expires_text": "Expires in 48 hours"}},
        {"block_type": "product_hero", "content": {**TH11, "section_title": "Top Pick"}},
        {"block_type": "urgency", "content": {"message": "This offer won't be sent again."}},
        {"block_type": "cta", "content": {"text": "Save 15% Now", "url": LDAS_URL, "color": BLUE}},
    ]),

    # ── Browse Abandon ──
    17: ("browse_abandon", [
        {"block_type": "hero", "content": {"headline": "Still Thinking It Over?", "subheadline": "The product you viewed is still available"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "You Were Looking At"}},
        {"block_type": "feature_highlights", "content": {"items": ["40-hour battery life with charging case", "Qualcomm Bluetooth 5.0 chip", "Dual-mic ENC noise cancellation", "3 charging options (USB-C, case, car)"], "section_title": "G10 Highlights", "icon_type": "check", "columns": 2}},
        {"block_type": "cta", "content": {"text": "View Product", "url": LDAS_URL + "/products/ldas-bluetooth-headset-g10", "color": BLUE, "secondary_text": "Browse All", "secondary_url": LDAS_URL + "/collections/all"}},
    ]),
    18: ("browse_abandon", [
        {"block_type": "hero", "content": {"headline": "Drivers Love This One", "subheadline": "See why the G10 is our #1 seller"}},
        {"block_type": "product_hero", "content": {**G10, "section_title": "Trending Now"}},
        {"block_type": "driver_testimonial", "content": {"quote": "Best headset I've owned. Battery lasts my whole shift and calls are crystal clear.", "author_name": "Gary", "author_title": "Canada", "product": "G10"}},
        {"block_type": "trust_reassurance", "content": {"items": [{"icon": "package", "text": "Free Shipping on $50+"}, {"icon": "shield", "text": "30-Day Money Back"}, {"icon": "maple", "text": "Canadian Company"}, {"icon": "star", "text": "4.8/5 Stars"}]}},
        {"block_type": "cta", "content": {"text": "Get the G10", "url": LDAS_URL + "/products/ldas-bluetooth-headset-g10", "color": BLUE}},
    ]),
}


def main():
    updated = 0
    for tid, (family, blocks) in TEMPLATES.items():
        try:
            t = EmailTemplate.get_by_id(tid)
        except EmailTemplate.DoesNotExist:
            print("  SKIP [%d] not found" % tid)
            continue

        t.template_format = "blocks"
        t.blocks_json = json.dumps(blocks)
        t.template_family = family
        t.save()
        updated += 1
        print("  OK [%d] %s -> %s (%d blocks)" % (tid, t.name, family, len(blocks)))

    print("\nUpdated %d / %d templates" % (updated, len(TEMPLATES)))


if __name__ == "__main__":
    main()
