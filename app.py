"""
=====================================
  IN-HOUSE EMAIL MARKETING PLATFORM
  Built for Davinder | Powered by Amazon SES
=====================================
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from database import (db, Contact, EmailTemplate, Campaign, CampaignEmail, init_db,
                      WarmupConfig, WarmupLog, get_warmup_config,
                      Flow, FlowStep, FlowEnrollment, FlowEmail, AgentMessage)
from email_sender import send_campaign_email, test_ses_connection
from shopify_sync import sync_shopify_customers, verify_shopify_webhook, handle_shopify_customer_webhook
import json
import os
import subprocess
import sys
from datetime import datetime, date, timedelta
import threading

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_key_2024")

@app.template_filter("fromjson")
def _fromjson(s):
    import json
    try: return json.loads(s)
    except: return []

# ─────────────────────────────────
#  HTTP BASIC AUTH
# ─────────────────────────────────
from functools import wraps
from flask import Response

def _check_auth(username, password):
    return (username == os.environ.get("ADMIN_USERNAME", "admin") and
            password == os.environ.get("ADMIN_PASSWORD", ""))

def _request_auth():
    return Response(
        "Access denied. Please log in.",
        401,
        {"WWW-Authenticate": 'Basic realm="MailEngineHub"'}
    )

@app.before_request
def require_auth():
    # Unsubscribe link must stay public (embedded in all sent emails)
    if request.path.startswith("/contacts/unsubscribe"):
        return
    if request.path.startswith("/webhooks/shopify/"):
        return  # Shopify webhooks — verified by HMAC, not admin login
    if request.path in ("/api/track", "/api/identify"):
        return  # Shopify pixel / identity resolution — public
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return _request_auth()

# ─────────────────────────────────
#  BACKGROUND SCHEDULER (flows)
# ─────────────────────────────────
# Import here to keep it near the app definition; scheduler is started
# at the bottom after all route/function definitions are in place.
from apscheduler.schedulers.background import BackgroundScheduler
try:
    from ai_engine import run_nightly_scoring, run_nightly_plan
    _ai_engine_available = True
except ImportError:
    _ai_engine_available = False
import atexit
_scheduler = BackgroundScheduler(daemon=True)

# ─────────────────────────────────
#  WARMUP PHASE SCHEDULE
# ─────────────────────────────────
WARMUP_PHASES = {
    1: {"label": "Ignition",      "daily_limit": 50,     "days": 3},
    2: {"label": "Spark",         "daily_limit": 150,    "days": 4},
    3: {"label": "Gaining Trust", "daily_limit": 350,    "days": 7},
    4: {"label": "Building",      "daily_limit": 750,    "days": 7},
    5: {"label": "Momentum",      "daily_limit": 1500,   "days": 7},
    6: {"label": "Scaling",       "daily_limit": 3000,   "days": 7},
    7: {"label": "High Volume",   "daily_limit": 7000,   "days": 7},
    8: {"label": "Full Send",     "daily_limit": 999999, "days": 99},
}


def _compute_health_score(config):
    """Return int 0–100 based on checklist + sending performance."""
    score = 0
    # Checklist (50 pts max)
    if config.check_spf:          score += 10
    if config.check_dkim:         score += 10
    if config.check_dmarc:        score += 8
    if config.check_sandbox:      score += 10
    if config.check_list_cleaned: score += 6
    if config.check_subdomain:    score += 6
    # Sending performance — last 14 days (50 pts max)
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent = (CampaignEmail
              .select()
              .where(CampaignEmail.created_at >= cutoff))
    total_sent    = recent.where(CampaignEmail.status == "sent").count()
    total_opened  = recent.where(CampaignEmail.opened == True).count()
    total_bounced = recent.where(CampaignEmail.status == "bounced").count()
    if total_sent > 0:
        open_rate   = total_opened  / total_sent * 100
        bounce_rate = total_bounced / total_sent * 100
        if open_rate >= 20:   score += 25
        elif open_rate >= 15: score += 15
        elif open_rate >= 10: score += 8
        if bounce_rate < 1:   score += 25
        elif bounce_rate < 2: score += 15
        elif bounce_rate < 5: score += 8
    return min(score, 100)


def _check_phase_advance(config):
    """Auto-advance warmup phase if metrics are healthy and days have elapsed."""
    if not config.is_active or not config.warmup_started_at or config.current_phase >= 8:
        return config
    phase_info  = WARMUP_PHASES[config.current_phase]
    days_active = (datetime.now() - config.warmup_started_at).days
    # Calculate days spent in phases before current
    days_in_prior = sum(WARMUP_PHASES[p]["days"] for p in range(1, config.current_phase))
    days_in_phase = days_active - days_in_prior
    if days_in_phase < phase_info["days"]:
        return config  # Not enough time in current phase yet
    # Check metrics are healthy enough to advance
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    recent = CampaignEmail.select().where(CampaignEmail.created_at >= cutoff)
    sent    = recent.where(CampaignEmail.status == "sent").count()
    opened  = recent.where(CampaignEmail.opened == True).count()
    bounced = recent.where(CampaignEmail.status == "bounced").count()
    if sent > 0:
        open_rate   = opened  / sent * 100
        bounce_rate = bounced / sent * 100
        if open_rate >= 15 and bounce_rate < 3:
            config.current_phase = min(config.current_phase + 1, 8)
            config.save()
    return config


def _update_warmup_log(phase, daily_limit):
    """Update (or create) today's WarmupLog row with current CampaignEmail counts."""
    today_str = date.today().isoformat()
    log, _    = WarmupLog.get_or_create(log_date=today_str,
                                         defaults={"phase": phase, "daily_limit": daily_limit})
    log.phase        = phase
    log.daily_limit  = daily_limit
    cutoff = today_str + " 00:00:00"
    log.emails_sent    = (CampaignEmail.select()
                          .where(CampaignEmail.status == "sent",
                                 CampaignEmail.created_at >= cutoff).count())
    log.emails_opened  = (CampaignEmail.select()
                          .where(CampaignEmail.opened == True,
                                 CampaignEmail.created_at >= cutoff).count())
    log.emails_bounced = (CampaignEmail.select()
                          .where(CampaignEmail.status == "bounced",
                                 CampaignEmail.created_at >= cutoff).count())
    log.save()
    return log

# ─────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────
@app.route("/")
def dashboard():
    total_contacts  = Contact.select().count()
    total_campaigns = Campaign.select().count()
    total_sent      = CampaignEmail.select().where(CampaignEmail.status == "sent").count()
    total_opened    = CampaignEmail.select().where(CampaignEmail.opened == True).count()
    open_rate = round((total_opened / total_sent * 100), 1) if total_sent > 0 else 0

    recent_campaigns = (Campaign.select()
                        .order_by(Campaign.created_at.desc())
                        .limit(5))

    warmup_config = get_warmup_config()
    warmup_health = _compute_health_score(warmup_config)
    if warmup_health >= 90:   warmup_color = "#6366f1"
    elif warmup_health >= 75: warmup_color = "#22c55e"
    elif warmup_health >= 50: warmup_color = "#f97316"
    else:                     warmup_color = "#ef4444"

    return render_template("dashboard.html",
        total_contacts=total_contacts,
        total_campaigns=total_campaigns,
        total_sent=total_sent,
        open_rate=open_rate,
        recent_campaigns=recent_campaigns,
        warmup_config=warmup_config,
        warmup_health=warmup_health,
        warmup_color=warmup_color,
        warmup_phases=WARMUP_PHASES,
    )

# ─────────────────────────────────
#  CONTACTS
# ─────────────────────────────────
@app.route("/contacts")
def contacts():
    global _shopify_sync_state
    # Reset sync state when page loads to prevent infinite reload loop
    if _shopify_sync_state.get("done"):
        _shopify_sync_state = {"running": False, "synced": 0, "error": None, "done": False}

    page           = int(request.args.get("page", 1))
    per_page       = 50
    search         = request.args.get("search", "")
    tag            = request.args.get("tag", "")
    country_filter = request.args.get("country_filter", "")

    query = Contact.select()
    if search:
        query = query.where(
            (Contact.email.contains(search)) |
            (Contact.first_name.contains(search)) |
            (Contact.last_name.contains(search))
        )
    if tag:
        query = query.where(Contact.tags.contains(tag))
    if country_filter:
        query = query.where(Contact.country == country_filter)

    total         = query.count()
    contacts      = query.order_by(Contact.created_at.desc()).paginate(page, per_page)
    shopify_total = Contact.select().where(Contact.source == "shopify").count()

    # Unique countries for the filter dropdown (only non-empty)
    countries = sorted(set(
        c.country for c in Contact.select(Contact.country)
        if c.country
    ))

    return render_template("contacts.html",
        contacts=contacts,
        total=total,
        page=page,
        per_page=per_page,
        search=search,
        tag=tag,
        country_filter=country_filter,
        shopify_total=shopify_total,
        countries=countries,
    )

@app.route("/contacts/import-csv", methods=["POST"])
def import_csv():
    import csv, io
    file = request.files.get("file")
    if not file:
        flash("No file selected", "error")
        return redirect(url_for("contacts"))

    content = file.read().decode("utf-8")
    reader  = csv.DictReader(io.StringIO(content))
    imported = 0
    skipped  = 0

    for row in reader:
        email = row.get("email") or row.get("Email") or row.get("EMAIL", "").strip()
        if not email:
            skipped += 1
            continue
        contact, created = Contact.get_or_create(
            email=email.lower(),
            defaults={
                "first_name": row.get("first_name") or row.get("First Name", ""),
                "last_name":  row.get("last_name")  or row.get("Last Name", ""),
                "phone":      row.get("phone", ""),
                "tags":       row.get("tags", ""),
                "source":     "csv_import",
                "subscribed": True
            }
        )
        if created:
            imported += 1
            _enroll_contact_in_flows(contact, "contact_created")
        else:
            skipped += 1

    flash(f"Imported {imported} contacts. Skipped {skipped} duplicates.", "success")
    return redirect(url_for("contacts"))

_shopify_sync_state = {"running": False, "synced": 0, "error": None, "done": False}

def _run_shopify_sync_bg():
    """Background thread: run full Shopify sync and update _shopify_sync_state."""
    global _shopify_sync_state
    _shopify_sync_state = {"running": True, "synced": 0, "error": None, "done": False}
    try:
        def on_progress(n):
            _shopify_sync_state["synced"] = n

        synced, error, new_contacts = sync_shopify_customers(progress_callback=on_progress)
        for contact in new_contacts:
            _enroll_contact_in_flows(contact, "contact_created")
        _shopify_sync_state.update({"running": False, "synced": synced,
                                    "error": error, "done": True})
    except Exception as e:
        _shopify_sync_state.update({"running": False, "error": str(e), "done": True})


@app.route("/contacts/sync-shopify", methods=["POST"])
def sync_shopify():
    if _shopify_sync_state.get("running"):
        flash(f"Sync already running ({_shopify_sync_state['synced']:,} so far)…", "warning")
        return redirect(url_for("contacts"))
    thread = threading.Thread(target=_run_shopify_sync_bg, daemon=True)
    thread.start()
    flash("Shopify sync started in background — the page will update automatically.", "success")
    return redirect(url_for("contacts"))


@app.route("/api/contacts/sync-status")
def api_sync_status():
    return jsonify(_shopify_sync_state)


# ─────────────────────────────────
#  SHOPIFY WEBHOOKS
# ─────────────────────────────────
@app.route("/webhooks/shopify/customer/create", methods=["POST"])
def webhook_shopify_customer_create():
    """
    Receive real-time Shopify customer creation webhook.
    Shopify sends the customer object as the root JSON body (not nested).
    """
    try:
        raw_body = request.get_data()

        # Verify HMAC signature from Shopify
        is_valid, error = verify_shopify_webhook(raw_body, request.headers)
        if not is_valid:
            app.logger.warning(f"Customer create webhook HMAC failed: {error}")
            return jsonify({"error": "Unauthorized"}), 401

        # Shopify sends customer data at root level (not nested under "customer")
        customer = request.get_json(silent=True) or {}
        if not customer:
            return jsonify({"error": "No JSON payload"}), 400

        contact, created = handle_shopify_customer_webhook(customer)

        if contact:
            # Create CustomerProfile stub if missing
            from database import CustomerProfile
            CustomerProfile.get_or_create(
                contact=contact,
                defaults={"email": contact.email, "last_computed_at": datetime.now()}
            )

            # Trigger background enrichment to pull in any activity data
            _email_copy = contact.email
            def _enrich_bg():
                import sys as _s; _s.path.insert(0, '/var/www/mailengine')
                from activity_sync import enrich_single_profile
                enrich_single_profile(_email_copy)
            import threading as _th
            _th.Thread(target=_enrich_bg, daemon=True).start()

            # Auto-enroll new contacts in flows
            if created:
                _enroll_contact_in_flows(contact, "contact_created")
                app.logger.info(f"Shopify customer webhook: new contact {contact.email}")
            else:
                app.logger.info(f"Shopify customer webhook: updated contact {contact.email}")

            return jsonify({"success": True, "contact_id": contact.id, "created": created}), 200
        else:
            return jsonify({"error": "No valid email in webhook"}), 400

    except Exception as e:
        app.logger.error(f"Customer create webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/webhooks/shopify/customer/update", methods=["POST"])
def webhook_shopify_customer_update():
    """
    Receive Shopify customer update webhook (email consent changes, name updates, etc.).
    Shopify sends the customer object as the root JSON body.
    """
    try:
        raw_body = request.get_data()
        is_valid, error = verify_shopify_webhook(raw_body, request.headers)
        if not is_valid:
            app.logger.warning(f"Customer update webhook HMAC failed: {error}")
            return jsonify({"error": "Unauthorized"}), 401

        customer = request.get_json(silent=True) or {}
        if not customer:
            return jsonify({"error": "No JSON payload"}), 400

        contact, created = handle_shopify_customer_webhook(customer)

        if contact:
            # Ensure CustomerProfile exists
            from database import CustomerProfile
            CustomerProfile.get_or_create(
                contact=contact,
                defaults={"email": contact.email, "last_computed_at": datetime.now()}
            )
            app.logger.info(f"Shopify customer update webhook: {contact.email} (subscribed={contact.subscribed})")
            return jsonify({"success": True, "contact_id": contact.id, "updated": True}), 200
        else:
            return jsonify({"error": "No valid email in webhook"}), 400

    except Exception as e:
        app.logger.error(f"Customer update webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/contacts/unsubscribe/<email>")
def unsubscribe(email):
    try:
        contact = Contact.get(Contact.email == email)
        contact.subscribed = False
        contact.save()
        (FlowEnrollment.update(status="cancelled")
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status == "active")
                       .execute())
        return render_template("unsubscribe.html", email=email, success=True)
    except:
        return render_template("unsubscribe.html", email=email, success=False)

# ─────────────────────────────────
#  TEMPLATES
# ─────────────────────────────────
@app.route("/templates")
def templates():
    templates = EmailTemplate.select().order_by(EmailTemplate.created_at.desc())
    return render_template("templates.html", templates=templates)

@app.route("/templates/new", methods=["GET", "POST"])
def new_template():
    if request.method == "POST":
        template = EmailTemplate.create(
            name        = request.form["name"],
            subject     = request.form["subject"],
            html_body   = request.form["html_body"],
            preview_text= request.form.get("preview_text", "")
        )
        flash(f"Template '{template.name}' saved!", "success")
        return redirect(url_for("templates"))
    return render_template("template_editor.html", template=None)

@app.route("/templates/<int:template_id>/edit", methods=["GET", "POST"])
def edit_template(template_id):
    template = EmailTemplate.get_by_id(template_id)
    if request.method == "POST":
        template.name         = request.form["name"]
        template.subject      = request.form["subject"]
        template.html_body    = request.form["html_body"]
        template.preview_text = request.form.get("preview_text", "")
        template.save()
        flash("Template updated!", "success")
        return redirect(url_for("templates"))
    return render_template("template_editor.html", template=template)

@app.route("/templates/<int:template_id>/delete", methods=["POST"])
def delete_template(template_id):
    EmailTemplate.delete_by_id(template_id)
    flash("Template deleted.", "success")
    return redirect(url_for("templates"))

# ─────────────────────────────────
#  CAMPAIGNS
# ─────────────────────────────────
@app.route("/campaigns")
def campaigns():
    campaigns = Campaign.select().order_by(Campaign.created_at.desc())
    return render_template("campaigns.html", campaigns=campaigns)

@app.route("/campaigns/new", methods=["GET", "POST"])
def new_campaign():
    templates = EmailTemplate.select()
    if request.method == "POST":
        campaign = Campaign.create(
            name            = request.form["name"],
            from_name       = request.form["from_name"],
            from_email      = request.form["from_email"],
            reply_to        = request.form.get("reply_to", ""),
            template_id     = request.form["template_id"],
            segment_filter  = (request.form.get("custom_tag", "").strip()
                             if request.form.get("segment_filter") == "custom"
                             else request.form.get("segment_filter", "all")),
            status          = "draft"
        )
        flash(f"Campaign '{campaign.name}' created!", "success")
        return redirect(url_for("campaign_detail", campaign_id=campaign.id))
    from_email_default = os.getenv("DEFAULT_FROM_EMAIL", "news@news.ldaselectronics.com")
    return render_template("campaign_form.html", templates=templates, campaign=None,
        default_from_name="LDAS Electronics",
        default_from_email=from_email_default)

@app.route("/api/campaign/recipient-count")
def api_recipient_count():
    segment = request.args.get("segment", "all")
    tag = request.args.get("tag", "").strip()
    query = Contact.select().where(Contact.subscribed == True)
    if segment == "custom" and tag:
        query = query.where(Contact.tags.contains(tag))
    elif segment and segment != "all":
        query = query.where(Contact.tags.contains(segment))
    return jsonify({"count": query.count()})


@app.route("/campaigns/<int:campaign_id>")
def campaign_detail(campaign_id):
    campaign = Campaign.get_by_id(campaign_id)
    template = EmailTemplate.get_by_id(campaign.template_id)

    # Build recipient count
    contacts = _get_campaign_contacts(campaign)
    recipient_count = len(contacts)

    # Stats
    sent    = CampaignEmail.select().where(CampaignEmail.campaign == campaign, CampaignEmail.status == "sent").count()
    opened  = CampaignEmail.select().where(CampaignEmail.campaign == campaign, CampaignEmail.opened == True).count()
    clicked = CampaignEmail.select().where(CampaignEmail.campaign == campaign, CampaignEmail.clicked == True).count()
    bounced = CampaignEmail.select().where(CampaignEmail.campaign == campaign, CampaignEmail.status == "bounced").count()
    open_rate    = round((opened  / sent * 100), 1) if sent > 0 else 0
    click_rate   = round((clicked / sent * 100), 1) if sent > 0 else 0
    bounce_rate  = round((bounced / sent * 100), 1) if sent > 0 else 0

    return render_template("campaign_detail.html",
        campaign=campaign,
        template=template,
        recipient_count=recipient_count,
        sent=sent, opened=opened, clicked=clicked, bounced=bounced,
        open_rate=open_rate, click_rate=click_rate, bounce_rate=bounce_rate
    )

@app.route("/campaigns/<int:campaign_id>/send", methods=["POST"])
def send_campaign(campaign_id):
    campaign = Campaign.get_by_id(campaign_id)
    if campaign.status == "sent":
        flash("This campaign has already been sent.", "error")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))
    if campaign.status == "sending":
        flash("This campaign is already sending.", "error")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    # Launch send in background thread so UI stays responsive
    thread = threading.Thread(target=_send_campaign_async, args=(campaign_id,))
    thread.daemon = True
    thread.start()

    campaign.status = "sending"
    campaign.save()

    flash("Campaign is sending! Refresh in a moment to see progress.", "success")
    return redirect(url_for("campaign_detail", campaign_id=campaign_id))

def _send_campaign_async(campaign_id):
    campaign = Campaign.get_by_id(campaign_id)
    template = EmailTemplate.get_by_id(campaign.template_id)
    contacts = _get_campaign_contacts(campaign)

    # ── Warmup enforcement ────────────────────────────────────────
    warmup = get_warmup_config()
    daily_limit = None
    if warmup.is_active:
        warmup = _check_phase_advance(warmup)
        today_str = date.today().isoformat()
        if warmup.last_reset_date != today_str:
            warmup.emails_sent_today = 0
            warmup.last_reset_date   = today_str
            warmup.save()
        daily_limit = WARMUP_PHASES[warmup.current_phase]["daily_limit"]
    # ─────────────────────────────────────────────────────────────

    sent_count = 0
    for contact in contacts:
        if not contact.subscribed:
            continue
        # ── Daily limit check ─────────────────────────────────────
        if daily_limit is not None and warmup.emails_sent_today >= daily_limit:
            campaign.status = "paused"
            campaign.save()
            if warmup.is_active:
                _update_warmup_log(warmup.current_phase, daily_limit)
            return
        # ─────────────────────────────────────────────────────────
        # Personalise
        html = template.html_body
        html = html.replace("{{first_name}}", contact.first_name or "Friend")
        html = html.replace("{{last_name}}",  contact.last_name  or "")
        html = html.replace("{{email}}",      contact.email)
        html += f'<img src="https://mailenginehub.com/track/open/{campaign_id}/{contact.id}" width="1" height="1" />'
        html = html.replace("{{unsubscribe_url}}",
            f"https://mailenginehub.com/contacts/unsubscribe/{contact.email}")

        subject = template.subject.replace("{{first_name}}", contact.first_name or "Friend")

        success, error = send_campaign_email(
            to_email   = contact.email,
            to_name    = f"{contact.first_name} {contact.last_name}".strip(),
            from_email = campaign.from_email,
            from_name  = campaign.from_name,
            subject    = subject,
            html_body  = html
        )

        status = "sent" if success else "failed"
        CampaignEmail.create(
            campaign = campaign,
            contact  = contact,
            status   = status,
            error_msg= error or ""
        )
        if success:
            sent_count += 1
            if daily_limit is not None:
                warmup.emails_sent_today += 1
                warmup.save()

    campaign.status    = "sent"
    campaign.sent_at   = datetime.now()
    campaign.save()
    if warmup.is_active:
        _update_warmup_log(warmup.current_phase, daily_limit or 999999)

def _get_campaign_contacts(campaign):
    query = Contact.select().where(Contact.subscribed == True)
    if campaign.segment_filter and campaign.segment_filter != "all":
        query = query.where(Contact.tags.contains(campaign.segment_filter))
    return list(query)

# ─────────────────────────────────
#  TRACKING
# ─────────────────────────────────
@app.route("/track/open/<int:campaign_id>/<int:contact_id>")
def track_open(campaign_id, contact_id):
    try:
        ce = CampaignEmail.get(
            CampaignEmail.campaign == campaign_id,
            CampaignEmail.contact  == contact_id
        )
        if not ce.opened:
            ce.opened    = True
            ce.opened_at = datetime.now()
            ce.save()
    except:
        pass
    # Return a transparent 1x1 pixel
    from flask import Response
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(pixel, mimetype="image/gif")

# ─────────────────────────────────
#  WARMUP / DELIVERABILITY
# ─────────────────────────────────
@app.route("/warmup")
def warmup_dashboard():
    config      = get_warmup_config()
    health      = _compute_health_score(config)
    phase_info  = WARMUP_PHASES[config.current_phase]
    daily_limit = phase_info["daily_limit"]

    # Days warming
    days_warming = 0
    if config.warmup_started_at:
        days_warming = max(0, (datetime.now() - config.warmup_started_at).days)

    # Overall sending performance (last 14 days)
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent      = CampaignEmail.select().where(CampaignEmail.created_at >= cutoff)
    total_sent  = recent.where(CampaignEmail.status == "sent").count()
    total_open  = recent.where(CampaignEmail.opened == True).count()
    total_bnc   = recent.where(CampaignEmail.status == "bounced").count()
    open_rate   = round(total_open / total_sent * 100, 1) if total_sent > 0 else 0
    bounce_rate = round(total_bnc  / total_sent * 100, 1) if total_sent > 0 else 0

    # Last 14 days chart data
    chart_labels, chart_sent, chart_open_rate, chart_bounce_rate = [], [], [], []
    for i in range(13, -1, -1):
        day = (date.today() - timedelta(days=i)).isoformat()
        chart_labels.append(day[5:])  # MM-DD
        log = WarmupLog.get_or_none(WarmupLog.log_date == day)
        if log and log.emails_sent > 0:
            chart_sent.append(log.emails_sent)
            chart_open_rate.append(round(log.emails_opened / log.emails_sent * 100, 1))
            chart_bounce_rate.append(round(log.emails_bounced / log.emails_sent * 100, 1))
        else:
            chart_sent.append(0)
            chart_open_rate.append(0)
            chart_bounce_rate.append(0)

    # Health colour
    if health >= 90:   health_color = "#6366f1"
    elif health >= 75: health_color = "#22c55e"
    elif health >= 50: health_color = "#f97316"
    else:              health_color = "#ef4444"

    return render_template("warmup.html",
        config=config,
        health=health,
        health_color=health_color,
        phase_info=phase_info,
        daily_limit=daily_limit,
        days_warming=days_warming,
        open_rate=open_rate,
        bounce_rate=bounce_rate,
        total_sent=total_sent,
        chart_labels=json.dumps(chart_labels),
        chart_sent=json.dumps(chart_sent),
        chart_open_rate=json.dumps(chart_open_rate),
        chart_bounce_rate=json.dumps(chart_bounce_rate),
        warmup_phases=WARMUP_PHASES,
    )


@app.route("/warmup/toggle", methods=["POST"])
def warmup_toggle():
    config = get_warmup_config()
    if not config.is_active:
        config.is_active = True
        if not config.warmup_started_at:
            config.warmup_started_at = datetime.now()
        flash("Warmup mode enabled. Daily limits are now enforced.", "success")
    else:
        config.is_active = False
        flash("Warmup mode disabled. Campaigns will send without daily limits.", "warning")
    config.save()
    return redirect(url_for("warmup_dashboard"))


@app.route("/warmup/checklist", methods=["POST"])
def warmup_checklist():
    config = get_warmup_config()
    config.check_spf          = "check_spf"          in request.form
    config.check_dkim         = "check_dkim"         in request.form
    config.check_dmarc        = "check_dmarc"        in request.form
    config.check_sandbox      = "check_sandbox"      in request.form
    config.check_list_cleaned = "check_list_cleaned" in request.form
    config.check_subdomain    = "check_subdomain"    in request.form
    config.save()
    flash("Checklist saved!", "success")
    return redirect(url_for("warmup_dashboard"))


@app.route("/warmup/advance-phase", methods=["POST"])
def warmup_advance_phase():
    config = get_warmup_config()
    if config.current_phase < 8:
        config.current_phase += 1
        config.save()
        flash(f"Advanced to Phase {config.current_phase} — {WARMUP_PHASES[config.current_phase]['label']}. "
              f"New daily limit: {WARMUP_PHASES[config.current_phase]['daily_limit']:,} emails.", "success")
    else:
        flash("Already at maximum phase (Full Send).", "warning")
    return redirect(url_for("warmup_dashboard"))


@app.route("/api/warmup/health")
def api_warmup_health():
    config      = get_warmup_config()
    health      = _compute_health_score(config)
    phase_info  = WARMUP_PHASES[config.current_phase]
    checklist   = sum([config.check_spf, config.check_dkim, config.check_dmarc,
                       config.check_sandbox, config.check_list_cleaned, config.check_subdomain])
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent     = CampaignEmail.select().where(CampaignEmail.created_at >= cutoff)
    total_sent = recent.where(CampaignEmail.status == "sent").count()
    total_open = recent.where(CampaignEmail.opened == True).count()
    total_bnc  = recent.where(CampaignEmail.status == "bounced").count()
    return jsonify({
        "health_score":   health,
        "phase":          config.current_phase,
        "phase_label":    phase_info["label"],
        "daily_limit":    phase_info["daily_limit"],
        "sent_today":     config.emails_sent_today,
        "warmup_active":  config.is_active,
        "open_rate":      round(total_open / total_sent * 100, 1) if total_sent > 0 else 0,
        "bounce_rate":    round(total_bnc  / total_sent * 100, 1) if total_sent > 0 else 0,
        "checklist_done": checklist,
    })


# ─────────────────────────────────
#  AUTOMATION FLOWS — CORE ENGINE
# ─────────────────────────────────

def _enroll_contact_in_flows(contact, trigger_type, trigger_value=""):
    """Enroll a contact in all active flows matching trigger_type (and trigger_value if relevant)."""
    query = Flow.select().where(Flow.is_active == True, Flow.trigger_type == trigger_type)
    if trigger_type == "tag_added" and trigger_value:
        query = query.where(Flow.trigger_value == trigger_value)

    for flow in query:
        first_step = (FlowStep.select()
                      .where(FlowStep.flow == flow)
                      .order_by(FlowStep.step_order)
                      .first())
        if not first_step:
            continue
        next_send = datetime.now() + timedelta(hours=first_step.delay_hours)
        try:
            FlowEnrollment.create(
                flow=flow,
                contact=contact,
                current_step=1,
                next_send_at=next_send,
                status="active",
            )
        except Exception:
            pass  # Unique constraint — already enrolled


def _process_flow_enrollments():
    """Run every 60 seconds. Send pending flow emails whose next_send_at has passed."""
    now = datetime.now()
    pending = (FlowEnrollment.select(FlowEnrollment, Flow, Contact)
               .join(Flow)
               .switch(FlowEnrollment)
               .join(Contact)
               .where(FlowEnrollment.status == "active",
                      FlowEnrollment.next_send_at <= now))

    warmup = get_warmup_config()
    today_str = date.today().isoformat()
    if warmup.is_active and warmup.last_reset_date != today_str:
        warmup.emails_sent_today = 0
        warmup.last_reset_date   = today_str
        warmup.save()

    for enrollment in pending:
        contact = enrollment.contact
        if not contact.subscribed:
            enrollment.status = "cancelled"
            enrollment.save()
            continue

        step = (FlowStep.select()
                .where(FlowStep.flow == enrollment.flow,
                       FlowStep.step_order == enrollment.current_step)
                .first())
        if not step:
            enrollment.status = "completed"
            enrollment.save()
            continue

        # Respect warmup daily limit
        daily_limit = WARMUP_PHASES[warmup.current_phase]["daily_limit"] if warmup.is_active else None
        if daily_limit is not None and warmup.emails_sent_today >= daily_limit:
            continue  # Skip until tomorrow

        template = step.template
        html = template.html_body
        html = html.replace("{{first_name}}", contact.first_name or "Friend")
        html = html.replace("{{last_name}}",  contact.last_name  or "")
        html = html.replace("{{email}}",      contact.email)
        html = html.replace("{{unsubscribe_url}}",
                            f"https://mailenginehub.com/contacts/unsubscribe/{contact.email}")
        html += (f'<img src="https://mailenginehub.com/track/flow-open/{enrollment.id}/{step.id}"'
                 f' width="1" height="1" />')

        subject = step.subject_override or template.subject
        subject = subject.replace("{{first_name}}", contact.first_name or "Friend")

        from_email = step.from_email or os.getenv("DEFAULT_FROM_EMAIL", "")
        from_name  = step.from_name

        success, error = send_campaign_email(
            to_email=contact.email,
            to_name=f"{contact.first_name} {contact.last_name}".strip(),
            from_email=from_email,
            from_name=from_name,
            subject=subject,
            html_body=html,
        )

        fe = FlowEmail.create(
            enrollment=enrollment,
            step=step,
            contact=contact,
            status="sent" if success else "failed",
        )

        if success and warmup.is_active:
            warmup.emails_sent_today += 1
            warmup.save()

        # Advance to next step or complete
        next_step = (FlowStep.select()
                     .where(FlowStep.flow == enrollment.flow,
                            FlowStep.step_order == enrollment.current_step + 1)
                     .first())
        if next_step:
            enrollment.current_step = next_step.step_order
            enrollment.next_send_at = datetime.now() + timedelta(hours=next_step.delay_hours)
            enrollment.save()
        else:
            enrollment.status = "completed"
            enrollment.save()


def _check_passive_triggers():
    """Run every 30 min. Check no_purchase_days triggers and cancel unsubscribed enrollments."""
    # Cancel enrollments for unsubscribed contacts
    unsubbed = Contact.select().where(Contact.subscribed == False)
    for contact in unsubbed:
        (FlowEnrollment.update(status="cancelled")
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status == "active")
                       .execute())

    # no_purchase_days: enroll shopify contacts not yet in these flows
    winback_flows = (Flow.select()
                     .where(Flow.is_active == True, Flow.trigger_type == "no_purchase_days"))
    for flow in winback_flows:
        try:
            days = int(flow.trigger_value)
        except (ValueError, TypeError):
            continue
        cutoff = datetime.now() - timedelta(days=days)
        shopify_contacts = (Contact.select()
                            .where(Contact.source == "shopify",
                                   Contact.created_at <= cutoff,
                                   Contact.subscribed == True))
        for contact in shopify_contacts:
            first_step = (FlowStep.select()
                          .where(FlowStep.flow == flow)
                          .order_by(FlowStep.step_order)
                          .first())
            if not first_step:
                continue
            try:
                FlowEnrollment.create(
                    flow=flow,
                    contact=contact,
                    current_step=1,
                    next_send_at=datetime.now(),
                    status="active",
                )
            except Exception:
                pass  # Already enrolled

    # ── Phase G: Behavioural Trigger Detection (queue, don't send while in sandbox) ──
    _detect_behavioural_triggers()


def _detect_behavioural_triggers():
    """
    Scan for browse abandonment, cart abandonment, churn risk, high-intent visitors.
    Queues PendingTrigger records — does NOT trigger email sends (sandbox safe).
    """
    from database import CustomerProfile, CustomerActivity, PendingTrigger, ShopifyOrder
    import json as _json

    now = datetime.now()

    # ── 1. Browse Abandonment: viewed product 2+ times in last 48hrs, didn't buy ──
    cutoff_48h = now - timedelta(hours=48)
    try:
        # Get emails with recent product views
        from peewee import fn
        browse_candidates = (
            CustomerActivity.select(CustomerActivity.email, fn.COUNT(CustomerActivity.id).alias('view_count'))
            .where(CustomerActivity.event_type == 'viewed_product')
            .where(CustomerActivity.occurred_at >= cutoff_48h)
            .where(CustomerActivity.email != '')
            .group_by(CustomerActivity.email)
            .having(fn.COUNT(CustomerActivity.id) >= 2)
        )
        for row in browse_candidates:
            email = row.email
            # Skip if already triggered recently
            existing = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == 'browse_abandonment',
                PendingTrigger.detected_at >= cutoff_48h
            ).count()
            if existing > 0:
                continue

            # Check they didn't buy recently
            recent_order = ShopifyOrder.select().where(
                ShopifyOrder.email == email,
                ShopifyOrder.created_at >= cutoff_48h
            ).count()
            if recent_order > 0:
                continue

            # Get the product they viewed most
            views = (CustomerActivity.select()
                .where(CustomerActivity.email == email,
                       CustomerActivity.event_type == 'viewed_product',
                       CustomerActivity.occurred_at >= cutoff_48h)
                .order_by(CustomerActivity.occurred_at.desc()))
            products = {}
            for v in views:
                try:
                    data = _json.loads(v.event_data or '{}')
                    title = data.get('product_title', '').strip()
                    if title:
                        products[title] = products.get(title, 0) + 1
                except:
                    pass

            if products:
                top_product = max(products, key=products.get)
                PendingTrigger.create(
                    email=email,
                    trigger_type='browse_abandonment',
                    trigger_data=_json.dumps({
                        'product': top_product,
                        'view_count': products[top_product],
                        'all_products': dict(list(products.items())[:5])
                    }),
                    detected_at=now,
                    status='pending'
                )
    except Exception as _e:
        app.logger.warning("Browse abandonment detection error: %s" % _e)

    # ── 2. Cart Abandonment: abandoned_checkout with no completed order ──
    cutoff_4h = now - timedelta(hours=4)
    cutoff_7d = now - timedelta(days=7)
    try:
        cart_events = (CustomerActivity.select()
            .where(CustomerActivity.event_type == 'abandoned_checkout')
            .where(CustomerActivity.occurred_at >= cutoff_7d)
            .where(CustomerActivity.email != ''))

        for event in cart_events:
            email = event.email
            # Skip if already triggered
            existing = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == 'cart_abandonment',
                PendingTrigger.detected_at >= cutoff_7d
            ).count()
            if existing > 0:
                continue

            # Check they didn't complete the order
            completed = (CustomerActivity.select().where(
                CustomerActivity.email == email,
                CustomerActivity.event_type.in_(['completed_checkout', 'placed_order']),
                CustomerActivity.occurred_at >= event.occurred_at
            ).count())
            if completed > 0:
                continue

            try:
                data = _json.loads(event.event_data or '{}')
            except:
                data = {}

            PendingTrigger.create(
                email=email,
                trigger_type='cart_abandonment',
                trigger_data=_json.dumps({
                    'checkout_id': data.get('checkout_id', ''),
                    'products': data.get('products', []),
                    'total': data.get('total', ''),
                    'item_count': data.get('item_count', 0)
                }),
                detected_at=now,
                status='pending'
            )
    except Exception as _e:
        app.logger.warning("Cart abandonment detection error: %s" % _e)

    # ── 3. Churn Risk High: churn_risk >= 1.5 for customers with orders ──
    try:
        churn_profiles = (CustomerProfile.select()
            .where(CustomerProfile.churn_risk >= 1.5)
            .where(CustomerProfile.total_orders > 0))

        for profile in churn_profiles:
            email = profile.email
            # Skip if already triggered in last 30 days
            cutoff_30d = now - timedelta(days=30)
            existing = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == 'churn_risk_high',
                PendingTrigger.detected_at >= cutoff_30d
            ).count()
            if existing > 0:
                continue

            PendingTrigger.create(
                email=email,
                trigger_type='churn_risk_high',
                trigger_data=_json.dumps({
                    'churn_risk': profile.churn_risk,
                    'predicted_ltv': profile.predicted_ltv,
                    'days_since_last_order': profile.days_since_last_order,
                    'total_orders': profile.total_orders,
                    'avg_order_value': profile.avg_order_value
                }),
                detected_at=now,
                status='pending'
            )
    except Exception as _e:
        app.logger.warning("Churn risk detection error: %s" % _e)

    # ── 4. High Intent No Purchase: engagement > 50, 0 orders ──
    try:
        intent_profiles = (CustomerProfile.select()
            .where(CustomerProfile.website_engagement_score >= 50)
            .where(CustomerProfile.total_orders == 0))

        for profile in intent_profiles:
            email = profile.email
            cutoff_7d = now - timedelta(days=7)
            existing = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == 'high_engagement_no_purchase',
                PendingTrigger.detected_at >= cutoff_7d
            ).count()
            if existing > 0:
                continue

            PendingTrigger.create(
                email=email,
                trigger_type='high_engagement_no_purchase',
                trigger_data=_json.dumps({
                    'engagement_score': profile.website_engagement_score,
                    'product_views': profile.total_product_views,
                    'last_viewed': profile.last_viewed_product or ''
                }),
                detected_at=now,
                status='pending'
            )
    except Exception as _e:
        app.logger.warning("High intent detection error: %s" % _e)

    # Log summary
    try:
        browse_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'browse_abandonment', PendingTrigger.status == 'pending').count()
        cart_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'cart_abandonment', PendingTrigger.status == 'pending').count()
        churn_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'churn_risk_high', PendingTrigger.status == 'pending').count()
        intent_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'high_engagement_no_purchase', PendingTrigger.status == 'pending').count()
        app.logger.info("Trigger detection: browse=%d, cart=%d, churn=%d, intent=%d" % (browse_count, cart_count, churn_count, intent_count))
    except:
        pass


# ─────────────────────────────────
#  AUTOMATION FLOWS — ROUTES
# ─────────────────────────────────

@app.route("/flows")
def flows():
    all_flows = Flow.select().order_by(Flow.created_at.desc())
    # Attach stats to each flow object
    flow_stats = {}
    for flow in all_flows:
        enrolled  = FlowEnrollment.select().where(FlowEnrollment.flow == flow).count()
        completed = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                                   FlowEnrollment.status == "completed").count()
        emails    = FlowEmail.select().join(FlowEnrollment).where(FlowEnrollment.flow == flow)
        sent      = emails.where(FlowEmail.status == "sent").count()
        opened    = emails.where(FlowEmail.opened == True).count()
        open_rate = round(opened / sent * 100, 1) if sent > 0 else 0
        flow_stats[flow.id] = {
            "enrolled": enrolled, "completed": completed,
            "sent": sent, "open_rate": open_rate,
        }

    total_enrolled = FlowEnrollment.select().count()
    total_sent     = FlowEmail.select().where(FlowEmail.status == "sent").count()

    return render_template("flows.html",
        flows=all_flows,
        flow_stats=flow_stats,
        total_enrolled=total_enrolled,
        total_sent=total_sent,
    )


@app.route("/flows/new", methods=["GET", "POST"])
def new_flow():
    if request.method == "POST":
        flow = Flow.create(
            name=request.form["name"],
            description=request.form.get("description", ""),
            trigger_type=request.form["trigger_type"],
            trigger_value=request.form.get("trigger_value", ""),
            is_active=False,
        )
        flash(f"Flow '{flow.name}' created! Add steps below, then enable it.", "success")
        return redirect(url_for("flow_detail", flow_id=flow.id))
    return render_template("flows.html", show_create=True,
                           flows=Flow.select().order_by(Flow.created_at.desc()),
                           flow_stats={}, total_enrolled=0, total_sent=0)


@app.route("/flows/<int:flow_id>")
def flow_detail(flow_id):
    flow  = Flow.get_by_id(flow_id)
    steps = FlowStep.select().where(FlowStep.flow == flow).order_by(FlowStep.step_order)
    templates = EmailTemplate.select().order_by(EmailTemplate.name)

    enrollments = (FlowEnrollment.select(FlowEnrollment, Contact)
                   .join(Contact)
                   .where(FlowEnrollment.flow == flow)
                   .order_by(FlowEnrollment.enrolled_at.desc())
                   .limit(50))

    total    = FlowEnrollment.select().where(FlowEnrollment.flow == flow).count()
    active   = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                              FlowEnrollment.status == "active").count()
    completed = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                               FlowEnrollment.status == "completed").count()
    cancelled = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                               FlowEnrollment.status == "cancelled").count()

    flow_emails = FlowEmail.select().join(FlowEnrollment).where(FlowEnrollment.flow == flow)
    sent   = flow_emails.where(FlowEmail.status == "sent").count()
    opened = flow_emails.where(FlowEmail.opened == True).count()
    open_rate = round(opened / sent * 100, 1) if sent > 0 else 0

    return render_template("flow_detail.html",
        flow=flow,
        steps=steps,
        templates=templates,
        enrollments=enrollments,
        total=total, active=active, completed=completed, cancelled=cancelled,
        sent=sent, open_rate=open_rate,
    )


@app.route("/flows/<int:flow_id>/toggle", methods=["POST"])
def flow_toggle(flow_id):
    flow = Flow.get_by_id(flow_id)
    flow.is_active = not flow.is_active
    flow.save()
    state = "enabled" if flow.is_active else "disabled"
    flash(f"Flow '{flow.name}' {state}.", "success")
    return redirect(url_for("flow_detail", flow_id=flow_id))


@app.route("/flows/<int:flow_id>/delete", methods=["POST"])
def flow_delete(flow_id):
    flow = Flow.get_by_id(flow_id)
    name = flow.name
    # Cancel active enrollments
    (FlowEnrollment.update(status="cancelled")
                   .where(FlowEnrollment.flow == flow, FlowEnrollment.status == "active")
                   .execute())
    # Delete steps and flow
    FlowStep.delete().where(FlowStep.flow == flow).execute()
    flow.delete_instance()
    flash(f"Flow '{name}' deleted.", "success")
    return redirect(url_for("flows"))


@app.route("/flows/<int:flow_id>/steps/add", methods=["POST"])
def flow_add_step(flow_id):
    flow = Flow.get_by_id(flow_id)
    last = (FlowStep.select()
            .where(FlowStep.flow == flow)
            .order_by(FlowStep.step_order.desc())
            .first())
    order = (last.step_order + 1) if last else 1

    delay_raw  = request.form.get("delay_hours", "0")
    delay_unit = request.form.get("delay_unit", "hours")
    try:
        delay_val = int(delay_raw)
    except ValueError:
        delay_val = 0
    if delay_unit == "days":
        delay_val *= 24

    FlowStep.create(
        flow=flow,
        step_order=order,
        delay_hours=delay_val,
        template_id=request.form["template_id"],
        from_name=request.form.get("from_name", ""),
        from_email=request.form.get("from_email", ""),
        subject_override=request.form.get("subject_override", ""),
    )
    flash("Step added.", "success")
    return redirect(url_for("flow_detail", flow_id=flow_id))


@app.route("/flows/<int:flow_id>/steps/<int:step_id>/delete", methods=["POST"])
def flow_delete_step(flow_id, step_id):
    step = FlowStep.get_by_id(step_id)
    step.delete_instance()
    # Re-number remaining steps
    remaining = (FlowStep.select()
                 .where(FlowStep.flow_id == flow_id)
                 .order_by(FlowStep.step_order))
    for i, s in enumerate(remaining, start=1):
        s.step_order = i
        s.save()
    flash("Step removed.", "success")
    return redirect(url_for("flow_detail", flow_id=flow_id))


@app.route("/flows/<int:flow_id>/enroll-test", methods=["POST"])
def flow_enroll_test(flow_id):
    flow = Flow.get_by_id(flow_id)
    email = request.form.get("test_email", "").strip().lower()
    if not email:
        flash("Enter a test email address.", "error")
        return redirect(url_for("flow_detail", flow_id=flow_id))

    contact, _ = Contact.get_or_create(
        email=email,
        defaults={"source": "manual", "subscribed": True}
    )
    _enroll_contact_in_flows(contact, flow.trigger_type, flow.trigger_value)

    # If trigger_type doesn't match, enroll directly
    first_step = (FlowStep.select()
                  .where(FlowStep.flow == flow)
                  .order_by(FlowStep.step_order)
                  .first())
    if first_step:
        try:
            FlowEnrollment.create(
                flow=flow,
                contact=contact,
                current_step=1,
                next_send_at=datetime.now(),
                status="active",
            )
            flash(f"Test contact {email} enrolled. Flow will send on next processor run.", "success")
        except Exception:
            flash(f"{email} is already enrolled in this flow.", "warning")
    else:
        flash("Add at least one step before enrolling a test contact.", "error")
    return redirect(url_for("flow_detail", flow_id=flow_id))


@app.route("/api/flows/<int:flow_id>/stats")
def api_flow_stats(flow_id):
    flow = Flow.get_by_id(flow_id)
    enrolled  = FlowEnrollment.select().where(FlowEnrollment.flow == flow).count()
    active    = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                               FlowEnrollment.status == "active").count()
    completed = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                               FlowEnrollment.status == "completed").count()
    fe    = FlowEmail.select().join(FlowEnrollment).where(FlowEnrollment.flow == flow)
    sent  = fe.where(FlowEmail.status == "sent").count()
    opened = fe.where(FlowEmail.opened == True).count()
    return jsonify({
        "enrolled": enrolled, "active": active, "completed": completed,
        "sent": sent,
        "open_rate": round(opened / sent * 100, 1) if sent > 0 else 0,
    })


@app.route("/track/flow-open/<int:enrollment_id>/<int:step_id>")
def track_flow_open(enrollment_id, step_id):
    try:
        fe = FlowEmail.get(
            FlowEmail.enrollment == enrollment_id,
            FlowEmail.step == step_id,
        )
        if not fe.opened:
            fe.opened    = True
            fe.opened_at = datetime.now()
            fe.save()
    except Exception:
        pass
    from flask import Response
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(pixel, mimetype="image/gif")


# ─────────────────────────────────
#  SETTINGS / TEST
# ─────────────────────────────────
@app.route("/settings")
def settings():
    import os
    config = {
        "aws_region":          os.getenv("AWS_REGION", ""),
        "aws_access_key":      ("*" * 16) if os.getenv("AWS_ACCESS_KEY_ID") else "",
        "shopify_store":       os.getenv("SHOPIFY_STORE_URL", ""),
        "shopify_token_set":   bool(os.getenv("SHOPIFY_ACCESS_TOKEN")),
        "from_email":          os.getenv("DEFAULT_FROM_EMAIL", ""),
    }
    return render_template("settings.html", config=config)

@app.route("/settings/test-ses", methods=["POST"])
def test_ses():
    test_email = request.form.get("test_email")
    success, message = test_ses_connection(test_email)
    if success:
        flash(f"SES connection works! Test email sent to {test_email}", "success")
    else:
        flash(f"SES Error: {message}", "error")
    return redirect(url_for("settings"))

# ─────────────────────────────────
#  API (for future automation)
# ─────────────────────────────────
@app.route("/api/contacts/count")
def api_contacts_count():
    return jsonify({"count": Contact.select().count()})

@app.route("/api/campaign/<int:campaign_id>/status")
def api_campaign_status(campaign_id):
    campaign = Campaign.get_by_id(campaign_id)
    sent     = CampaignEmail.select().where(CampaignEmail.campaign == campaign).count()
    return jsonify({"status": campaign.status, "sent": sent})

# ─────────────────────────────────
#  IT AGENT
# ─────────────────────────────────

try:
    import anthropic as _anthropic_lib
    _anthropic_available = True
except ImportError:
    _anthropic_available = False

_PROJECT_ROOT  = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_CLI    = r"C:\Users\davin\AppData\Roaming\Claude\claude-code\2.1.51\claude.exe"


def _get_agent_mode():
    """Return 'api' (Anthropic key), 'cli' (Claude Code), or 'none'."""
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return "api"
    if os.path.isfile(_CLAUDE_CLI):
        return "cli"
    return "none"


def _agent_via_cli(messages):
    """Send a chat turn through the local Claude Code CLI — no API key needed.
    Injects full conversation history into the prompt so context is preserved."""
    system_prompt = _agent_system_prompt()

    if len(messages) > 1:
        history_block = "=== Conversation so far ===\n"
        for msg in messages[:-1]:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_block += f"{role}: {msg['content']}\n\n"
        prompt = history_block + "=== Latest request ===\n" + messages[-1]["content"]
    else:
        prompt = messages[-1]["content"]

    try:
        # Strip CLAUDECODE env var so nested Claude Code session is allowed
        import copy as _copy
        clean_env = _copy.copy(os.environ)
        clean_env.pop("CLAUDECODE", None)
        clean_env.pop("CLAUDE_CODE_SESSION_ID", None)

        result = subprocess.run(
            [
                _CLAUDE_CLI, "-p", prompt,
                "--system-prompt", system_prompt,
                "--no-session-persistence",
                "--output-format", "text",
                "--add-dir", _PROJECT_ROOT,
                "--model", "haiku",
                "--dangerously-skip-permissions",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=_PROJECT_ROOT,
            env=clean_env,
        )
        text = result.stdout.strip()
        if text:
            return text, None
        err = result.stderr.strip() or "No output from Claude CLI"
        return None, err
    except subprocess.TimeoutExpired:
        return None, "Request timed out after 3 minutes."
    except Exception as e:
        return None, str(e)

AGENT_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of any file in the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root, e.g. 'app.py' or 'templates/base.html'"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a project file. Use for bug fixes, feature additions, and config changes. Always read the file first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
                "content": {"type": "string", "description": "Full file content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "run_health_check",
        "description": "Run health_check.py to verify all routes, imports, and templates are working.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "read_logs",
        "description": "Read recent entries from the server or watchdog log files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_type": {"type": "string", "enum": ["server", "watchdog"], "description": "Which log to read"},
                "lines": {"type": "integer", "description": "Number of lines from end of log (default: 60)"}
            },
            "required": ["log_type"]
        }
    },
    {
        "name": "query_db",
        "description": "Run a read-only SQL SELECT query against the SQLite database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL SELECT query (SELECT only — no INSERT/UPDATE/DELETE)"}
            },
            "required": ["sql"]
        }
    },
    {
        "name": "list_files",
        "description": "List all files in the project directory.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "restart_server",
        "description": "Restart the Flask server so code changes take effect. The watchdog will bring it back up automatically within 10 seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Reason for restarting"}
            },
            "required": ["reason"]
        }
    }
]


def _agent_execute_tool(tool_name, tool_input):
    """Execute an agent tool and return the result as a string."""

    if tool_name == "read_file":
        path = tool_input.get("path", "")
        full = os.path.normpath(os.path.join(_PROJECT_ROOT, path))
        if not full.startswith(_PROJECT_ROOT):
            return "Error: path is outside the project root."
        try:
            with open(full, "r", encoding="utf-8") as f:
                data = f.read()
            return data if data else "(empty file)"
        except FileNotFoundError:
            return f"Error: file not found — {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    elif tool_name == "write_file":
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")
        full = os.path.normpath(os.path.join(_PROJECT_ROOT, path))
        if not full.startswith(_PROJECT_ROOT):
            return "Error: path is outside the project root."
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Wrote {len(content):,} characters to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    elif tool_name == "run_health_check":
        try:
            result = subprocess.run(
                [sys.executable, "health_check.py"],
                cwd=_PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = (result.stdout + result.stderr).strip()
            return out if out else "Health check completed (no output)."
        except subprocess.TimeoutExpired:
            return "Error: health check timed out after 30 seconds."
        except Exception as e:
            return f"Error running health check: {e}"

    elif tool_name == "read_logs":
        log_type = tool_input.get("log_type", "server")
        lines = int(tool_input.get("lines", 60))
        log_file = os.path.join(_PROJECT_ROOT, f"{log_type}.log")
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:]) if all_lines else "(empty log)"
        except FileNotFoundError:
            return f"Log file not found: {log_type}.log"
        except Exception as e:
            return f"Error reading log: {e}"

    elif tool_name == "query_db":
        sql = tool_input.get("sql", "").strip()
        if not sql.upper().startswith("SELECT"):
            return "Error: only SELECT queries are permitted."
        try:
            cursor = db.execute_sql(sql)
            rows = cursor.fetchall()
            if not rows:
                return "Query returned 0 rows."
            cols = [d[0] for d in cursor.description]
            result = [cols] + [list(r) for r in rows[:100]]
            return json.dumps(result, default=str, indent=2)
        except Exception as e:
            return f"Error querying DB: {e}"

    elif tool_name == "list_files":
        out = []
        for root, dirs, files in os.walk(_PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules") and not d.startswith(".")]
            for fn in files:
                if not fn.endswith((".pyc", ".db-journal", ".db-shm", ".db-wal")):
                    rel = os.path.relpath(os.path.join(root, fn), _PROJECT_ROOT)
                    out.append(rel.replace("\\", "/"))
        return "\n".join(sorted(out))

    elif tool_name == "restart_server":
        reason = tool_input.get("reason", "code changes applied")
        def _do_restart():
            import time
            time.sleep(1.5)
            os._exit(1)  # watchdog detects exit and restarts
        threading.Thread(target=_do_restart, daemon=True).start()
        return f"Restart initiated ({reason}). Server will be back in ~10 seconds."

    return f"Unknown tool: {tool_name}"


def _agent_system_prompt():
    """Build the live system prompt injecting CLAUDE.md + current DB stats."""
    claude_md = ""
    try:
        with open(os.path.join(_PROJECT_ROOT, "CLAUDE.md"), "r", encoding="utf-8") as f:
            claude_md = f.read()
    except Exception:
        claude_md = "(CLAUDE.md not found)"

    try:
        total_contacts  = Contact.select().count()
        subscribed      = Contact.select().where(Contact.subscribed == True).count()
        total_campaigns = Campaign.select().count()
        total_templates = EmailTemplate.select().count()
        total_flows     = Flow.select().count()
        stats = (f"Contacts: {total_contacts:,} total, {subscribed:,} subscribed | "
                 f"Campaigns: {total_campaigns} | Templates: {total_templates} | Flows: {total_flows}")
    except Exception:
        stats = "(could not load live stats)"

    return f"""You are the dedicated IT agent for MailEngine — an in-house email marketing platform built for Davinder.
Your sole job is to maintain, debug, monitor, and improve this project 24/7.

## Project Knowledge
{claude_md}

## Live Stats (right now)
{stats}

## Your Tools
- **read_file** — always read a file before modifying it
- **write_file** — fix bugs, add features, update config
- **run_health_check** — verify all routes/templates/imports after changes
- **read_logs** — diagnose crashes, errors, unexpected behaviour
- **query_db** — analyse data, check counts, debug issues (SELECT only — never modify data)
- **list_files** — see all project files
- **restart_server** — apply code changes (watchdog auto-restarts within ~10s)

## Rules
- Always read before writing. Run health_check after significant code changes.
- Restart the server after code changes for them to take effect.
- Be direct and concise. Show your work briefly. Don't be verbose.
- Today: {datetime.now().strftime('%Y-%m-%d %H:%M')}"""


@app.route("/agent")
def agent():
    mode = _get_agent_mode()
    history = AgentMessage.select().order_by(AgentMessage.id.asc()).limit(200)
    return render_template("agent.html", agent_mode=mode, history=history)


@app.route("/api/agent/chat", methods=["POST"])
def api_agent_chat():
    mode = _get_agent_mode()

    if mode == "none":
        return jsonify({"error": "No agent available. Add ANTHROPIC_API_KEY to .env, or ensure Claude Code is installed."}), 503

    data = request.get_json() or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Save user message
    AgentMessage.create(role="user", content=user_message)

    # Build conversation history (last 40 messages = ~20 turns)
    recent = list(AgentMessage.select().order_by(AgentMessage.id.asc()).limit(40))
    messages = [{"role": m.role, "content": m.content} for m in recent]

    # ── Claude Code CLI mode (no API key needed) ──
    if mode == "cli":
        text, error = _agent_via_cli(messages)
        if error:
            return jsonify({"error": error}), 500
        AgentMessage.create(role="assistant", content=text, tool_calls="[]")
        return jsonify({"response": text, "tool_calls": [], "mode": "cli"})

    # ── Direct Anthropic API mode (API key in .env) ──
    if not _anthropic_available:
        return jsonify({"error": "anthropic package not installed — run: pip install anthropic"}), 503

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    client = _anthropic_lib.Anthropic(api_key=api_key)
    tool_log = []

    try:
        while True:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=_agent_system_prompt(),
                tools=AGENT_TOOLS,
                messages=messages,
            )

            text_parts, tool_blocks = [], []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_blocks.append(block)

            if response.stop_reason == "end_turn" or not tool_blocks:
                final_text = "\n".join(text_parts).strip()
                AgentMessage.create(
                    role="assistant",
                    content=final_text,
                    tool_calls=json.dumps(tool_log),
                )
                return jsonify({"response": final_text, "tool_calls": tool_log, "mode": "api"})

            # Execute tools and loop
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_blocks:
                result = _agent_execute_tool(block.name, block.input)
                tool_log.append({
                    "tool": block.name,
                    "input": block.input,
                    "result": result[:1000] + "…" if len(result) > 1000 else result,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agent/clear", methods=["POST"])
def api_agent_clear():
    AgentMessage.delete().execute()
    return jsonify({"ok": True})




# ─────────────────────────────────────────────────────────────────────────────
# Customer Profiles Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/profiles")
def profiles_list():
    from database import Contact, CustomerProfile, ShopifyOrder
    import json as _json
    from peewee import fn

    q        = request.args.get("q", "").strip()
    tier     = request.args.get("tier", "")
    category = request.args.get("category", "")
    sort     = request.args.get("sort", "spent_desc")
    page     = int(request.args.get("page", 1))
    per_page = 50

    # Base query — join Contact + CustomerProfile
    query = (CustomerProfile
             .select(CustomerProfile, Contact)
             .join(Contact, on=(CustomerProfile.contact_id == Contact.id))
             .where(CustomerProfile.contact_id.is_null(False)))

    if q:
        query = query.where(
            (Contact.email.contains(q)) |
            (Contact.first_name.contains(q)) |
            (Contact.last_name.contains(q))
        )
    if tier:
        query = query.where(CustomerProfile.price_tier == tier)
    if category:
        query = query.where(CustomerProfile.top_categories.contains(category))

    # Sorting
    if sort == "orders_desc":
        query = query.order_by(CustomerProfile.total_orders.desc())
    elif sort == "recent":
        query = query.order_by(CustomerProfile.days_since_last_order.asc())
    elif sort == "lapsed":
        query = query.order_by(CustomerProfile.days_since_last_order.desc())
    elif sort == "name":
        query = query.order_by(Contact.first_name.asc())
    else:  # spent_desc
        query = query.order_by(CustomerProfile.total_spent.desc())

    total       = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page        = max(1, min(page, total_pages))
    rows_raw    = list(query.paginate(page, per_page))

    # Build display rows
    profiles = []
    for p in rows_raw:
        contact = p.contact
        top_cats = _json.loads(p.top_categories or "[]")
        profiles.append({
            "contact_id":     contact.id,
            "name":           f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email,
            "email":          contact.email,
            "total_orders":   p.total_orders,
            "total_spent":    p.total_spent,
            "days_since":     p.days_since_last_order,
            "top_category":   top_cats[0] if top_cats else "",
            "price_tier":     p.price_tier,
            "has_used_discount": p.has_used_discount,
            "location":       f"{p.city}, {p.province}".strip(", ") if (p.city or p.province) else "",
        })

    # Aggregate stats
    all_profiles = list(CustomerProfile.select())
    buyers          = sum(1 for p in all_profiles if p.total_orders > 0)
    no_purchase     = len(all_profiles) - buyers
    repeat_buyers   = sum(1 for p in all_profiles if p.total_orders >= 2)
    discount_users  = sum(1 for p in all_profiles if p.has_used_discount)
    total_contacts  = CustomerProfile.select().count()
    spent_vals      = [p.avg_order_value for p in all_profiles if p.avg_order_value > 0]
    avg_order_value = sum(spent_vals) / len(spent_vals) if spent_vals else 0

    # Build query string for pagination (exclude page)
    qs_parts = []
    for k, v in [("q", q), ("tier", tier), ("category", category), ("sort", sort)]:
        if v:
            qs_parts.append(f"{k}={v}")
    query_string = "&".join(qs_parts)

    return render_template("profiles.html",
        profiles=profiles,
        total=total,
        buyers=buyers,
        no_purchase=no_purchase,
        repeat_buyers=repeat_buyers,
        discount_users=discount_users,
        avg_order_value=avg_order_value,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        q=q, tier=tier, category=category, sort=sort,
        query_string=query_string,
    )


@app.route("/profiles/<int:contact_id>")
def profile_detail(contact_id):
    from database import Contact, CustomerProfile, ShopifyOrder, ShopifyOrderItem, CampaignEmail
    import json as _json

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return "Contact not found", 404

    profile = CustomerProfile.get_or_none(CustomerProfile.contact_id == contact_id)
    if not profile:
        return "Profile not found", 404

    # Orders with line items
    orders = list(
        ShopifyOrder.select()
        .where(ShopifyOrder.email == contact.email.lower())
        .order_by(ShopifyOrder.ordered_at.desc())
    )
    for o in orders:
        o.items = list(ShopifyOrderItem.select().where(ShopifyOrderItem.order_id == o.id))

    # Top products from profile
    top_products = _json.loads(profile.top_products or "[]")

    # Category breakdown with counts
    all_bought = _json.loads(profile.all_products_bought or "[]")
    cat_counts = {}
    for item in all_bought:
        cat = item.get("category", "Other")
        cat_counts[cat] = cat_counts.get(cat, 0) + item.get("qty", 1)
    top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])

    # Email activity (last 20)
    email_activity = list(
        CampaignEmail.select()
        .where(CampaignEmail.contact_id == contact_id)
        .order_by(CampaignEmail.created_at.desc())
        .limit(20)
    )

    # Website activity / journey (last 30 events)
    from database import CustomerActivity
    import json as _json2
    raw_activity = list(
        CustomerActivity.select()
        .where(CustomerActivity.email == contact.email.lower())
        .order_by(CustomerActivity.occurred_at.desc())
        .limit(30)
    )
    website_activity = []
    for a in raw_activity:
        try:
            data = _json2.loads(a.event_data or '{}')
        except Exception:
            data = {}
        website_activity.append({
            'event_type': a.event_type,
            'event_data': data,
            'source':     a.source,
            'occurred_at': a.occurred_at,
        })

    # Marketing intel — intent signals
    product_views = {}
    search_terms = []
    blog_reads = []
    bought_products = set(top_products)
    for a in raw_activity:
        try:
            data = _json2.loads(a.event_data or '{}')
        except Exception:
            data = {}
        if a.event_type == 'viewed_product' and data.get('product_title'):
            t = data['product_title']
            product_views[t] = product_views.get(t, 0) + 1
        elif a.event_type == 'searched' and data.get('query'):
            if data['query'] not in search_terms:
                search_terms.append(data['query'])
        elif a.event_type == 'viewed_blog' and data.get('article_title'):
            if data['article_title'] not in blog_reads:
                blog_reads.append(data['article_title'])

    # Products viewed but never purchased
    viewed_not_bought = [(p, c) for p, c in sorted(product_views.items(), key=lambda x: -x[1])
                         if p not in bought_products]
    top_intent = viewed_not_bought[0][0] if viewed_not_bought else ''

    # Pre-generate personalised email body suggestion for the "Send Targeted Email" modal
    _lines = [f"Hi {contact.first_name or 'there'},", ""]
    if top_intent:
        _lines += [
            f"We noticed you've been checking out the {top_intent}.",
            "We'd love to help you get it — here's a special offer just for you:",
            "",
            "Shop now: https://ldas-electronics.com",
            "",
        ]
    if search_terms:
        _lines.append(f"We also see you've been searching for: {', '.join(search_terms[:2])}. We have great options waiting for you!")
        _lines.append("")
    _lines += [
        "If you have any questions, we're always happy to help.",
        "",
        "Best regards,",
        "The LDAS Electronics Team",
    ]
    quick_email_body_text = "\n".join(_lines)

    # Phase G: Load intelligence data
    import json as _json2
    _pending_triggers = []
    _product_recs = []
    _churn_label = "unknown"
    _churn_color = "var(--text3)"
    try:
        from database import PendingTrigger
        _pending_triggers = list(PendingTrigger.select()
            .where(PendingTrigger.email == contact.email)
            .where(PendingTrigger.status == "pending")
            .order_by(PendingTrigger.detected_at.desc())
            .limit(5))
    except:
        pass
    try:
        if profile and profile.product_recommendations:
            _product_recs = _json2.loads(profile.product_recommendations or "[]")[:5]
    except:
        pass
    if profile:
        cr = profile.churn_risk
        if cr < 1.0:
            _churn_label = "On Track"
            _churn_color = "var(--green)"
        elif cr < 1.5:
            _churn_label = "Overdue"
            _churn_color = "var(--amber)"
        elif cr < 2.0:
            _churn_label = "At Risk"
            _churn_color = "var(--red)"
        else:
            _churn_label = "Likely Churned"
            _churn_color = "var(--red)"

    return render_template("profile_detail.html",
        contact=contact,
        profile=profile,
        orders=orders,
        top_products=top_products,
        top_categories=top_categories,
        email_activity=email_activity,
        website_activity=website_activity,
        viewed_not_bought=viewed_not_bought[:5],
        search_terms=search_terms[:5],
        blog_reads=blog_reads[:5],
        top_intent_product=top_intent,
        quick_email_body=quick_email_body_text,
        pending_triggers=_pending_triggers,
        product_recs=_product_recs,
        churn_label=_churn_label,
        churn_color=_churn_color,
    )



@app.route("/profiles/<int:contact_id>/send-quick-email", methods=["POST"])
def send_quick_email(contact_id):
    """Send a one-off targeted email to a single contact."""
    from email_sender import send_campaign_email
    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact or not contact.email:
        return jsonify({"success": False, "error": "Contact not found"}), 404
    subject   = request.form.get("subject", "").strip()
    html_body = request.form.get("html_body", "").strip()
    from_name  = "LDAS Electronics"
    from_email = os.getenv("DEFAULT_FROM_EMAIL", "news@news.ldaselectronics.com")
    if not subject or not html_body:
        return jsonify({"success": False, "error": "Subject and message body are required"}), 400
    success, error = send_campaign_email(
        to_email=contact.email,
        to_name=(contact.first_name or contact.email),
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        html_body=html_body,
    )
    if success:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": error or "Send failed"}), 500


@app.route("/profiles/<int:contact_id>/ai-email-preview", methods=["POST"])
def ai_email_preview(contact_id):
    """Generate an AI email preview for a specific contact."""
    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    purpose = request.form.get("purpose", "winback").strip()

    try:
        from ai_engine import generate_personalized_email
        result = generate_personalized_email(contact.email, purpose)
        if result:
            return jsonify({
                "success": True,
                "subject": result["subject"],
                "body_text": result["body_text"],
                "body_html": result["body_html"],
                "reasoning": result["reasoning"],
            })
        else:
            return jsonify({"success": False, "error": "AI generation failed"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/ai-engine")
def ai_engine_dashboard():
    from database import ContactScore, AIMarketingPlan, AIDecisionLog, CustomerProfile, PendingTrigger, AIGeneratedEmail
    from peewee import fn

    segments = {}
    for seg in ["champion", "loyal", "potential", "at_risk", "lapsed", "new"]:
        segments[seg] = ContactScore.select().where(ContactScore.rfm_segment == seg).count()
    today = datetime.now().strftime("%Y-%m-%d")
    plan = AIMarketingPlan.get_or_none(AIMarketingPlan.plan_date == today)
    recent_plans = list(AIMarketingPlan.select().order_by(AIMarketingPlan.created_at.desc()).limit(7))
    recent_decisions = list(AIDecisionLog.select().order_by(AIDecisionLog.id.desc()).limit(50))
    total_scored = ContactScore.select().count()

    # Phase G: Churn distribution
    churn_dist = {"on_track": 0, "overdue": 0, "at_risk": 0, "churned": 0, "never_purchased": 0}
    try:
        churn_dist["on_track"] = CustomerProfile.select().where(
            CustomerProfile.total_orders > 0, CustomerProfile.churn_risk < 1.0).count()
        churn_dist["overdue"] = CustomerProfile.select().where(
            CustomerProfile.total_orders > 0, CustomerProfile.churn_risk >= 1.0, CustomerProfile.churn_risk < 1.5).count()
        churn_dist["at_risk"] = CustomerProfile.select().where(
            CustomerProfile.total_orders > 0, CustomerProfile.churn_risk >= 1.5, CustomerProfile.churn_risk < 2.0).count()
        churn_dist["churned"] = CustomerProfile.select().where(
            CustomerProfile.total_orders > 0, CustomerProfile.churn_risk >= 2.0).count()
        churn_dist["never_purchased"] = CustomerProfile.select().where(
            CustomerProfile.total_orders == 0).count()
    except:
        pass

    # Phase G: Revenue at risk
    revenue_at_risk = 0.0
    revenue_on_track = 0.0
    try:
        rev_risk = CustomerProfile.select(fn.SUM(CustomerProfile.predicted_ltv)).where(
            CustomerProfile.churn_risk >= 1.5, CustomerProfile.total_orders > 0).scalar()
        revenue_at_risk = rev_risk or 0.0
        rev_ok = CustomerProfile.select(fn.SUM(CustomerProfile.predicted_ltv)).where(
            CustomerProfile.churn_risk < 1.0, CustomerProfile.total_orders > 0).scalar()
        revenue_on_track = rev_ok or 0.0
    except:
        pass

    # Phase G: Pending triggers
    trigger_counts = {"browse_abandonment": 0, "cart_abandonment": 0, "churn_risk_high": 0, "high_engagement_no_purchase": 0}
    total_triggers = 0
    try:
        for tt in trigger_counts:
            c = PendingTrigger.select().where(PendingTrigger.trigger_type == tt, PendingTrigger.status == "pending").count()
            trigger_counts[tt] = c
            total_triggers += c
    except:
        pass

    # Phase G: Recent AI emails
    recent_ai_emails = []
    total_ai_emails = 0
    try:
        recent_ai_emails = list(AIGeneratedEmail.select().order_by(AIGeneratedEmail.generated_at.desc()).limit(10))
        total_ai_emails = AIGeneratedEmail.select().count()
    except:
        pass

    # Phase G: Top recommended products across all customers
    top_recs = {}
    try:
        import json as _json6
        profiles_with_recs = CustomerProfile.select().where(
            CustomerProfile.product_recommendations != "[]",
            CustomerProfile.product_recommendations != "")
        for p in profiles_with_recs:
            try:
                recs = _json6.loads(p.product_recommendations or "[]")
                for r in recs:
                    top_recs[r] = top_recs.get(r, 0) + 1
            except:
                pass
        top_recs = sorted(top_recs.items(), key=lambda x: -x[1])[:10]
    except:
        top_recs = []

    return render_template("ai_engine.html",
        segments=segments, plan=plan, recent_plans=recent_plans,
        recent_decisions=recent_decisions, total_scored=total_scored,
        churn_dist=churn_dist, revenue_at_risk=revenue_at_risk,
        revenue_on_track=revenue_on_track,
        trigger_counts=trigger_counts, total_triggers=total_triggers,
        recent_ai_emails=recent_ai_emails, total_ai_emails=total_ai_emails,
        top_recs=top_recs)



@app.route("/api/ai-engine/sample-email", methods=["POST"])
def ai_engine_sample_email():
    """Pick a random contact from a segment and generate an AI email preview."""
    from database import ContactScore, Contact
    import random as _random

    data = request.get_json(silent=True) or {}
    segment = data.get("segment", "at_risk")
    purpose = data.get("purpose", "winback")

    try:
        # Get contacts in this segment
        scored = list(ContactScore.select()
            .where(ContactScore.rfm_segment == segment)
            .limit(50))

        if not scored:
            return jsonify({"success": False, "error": "No contacts in segment: " + segment}), 404

        # Pick a random one
        pick = _random.choice(scored)
        contact = pick.contact

        # Generate AI email
        from ai_engine import generate_personalized_email
        result = generate_personalized_email(contact.email, purpose)

        if result:
            return jsonify({
                "success": True,
                "email": contact.email,
                "subject": result["subject"],
                "body_text": result["body_text"],
                "body_html": result.get("body_html", ""),
                "reasoning": result["reasoning"],
            })
        else:
            return jsonify({"success": False, "error": "AI generation returned empty"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/ai-engine/run-now", methods=["POST"])
def ai_engine_run_now():
    if not _ai_engine_available:
        return jsonify({"error": "AI engine not available"}), 503
    import threading
    def _run():
        run_nightly_scoring()
        plan_id = __import__("ai_engine").generate_daily_plan()
        __import__("ai_engine").execute_plan(plan_id)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "AI engine started — check dashboard in ~60 seconds"})



# ─────────────────────────────────────────────────────────────
#  CUSTOMER ACTIVITY FEED
# ─────────────────────────────────────────────────────────────

@app.route("/activity")
def activity_feed():
    from database import CustomerActivity, Contact, init_db
    import json as _json
    init_db()

    event_filter = request.args.get("type", "")

    query = (CustomerActivity.select()
             .order_by(CustomerActivity.occurred_at.desc()))

    if event_filter:
        query = query.where(CustomerActivity.event_type == event_filter)

    activities = list(query.limit(200))

    # Enrich with contact names
    email_to_name = {}
    for c in Contact.select(Contact.email, Contact.first_name, Contact.last_name):
        name = f"{c.first_name or ''} {c.last_name or ''}".strip() or c.email
        email_to_name[c.email.lower()] = name

    feed = []
    for a in activities:
        try:
            data = _json.loads(a.event_data or "{}")
        except Exception:
            data = {}
        feed.append({
            "id":          a.id,
            "email":       a.email,
            "name":        email_to_name.get((a.email or "").lower(), a.email or "Unknown"),
            "event_type":  a.event_type,
            "event_data":  data,
            "source":      a.source,
            "occurred_at": a.occurred_at,
        })

    # Stats
    from datetime import datetime, timedelta
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = datetime.now() - timedelta(days=7)

    total_events   = CustomerActivity.select().count()
    today_events   = CustomerActivity.select().where(CustomerActivity.occurred_at >= today_start).count()
    week_events    = CustomerActivity.select().where(CustomerActivity.occurred_at >= week_start).count()
    abandoned      = CustomerActivity.select().where(CustomerActivity.event_type == "abandoned_checkout").count()
    checkouts_done = CustomerActivity.select().where(CustomerActivity.event_type == "completed_checkout").count()
    placed_orders  = CustomerActivity.select().where(CustomerActivity.event_type == "placed_order").count()

    return render_template("activity.html",
        feed=feed,
        total_events=total_events,
        today_events=today_events,
        week_events=week_events,
        abandoned=abandoned,
        checkouts_done=checkouts_done,
        placed_orders=placed_orders,
        event_filter=event_filter,
    )


@app.route("/api/activity/feed")
def api_activity_feed():
    """JSON endpoint for live auto-refresh of activity feed."""
    from database import CustomerActivity, Contact, init_db
    import json as _json
    init_db()

    since_id     = request.args.get("since_id", 0, type=int)
    event_filter = request.args.get("type", "")
    limit        = request.args.get("limit", 20, type=int)

    query = (CustomerActivity.select()
             .order_by(CustomerActivity.id.desc())
             .limit(limit))

    if since_id:
        query = query.where(CustomerActivity.id > since_id)
    if event_filter:
        query = query.where(CustomerActivity.event_type == event_filter)

    email_to_name = {}
    for c in Contact.select(Contact.email, Contact.first_name, Contact.last_name):
        name = f"{c.first_name or ''} {c.last_name or ''}".strip() or c.email
        email_to_name[c.email.lower()] = name

    items = []
    for a in query:
        try:
            data = _json.loads(a.event_data or "{}")
        except Exception:
            data = {}
        items.append({
            "id":         a.id,
            "email":      a.email,
            "name":       email_to_name.get((a.email or "").lower(), a.email or "Unknown"),
            "event_type": a.event_type,
            "event_data": data,
            "source":     a.source,
            "occurred_at": a.occurred_at.isoformat() if a.occurred_at else "",
        })

    return jsonify({"items": items, "count": len(items)})



@app.route("/api/identify", methods=["POST", "OPTIONS"])
def identify_visitor():
    from database import CustomerActivity, Contact
    """Retroactively link a session_id to an email — session stitching."""
    # CORS — called from Shopify checkout domain (checkout.shopify.com)
    if request.method == "OPTIONS":
        resp = Response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    email      = (data.get("email")      or "").strip().lower()

    if not session_id or not email or "@" not in email:
        resp = jsonify({"ok": False, "error": "session_id and valid email required"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 400

    # Link to known contact if they exist
    contact = Contact.get_or_none(Contact.email == email)
    contact_id = contact.id if contact else None

    # Retroactively stitch: update all anonymous events in this session
    updated = (
        CustomerActivity.update(email=email, contact_id=contact_id)
        .where(CustomerActivity.session_id == session_id)
        .where(CustomerActivity.email == "")
        .execute()
    )

    app.logger.info(f"Identified session {session_id[:8]}… → {email} ({updated} events stitched)")

    # Create Contact + CustomerProfile stub for first-time emails
    is_new_contact = False
    if not contact:
        try:
            from database import CustomerProfile
            contact = Contact.create(
                email=email,
                source="pixel_capture",
                subscribed=False,
                created_at=datetime.now()
            )
            CustomerProfile.get_or_create(
                contact=contact,
                defaults={"email": email, "last_computed_at": datetime.now()}
            )
            is_new_contact = True
            app.logger.info(f"New contact created from pixel capture: {email}")
        except Exception as _ce:
            app.logger.warning(f"Pixel contact create failed ({email}): {_ce}")

    # Trigger async single-profile enrichment in background
    if updated > 0 or is_new_contact:
        import threading as _th
        _email_copy = email
        def _enrich_bg():
            import sys as _s; _s.path.insert(0, '/var/www/mailengine')
            from activity_sync import enrich_single_profile
            enrich_single_profile(_email_copy)
        _th.Thread(target=_enrich_bg, daemon=True).start()

    resp = jsonify({"ok": True, "updated": updated, "new_contact": is_new_contact})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

@app.route("/api/track", methods=["POST", "OPTIONS"])
def track_event():
    """Receive events from the website tracking pixel. CORS-enabled."""
    from database import CustomerActivity, Contact, init_db
    import json as _json
    init_db()

    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
    }

    if request.method == "OPTIONS":
        return "", 204, cors_headers

    try:
        payload    = request.get_json(silent=True) or {}
        email      = (payload.get("email") or "").lower().strip()
        event_type = payload.get("event_type", "pixel_event")
        event_data = payload.get("event_data", {})
        session_id = payload.get("session_id", "")

        contact_id = None
        if email:
            c = Contact.get_or_none(Contact.email == email)
            if c:
                contact_id = c.id

        CustomerActivity.create(
            contact_id  = contact_id,
            email       = email,
            event_type  = event_type,
            event_data  = _json.dumps(event_data),
            source      = "pixel",
            source_ref  = "",
            session_id  = session_id,
            occurred_at = datetime.now(),
        )
        # Real-time last_active_at for known profiles (lightweight — no full re-analysis)
        if email:
            try:
                from database import CustomerProfile
                CustomerProfile.update(last_active_at=datetime.now())                     .where(CustomerProfile.email == email)                     .execute()
            except Exception:
                pass

        return jsonify({"ok": True}), 200, cors_headers
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400, cors_headers


@app.route("/activity/sync", methods=["POST"])
def activity_sync_trigger():
    """Manually trigger full activity sync in background."""
    import threading, sys as _sys
    def _run():
        _sys.path.insert(0, "/var/www/mailengine")
        import activity_sync as _sync
        _sync.run_full_activity_sync()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Activity sync started — refresh in ~30 seconds"})


# ─────────────────────────────────
#  START BACKGROUND SCHEDULER
# ─────────────────────────────────
# Guard prevents double-scheduling in Flask debug/reloader mode.
if not _scheduler.running:
    _scheduler.add_job(_process_flow_enrollments, "interval", seconds=60,
                       id="flow_processor", replace_existing=True)
    _scheduler.add_job(_check_passive_triggers, "interval", minutes=30,
                       id="passive_triggers", replace_existing=True)
    if _ai_engine_available:  # AI cron jobs disabled until platform is ready
        pass
#        _scheduler.add_job(run_nightly_scoring, "cron", hour=1, minute=0, id="ai_scoring", replace_existing=True)
#        _scheduler.add_job(run_nightly_plan, "cron", hour=2, minute=0, id="ai_plan", replace_existing=True)
    # Nightly activity sync + profile enrichment at 3am
    def _run_nightly_activity_sync():
        try:
            import sys as _s; _s.path.insert(0, '/var/www/mailengine')
            from activity_sync import run_full_activity_sync
            app.logger.info("Nightly activity sync starting...")
            results = run_full_activity_sync()
            app.logger.info(f"Nightly activity sync complete: {results}")
        except Exception as _e:
            app.logger.error(f"Nightly activity sync failed: {_e}")

    _scheduler.add_job(_run_nightly_activity_sync, "cron", hour=3, minute=0,
                       id="activity_sync_nightly", replace_existing=True)

    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))

if __name__ == "__main__":
    init_db()
    print("\n" + "="*50)
    print("  Email Marketing Platform Running!")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
