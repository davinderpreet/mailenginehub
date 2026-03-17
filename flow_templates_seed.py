"""
flow_templates_seed.py — Production Automation Flows + Email Templates

Creates 5 multi-step flows with 20 professional body-only email templates
(shell_version=1, wrapped by email_shell.py at send time).

All flows created DISABLED — user enables after review.

Flow design: aggressive touch-point cadence, front-loaded during peak interest.
- Welcome: 5 steps over 8 days (front-loaded first 36h)
- Checkout Recovery: 4 steps over 3 days (fast escalation)
- Post-Purchase: 4 steps over 21 days (tighter repeat window)
- Win-Back: 4 steps over 14 days (trigger at 60 days, not 90)
- Browse Abandonment: 3 steps over 3 days (with incentive)

Usage on VPS:
    cd /var/www/mailengine
    source venv/bin/activate
    python3 -c "from flow_templates_seed import seed_production_flows; seed_production_flows()"
"""

from database import db, Flow, FlowStep, EmailTemplate
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
# BRAND CONSTANTS (match email_shell.py)
# ═══════════════════════════════════════════════════════════════
BRAND_COLOR = "#063cff"
BRAND_DARK  = "#0532d4"
TEXT_DARK    = "#1a1a2e"
TEXT_MID     = "#4a5568"
TEXT_LIGHT   = "#718096"
BRAND_URL    = "https://ldas.ca"


# ═══════════════════════════════════════════════════════════════
# REUSABLE HTML SNIPPETS (body-only — no <html>, <head>, <body>)
# ═══════════════════════════════════════════════════════════════

def _heading(text):
    return f'<h2 style="margin:0 0 12px;font-size:22px;font-weight:700;color:{TEXT_DARK};">{text}</h2>'

def _para(text):
    return f'<p style="margin:0 0 14px;font-size:15px;color:{TEXT_MID};line-height:1.7;">{text}</p>'

def _button(text, url=BRAND_URL):
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 0;">
<a href="{url}" style="display:inline-block;background:{BRAND_COLOR};color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:10px;font-weight:700;font-size:15px;">{text}</a>
</td></tr></table>'''

def _divider():
    return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:8px 0;"><hr style="border:none;border-top:1px solid #eeeef2;margin:0;" /></td></tr></table>'

def _discount_box(code, description):
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:16px 0;">
<div style="background:#f0f4ff;border:2px dashed {BRAND_COLOR};border-radius:12px;padding:24px;text-align:center;">
  <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:2px;color:{BRAND_COLOR};text-transform:uppercase;">YOUR CODE</p>
  <p style="margin:0 0 8px;font-size:32px;font-weight:900;color:{TEXT_DARK};letter-spacing:3px;">{code}</p>
  <p style="margin:0;font-size:14px;color:{TEXT_MID};">{description}</p>
</div>
</td></tr></table>'''

def _product_row(title, desc, img_placeholder=""):
    """Simple product highlight row."""
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding:12px 0;border-bottom:1px solid #f1f5f9;">
  <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:{TEXT_DARK};">{title}</p>
  <p style="margin:0;font-size:13px;color:{TEXT_MID};">{desc}</p>
</td></tr></table>'''


# ═══════════════════════════════════════════════════════════════
# 20 EMAIL TEMPLATES (body-only <tr> blocks)
# ═══════════════════════════════════════════════════════════════

TEMPLATES = [
    # ────────────────────────────────────────────
    # FLOW 1: Welcome Series (5 steps)
    # ────────────────────────────────────────────
    {
        "name": "Welcome — Brand Intro + 5% Off",
        "subject": "Welcome to LDAS Electronics, {{first_name}}!",
        "preview_text": "Thanks for joining — here's 5% off your first order.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Welcome to LDAS Electronics, {{{{first_name}}}}!")}
  {_para("We're thrilled to have you. LDAS Electronics is Canada's trusted source for Bluetooth speakers, headsets, dash cams, and everyday electronics — built for quality, priced for value.")}
  {_para("As a welcome gift, here's <strong>5% off</strong> your first order:")}
  {_discount_box("WELCOME5", "5% off your first order — no minimum")}
  {_para("Browse our store and find something you'll love:")}
  {_button("Shop Now")}
  {_para("Questions? Just reply to this email — we're real people and we love helping.")}
  {_para("Cheers,<br/><strong>The LDAS Team</strong>")}
</td></tr>'''
    },
    {
        "name": "Welcome — Bestsellers Showcase",
        "subject": "Our bestsellers — picked for you, {{first_name}}",
        "preview_text": "These are the products our customers love most.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Our Customers' Top Picks")}
  {_para("Hey {{{{first_name}}}}, here are the products people keep coming back for:")}
  {_product_row("Bluetooth Speakers", "Crystal-clear sound, rugged build, all-day battery. Perfect for the jobsite or the backyard.")}
  {_product_row("Dash Cameras", "HD recording, night vision, loop recording. Protect yourself on the road.")}
  {_product_row("Wireless Headsets", "Noise-cancelling, comfortable all day, Bluetooth 5.0. Ideal for truckers and commuters.")}
  {_product_row("LED Work Lights", "Bright, durable, and energy-efficient. See clearly in any condition.")}
  {_para("Every product comes with free Canadian shipping on orders over $50.")}
  {_button("Browse All Products")}
</td></tr>'''
    },
    {
        "name": "Welcome — Social Proof",
        "subject": "Why truckers choose LDAS, {{first_name}}",
        "preview_text": "Real customers, real stories.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Why Thousands Trust LDAS")}
  {_para("Hey {{{{first_name}}}}, don't just take our word for it — hear from real customers:")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="padding:16px 20px;background:#f8f9ff;border-left:4px solid {BRAND_COLOR};border-radius:0 8px 8px 0;margin-bottom:12px;">
      <p style="margin:0 0 6px;font-size:14px;color:{TEXT_DARK};font-style:italic;">"Best Bluetooth speaker I've owned. Survived a drop off my truck and still sounds perfect."</p>
      <p style="margin:0;font-size:12px;color:{TEXT_LIGHT};font-weight:600;">— Mike R., Ontario</p>
    </td></tr>
  </table>
  <br/>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="padding:16px 20px;background:#f8f9ff;border-left:4px solid {BRAND_COLOR};border-radius:0 8px 8px 0;">
      <p style="margin:0 0 6px;font-size:14px;color:{TEXT_DARK};font-style:italic;">"The dash cam paid for itself the first month. Crystal clear footage, even at night."</p>
      <p style="margin:0;font-size:12px;color:{TEXT_LIGHT};font-weight:600;">— Sarah T., Alberta</p>
    </td></tr>
  </table>
  {_divider()}
  {_para("<strong>What sets us apart:</strong>")}
  {_para("&bull; Canadian-owned, shipping from Ontario<br/>&bull; 30-day hassle-free returns<br/>&bull; Real human support (we answer within hours)<br/>&bull; Products tested by real truckers and tradespeople")}
  {_button("Shop With Confidence")}
</td></tr>'''
    },
    {
        "name": "Welcome — Last Chance 5% Off",
        "subject": "Last chance for 5% off, {{first_name}}",
        "preview_text": "Your welcome discount expires soon.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Your 5% Off Expires Soon")}
  {_para("Hey {{{{first_name}}}}, just a friendly heads up — your welcome discount is about to expire.")}
  {_para("If there's something you've been eyeing, now's the time:")}
  {_discount_box("WELCOME5", "5% off — use it before it's gone")}
  {_para("Remember: free shipping on orders over $50, and every order comes with our 30-day return guarantee.")}
  {_button("Use My Discount")}
  {_para("After this, you'll still get our best deals and new product alerts — but this specific code won't last forever.")}
  {_para("— The LDAS Team")}
</td></tr>'''
    },

    {
        "name": "Welcome — Free Shipping Nudge",
        "subject": "Free shipping on us, {{first_name}}",
        "preview_text": "No minimum — free shipping on your first order.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("One Last Thing, {{{{first_name}}}}")}
  {_para("We know picking the right gear takes time. So here's something to make it easier — <strong>free shipping on your next order</strong>, no minimum.")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:16px 0;">
  <div style="background:#f0f4ff;border:2px dashed {BRAND_COLOR};border-radius:12px;padding:24px;text-align:center;">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:2px;color:{BRAND_COLOR};text-transform:uppercase;">FREE SHIPPING</p>
    <p style="margin:0 0 8px;font-size:24px;font-weight:900;color:{TEXT_DARK};">No minimum order</p>
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">Just shop and we'll cover the shipping — valid 7 days</p>
  </div>
  </td></tr></table>
  {_para("Our most popular picks ship same day from Ontario:")}
  {_product_row("Bluetooth Speakers", "Starting at $29.99 — rugged, loud, and built to last.")}
  {_product_row("Dash Cameras", "Starting at $49.99 — HD recording, night vision, peace of mind.")}
  {_button("Shop Now — Free Shipping")}
  {_para("This is our way of saying we'd really love to earn your first order.")}
  {_para("— The LDAS Team")}
</td></tr>'''
    },

    # ────────────────────────────────────────────
    # FLOW 2: Abandoned Checkout Recovery (4 steps)
    # ────────────────────────────────────────────
    {
        "name": "Checkout Abandoned — Reminder",
        "subject": "You left something behind, {{first_name}}",
        "preview_text": "Your cart is waiting for you.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("You Left Something Behind")}
  {_para("Hey {{{{first_name}}}}, looks like you started checking out but didn't finish. No worries — your items are still waiting.")}
  {_para("Here's what's in your cart:")}
  <div style="background:#f8f9ff;border-radius:10px;padding:16px 20px;margin:16px 0;">
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">{{{{cart_items}}}}</p>
  </div>
  {_button("Complete Your Order", "{{checkout_url}}")}
  {_para("Questions about a product? Just reply to this email — we're happy to help.")}
</td></tr>'''
    },
    {
        "name": "Checkout Abandoned — Urgency",
        "subject": "Still thinking it over, {{first_name}}?",
        "preview_text": "Your cart items are selling fast.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Still Thinking It Over?")}
  {_para("Hey {{{{first_name}}}}, your items are still in your cart — but we can't guarantee they'll stay in stock.")}
  <div style="background:#f8f9ff;border-radius:10px;padding:16px 20px;margin:16px 0;">
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">{{{{cart_items}}}}</p>
  </div>
  {_para("<strong>Reminder:</strong> All orders ship free across Canada on orders over $50. Plus, our 30-day return policy means zero risk.")}
  {_button("Complete Your Order", "{{checkout_url}}")}
  {_para("If something held you up — shipping cost, product questions, or anything else — just reply and we'll sort it out.")}
</td></tr>'''
    },
    {
        "name": "Checkout Abandoned — 10% Recovery",
        "subject": "Here's 10% off to complete your order, {{first_name}}",
        "preview_text": "We saved your cart + added a discount.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("We Saved Your Cart")}
  {_para("Hey {{{{first_name}}}}, we really want you to love what you picked out. Here's <strong>10% off</strong> to make it easier:")}
  {_discount_box("SAVE10", "10% off your order — 48 hours only")}
  <div style="background:#f8f9ff;border-radius:10px;padding:16px 20px;margin:16px 0;">
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">{{{{cart_items}}}}</p>
  </div>
  {_button("Complete Order with 10% Off", "{{checkout_url}}")}
  {_para("This code expires in 48 hours. After that, your cart items may sell out.")}
</td></tr>'''
    },

    {
        "name": "Checkout Abandoned — Final Scarcity",
        "subject": "Your cart is about to expire, {{first_name}}",
        "preview_text": "Last chance — we can't hold your items much longer.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Your Cart Expires Tonight")}
  {_para("Hey {{{{first_name}}}}, this is our last reminder. We've been holding your items, but we can't guarantee availability much longer.")}
  <div style="background:#f8f9ff;border-radius:10px;padding:16px 20px;margin:16px 0;">
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">{{{{cart_items}}}}</p>
  </div>
  {_para("Your <strong>10% off code</strong> is still active — but not for long:")}
  {_discount_box("SAVE10", "10% off — expires tonight")}
  {_button("Complete Your Order Now", "{{checkout_url}}")}
  {_divider()}
  {_para("<strong>Still on the fence?</strong> Here's what other customers say:")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="padding:16px 20px;background:#f8f9ff;border-left:4px solid {BRAND_COLOR};border-radius:0 8px 8px 0;">
      <p style="margin:0 0 6px;font-size:14px;color:{TEXT_DARK};font-style:italic;">"Ordered on a Monday, arrived Wednesday. Great quality — wish I hadn't waited so long."</p>
      <p style="margin:0;font-size:12px;color:{TEXT_LIGHT};font-weight:600;">— Verified LDAS Customer</p>
    </td></tr>
  </table>
  {_para("30-day hassle-free returns. No risk — just reply if you need help.")}
</td></tr>'''
    },

    # ────────────────────────────────────────────
    # FLOW 3: Post-Purchase Follow-Up (4 steps)
    # ────────────────────────────────────────────
    {
        "name": "Post-Purchase — Thank You",
        "subject": "Thanks for your order, {{first_name}}!",
        "preview_text": "Your order is on its way + care tips inside.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Thank You for Your Order!")}
  {_para("Hey {{{{first_name}}}}, thanks for shopping with LDAS Electronics! Your order is being prepared and will be on its way soon.")}
  {_divider()}
  {_heading("Quick Product Care Tips")}
  {_para("<strong>Bluetooth Devices:</strong> Fully charge before first use. Keep firmware updated for best performance.")}
  {_para("<strong>Dash Cameras:</strong> Format your SD card monthly for reliable recording. Mount away from direct sunlight.")}
  {_para("<strong>Speakers:</strong> Avoid prolonged exposure to water even if water-resistant. Store in a cool, dry place.")}
  {_divider()}
  {_para("Need help with setup or have questions? Reply to this email — our team typically responds within a few hours.")}
  {_button("Track Your Order")}
</td></tr>'''
    },
    {
        "name": "Post-Purchase — Review Request",
        "subject": "How's your new gear, {{first_name}}?",
        "preview_text": "We'd love to hear what you think.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("How's Everything Going?")}
  {_para("Hey {{{{first_name}}}}, you've had your order for a couple of weeks now — we hope you're loving it!")}
  {_para("If you have a moment, we'd really appreciate a quick review. It helps other shoppers make confident decisions and helps us keep improving.")}
  {_button("Leave a Review", f"{BRAND_URL}/pages/reviews")}
  {_divider()}
  {_heading("You Might Also Like")}
  {_para("Based on what our customers pair together, here are a few recommendations:")}
  {_product_row("Phone Mounts & Holders", "Keep your device secure on the road. Fits most dashboards.")}
  {_product_row("Charging Cables & Adapters", "Fast-charge compatible, braided for durability.")}
  {_product_row("Protective Cases & Covers", "Keep your gear safe from drops and scratches.")}
  {_button("Browse Accessories")}
</td></tr>'''
    },
    {
        "name": "Post-Purchase — Loyalty Discount",
        "subject": "A special thank you, {{first_name}}",
        "preview_text": "You've earned an exclusive loyalty discount.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("You've Earned a Reward")}
  {_para("Hey {{{{first_name}}}}, it's been a month since your order, and we hope your products are serving you well.")}
  {_para("As a thank you for being a valued customer, here's an exclusive loyalty discount:")}
  {_discount_box("LOYAL10", "10% off your next order — just for you")}
  {_para("This code is exclusive to returning customers and doesn't expire for 30 days.")}
  {_para("We're always adding new products — here's a sneak peek at what's new:")}
  {_button("See What's New")}
  {_para("Thank you for choosing LDAS Electronics. We genuinely appreciate your business.")}
  {_para("— The LDAS Team")}
</td></tr>'''
    },

    {
        "name": "Post-Purchase — Cross-Sell Accessories",
        "subject": "Upgrade your setup, {{first_name}}",
        "preview_text": "Top accessories that pair perfectly with your gear.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Complete Your Setup")}
  {_para("Hey {{{{first_name}}}}, now that you've had some time with your gear, here are the accessories our customers buy most to get even more out of their electronics:")}
  {_product_row("Heavy-Duty Phone Mounts", "Vibration-proof, fits any dashboard. Perfect for truckers and commuters.")}
  {_product_row("Fast-Charge Car Adapters", "Dual USB-C, 45W output. Charge your devices at full speed on the road.")}
  {_product_row("Premium Carrying Cases", "Hardshell protection for speakers, cameras, and headsets.")}
  {_product_row("Extra-Long Braided Cables", "6ft USB-C and Lightning — built to last through daily use.")}
  {_divider()}
  {_para("As a returning customer, you always get <strong>free shipping on orders over $50</strong>.")}
  {_button("Browse Accessories")}
  {_para("See something you like? Reply to this email and we'll help you pick the right fit.")}
</td></tr>'''
    },

    # ────────────────────────────────────────────
    # FLOW 4: Win-Back Lapsed (4 steps)
    # ────────────────────────────────────────────
    {
        "name": "Win-Back — We Miss You",
        "subject": "We miss you, {{first_name}}!",
        "preview_text": "It's been a while — here's what's new.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("We Miss You, {{{{first_name}}}}!")}
  {_para("It's been a while since your last visit, and a lot has changed at LDAS Electronics. We've been busy adding new products, improving quality, and making your shopping experience even better.")}
  {_divider()}
  {_heading("What's New")}
  {_para("&bull; <strong>New Bluetooth 5.3 speakers</strong> — even better sound, longer battery life<br/>&bull; <strong>4K dash cameras</strong> — ultra-sharp footage day and night<br/>&bull; <strong>Expanded accessory line</strong> — mounts, cables, cases, and more<br/>&bull; <strong>Faster shipping</strong> — most orders ship same day")}
  {_button("See What's New")}
  {_para("We'd love to see you back. If there's anything we can help with, just reply.")}
</td></tr>'''
    },
    {
        "name": "Win-Back — 10% Comeback Offer",
        "subject": "Here's 10% off to come back, {{first_name}}",
        "preview_text": "We've missed you — here's a comeback offer.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("We Want You Back")}
  {_para("Hey {{{{first_name}}}}, we noticed you haven't shopped with us in a while. We get it — life gets busy. But we'd love to have you back.")}
  {_para("Here's a little incentive:")}
  {_discount_box("COMEBACK10", "10% off your next order")}
  {_para("This code is valid for 14 days. Use it on anything in our store — no minimum order.")}
  {_para("Plus, remember: free shipping on orders over $50 and hassle-free 30-day returns.")}
  {_button("Redeem My Discount")}
</td></tr>'''
    },
    {
        "name": "Win-Back — Final Push 15% Off",
        "subject": "Last chance: 15% off expires in 48h, {{first_name}}",
        "preview_text": "Final offer — 15% off before it's gone.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Last Chance — 15% Off")}
  {_para("Hey {{{{first_name}}}}, this is our final nudge. We've been saving a spot for you, and this is our best offer:")}
  {_discount_box("LASTCHANCE15", "15% off everything — expires in 48 hours")}
  {_para("This is the biggest discount we offer. After 48 hours, this code is gone for good.")}
  {_para("<strong>What are you waiting for?</strong>")}
  {_button("Shop Now — 15% Off")}
  {_para("If you're no longer interested in hearing from us, we understand. You can unsubscribe using the link below — no hard feelings.")}
</td></tr>'''
    },

    {
        "name": "Win-Back — Free Shipping Last Chance",
        "subject": "Free shipping — no strings, {{first_name}}",
        "preview_text": "We'll cover shipping on your next order. No minimum.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Different Offer, Same Love")}
  {_para("Hey {{{{first_name}}}}, we get it — discounts aren't for everyone. So instead, here's something simpler:")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:16px 0;">
  <div style="background:#f0f4ff;border:2px dashed {BRAND_COLOR};border-radius:12px;padding:24px;text-align:center;">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:2px;color:{BRAND_COLOR};text-transform:uppercase;">FREE SHIPPING</p>
    <p style="margin:0 0 8px;font-size:24px;font-weight:900;color:{TEXT_DARK};">On your next order</p>
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">No minimum — no code needed — just shop</p>
  </div>
  </td></tr></table>
  {_para("We've added a lot since you last visited:")}
  {_product_row("New 4K Dash Cameras", "Ultra-sharp footage, GPS logging, parking mode. Starting at $59.99.")}
  {_product_row("Bluetooth 5.3 Speakers", "Longer battery, deeper bass, rugged waterproof build.")}
  {_button("Shop Now — Free Shipping")}
  {_para("This is our last email about coming back. If you'd rather not hear from us, no hard feelings — unsubscribe below.")}
</td></tr>'''
    },

    # ────────────────────────────────────────────
    # FLOW 5: Browse Abandonment (3 steps)
    # ────────────────────────────────────────────
    {
        "name": "Browse Abandon — Product Reminder",
        "subject": "Still thinking about it, {{first_name}}?",
        "preview_text": "The product you were looking at is still available.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Still Thinking About It?")}
  {_para("Hey {{{{first_name}}}}, we noticed you were browsing our store recently. Sometimes it helps to sleep on it — but we wanted to make sure you didn't forget.")}
  {_para("The product you were looking at is still available and ready to ship.")}
  {_button("Continue Shopping")}
  {_divider()}
  {_para("<strong>Why shop with LDAS?</strong>")}
  {_para("&bull; Free Canadian shipping on orders over $50<br/>&bull; 30-day hassle-free returns<br/>&bull; Real human support — just reply to this email<br/>&bull; Trusted by thousands of Canadian customers")}
</td></tr>'''
    },
    {
        "name": "Browse Abandon — Social Proof",
        "subject": "Popular choice — don't miss out, {{first_name}}",
        "preview_text": "Other customers are loving this product.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("Popular Choice")}
  {_para("Hey {{{{first_name}}}}, the product you were looking at is one of our most popular items. Here's why customers love it:")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="padding:16px 20px;background:#f8f9ff;border-left:4px solid {BRAND_COLOR};border-radius:0 8px 8px 0;">
      <p style="margin:0 0 6px;font-size:14px;color:{TEXT_DARK};font-style:italic;">"Exactly what I needed. Great quality and fast shipping to my door."</p>
      <p style="margin:0;font-size:12px;color:{TEXT_LIGHT};font-weight:600;">— Verified LDAS Customer</p>
    </td></tr>
  </table>
  <br/>
  {_para("Don't wait too long — popular items can sell out.")}
  {_button("Get It Before It's Gone")}
  {_para("Remember: 30-day returns, so there's zero risk in trying it out.")}
</td></tr>'''
    },
    {
        "name": "Browse Abandon — Free Shipping Push",
        "subject": "Free shipping on the item you were eyeing, {{first_name}}",
        "preview_text": "We'll ship it free — no minimum required.",
        "html_body": f'''<tr><td style="padding:28px 30px;">
  {_heading("We'll Ship It Free")}
  {_para("Hey {{{{first_name}}}}, still thinking about that product you checked out? Here's a nudge — <strong>free shipping, no minimum</strong>.")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:16px 0;">
  <div style="background:#f0f4ff;border:2px dashed {BRAND_COLOR};border-radius:12px;padding:24px;text-align:center;">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:2px;color:{BRAND_COLOR};text-transform:uppercase;">FREE SHIPPING</p>
    <p style="margin:0 0 8px;font-size:24px;font-weight:900;color:{TEXT_DARK};">On any order</p>
    <p style="margin:0;font-size:14px;color:{TEXT_MID};">No minimum — ships from Ontario — valid 48 hours</p>
  </div>
  </td></tr></table>
  {_button("Continue Shopping — Free Shipping")}
  {_para("30-day hassle-free returns. If it's not right, send it back — on us.")}
  {_para("— The LDAS Team")}
</td></tr>'''
    },
]


# ═══════════════════════════════════════════════════════════════
# FLOW DEFINITIONS (5 flows with step configs)
# ═══════════════════════════════════════════════════════════════

FLOWS = [
    {
        "name": "Welcome Series",
        "description": "5-step welcome sequence for new subscribers. Front-loaded: brand intro + discount (0h), bestsellers (12h), social proof (36h), discount urgency (72h), free shipping nudge (192h).",
        "trigger_type": "contact_created",
        "trigger_value": "",
        "steps": [
            {"template": "Welcome — Brand Intro + 5% Off",     "delay_hours": 0},     # Immediate
            {"template": "Welcome — Bestsellers Showcase",     "delay_hours": 12},    # 12h — strike while hot
            {"template": "Welcome — Social Proof",             "delay_hours": 36},    # 36h — still in first 2 days
            {"template": "Welcome — Last Chance 5% Off",       "delay_hours": 72},    # 3 days — urgency close
            {"template": "Welcome — Free Shipping Nudge",      "delay_hours": 192},   # 8 days — different angle for holdouts
        ]
    },
    {
        "name": "Abandoned Checkout Recovery",
        "description": "4-step checkout recovery. Fast escalation: reminder (1h), urgency (6h), 10% off (24h), final scarcity (72h). Exits on purchase.",
        "trigger_type": "checkout_abandoned",
        "trigger_value": "",
        "steps": [
            {"template": "Checkout Abandoned — Reminder",       "delay_hours": 1},    # 1h — quick nudge
            {"template": "Checkout Abandoned — Urgency",        "delay_hours": 6},    # 6h — same-day pressure
            {"template": "Checkout Abandoned — 10% Recovery",   "delay_hours": 24},   # 24h — discount while warm
            {"template": "Checkout Abandoned — Final Scarcity", "delay_hours": 72},   # 72h — last attempt
        ]
    },
    {
        "name": "Post-Purchase Follow-Up",
        "description": "4-step post-purchase nurture. Tighter repeat window: thank you (48h), review + recs (7d), loyalty discount (14d), cross-sell accessories (21d).",
        "trigger_type": "order_placed",
        "trigger_value": "",
        "steps": [
            {"template": "Post-Purchase — Thank You",              "delay_hours": 48},    # 2 days — excitement fresh
            {"template": "Post-Purchase — Review Request",         "delay_hours": 168},   # 7 days — product in hand
            {"template": "Post-Purchase — Loyalty Discount",       "delay_hours": 336},   # 14 days — repeat window
            {"template": "Post-Purchase — Cross-Sell Accessories", "delay_hours": 504},   # 21 days — accessories push
        ]
    },
    {
        "name": "Win-Back Lapsed Customers",
        "description": "4-step win-back for customers inactive 60+ days. Faster cadence: what's new (0h), 10% off (4d), 15% off (8d), free shipping (14d).",
        "trigger_type": "no_purchase_days",
        "trigger_value": "60",
        "steps": [
            {"template": "Win-Back — We Miss You",                 "delay_hours": 0},     # Immediate
            {"template": "Win-Back — 10% Comeback Offer",          "delay_hours": 96},    # 4 days — faster escalation
            {"template": "Win-Back — Final Push 15% Off",          "delay_hours": 192},   # 8 days — stronger push
            {"template": "Win-Back — Free Shipping Last Chance",   "delay_hours": 336},   # 14 days — different angle
        ]
    },
    {
        "name": "Browse Abandonment",
        "description": "3-step browse abandonment. Product reminder (2h), social proof (24h), free shipping incentive (72h).",
        "trigger_type": "browse_abandonment",
        "trigger_value": "",
        "steps": [
            {"template": "Browse Abandon — Product Reminder",     "delay_hours": 2},    # 2h — fast while browsing fresh
            {"template": "Browse Abandon — Social Proof",          "delay_hours": 24},   # 24h — next day
            {"template": "Browse Abandon — Free Shipping Push",    "delay_hours": 72},   # 72h — incentive for holdouts
        ]
    },
]


def seed_production_flows():
    """
    Create all 20 templates and 5 flows with proper step linkage.
    Idempotent — skips if flows with these names already exist.
    """
    db.connect(reuse_if_open=True)

    created_templates = 0
    created_flows = 0

    # 1) Create templates
    template_map = {}
    for t in TEMPLATES:
        existing = EmailTemplate.get_or_none(EmailTemplate.name == t["name"])
        if existing:
            template_map[t["name"]] = existing
            print(f"  [SKIP] Template already exists: {t['name']}")
            continue
        tmpl = EmailTemplate.create(
            name=t["name"],
            subject=t["subject"],
            preview_text=t["preview_text"],
            html_body=t["html_body"],
            shell_version=1,
        )
        template_map[t["name"]] = tmpl
        created_templates += 1
        print(f"  [OK] Created template: {t['name']}")

    # 2) Create flows + steps
    for f in FLOWS:
        existing = Flow.get_or_none(Flow.name == f["name"])
        if existing:
            print(f"  [SKIP] Flow already exists: {f['name']}")
            continue

        flow = Flow.create(
            name=f["name"],
            description=f["description"],
            trigger_type=f["trigger_type"],
            trigger_value=f["trigger_value"],
            is_active=False,
        )
        created_flows += 1

        for i, step_cfg in enumerate(f["steps"], start=1):
            tmpl = template_map.get(step_cfg["template"])
            if not tmpl:
                print(f"  [WARN] Template not found: {step_cfg['template']}")
                continue
            FlowStep.create(
                flow=flow,
                step_order=i,
                delay_hours=step_cfg["delay_hours"],
                template=tmpl,
                from_name="",
                from_email="",
                subject_override="",
            )

        step_count = len(f["steps"])
        print(f"  [OK] Created flow: {f['name']} ({step_count} steps, DISABLED)")

    print(f"\n[DONE] {created_templates} new templates + {created_flows} new flows created.")
    return created_templates, created_flows


if __name__ == "__main__":
    seed_production_flows()
