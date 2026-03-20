"""
=====================================
  IN-HOUSE EMAIL MARKETING PLATFORM
  Built for Davinder | Powered by Amazon SES
=====================================
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from database import (db, Contact, EmailTemplate, Campaign, CampaignEmail, init_db,
                      WarmupConfig, WarmupLog, get_warmup_config,
                      Flow, FlowStep, FlowEnrollment, FlowEmail, AbandonedCheckout, AgentMessage,
                      SuppressionEntry, BounceLog, PreflightLog, PendingTrigger,
                      get_bounce_stats_by_domain, AutoEmail, DeliveryQueue)
from email_sender import send_campaign_email, test_ses_connection
from discount_engine import generate_discount_code
from token_utils import create_token, verify_token
from shopify_sync import sync_shopify_customers, verify_shopify_webhook, handle_shopify_customer_webhook
import json
import os
import fcntl
import subprocess
import sys
from datetime import datetime, date, timedelta
import threading

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_key_2024")

from studio_routes import studio_bp
app.register_blueprint(studio_bp)
SITE_URL = os.environ.get("SITE_URL", "https://mailenginehub.com").rstrip("/")
APP_DIR  = os.path.dirname(os.path.abspath(__file__))


def _tag_match(tag):
    """Exact comma-delimited tag match. Prevents 'vip' matching 'vip-gold'."""
    from peewee import SQL
    return SQL("(',' || tags || ',') LIKE ?", '%,' + tag.strip() + ',%')

@app.template_filter("fromjson")
def _fromjson(s):
    import json
    try: return json.loads(s)
    except: return []

# ── Toronto / Eastern Time filter ──
from zoneinfo import ZoneInfo
_ET = ZoneInfo("America/Toronto")
_UTC = ZoneInfo("UTC")

@app.template_filter("et")
def _format_eastern(dt, fmt="%b %d, %Y %I:%M %p"):
    """Convert naive-UTC datetime to Toronto/Eastern and format with AM/PM."""
    if not dt:
        return ""
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt.astimezone(_ET).strftime(fmt)
    except Exception:
        return str(dt)

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
    # Public routes: unsubscribe links, tracking pixels, webhooks, pixel
    public_prefixes = (
        "/contacts/unsubscribe",   # legacy unsubscribe format
        "/unsubscribe/",           # new token-based unsubscribe
        "/track/",                 # tracking pixels (old + new)
        "/webhooks/",              # SES + Shopify webhooks
        "/static/",                # static assets (popup widget JS)
    )
    if any(request.path.startswith(p) for p in public_prefixes):
        return
    if request.path in ("/api/track", "/api/identify", "/api/subscribe"):
        return  # Shopify pixel / identity resolution + popup subscribe — public
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return _request_auth()

# ─────────────────────────────────
#  BACKGROUND SCHEDULER (flows)
# ─────────────────────────────────
# Import here to keep it near the app definition; scheduler is started
# at the bottom after all route/function definitions are in place.
from apscheduler.schedulers.background import BackgroundScheduler
# ai_engine scoring is called inside scheduler job and Run Now endpoint
_ai_engine_available = True  # Kept for backward compat with dashboard check

def _cascade_by_email(email, trigger="unknown"):
    """Background cascade for a contact identified by email."""
    import threading
    def _do():
        try:
            c = Contact.get_or_none(Contact.email == email)
            if c:
                from cascade import cascade_contact
                cascade_contact(c.id, trigger=trigger)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()
import atexit

# ─────────────────────────────────
#  SES BOUNCE/COMPLAINT WEBHOOK
# ─────────────────────────────────
@app.route("/webhooks/ses", methods=["POST"])
def ses_webhook():
    """Handle SES bounce/complaint notifications via SNS.
    Verifies SNS message signature before processing."""
    import json as json_module
    try:
        # SNS sends with Content-Type: text/plain — use get_data() for raw body
        raw_body = request.get_data(as_text=True)
        if not raw_body:
            print("[SES webhook] Empty request body")
            return jsonify({"status": "ok", "message": "empty body"}), 200

        payload = json_module.loads(raw_body)

        # ── SNS Signature Verification ───────────────────
        try:
            from sns_verify import verify_sns_message
            is_valid, verify_error = verify_sns_message(payload)
            if not is_valid:
                print(f"[SES webhook] Signature verification FAILED: {verify_error}")
                return jsonify({"error": "Signature verification failed"}), 403
        except ImportError:
            print("[SES webhook] sns_verify not available — skipping verification")
        except Exception as verify_ex:
            print(f"[SES webhook] Verification error (allowing): {verify_ex}")

        msg_type = request.headers.get("x-amz-sns-message-type", "") or payload.get("Type", "")

        # ── Subscription confirmation ────────────────────
        if msg_type == "SubscriptionConfirmation":
            subscribe_url = payload.get("SubscribeURL", "")
            if subscribe_url:
                import requests as req
                req.get(subscribe_url, timeout=10)
                print(f"[SNS] Confirmed subscription: {subscribe_url[:80]}")
            return jsonify({"status": "confirmed"}), 200

        # ── Notification ─────────────────────────────────
        if msg_type == "Notification":
            message = json_module.loads(payload.get("Message", "{}"))
            notif_type = message.get("notificationType", "")

            # ── Extract attribution from SES mail object ──
            mail_obj = message.get("mail", {})
            ses_msg_id = mail_obj.get("messageId", "")
            ses_tags = mail_obj.get("tags", {})
            attr_campaign_id = 0
            attr_template_id = 0
            attr_subject = ""
            try:
                attr_campaign_id = int(ses_tags.get("campaign_id", ["0"])[0])
            except (IndexError, ValueError, TypeError) as e:
                app.logger.warning("[SilentFix] Bounce campaign attribution: %s" % e)
            try:
                attr_template_id = int(ses_tags.get("template_id", ["0"])[0])
            except (IndexError, ValueError, TypeError):
                pass
            # Look up subject from campaign
            if attr_campaign_id:
                try:
                    _camp = Campaign.get_by_id(attr_campaign_id)
                    _tpl = EmailTemplate.get_by_id(_camp.template_id)
                    attr_subject = _tpl.subject[:50]
                    if not attr_template_id:
                        attr_template_id = _camp.template_id
                except Exception:
                    pass
            # Try to attribute to AutoEmail if no campaign_id
            auto_email_bounce_type = None
            if not attr_campaign_id and ses_msg_id:
                try:
                    ae = AutoEmail.select().where(
                        AutoEmail.ses_message_id == ses_msg_id
                    ).first()
                    if ae:
                        auto_email_bounce_type = message.get("bounce", {}).get("bounceType") or \
                                                 ("complaint" if message.get("notificationType") == "Complaint" else "bounce")
                        ae.status = "bounced"
                        ae.error_msg = auto_email_bounce_type
                        ae.save()
                        if not attr_template_id:
                            attr_template_id = ae.template_id
                except Exception:
                    pass

            if notif_type == "Bounce":
                bounce = message.get("bounce", {})
                bounce_type = bounce.get("bounceType", "")
                recipients = bounce.get("bouncedRecipients", [])

                for r in recipients:
                    email = r.get("emailAddress", "").lower().strip()
                    if not email:
                        continue

                    diagnostic = r.get("diagnosticCode", "")
                    domain = email.split("@")[-1] if "@" in email else ""

                    # Log every bounce with attribution
                    try:
                        BounceLog.create(
                            email=email,
                            event_type="Bounce",
                            sub_type=bounce_type,
                            diagnostic=diagnostic[:500],
                            campaign_id=attr_campaign_id,
                            recipient_domain=domain,
                            template_id=attr_template_id,
                            subject_family=attr_subject,
                            ses_message_id=ses_msg_id,
                        )
                    except Exception as e:
                        print(f"[SES] BounceLog error: {e}")

                    # Hard bounce → suppress
                    if bounce_type == "Permanent":
                        try:
                            SuppressionEntry.get_or_create(
                                email=email,
                                defaults={
                                    "reason": "hard_bounce",
                                    "source": "ses_notification",
                                    "detail": diagnostic[:500],
                                }
                            )
                            # Auto-unsubscribe + set contact suppression fields
                            try:
                                Contact.update(
                                    subscribed=False,
                                    suppression_reason="hard_bounce",
                                    suppression_source="ses_notification",
                                ).where(Contact.email == email).execute()
                                # Cascade: update decision to wait (suppressed)
                                try:
                                    _c = Contact.get_or_none(Contact.email == email)
                                    if _c:
                                        from cascade import cascade_contact
                                        cascade_contact(_c.id, trigger="ses_hard_bounce")
                                except Exception as e:
                                    app.logger.warning("[SilentFix] Cascade after bounce for %s: %s" % (email, e))
                            except Exception as e:
                                app.logger.error("[SilentFix] CRITICAL — failed to suppress bounced contact %s: %s" % (email, e))
                            print(f"[SES] Hard bounce suppressed: {email}")
                        except Exception as e:
                            print(f"[SES] Suppression error: {e}")

            elif notif_type == "Complaint":
                complaint = message.get("complaint", {})
                recipients = complaint.get("complainedRecipients", [])
                complaint_type = complaint.get("complaintFeedbackType", "abuse")

                for r in recipients:
                    email = r.get("emailAddress", "").lower().strip()
                    if not email:
                        continue

                    domain = email.split("@")[-1] if "@" in email else ""

                    # Log complaint with attribution
                    try:
                        BounceLog.create(
                            email=email,
                            event_type="Complaint",
                            sub_type=complaint_type,
                            diagnostic=f"Complaint via {complaint.get('userAgent', 'unknown')}",
                            campaign_id=attr_campaign_id,
                            recipient_domain=domain,
                            template_id=attr_template_id,
                            subject_family=attr_subject,
                            ses_message_id=ses_msg_id,
                        )
                    except Exception as e:
                        print(f"[SES] BounceLog error: {e}")

                    # All complaints → suppress
                    try:
                        SuppressionEntry.get_or_create(
                            email=email,
                            defaults={
                                "reason": "complaint",
                                "source": "ses_notification",
                                "detail": f"Type: {complaint_type}",
                            }
                        )
                        try:
                            Contact.update(
                                subscribed=False,
                                suppression_reason="complaint",
                                suppression_source="ses_notification",
                            ).where(Contact.email == email).execute()
                            # Cascade: update decision to wait (suppressed)
                            try:
                                _c = Contact.get_or_none(Contact.email == email)
                                if _c:
                                    from cascade import cascade_contact
                                    cascade_contact(_c.id, trigger="ses_complaint")
                            except Exception as e:
                                app.logger.warning("[SilentFix] Cascade after complaint for %s: %s" % (email, e))
                        except Exception:
                            pass
                        print(f"[SES] Complaint suppressed: {email}")
                    except Exception as e:
                        print(f"[SES] Suppression error: {e}")

        return jsonify({"status": "processed"}), 200

    except Exception as e:
        print(f"[SES webhook] Error: {e}")
        return jsonify({"error": str(e)}), 500


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
    """Return int 0–100 based on checklist + sending performance + complaint rate."""
    score = 0
    # Checklist (45 pts max)
    if config.check_spf:          score += 10
    if config.check_dkim:         score += 10
    if config.check_dmarc:        score += 8
    if config.check_sandbox:      score += 8
    if config.check_list_cleaned: score += 4
    if config.check_subdomain:    score += 5
    # Sending performance — last 14 days (40 pts max)
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent = (CampaignEmail
              .select()
              .where(CampaignEmail.created_at >= cutoff))
    total_sent    = recent.where(CampaignEmail.status == "sent").count()
    total_opened  = recent.where(CampaignEmail.opened == True).count()
    total_bounced = recent.where(CampaignEmail.status == "bounced").count()
    # Include FlowEmail data
    flow_recent = FlowEmail.select().where(FlowEmail.sent_at >= cutoff)
    total_sent    += flow_recent.where(FlowEmail.status == "sent").count()
    total_opened  += flow_recent.where(FlowEmail.opened == True).count()
    total_bounced += flow_recent.where(FlowEmail.status == "bounced").count()
    # Include AutoEmail data
    auto_recent = AutoEmail.select().where(AutoEmail.sent_at >= cutoff)
    total_sent    += auto_recent.where(AutoEmail.status == "sent").count()
    total_opened  += auto_recent.where(AutoEmail.opened == True).count()
    total_bounced += auto_recent.where(AutoEmail.status == "bounced").count()
    if total_sent > 0:
        open_rate   = total_opened  / total_sent * 100
        bounce_rate = total_bounced / total_sent * 100
        if open_rate >= 20:   score += 20
        elif open_rate >= 15: score += 12
        elif open_rate >= 10: score += 6
        if bounce_rate < 1:   score += 20
        elif bounce_rate < 2: score += 12
        elif bounce_rate < 5: score += 6
    # Complaint rate — last 14 days (15 pts max)
    try:
        from database import BounceLog
        complaint_count = (BounceLog.select()
                          .where(BounceLog.event_type == "Complaint",
                                 BounceLog.timestamp >= cutoff)
                          .count())
        if total_sent > 0:
            complaint_rate = complaint_count / total_sent * 100
            if complaint_rate < 0.05:   score += 15   # Excellent
            elif complaint_rate < 0.1:  score += 12   # Good (under target)
            elif complaint_rate < 0.3:  score += 5    # Warning zone
            # >= 0.3% = 0 points (danger zone)
        else:
            score += 15  # No sends = no complaints = full points
    except Exception:
        score += 15  # Table doesn't exist yet = full points
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
    # Include FlowEmail data
    flow_r = FlowEmail.select().where(FlowEmail.sent_at >= cutoff)
    sent    += flow_r.where(FlowEmail.status == "sent").count()
    opened  += flow_r.where(FlowEmail.opened == True).count()
    bounced += flow_r.where(FlowEmail.status == "bounced").count()
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
    # Include FlowEmail data
    log.emails_sent    += FlowEmail.select().where(FlowEmail.status == "sent", FlowEmail.sent_at >= cutoff).count()
    log.emails_opened  += FlowEmail.select().where(FlowEmail.opened == True, FlowEmail.sent_at >= cutoff).count()
    log.emails_bounced += FlowEmail.select().where(FlowEmail.status == "bounced", FlowEmail.sent_at >= cutoff).count()
    # Include AutoEmail data
    log.emails_sent    += AutoEmail.select().where(AutoEmail.status == "sent", AutoEmail.sent_at >= cutoff).count()
    log.emails_opened  += AutoEmail.select().where(AutoEmail.opened == True, AutoEmail.sent_at >= cutoff).count()
    log.emails_bounced += AutoEmail.select().where(AutoEmail.status == "bounced", AutoEmail.sent_at >= cutoff).count()
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
    total_sent     += FlowEmail.select().where(FlowEmail.status == "sent").count()
    total_sent     += AutoEmail.select().where(AutoEmail.status == "sent").count()
    total_opened    = CampaignEmail.select().where(CampaignEmail.opened == True).count()
    total_opened   += FlowEmail.select().where(FlowEmail.opened == True).count()
    total_opened   += AutoEmail.select().where(AutoEmail.opened == True).count()
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

    # Trigger backlog stats
    from peewee import fn as _fn
    trigger_backlog = {"pending": 0, "processed": 0, "skipped_stale": 0,
                       "skipped_duplicate": 0, "skipped_no_flow": 0, "skipped": 0, "failed": 0}
    try:
        rows = list(PendingTrigger
                    .select(PendingTrigger.status, _fn.COUNT(PendingTrigger.id).alias("cnt"))
                    .group_by(PendingTrigger.status)
                    .dicts())
        for r in rows:
            trigger_backlog[r["status"]] = trigger_backlog.get(r["status"], 0) + r["cnt"]
    except Exception:
        pass

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
        trigger_backlog=trigger_backlog,
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
        query = query.where(_tag_match(tag))
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

    # Fetch CustomerProfile data for accurate order values
    contact_ids = [c.id for c in contacts]
    profile_map = {}
    if contact_ids:
        try:
            _profiles = CustomerProfile.select(
                CustomerProfile.contact,
                CustomerProfile.total_orders,
                CustomerProfile.total_spent,
            ).where(CustomerProfile.contact.in_(contact_ids))
            profile_map = {p.contact_id: p for p in _profiles}
        except Exception:
            pass

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
        profile_map=profile_map,
    )


# ─────────────────────────────────
#  EMAIL VALIDATION (Deliverability)
# ─────────────────────────────────

# Common disposable email domains (subset — blocks the worst offenders)
_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "guerrillamailblock.com", "grr.la",
    "dispostable.com", "trashmail.com", "fakeinbox.com", "temp-mail.org",
    "10minutemail.com", "mohmal.com", "harakirimail.com", "maildrop.cc",
    "mailnesia.com", "tempr.email", "discard.email", "getnada.com",
    "guerrillamail.info", "guerrillamail.net", "tempail.com", "spamgourmet.com",
    "mytrashmail.com", "mailcatch.com", "mintemail.com", "safetymail.info",
    "jetable.org", "trashmail.net", "trashmail.me", "yopmail.fr", "yopmail.net",
    "mailexpire.com", "temporarymail.com", "anonbox.net", "binkmail.com",
    "spaml.com", "spamcero.com", "wegwerfmail.de", "trash-mail.com",
    "einrot.com", "cuvox.de", "armyspy.com", "dayrep.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com", "superrito.com", "teleworm.us",
}

def _validate_email(email):
    """
    Validate email for deliverability. Returns (valid: bool, reason: str).
    Checks: syntax, disposable domain, MX record.
    """
    import re as re_mod
    email = email.strip().lower()

    # 1. Basic syntax check
    if not re_mod.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        return False, "invalid_syntax"

    domain = email.split("@")[1]

    # 2. Disposable domain check
    if domain in _DISPOSABLE_DOMAINS:
        return False, "disposable_domain"

    # 3. MX record check (verify domain can receive email)
    try:
        import dns.resolver
        try:
            dns.resolver.resolve(domain, "MX")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            return False, "no_mx_record"
        except dns.resolver.LifetimeTimeout:
            pass  # Timeout = assume valid (don't block on slow DNS)
    except ImportError:
        pass  # dnspython not installed — skip MX check

    return True, "valid"

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
    invalid_syntax = 0
    invalid_domain = 0
    invalid_mx     = 0

    for row in reader:
        email = row.get("email") or row.get("Email") or row.get("EMAIL", "").strip()
        if not email:
            skipped += 1
            continue

        # Validate email before importing (centralized sanitizer)
        from email_sanitizer import sanitize_email
        _san = sanitize_email(email)
        if not _san["valid"]:
            if _san["reason"] == "invalid_syntax":    invalid_syntax += 1
            elif _san["reason"] == "disposable_domain": invalid_domain += 1
            elif _san["reason"] == "no_mx_record":    invalid_mx += 1
            skipped += 1
            continue
        email = _san["email"]  # use corrected email

        contact, created = Contact.get_or_create(
            email=email,
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

    skip_detail = []
    if invalid_syntax > 0: skip_detail.append(f"invalid syntax: {invalid_syntax}")
    if invalid_domain > 0: skip_detail.append(f"disposable domain: {invalid_domain}")
    if invalid_mx > 0:     skip_detail.append(f"no MX record: {invalid_mx}")
    dup_count = skipped - invalid_syntax - invalid_domain - invalid_mx
    if dup_count > 0:      skip_detail.append(f"duplicates: {dup_count}")
    detail_str = f" ({', '.join(skip_detail)})" if skip_detail else ""
    flash(f"Imported {imported} contacts. Skipped {skipped}{detail_str}.", "success")
    return redirect(url_for("contacts"))


@app.route("/api/sanitize-contacts", methods=["POST"])
def sanitize_contacts_api():
    """Bulk sanitize all existing contacts. Admin-only."""
    from email_sanitizer import bulk_sanitize_contacts
    try:
        stats = bulk_sanitize_contacts()
        return jsonify({"ok": True, "stats": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
            # Canonical identity resolution: profile, enrichment, cascade
            from identity_resolution import resolve_identity
            resolve_identity(
                email=contact.email,
                shopify_id=str(customer.get("id", "")),
                source="shopify_customer",
                first_name=customer.get("first_name", ""),
                last_name=customer.get("last_name", ""),
                create_if_missing=False,
            )

            # Enroll new contacts in welcome flows
            if created and contact.subscribed:
                _enroll_contact_in_flows(contact, "contact_created")
                app.logger.info(f"Shopify customer webhook: enrolled new contact {contact.email} in welcome flows")

            app.logger.info(f"Shopify customer webhook: {'new' if created else 'updated'} contact {contact.email}")
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
            # Canonical identity resolution: ensures profile, enrichment, cascade
            from identity_resolution import resolve_identity
            resolve_identity(
                email=contact.email,
                shopify_id=str(customer.get("id", "")),
                source="shopify_customer",
                create_if_missing=False,
            )

            app.logger.info(f"Shopify customer update webhook: {contact.email} (subscribed={contact.subscribed})")
            return jsonify({"success": True, "contact_id": contact.id, "updated": True}), 200
        else:
            return jsonify({"error": "No valid email in webhook"}), 400

    except Exception as e:
        app.logger.error(f"Customer update webhook error: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────
#  SHOPIFY CHECKOUT + ORDER WEBHOOKS
# ─────────────────────────────────
@app.route("/webhooks/shopify/checkout/create", methods=["POST"])
def webhook_shopify_checkout_create():
    """Store abandoned checkout data from Shopify for cart recovery flows."""
    try:
        raw_body = request.get_data()
        is_valid, error = verify_shopify_webhook(raw_body, request.headers)
        if not is_valid:
            app.logger.warning(f"Checkout webhook HMAC failed: {error}")
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)
        checkout_id = str(data.get("id", ""))
        email = (data.get("email") or "").strip().lower()
        if not checkout_id or not email:
            return jsonify({"error": "Missing checkout_id or email"}), 400

        # Parse line items
        line_items = []
        for item in data.get("line_items", []):
            line_items.append({
                "title": item.get("title", ""),
                "quantity": item.get("quantity", 1),
                "price": item.get("price", "0.00"),
                "image_url": item.get("image_url", "") or "",
            })

        # Find contact
        contact = Contact.get_or_none(Contact.email == email)

        import json as _json
        AbandonedCheckout.insert(
            shopify_checkout_id=checkout_id,
            email=email,
            contact=contact,
            checkout_url=data.get("abandoned_checkout_url") or data.get("checkout_url", ""),
            total_price=float(data.get("total_price", 0)),
            currency=data.get("currency", "CAD"),
            line_items_json=_json.dumps(line_items),
            recovered=False,
            abandoned_at=data.get("created_at") or datetime.now().isoformat(),
            enrolled_in_flow=False,
        ).on_conflict(
            conflict_target=[AbandonedCheckout.shopify_checkout_id],
            update={
                AbandonedCheckout.email: email,
                AbandonedCheckout.total_price: float(data.get("total_price", 0)),
                AbandonedCheckout.line_items_json: _json.dumps(line_items),
                AbandonedCheckout.checkout_url: data.get("abandoned_checkout_url") or data.get("checkout_url", ""),
            }
        ).execute()

        app.logger.info(f"Checkout webhook stored: {email} (checkout {checkout_id})")

        # Smart exit: cancel browse abandonment flows when customer starts checkout
        if contact:
            _exit_flows_by_trigger_type(
                contact,
                ["browse_abandonment"],
                reason_code="flow_exit_checkout_started",
            )

        # Identity resolution: stitch session + tokens if email discovered via checkout
        if email:
            from identity_resolution import resolve_identity
            resolve_identity(
                email=email, source="shopify_checkout", create_if_missing=False,
                checkout_token=str(data.get("token", "")),
                cart_token=str(data.get("cart_token", "")),
            )

        return jsonify({"success": True}), 200

    except Exception as e:
        app.logger.error(f"Checkout webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/webhooks/shopify/order/create", methods=["POST"])
def webhook_shopify_order_create():
    """
    Handle Shopify order creation:
    1. Mark any matching abandoned checkout as recovered
    2. Cancel any active checkout_abandoned flow enrollments
    3. Enroll contact in order_placed flows
    """
    try:
        raw_body = request.get_data()
        is_valid, error = verify_shopify_webhook(raw_body, request.headers)
        if not is_valid:
            app.logger.warning(f"Order webhook HMAC failed: {error}")
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)
        email = (data.get("email") or data.get("contact_email") or "").strip().lower()
        if not email:
            return jsonify({"error": "No email in order"}), 400

        # Identity resolution: ensure contact exists and stitch any anonymous activity
        from identity_resolution import resolve_identity
        id_result = resolve_identity(
            email=email, source="shopify_order", create_if_missing=False,
            checkout_token=str(data.get("checkout_token", "")),
        )
        contact = id_result["contact"]

        # 1. Mark abandoned checkouts as recovered
        (AbandonedCheckout.update(recovered=True, recovered_at=datetime.now())
         .where(AbandonedCheckout.email == email, AbandonedCheckout.recovered == False)
         .execute())

        if contact:
            # 2. Smart exit: cancel ALL abandonment + winback + welcome flows on purchase
            # Welcome flow should stop once customer converts — no "5% off" after they bought
            _exit_flows_by_trigger_type(
                contact,
                ["checkout_abandoned", "browse_abandonment", "cart_abandonment", "no_purchase_days", "contact_created"],
                reason_code="flow_exit_purchase",
            )

            # 3. Enroll in order_placed flows
            _enroll_contact_in_flows(contact, "order_placed")
            app.logger.info(f"Order webhook: {email} — exited abandonment/winback flows, enrolled in post-purchase")

        return jsonify({"success": True}), 200

    except Exception as e:
        app.logger.error(f"Order webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/contacts/unsubscribe/<email>", methods=["GET", "POST"])
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



@app.route("/contacts/unsubscribe-oneclick", methods=["POST"])
def unsubscribe_oneclick():
    """RFC 8058 one-click unsubscribe endpoint.
    Email clients POST: List-Unsubscribe=One-Click to this URL."""
    email = request.args.get("email", "").strip().lower()
    if not email:
        return "Missing email", 400
    try:
        contact = Contact.get(Contact.email == email)
        contact.subscribed = False
        contact.save()
        (FlowEnrollment.update(status="cancelled")
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status == "active")
                       .execute())
        return "Unsubscribed", 200
    except Contact.DoesNotExist:
        return "OK", 200  # Don't reveal whether email exists


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
#  BLOCK TEMPLATE BUILDER
# ─────────────────────────────────
@app.route("/templates/new-blocks")
def new_blocks_template():
    """Render empty block template builder."""
    from condition_engine import CONDITION_FIELDS, CONDITION_OPERATORS, TEMPLATE_FAMILIES
    from block_registry import BLOCK_TYPES
    contacts = list(Contact.select(Contact.id, Contact.email, Contact.first_name)
                    .where(Contact.subscribed == True)
                    .order_by(Contact.email)
                    .limit(50))
    return render_template("template_builder.html",
        template=None,
        block_types=BLOCK_TYPES,
        condition_fields=CONDITION_FIELDS,
        condition_operators=CONDITION_OPERATORS,
        template_families=TEMPLATE_FAMILIES,
        contacts=contacts,
    )

@app.route("/templates/<int:template_id>/edit-blocks")
def edit_blocks_template(template_id):
    """Render block template builder with existing template data."""
    template = EmailTemplate.get_by_id(template_id)
    from condition_engine import CONDITION_FIELDS, CONDITION_OPERATORS, TEMPLATE_FAMILIES
    from block_registry import BLOCK_TYPES
    contacts = list(Contact.select(Contact.id, Contact.email, Contact.first_name)
                    .where(Contact.subscribed == True)
                    .order_by(Contact.email)
                    .limit(50))
    return render_template("template_builder.html",
        template=template,
        block_types=BLOCK_TYPES,
        condition_fields=CONDITION_FIELDS,
        condition_operators=CONDITION_OPERATORS,
        template_families=TEMPLATE_FAMILIES,
        contacts=contacts,
    )

@app.route("/api/templates/create-blocks", methods=["POST"])
def api_create_blocks_template():
    """Create a new blocks-format template.

    POST JSON: {name, subject, preview_text, family, blocks, ai_enabled}
    Returns: {id, success: true}
    """
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    subject = data.get("subject", "").strip()

    if not name or not subject:
        return jsonify({"error": "name and subject are required"}), 400

    blocks = data.get("blocks", [])
    if not isinstance(blocks, list):
        return jsonify({"error": "blocks must be a list"}), 400

    import json as _json
    template = EmailTemplate.create(
        name=name,
        subject=subject,
        preview_text=data.get("preview_text", ""),
        html_body="",
        template_format="blocks",
        blocks_json=_json.dumps(blocks),
        template_family=data.get("family", ""),
        ai_enabled=bool(data.get("ai_enabled", False)),
        block_ai_overrides=_json.dumps(data.get("block_ai_overrides", {})),
    )
    return jsonify({"id": template.id, "success": True})

@app.route("/api/templates/<int:template_id>/save-blocks", methods=["POST"])
def api_save_blocks(template_id):
    """Save blocks JSON + metadata for a template.

    POST JSON: {name, subject, preview_text, family, blocks, ai_enabled, block_ai_overrides}
    Returns: {success: true, warnings: [...]}
    """
    try:
        template = EmailTemplate.get_by_id(template_id)
    except EmailTemplate.DoesNotExist:
        return jsonify({"error": "template not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    import json as _json

    blocks = data.get("blocks", [])
    if not isinstance(blocks, list):
        return jsonify({"error": "blocks must be a list"}), 400

    # Validate before saving
    from block_registry import validate_template
    blocks_json_str = _json.dumps(blocks)
    family = data.get("family", template.template_family)
    warnings = validate_template(blocks_json_str, family=family or None)

    # Check for errors (not just warnings)
    errors = [w for w in warnings if w.get("level") == "error"]
    # Save even with warnings, but not with hard errors if family enforcement fails
    # (validate_template returns advisory warnings — hard gate is enforce_family_constraints)

    template.name = data.get("name", template.name).strip() or template.name
    template.subject = data.get("subject", template.subject).strip() or template.subject
    template.preview_text = data.get("preview_text", template.preview_text)
    template.template_format = "blocks"
    template.blocks_json = blocks_json_str
    template.template_family = family
    template.ai_enabled = bool(data.get("ai_enabled", template.ai_enabled))
    template.block_ai_overrides = _json.dumps(data.get("block_ai_overrides", {}))
    template.updated_at = datetime.now()
    template.save()

    return jsonify({"success": True, "warnings": warnings})


# ─────────────────────────────────
#  BLOCK TEMPLATE PREVIEW
# ─────────────────────────────────
@app.route("/api/templates/<int:template_id>/preview-blocks")
def preview_blocks_template(template_id):
    """Render a blocks-format template with sample data for inspection.
    Returns complete HTML email suitable for iframe or browser viewing.

    Phase 2 additions:
      ?contact_id=123  — Preview as a specific contact (resolves variants against their profile)
      ?validate=1      — Include validation warnings in JSON response
      ?explain=1       — Include block-level explainability (variant resolution trace)
      ?family=welcome  — Override family for validation (defaults to template.template_family)
    """
    template = EmailTemplate.get_or_none(EmailTemplate.id == template_id)
    if not template:
        return "Template not found", 404

    if getattr(template, 'template_format', 'html') != 'blocks':
        return "Not a blocks-format template (template_format='%s')" % getattr(template, 'template_format', 'html'), 400

    from block_registry import render_template_blocks, validate_template, BRAND_URL as _BRAND_URL

    # Sample products for preview (uses BRAND_URL from block_registry / email_shell)
    sample_products = [
        {"title": "Bluetooth Speaker Pro", "image_url": "", "price": "49.99",
         "product_url": _BRAND_URL, "compare_price": "69.99"},
        {"title": "HD Dash Camera", "image_url": "", "price": "89.99",
         "product_url": _BRAND_URL, "compare_price": ""},
    ]

    # Phase 2: Preview-as-contact — resolve variants against a real contact's profile
    contact = None
    contact_id = request.args.get("contact_id")
    if contact_id:
        try:
            contact = Contact.get_by_id(int(contact_id))
        except (Contact.DoesNotExist, ValueError):
            return jsonify({"error": "Contact %s not found" % contact_id}), 404

    # Phase 2: Explainability — return variant resolution trace
    want_explain = request.args.get("explain") == "1"
    want_validate = request.args.get("validate") == "1"

    if want_explain or want_validate:
        html, explain_list = render_template_blocks(
            template, contact=contact, products=sample_products, discount=None, explain=True
        )
    else:
        html = render_template_blocks(
            template, contact=contact, products=sample_products, discount=None
        )
        explain_list = None

    html = html.replace("{{unsubscribe_url}}", "#")
    html = html.replace("{{discount_code}}", "PREVIEW5")

    # If JSON response requested
    if want_validate or want_explain:
        result = {"html": html}

        if want_validate:
            family = request.args.get("family") or getattr(template, "template_family", "") or None
            result["warnings"] = validate_template(template.blocks_json, family=family)

        if want_explain:
            result["explain"] = explain_list or []
            # Include contact context if previewing as a contact
            if contact:
                try:
                    from condition_engine import get_contact_context
                    result["contact_context"] = get_contact_context(contact)
                    result["contact_info"] = {
                        "id": contact.id,
                        "email": contact.email,
                        "first_name": contact.first_name or "",
                        "last_name": contact.last_name or "",
                    }
                except ImportError:
                    pass

        return jsonify(result)

    return html


# ─────────────────────────────────
#  AI BLOCK CONTENT GENERATION (Phase 3)
# ─────────────────────────────────
@app.route("/api/templates/ai-generate-block", methods=["POST"])
def api_ai_generate_block():
    """Generate AI content for a single block type.

    POST JSON:
      {
        "block_type": "hero",
        "family": "welcome",        # optional
        "purpose": "welcome email", # optional
        "contact_id": 123,          # optional
        "fallback": {"headline": "Welcome!"}  # REQUIRED
      }

    Returns JSON:
      {"content": {...}, "ai_generated": true/false}
    """
    data = request.get_json(force=True, silent=True) or {}
    block_type = data.get("block_type", "")
    family = data.get("family", "")
    purpose = data.get("purpose", "")
    fallback = data.get("fallback", {})
    contact_id = data.get("contact_id")

    if not block_type:
        return jsonify({"error": "block_type is required"}), 400
    if not fallback:
        return jsonify({"error": "fallback content is required"}), 400

    from block_registry import BLOCK_TYPES
    if block_type not in BLOCK_TYPES:
        return jsonify({"error": "Unknown block_type '%s'" % block_type}), 400

    contact = None
    if contact_id:
        try:
            contact = Contact.get_by_id(int(contact_id))
        except (Contact.DoesNotExist, ValueError):
            pass

    try:
        from ai_content import generate_block_content, AI_WRITABLE_FIELDS
        writable = AI_WRITABLE_FIELDS.get(block_type, [])
        if not writable:
            return jsonify({"content": fallback, "ai_generated": False,
                            "reason": "No AI-writable fields for block type '%s'" % block_type})

        content = generate_block_content(
            block_type=block_type,
            contact=contact,
            family=family,
            fallback=fallback,
            purpose=purpose,
        )
        # Check if AI actually changed anything
        ai_generated = any(content.get(f) != fallback.get(f) for f in writable if f in content)
        return jsonify({"content": content, "ai_generated": ai_generated})

    except Exception as e:
        app.logger.error("[AI Content] Generate error: %s" % e)
        return jsonify({"content": fallback, "ai_generated": False,
                        "error": str(e)[:200]})


@app.route("/api/templates/ai-generate-template", methods=["POST"])
def api_ai_generate_template():
    """Generate AI content for all blocks in a template.

    POST JSON:
      {
        "blocks": [...],             # REQUIRED: list of block dicts
        "family": "welcome",         # optional
        "purpose": "welcome email",  # optional
        "contact_id": 123            # optional
      }

    Returns JSON:
      {"blocks": [...], "ai_fields_updated": int}
    """
    data = request.get_json(force=True, silent=True) or {}
    blocks = data.get("blocks", [])
    family = data.get("family", "")
    purpose = data.get("purpose", "")
    contact_id = data.get("contact_id")

    if not blocks or not isinstance(blocks, list):
        return jsonify({"error": "blocks list is required"}), 400

    contact = None
    if contact_id:
        try:
            contact = Contact.get_by_id(int(contact_id))
        except (Contact.DoesNotExist, ValueError):
            pass

    try:
        from ai_content import generate_template_content, AI_WRITABLE_FIELDS

        result_blocks = generate_template_content(
            blocks=blocks,
            family=family,
            contact=contact,
            purpose=purpose,
            fallback_blocks=blocks,
        )

        # Count how many fields were updated by AI
        updated = 0
        for i, (orig, new) in enumerate(zip(blocks, result_blocks)):
            bt = orig.get("block_type", "")
            writable = AI_WRITABLE_FIELDS.get(bt, [])
            for f in writable:
                if new.get("content", {}).get(f) != orig.get("content", {}).get(f):
                    updated += 1

        return jsonify({"blocks": result_blocks, "ai_fields_updated": updated})

    except Exception as e:
        app.logger.error("[AI Content] Template generate error: %s" % e)
        return jsonify({"blocks": blocks, "ai_fields_updated": 0,
                        "error": str(e)[:200]})


@app.route("/api/templates/<int:template_id>/test-send", methods=["POST"])
def api_template_test_send(template_id):
    """Send a test email with AI content to a specified address.

    POST JSON:
      {"email": "admin@ldas.ca", "contact_id": 123}

    Renders the template (with AI enabled regardless of ai_enabled flag),
    then sends via SES to the specified email address only.
    """
    data = request.get_json(force=True, silent=True) or {}
    test_email = data.get("email", "").strip()
    contact_id = data.get("contact_id")

    if not test_email:
        return jsonify({"error": "email address required"}), 400

    try:
        template = EmailTemplate.get_by_id(template_id)
    except EmailTemplate.DoesNotExist:
        return jsonify({"error": "template not found"}), 404

    if template.template_format != "blocks":
        return jsonify({"error": "test-send only works with block templates"}), 400

    contact = None
    if contact_id:
        try:
            contact = Contact.get_by_id(int(contact_id))
        except (Contact.DoesNotExist, ValueError):
            pass

    try:
        from block_registry import render_template_blocks
        html = render_template_blocks(template, contact=contact)

        # Send via SES
        from email_sender import send_campaign_email
        subject = "[TEST] %s" % template.subject
        if contact:
            subject = subject.replace("{{first_name}}", contact.first_name or "Friend")

        success, error, msg_id = send_campaign_email(
            to_email=test_email,
            to_name=contact.first_name if contact else "",
            subject=subject,
            html_body=html,
            from_email=os.environ.get("DEFAULT_FROM_EMAIL", "noreply@mailenginehub.com"),
            from_name="LDAS Test",
        )

        if success:
            return jsonify({"success": True, "message": "Test sent to %s" % test_email,
                            "ses_message_id": msg_id})
        else:
            return jsonify({"success": False, "error": error or "SES send failed"})

    except Exception as e:
        app.logger.error("[Test Send] Error: %s" % e)
        return jsonify({"success": False, "error": str(e)[:200]})


# ─────────────────────────────────
#  SENT EMAILS LOG
# ─────────────────────────────────
@app.route("/sent-emails")
def sent_emails():
    from datetime import datetime, timedelta

    page = int(request.args.get("page", 1))
    per_page = 50
    search = request.args.get("search", "").strip()
    email_type = request.args.get("type", "")
    status_filter = request.args.get("status", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    # Default to last 7 days
    default_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_from:
        date_from = default_from
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    dt_from = datetime.strptime(date_from, "%Y-%m-%d")
    dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    all_emails = []

    # ── Campaign Emails ──
    if email_type not in ("flow", "auto"):
        ce_query = (CampaignEmail
                    .select(CampaignEmail, Contact, Campaign)
                    .join(Contact, on=(CampaignEmail.contact == Contact.id))
                    .switch(CampaignEmail)
                    .join(Campaign, on=(CampaignEmail.campaign == Campaign.id))
                    .where(CampaignEmail.created_at.between(dt_from, dt_to)))

        if search:
            ce_query = ce_query.where(Contact.email.contains(search))
        if status_filter == "opened":
            ce_query = ce_query.where(CampaignEmail.opened == True)
        elif status_filter:
            ce_query = ce_query.where(CampaignEmail.status == status_filter)

        # Build subject lookup: campaign_id → subject
        campaign_subjects = {}
        for c in Campaign.select(Campaign.id, Campaign.name):
            try:
                tpl = EmailTemplate.get_by_id(c.template_id)
                campaign_subjects[c.id] = tpl.subject or c.name
            except Exception:
                campaign_subjects[c.id] = c.name

        for ce in ce_query:
            name = ""
            try:
                name = "{} {}".format(ce.contact.first_name or "", ce.contact.last_name or "").strip()
            except Exception:
                pass
            all_emails.append({
                "id": ce.id,
                "sent_at": ce.created_at,
                "email": ce.contact.email,
                "name": name or ce.contact.email,
                "contact_id": ce.contact.id,
                "type": "campaign",
                "source": ce.campaign.name,
                "subject": campaign_subjects.get(ce.campaign.id, ce.campaign.name).replace("{{first_name}}", ce.contact.first_name or "Friend").replace("{{last_name}}", ce.contact.last_name or "").replace("{{email}}", ce.contact.email or ""),
                "status": "opened" if ce.opened else ce.status,
                "opened": ce.opened,
                "opened_at": ce.opened_at,
                "error_msg": ce.error_msg or "",
            })

    # ── Flow Emails ──
    if email_type not in ("campaign", "auto"):
        fe_query = (FlowEmail
                    .select(FlowEmail, Contact, FlowStep, FlowEnrollment)
                    .join(Contact, on=(FlowEmail.contact == Contact.id))
                    .switch(FlowEmail)
                    .join(FlowStep, on=(FlowEmail.step == FlowStep.id))
                    .switch(FlowEmail)
                    .join(FlowEnrollment, on=(FlowEmail.enrollment == FlowEnrollment.id))
                    .where(FlowEmail.sent_at.between(dt_from, dt_to)))

        if search:
            fe_query = fe_query.where(Contact.email.contains(search))
        if status_filter == "opened":
            fe_query = fe_query.where(FlowEmail.opened == True)
        elif status_filter:
            fe_query = fe_query.where(FlowEmail.status == status_filter)

        # Build flow name + subject lookup
        flow_names = {}
        for f in Flow.select(Flow.id, Flow.name):
            flow_names[f.id] = f.name
        step_subjects = {}
        for s in FlowStep.select(FlowStep.id, FlowStep.flow, FlowStep.step_order, FlowStep.subject_override, FlowStep.template):
            try:
                subject = s.subject_override
                if not subject:
                    tpl = EmailTemplate.get_by_id(s.template_id)
                    subject = tpl.subject or ""
                step_subjects[s.id] = {
                    "flow_name": flow_names.get(s.flow_id, "Flow"),
                    "step_order": s.step_order,
                    "subject": subject,
                }
            except Exception:
                step_subjects[s.id] = {
                    "flow_name": flow_names.get(s.flow_id, "Flow"),
                    "step_order": s.step_order,
                    "subject": "",
                }

        for fe in fe_query:
            name = ""
            try:
                name = "{} {}".format(fe.contact.first_name or "", fe.contact.last_name or "").strip()
            except Exception:
                pass
            step_info = step_subjects.get(fe.step.id, {"flow_name": "Flow", "step_order": "?", "subject": ""})
            all_emails.append({
                "id": fe.id,
                "sent_at": fe.sent_at,
                "email": fe.contact.email,
                "name": name or fe.contact.email,
                "contact_id": fe.contact.id,
                "type": "flow",
                "source": "{} \u2192 Step {}".format(step_info["flow_name"], step_info["step_order"]),
                "subject": step_info["subject"].replace("{{first_name}}", fe.contact.first_name or "Friend").replace("{{last_name}}", fe.contact.last_name or "").replace("{{email}}", fe.contact.email or ""),
                "status": "opened" if fe.opened else fe.status,
                "opened": fe.opened,
                "opened_at": fe.opened_at,
                "error_msg": "",
            })

    # ── Auto-Pilot Emails ──
    if email_type not in ("campaign", "flow"):
        from peewee import JOIN
        ae_query = (AutoEmail
                    .select(AutoEmail, Contact, EmailTemplate)
                    .join(Contact, on=(AutoEmail.contact == Contact.id))
                    .switch(AutoEmail)
                    .join(EmailTemplate, on=(AutoEmail.template == EmailTemplate.id), join_type=JOIN.LEFT_OUTER)
                    .where(AutoEmail.sent_at.between(dt_from, dt_to)))

        if search:
            ae_query = ae_query.where(Contact.email.contains(search))
        if status_filter == "opened":
            ae_query = ae_query.where(AutoEmail.opened == True)
        elif status_filter:
            ae_query = ae_query.where(AutoEmail.status == status_filter)

        for ae in ae_query:
            name = ""
            try:
                name = "{} {}".format(ae.contact.first_name or "", ae.contact.last_name or "").strip()
            except Exception:
                pass
            try:
                tpl_name = ae.template.name if ae.template else None
            except Exception:
                tpl_name = None
            all_emails.append({
                "id": ae.id,
                "sent_at": ae.sent_at,
                "email": ae.contact.email,
                "name": name or ae.contact.email,
                "contact_id": ae.contact.id,
                "type": "auto",
                "source": "Auto-Pilot" + (f" \u2014 {tpl_name}" if tpl_name else ""),
                "subject": (ae.subject or "").replace("{{first_name}}", ae.contact.first_name or "Friend").replace("{{last_name}}", ae.contact.last_name or "").replace("{{email}}", ae.contact.email or ""),
                "status": "opened" if ae.opened else ae.status,
                "opened": ae.opened,
                "opened_at": ae.opened_at,
                "error_msg": ae.error_msg or "",
            })

    # Sort by time descending
    all_emails.sort(key=lambda x: x["sent_at"] or datetime.min, reverse=True)

    # Stats (from full filtered list, before pagination)
    total_count = len(all_emails)
    total_sent = sum(1 for e in all_emails if e["status"] in ("sent", "opened"))
    total_opened = sum(1 for e in all_emails if e["opened"])
    total_failed = sum(1 for e in all_emails if e["status"] in ("failed", "bounced"))
    open_rate = round(total_opened / total_sent * 100, 1) if total_sent > 0 else 0

    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    emails = all_emails[start:end]

    return render_template("sent_emails.html",
        emails=emails,
        page=page,
        per_page=per_page,
        total_count=total_count,
        total_sent=total_sent,
        total_opened=total_opened,
        total_failed=total_failed,
        open_rate=open_rate,
        search=search,
        email_type=email_type,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to,
        default_from=default_from,
    )


# ─────────────────────────────────
#  SENT EMAIL PREVIEW
# ─────────────────────────────────
@app.route("/sent-emails/preview/<email_type>/<int:email_id>")
def sent_email_preview(email_type, email_id):
    """Return the rendered HTML of a sent email (for iframe preview)."""
    try:
        if email_type == "campaign":
            ce = CampaignEmail.get_by_id(email_id)
            contact = ce.contact
            campaign = ce.campaign
            template = EmailTemplate.get_by_id(campaign.template_id)
        elif email_type == "flow":
            fe = FlowEmail.get_by_id(email_id)
            contact = fe.contact
            step = fe.step
            template = EmailTemplate.get_by_id(step.template_id)
        elif email_type == "auto":
            ae = AutoEmail.get_by_id(email_id)
            contact = ae.contact
            template = EmailTemplate.get_by_id(ae.template_id)
        else:
            return "Invalid email type", 400

        # Render blocks-based templates through the block registry
        if getattr(template, 'template_format', 'html') == 'blocks' and template.blocks_json:
            from block_registry import render_template_blocks
            html = render_template_blocks(template, contact=contact, products=[], discount=None)
        else:
            html = template.html_body or ""

        html = html.replace("{{first_name}}", contact.first_name or "Friend")
        html = html.replace("{{last_name}}", contact.last_name or "")
        html = html.replace("{{email}}", contact.email or "")
        html = html.replace("{{unsubscribe_url}}", "#")
        html = html.replace("{{discount_code}}", "PREVIEW")

        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return "<html><body><p style='padding:40px;font-family:sans-serif;color:#666;'>Email content not available: %s</p></body></html>" % str(e), 200, {"Content-Type": "text/html; charset=utf-8"}


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
        query = query.where(_tag_match(tag))
    elif segment and segment != "all":
        query = query.where(_tag_match(segment))
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

    # ── Campaign Preflight Checks ──────────────────────────
    try:
        from campaign_preflight import run_preflight
        preflight = run_preflight(campaign_id)

        # Store preflight result
        try:
            PreflightLog.create(
                campaign_id=campaign_id,
                overall=preflight.overall,
                checks_json=json.dumps(preflight.to_dict()["checks"]),
            )
        except Exception:
            pass

        if preflight.overall == "BLOCK":
            blocked_checks = [c for c in preflight.checks if c.status == "BLOCK"]
            reasons = "; ".join(c.message for c in blocked_checks)
            flash(f"Campaign BLOCKED by preflight: {reasons}", "error")
            return redirect(url_for("campaign_detail", campaign_id=campaign_id))

        if preflight.overall == "WARN":
            warn_checks = [c for c in preflight.checks if c.status == "WARN"]
            warnings = "; ".join(c.message for c in warn_checks[:3])
            flash(f"Preflight warnings (proceeding): {warnings}", "warning")
    except ImportError:
        pass  # campaign_preflight.py not deployed yet
    except Exception as pf_err:
        print(f"[Preflight] Error: {pf_err}")
    # ───────────────────────────────────────────────────────

    # Launch send in background thread so UI stays responsive
    thread = threading.Thread(target=_send_campaign_async, args=(campaign_id,))
    thread.daemon = True
    thread.start()

    campaign.status = "sending"
    campaign.save()

    flash("Campaign is sending! Refresh in a moment to see progress.", "success")
    return redirect(url_for("campaign_detail", campaign_id=campaign_id))

# ── Token URL helpers (Phase I security) ────────────────────
def _make_unsubscribe_url(contact):
    """Generate a signed unsubscribe URL for a contact."""
    token = create_token({"p": "unsub", "cid": contact.id, "e": contact.email})
    return f"{SITE_URL}/unsubscribe/{token}"

def _make_tracking_pixel_url(campaign_id, contact_id):
    """Generate a signed open-tracking pixel URL."""
    token = create_token({"p": "open", "cmp": campaign_id, "cid": contact_id})
    return f"{SITE_URL}/track/open/{token}"

def _make_flow_tracking_pixel_url(enrollment_id, step_id, contact_id):
    """Generate a signed flow open-tracking pixel URL."""
    token = create_token({"p": "fopen", "eid": enrollment_id, "sid": step_id, "cid": contact_id})
    return f"{SITE_URL}/track/flow-open/{token}"

def _increment_contact_send_counters(contact_id):
    """Increment the 7d and 30d email counters for a contact."""
    try:
        Contact.update(
            emails_received_7d=Contact.emails_received_7d + 1,
            emails_received_30d=Contact.emails_received_30d + 1,
        ).where(Contact.id == contact_id).execute()
    except Exception as e:
        app.logger.error("[SilentFix] send counter for contact %s: %s" % (contact_id, e))

def _send_campaign_async(campaign_id):
    """Enqueue all eligible contacts for a campaign into the delivery queue.
    The queue processor handles warmup pacing and actual sending.
    Every decision point is recorded in the ActionLedger.
    """
    from action_ledger import log_action, RC_UNSUBSCRIBED, RC_SUPPRESSED_ENTRY, RC_OK
    from delivery_engine import enqueue_email

    campaign = Campaign.get_by_id(campaign_id)
    template = EmailTemplate.get_by_id(campaign.template_id)
    contacts = _get_campaign_contacts(campaign)

    queued_count = 0
    for contact in contacts:
        if not contact.subscribed:
            log_action(contact, "campaign", campaign_id, "suppressed", RC_UNSUBSCRIBED,
                       source_type=campaign.name,
                       reason_detail="Contact unsubscribed")
            continue

        # ── Suppression list check ───────────────────────────
        try:
            from database import SuppressionEntry
            if SuppressionEntry.select().where(SuppressionEntry.email == contact.email).exists():
                log_action(contact, "campaign", campaign_id, "suppressed", RC_SUPPRESSED_ENTRY,
                           source_type=campaign.name,
                           reason_detail="Contact on suppression list")
                CampaignEmail.create(campaign=campaign, contact=contact, status="suppressed", error_msg="on suppression list")
                continue
        except Exception:
            pass

        # ── Next-best-message gating (skip contacts whose learned decision is "wait") ──
        try:
            from database import MessageDecision
            _decision = MessageDecision.get_or_none(MessageDecision.contact == contact)
            if _decision and _decision.action_type == "wait":
                _still_valid = (not _decision.expires_at) or _decision.expires_at > datetime.now()
                if _still_valid:
                    log_action(contact, "campaign", campaign_id, "suppressed", "RC_DECISION_WAIT",
                               source_type=campaign.name,
                               reason_detail="Next-best-message decision: wait (%s)" % (_decision.action_reason or "fatigue/suppression"))
                    CampaignEmail.create(campaign=campaign, contact=contact, status="suppressed", error_msg="decision_wait")
                    continue
        except Exception:
            pass

        # ── Personalise ─────────────────────────────────────────
        unsub_url = _make_unsubscribe_url(contact)

        if getattr(template, 'template_format', 'html') == 'blocks':
            # Block-based template -- render via block_registry
            from block_registry import render_template_blocks
            html = render_template_blocks(template, contact, products=[], discount=None)
            html = html.replace("{{unsubscribe_url}}", unsub_url)
        else:
            # Legacy HTML template -- existing path unchanged
            html = template.html_body
            html = html.replace("{{first_name}}", contact.first_name or "Friend")
            html = html.replace("{{last_name}}",  contact.last_name  or "")
            html = html.replace("{{email}}",      contact.email)
            html = html.replace("{{unsubscribe_url}}", unsub_url)
            # Wrap in email shell if template uses shell_version >= 1
            if getattr(template, 'shell_version', 0) >= 1:
                from email_shell import wrap_email
                html = wrap_email(html, preview_text=template.preview_text or '', unsubscribe_url=unsub_url)

        pixel_url = _make_tracking_pixel_url(campaign_id, contact.id)
        html += f'<img src="{pixel_url}" width="1" height="1" />'

        subject = template.subject.replace("{{first_name}}", contact.first_name or "Friend")

        # ── Log to ledger as "rendered" and enqueue ──
        ledger = log_action(contact, "campaign", campaign_id, "rendered", RC_OK,
                            source_type=campaign.name,
                            template_id=template.id,
                            subject=subject, preview_text=template.preview_text or "",
                            html=html, priority=50)

        enqueue_email(
            contact=contact,
            email_type="campaign",
            source_id=campaign_id,
            enrollment_id=0,
            step_id=0,
            template_id=template.id,
            from_name=campaign.from_name,
            from_email=campaign.from_email,
            subject=subject,
            html=html,
            unsubscribe_url=unsub_url,
            priority=50,
            ledger_id=ledger.id if ledger else 0,
            campaign_id=campaign_id,
        )
        queued_count += 1

    # Mark campaign as queued (queue processor will mark "sent" when all drained)
    campaign.status = "queued" if queued_count > 0 else "sent"
    if queued_count == 0:
        campaign.sent_at = datetime.now()
    campaign.save()

def _get_campaign_contacts(campaign):
    """Get campaign contacts sorted by engagement (most engaged first).
    During warmup, sending to engaged contacts first maximizes open rates
    and builds sender reputation faster."""
    from peewee import fn, SQL

    query = Contact.select().where(Contact.subscribed == True)
    if campaign.segment_filter and campaign.segment_filter != "all":
        query = query.where(_tag_match(campaign.segment_filter))

    # Count recent opens per contact (last 30 days)
    cutoff_30d = (date.today() - timedelta(days=30)).isoformat()
    recent_opens = (CampaignEmail
                    .select(CampaignEmail.contact, fn.COUNT(CampaignEmail.id).alias("open_count"))
                    .where(CampaignEmail.opened == True, CampaignEmail.created_at >= cutoff_30d)
                    .group_by(CampaignEmail.contact))

    # Build a dict of contact_id → recent opens
    open_counts = {}
    for row in recent_opens:
        open_counts[row.contact_id] = row.open_count

    contacts = list(query)
    # Sort: most engaged first (recent opens DESC, then total_orders DESC)
    contacts.sort(key=lambda c: (open_counts.get(c.id, 0), c.total_orders), reverse=True)
    return contacts

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
        # Update contact-level last_open_at
        try:
            Contact.update(last_open_at=datetime.now()).where(Contact.id == contact_id).execute()
        except Exception as e:
            app.logger.warning("[SilentFix] Update last_open_at for contact %s: %s" % (contact_id, e))
        # Real-time pipeline: refresh after email open
        try:
            from cascade import cascade_contact
            cascade_contact(contact_id, trigger="campaign_open")
        except Exception:
            pass
    except:
        pass
    # Return a transparent 1x1 pixel
    from flask import Response
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(pixel, mimetype="image/gif")


# ── Token-based unsubscribe + tracking (Phase I security) ────
@app.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe_token(token):
    """Token-based unsubscribe (replaces plain email in URL)."""
    payload = verify_token(token)
    if not payload or payload.get("p") != "unsub":
        return render_template("unsubscribe.html", email="", success=False), 400

    email = payload.get("e", "")
    try:
        contact = Contact.get(Contact.email == email)
        contact.subscribed = False
        contact.save()
        (FlowEnrollment.update(status="cancelled")
                       .where(FlowEnrollment.contact == contact,
                              FlowEnrollment.status == "active")
                       .execute())
        return render_template("unsubscribe.html", email=email, success=True)
    except Exception:
        return render_template("unsubscribe.html", email=email, success=False)


@app.route("/track/open/<token>")
def track_open_token(token):
    """Token-based open tracking."""
    payload = verify_token(token)
    if payload and payload.get("p") == "open":
        campaign_id = payload.get("cmp")
        contact_id = payload.get("cid")
        try:
            ce = CampaignEmail.get(
                CampaignEmail.campaign == campaign_id,
                CampaignEmail.contact  == contact_id
            )
            if not ce.opened:
                ce.opened    = True
                ce.opened_at = datetime.now()
                ce.save()
            Contact.update(last_open_at=datetime.now()).where(Contact.id == contact_id).execute()
        except Exception as e:
            app.logger.warning("[SilentFix] Update last_open_at for contact %s: %s" % (contact_id, e))
        # Real-time pipeline: refresh after email open
        try:
            from cascade import cascade_contact
            cascade_contact(contact_id, trigger="campaign_open_token")
        except Exception:
            pass
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response as Resp
    return Resp(pixel, mimetype="image/gif")


@app.route("/track/flow-open/<token>")
def track_flow_open_token(token):
    """Token-based flow open tracking."""
    payload = verify_token(token)
    if payload and payload.get("p") == "fopen":
        enrollment_id = payload.get("eid")
        step_id = payload.get("sid")
        contact_id = payload.get("cid")
        try:
            fe = FlowEmail.get(
                FlowEmail.enrollment == enrollment_id,
                FlowEmail.step == step_id,
            )
            if not fe.opened:
                fe.opened    = True
                fe.opened_at = datetime.now()
                fe.save()
            Contact.update(last_open_at=datetime.now()).where(Contact.id == contact_id).execute()
        except Exception as e:
            app.logger.warning("[SilentFix] Update last_open_at for contact %s: %s" % (contact_id, e))
        # Real-time pipeline: refresh after flow email open
        try:
            from cascade import cascade_contact
            cascade_contact(contact_id, trigger="flow_open_token")
        except Exception:
            pass
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response as Resp
    return Resp(pixel, mimetype="image/gif")


@app.route("/track/flow-click/<token>")
def track_flow_click(token):
    """Track a flow email link click. Token = base64(enrollment_id:step_id)."""
    import base64
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        enrollment_id, step_id = decoded.split(":")
        enrollment_id, step_id = int(enrollment_id), int(step_id)
    except Exception:
        return redirect(request.args.get("url", "https://ldas.ca"))

    from database import FlowEmail
    fe = FlowEmail.get_or_none(
        (FlowEmail.enrollment == enrollment_id) &
        (FlowEmail.step == step_id)
    )
    if fe and not fe.clicked:
        fe.clicked = True
        fe.clicked_at = datetime.now()
        fe.save()
        # Also mark as opened if not already
        if not fe.opened:
            fe.opened = True
            fe.opened_at = datetime.now()
            fe.save()

    redirect_url = request.args.get("url", "https://ldas.ca")
    return redirect(redirect_url)


@app.route("/track/auto-open/<token>")
def track_auto_open(token):
    """Track auto-pilot email opens via 1x1 pixel."""
    from itsdangerous import URLSafeSerializer
    s = URLSafeSerializer(app.secret_key, salt="auto-open")
    try:
        data = s.loads(token)
        auto_email_id = data.get("aeid")
        if not auto_email_id:
            raise ValueError("missing aeid")
    except Exception:
        pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        from flask import Response as Resp
        return Resp(pixel, mimetype="image/gif")

    try:
        ae = AutoEmail.get_by_id(auto_email_id)
        if not ae.opened:
            ae.opened = True
            ae.opened_at = datetime.now()
        ae.save()

        contact = ae.contact
        contact.last_open_at = datetime.now()
        contact.save()

        try:
            from cascade import cascade_contact
            cascade_contact(contact.id, trigger="auto_open")
        except Exception:
            pass
    except AutoEmail.DoesNotExist:
        pass
    except Exception as e:
        print(f"[AUTO-OPEN] Error: {e}", file=sys.stderr)

    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response as Resp
    return Resp(pixel, mimetype="image/gif")


@app.route("/track/auto-open/<int:contact_id>/<int:template_id>")
def track_auto_open_legacy(contact_id, template_id):
    """Legacy fallback for auto-open pixels sent before token-based tracking."""
    try:
        ae = (AutoEmail
              .select()
              .where(AutoEmail.contact == contact_id,
                     AutoEmail.template == template_id)
              .order_by(AutoEmail.sent_at.desc())
              .first())
        if ae and not ae.opened:
            ae.opened = True
            ae.opened_at = datetime.now()
            ae.save()

            contact = ae.contact
            contact.last_open_at = datetime.now()
            contact.save()
            try:
                from cascade import cascade_contact
                cascade_contact(contact.id, trigger="auto_open_legacy")
            except Exception:
                pass
    except Exception as e:
        print(f"[AUTO-OPEN-LEGACY] Error: {e}", file=sys.stderr)

    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response as Resp
    return Resp(pixel, mimetype="image/gif")


@app.route("/track/auto-click/<token>")
def track_auto_click(token):
    """Track auto-pilot email clicks and redirect to destination URL."""
    from itsdangerous import URLSafeSerializer
    s = URLSafeSerializer(app.secret_key, salt="auto-click")
    destination = request.args.get("url", "https://mailenginehub.com")

    try:
        data = s.loads(token)
        auto_email_id = data.get("aeid")
        if not auto_email_id:
            raise ValueError("missing aeid")
    except Exception:
        return redirect(destination)

    try:
        ae = AutoEmail.get_by_id(auto_email_id)
        if not ae.clicked:
            ae.clicked = True
            ae.clicked_at = datetime.now()
        ae.save()

        contact = ae.contact
        contact.last_click_at = datetime.now()
        contact.save()

        try:
            from cascade import cascade_contact
            cascade_contact(contact.id, trigger="auto_click")
        except Exception:
            pass
    except AutoEmail.DoesNotExist:
        pass
    except Exception as e:
        print(f"[AUTO-CLICK] Error: {e}", file=sys.stderr)

    return redirect(destination)


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
    # Include FlowEmail data
    flow_recent = FlowEmail.select().where(FlowEmail.sent_at >= cutoff)
    total_sent  += flow_recent.where(FlowEmail.status == "sent").count()
    total_open  += flow_recent.where(FlowEmail.opened == True).count()
    total_bnc   += flow_recent.where(FlowEmail.status == "bounced").count()
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

    # ── Complaint rate & suppression stats (Phase I) ──────────
    complaint_rate = 0
    total_complaints = 0
    total_suppressed = 0
    suppression_by_reason = {"hard_bounce": 0, "complaint": 0, "invalid": 0, "manual": 0}
    recent_events = []
    domain_stats = []

    try:
        from database import BounceLog, SuppressionEntry

        # Complaint rate (last 14 days)
        total_complaints = (BounceLog.select()
                           .where(BounceLog.event_type == "Complaint",
                                  BounceLog.timestamp >= cutoff)
                           .count())
        if total_sent > 0:
            complaint_rate = round(total_complaints / total_sent * 100, 3)

        # Suppression stats
        total_suppressed = SuppressionEntry.select().count()
        for reason in ["hard_bounce", "complaint", "invalid", "manual"]:
            suppression_by_reason[reason] = (SuppressionEntry.select()
                .where(SuppressionEntry.reason == reason).count())

        # Recent bounce/complaint events (last 20)
        recent_events = list(
            BounceLog.select()
            .order_by(BounceLog.timestamp.desc())
            .limit(20)
            .dicts()
        )
    except Exception:
        pass  # Tables may not exist yet

    # ── Domain-level stats ─────────────────────────────────
    try:
        from peewee import fn
        # Get send/open/bounce stats per recipient domain (top 10)
        # Campaign email domain stats
        domain_map = {}
        campaign_rows = (CampaignEmail
            .select(
                fn.SUBSTR(Contact.email, fn.INSTR(Contact.email, '@') + 1).alias('domain'),
                fn.COUNT(CampaignEmail.id).alias('sent'),
                fn.SUM(CampaignEmail.opened.cast('int')).alias('opens'),
            )
            .join(Contact, on=(CampaignEmail.contact == Contact.id))
            .where(CampaignEmail.status == "sent", CampaignEmail.created_at >= cutoff)
            .group_by(fn.SUBSTR(Contact.email, fn.INSTR(Contact.email, '@') + 1))
            .order_by(fn.COUNT(CampaignEmail.id).desc())
            .limit(20)
            .dicts())
        for row in campaign_rows:
            d = row.get("domain", "unknown")
            domain_map[d] = {"sent": row.get("sent", 0), "opens": row.get("opens", 0) or 0}

        # Flow email domain stats — merge in
        flow_rows = (FlowEmail
            .select(
                fn.SUBSTR(Contact.email, fn.INSTR(Contact.email, '@') + 1).alias('domain'),
                fn.COUNT(FlowEmail.id).alias('sent'),
                fn.SUM(FlowEmail.opened.cast('int')).alias('opens'),
            )
            .join(Contact, on=(FlowEmail.contact == Contact.id))
            .where(FlowEmail.status == "sent", FlowEmail.sent_at >= cutoff)
            .group_by(fn.SUBSTR(Contact.email, fn.INSTR(Contact.email, '@') + 1))
            .order_by(fn.COUNT(FlowEmail.id).desc())
            .limit(20)
            .dicts())
        for row in flow_rows:
            d = row.get("domain", "unknown")
            if d in domain_map:
                domain_map[d]["sent"] += row.get("sent", 0)
                domain_map[d]["opens"] += row.get("opens", 0) or 0
            else:
                domain_map[d] = {"sent": row.get("sent", 0), "opens": row.get("opens", 0) or 0}

        # Sort by sent count, take top 10
        domain_stats = []
        for d, v in sorted(domain_map.items(), key=lambda x: x[1]["sent"], reverse=True)[:10]:
            domain_stats.append({
                "domain": d,
                "sent": v["sent"],
                "opens": v["opens"],
                "open_rate": round(v["opens"] / v["sent"] * 100, 1) if v["sent"] > 0 else 0,
            })
    except Exception:
        pass

    # ── Phase I completion: decision layer data ────────────
    preflight_result = None
    try:
        last_pf = (PreflightLog.select()
                   .order_by(PreflightLog.created_at.desc())
                   .first())
        if last_pf:
            preflight_result = {
                "overall": last_pf.overall,
                "checks": json.loads(last_pf.checks_json),
                "campaign_id": last_pf.campaign_id,
                "timestamp": last_pf.created_at.strftime("%Y-%m-%d %H:%M"),
            }
    except Exception:
        pass

    # ── Google Postmaster Tools metrics ──────────────────
    postmaster_latest = None
    postmaster_trend = []
    try:
        from postmaster_tools import get_latest_metrics, get_metrics_trend
        postmaster_latest = get_latest_metrics()
        postmaster_trend = get_metrics_trend(days=14)
    except Exception:
        pass  # Module or credentials not available yet

    sent_today = config.emails_sent_today if config.is_active else 0
    safe_send_volume = max(0, daily_limit - sent_today) if config.is_active else daily_limit

    blocked_count = Contact.select().where(
        (Contact.suppression_reason != "") | (Contact.subscribed == False)
    ).count()

    suppression_breakdown = {}
    for reason in ["hard_bounce", "complaint", "invalid", "manual", "fatigue"]:
        cnt = Contact.select().where(Contact.suppression_reason == reason).count()
        if cnt > 0:
            suppression_breakdown[reason] = cnt

    risky_domains = []
    try:
        risky_domains = [d for d in get_bounce_stats_by_domain(days=30) if d["total"] >= 3]
    except Exception:
        pass

    if config.current_phase <= 2:
        recommended_speed = "Slow (1 email / 2 sec)"
    elif config.current_phase <= 4:
        recommended_speed = "Moderate (1 / sec)"
    elif config.current_phase <= 6:
        recommended_speed = "Fast (2-3 / sec)"
    else:
        recommended_speed = "Full speed (5+ / sec)"
    # ────────────────────────────────────────────────────────

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
        # Phase I additions
        complaint_rate=complaint_rate,
        total_complaints=total_complaints,
        total_suppressed=total_suppressed,
        suppression_by_reason=suppression_by_reason,
        recent_events=recent_events,
        domain_stats=domain_stats,
        # Phase I completion — decision layer
        preflight_result=preflight_result,
        safe_send_volume=safe_send_volume,
        blocked_count=blocked_count,
        suppression_breakdown=suppression_breakdown,
        risky_domains=risky_domains,
        recommended_speed=recommended_speed,
        # Google Postmaster Tools
        postmaster_latest=postmaster_latest,
        postmaster_trend=postmaster_trend,
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
    # Include FlowEmail data
    flow_r     = FlowEmail.select().where(FlowEmail.sent_at >= cutoff)
    total_sent += flow_r.where(FlowEmail.status == "sent").count()
    total_open += flow_r.where(FlowEmail.opened == True).count()
    total_bnc  += flow_r.where(FlowEmail.status == "bounced").count()
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

# ─────────────────────────────────
#  FLOW CONTROL: PAUSE / RESUME / EXIT
# ─────────────────────────────────
def _pause_lower_priority_enrollments(contact, new_flow):
    """Pause any active enrollments in flows with LOWER priority (higher number) than new_flow.
    Exception: if the enrollment hasn't sent Step 1 yet (e.g. Welcome Series discount code),
    force-send Step 1 immediately before pausing so the customer gets what they were promised.
    """
    from delivery_engine import enqueue_email, get_priority_for_trigger
    active_enrollments = (
        FlowEnrollment.select(FlowEnrollment, Flow)
        .join(Flow)
        .where(FlowEnrollment.contact == contact,
               FlowEnrollment.status == "active",
               Flow.priority > new_flow.priority,
               FlowEnrollment.flow != new_flow)
    )
    paused_count = 0
    for enrollment in active_enrollments:
        # Check if Step 1 has been sent
        first_step = (FlowStep.select()
                      .where(FlowStep.flow == enrollment.flow)
                      .order_by(FlowStep.step_order)
                      .first())
        step1_sent = False
        if first_step:
            step1_sent = FlowEmail.select().where(
                FlowEmail.enrollment == enrollment,
                FlowEmail.step == first_step
            ).exists()

        # If Step 1 hasn't sent yet, force-send it now before pausing
        if first_step and not step1_sent and contact.subscribed:
            try:
                template = EmailTemplate.get_by_id(first_step.template_id)
                _unsub = _make_unsubscribe_url(contact)

                # Determine discount purpose from flow trigger
                _TRIGGER_TO_PURPOSE = {
                    "checkout_abandoned": "cart_abandonment",
                    "browse_abandonment": "browse_abandonment",
                    "no_purchase_days":   "winback",
                    "contact_created":    "welcome",
                    "order_placed":       "loyalty_reward",
                }
                _dpurpose = _TRIGGER_TO_PURPOSE.get(enrollment.flow.trigger_type, "welcome")

                if getattr(template, 'template_format', 'html') == 'blocks':
                    from block_registry import render_template_blocks
                    from discount_engine import get_or_create_discount, get_discount_display
                    _dinfo = get_or_create_discount(contact.email, _dpurpose)
                    _ddisplay = get_discount_display(_dinfo) if _dinfo else None
                    html = render_template_blocks(template, contact, products=[], discount=_ddisplay)
                    html = html.replace("{{unsubscribe_url}}", _unsub)
                else:
                    html = template.html_body or ""
                    html = html.replace("{{first_name}}", contact.first_name or "Friend")
                    html = html.replace("{{last_name}}", contact.last_name or "")
                    html = html.replace("{{email}}", contact.email)
                    html = html.replace("{{unsubscribe_url}}", _unsub)
                    if "{{discount_code}}" in html:
                        try:
                            from discount_engine import get_or_create_discount
                            _result = get_or_create_discount(contact.email, _dpurpose)
                            _dcode = _result.get("code", "") if isinstance(_result, dict) else ""
                            html = html.replace("{{discount_code}}", _dcode)
                        except Exception:
                            html = html.replace("{{discount_code}}", "")

                subject = (first_step.subject_override or template.subject or "Welcome!").replace(
                    "{{first_name}}", contact.first_name or "Friend")

                _priority = get_priority_for_trigger(enrollment.flow.trigger_type)
                enqueue_email(
                    contact_id=contact.id,
                    email=contact.email,
                    subject=subject,
                    html=html,
                    email_type="flow",
                    source_id=enrollment.flow_id,
                    enrollment_id=enrollment.id,
                    step_id=first_step.id,
                    template_id=first_step.template_id,
                    priority=_priority,
                    unsubscribe_url=_unsub,
                )
                # Record flow email
                FlowEmail.create(
                    enrollment=enrollment,
                    step=first_step,
                    contact=contact,
                    status="queued",
                )
                # Advance enrollment to step 2
                enrollment.current_step = 2
                app.logger.info(
                    "[SmartExit] Force-sent Step 1 of '%s' for %s before pausing (discount code promised)"
                    % (enrollment.flow.name, contact.email))
            except Exception as e:
                app.logger.error("[SmartExit] Failed to force-send Step 1 for %s: %s" % (contact.email, e))

        enrollment.status = "paused"
        enrollment.paused_by_flow = new_flow.id
        enrollment.save()
        paused_count += 1
        app.logger.info(
            "[SmartExit] Paused '%s' (priority=%s) for contact #%s — entering '%s' (priority=%s)"
            % (enrollment.flow.name, enrollment.flow.priority, contact.id,
               new_flow.name, new_flow.priority))
    return paused_count


def _resume_paused_enrollments(completed_flow_id):
    """Resume enrollments that were paused by the given flow."""
    paused = list(FlowEnrollment.select().where(
        FlowEnrollment.paused_by_flow == completed_flow_id,
        FlowEnrollment.status == "paused"))
    resumed_count = 0
    for enrollment in paused:
        enrollment.status = "active"
        enrollment.paused_by_flow = 0
        enrollment.next_send_at = datetime.now()
        enrollment.save()
        resumed_count += 1
        app.logger.info(
            "[SmartExit] Resumed enrollment #%s (flow #%s) for contact #%s — pausing flow #%s completed/cancelled"
            % (enrollment.id, enrollment.flow_id, enrollment.contact_id, completed_flow_id))
    return resumed_count


def _exit_flows_by_trigger_type(contact, trigger_types, reason_code="flow_exit_purchase"):
    """Cancel active+paused enrollments in flows matching given trigger types.

    Logs an ActionLedger entry with status='exited' for each cancelled enrollment
    so flow exits are fully auditable.
    """
    from action_ledger import log_action
    flows_to_exit = list(Flow.select().where(Flow.trigger_type.in_(trigger_types)))
    for flow in flows_to_exit:
        enrollments = list(FlowEnrollment.select().where(
            FlowEnrollment.contact == contact,
            FlowEnrollment.flow == flow,
            FlowEnrollment.status.in_(["active", "paused"])))
        for enrollment in enrollments:
            old_status = enrollment.status
            enrollment.status = "cancelled"
            enrollment.save()
            log_action(
                contact, "flow", flow.id, "exited", reason_code,
                source_type=flow.name,
                enrollment_id=enrollment.id,
                reason_detail="Auto-exited from '%s' (was %s) — customer converted" % (flow.name, old_status),
            )
            app.logger.info(
                "[SmartExit] Exited '%s' for contact #%s (was %s) — %s"
                % (flow.name, contact.id, old_status, reason_code))
            # If this flow was pausing others, resume them
            _resume_paused_enrollments(flow.id)


def _enroll_contact_in_flows(contact, trigger_type, trigger_value=""):
    """Enroll a contact in all active flows matching trigger_type (and trigger_value if relevant).

    For order_placed flows: skips if contact completed the same flow in the last 30 days.
    This prevents repeat buyers getting the same post-purchase sequence every order.
    """
    query = Flow.select().where(Flow.is_active == True, Flow.trigger_type == trigger_type)
    if trigger_type == "tag_added" and trigger_value:
        query = query.where(Flow.trigger_value == trigger_value)

    for flow in query:
        # For order_placed: skip if recently completed this flow (30-day cooldown)
        if trigger_type == "order_placed":
            _thirty_days_ago = datetime.now() - timedelta(days=30)
            _recent = (FlowEnrollment.select()
                       .where(FlowEnrollment.flow == flow,
                              FlowEnrollment.contact == contact,
                              FlowEnrollment.status == "completed",
                              FlowEnrollment.enrolled_at >= _thirty_days_ago)
                       .first())
            if _recent:
                app.logger.info("[FlowEnroll] Skipping '%s' for %s — completed %s (30-day cooldown)"
                                % (flow.name, contact.email, _recent.enrolled_at.strftime('%Y-%m-%d')))
                continue

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
            # ── Phase 3: Pause lower-priority enrollments ──
            _pause_lower_priority_enrollments(contact, flow)
        except Exception:
            pass  # Unique constraint — already enrolled



def _get_last_email_sent_at(contact):
    """Return datetime of most recent email sent to this contact, or None.
    Checks both FlowEmail and CampaignEmail tables."""
    last_flow = (FlowEmail.select(FlowEmail.sent_at)
                 .where(FlowEmail.contact == contact, FlowEmail.status == "sent")
                 .order_by(FlowEmail.sent_at.desc()).first())
    last_campaign = (CampaignEmail.select(CampaignEmail.created_at)
                     .where(CampaignEmail.contact == contact, CampaignEmail.status == "sent")
                     .order_by(CampaignEmail.created_at.desc()).first())
    times = []
    if last_flow and last_flow.sent_at:
        times.append(last_flow.sent_at)
    if last_campaign and last_campaign.created_at:
        times.append(last_campaign.created_at)
    return max(times) if times else None


def _process_flow_enrollments():
    """Run every 60 seconds. Send pending flow emails whose next_send_at has passed.
    Every decision point is recorded in the ActionLedger for full traceability.
    Emails are enqueued into DeliveryQueue instead of sent directly.
    """
    from action_ledger import log_action, RC_COOLDOWN_ACTIVE, RC_UNSUBSCRIBED, \
        RC_SUPPRESSED_ENTRY, RC_NO_STEP, RC_WARMUP_LIMIT, RC_OK
    from delivery_engine import enqueue_email, get_priority_for_trigger
    from database import CustomerProfile, ContactScore, CustomerActivity

    now = datetime.now()
    pending = (FlowEnrollment.select(FlowEnrollment, Flow, Contact)
               .join(Flow)
               .switch(FlowEnrollment)
               .join(Contact)
               .where(FlowEnrollment.status == "active",
                      FlowEnrollment.next_send_at <= now)
               .order_by(Flow.priority.asc()))  # highest priority first

    warmup = get_warmup_config()
    today_str = date.today().isoformat()
    if warmup.is_active and warmup.last_reset_date != today_str:
        warmup.emails_sent_today = 0
        warmup.last_reset_date   = today_str
        warmup.save()

    # ── Track which contacts already enqueued this tick ──
    sent_contacts = set()

    for enrollment in pending:
        contact = enrollment.contact
        flow = enrollment.flow
        _priority = get_priority_for_trigger(flow.trigger_type)

        # ── Priority gate — one send per contact per tick ──
        if contact.id in sent_contacts:
            log_action(contact, "flow", flow.id, "suppressed", RC_COOLDOWN_ACTIVE,
                       source_type=flow.name, enrollment_id=enrollment.id,
                       priority=_priority,
                       reason_detail="Higher-priority flow already queued this tick")
            app.logger.info(
                "[Priority] Delayed '%s' (priority=%s) for %s — higher-priority flow already sending this tick"
                % (flow.name, flow.priority, contact.email))
            continue

        if not contact.subscribed:
            log_action(contact, "flow", flow.id, "suppressed", RC_UNSUBSCRIBED,
                       source_type=flow.name, enrollment_id=enrollment.id,
                       priority=_priority,
                       reason_detail="Contact unsubscribed")
            enrollment.status = "cancelled"
            enrollment.save()
            _resume_paused_enrollments(enrollment.flow_id)
            continue

        # ── Suppression list check ───────────────────────────
        try:
            from database import SuppressionEntry
            if SuppressionEntry.select().where(SuppressionEntry.email == contact.email).exists():
                log_action(contact, "flow", flow.id, "suppressed", RC_SUPPRESSED_ENTRY,
                           source_type=flow.name, enrollment_id=enrollment.id,
                           priority=_priority,
                           reason_detail="Contact on suppression list")
                enrollment.status = "cancelled"
                enrollment.save()
                continue
        except Exception:
            pass


        # ── Frequency cap (personalized via learning engine, floor 16h) ──────────
        try:
            from strategy_optimizer import get_contact_frequency_cap
            FREQ_CAP_HOURS = get_contact_frequency_cap(contact.id)
        except Exception:
            FREQ_CAP_HOURS = 16  # Fallback to original static cap
        last_sent_at = _get_last_email_sent_at(contact)
        if last_sent_at:
            hours_since = (now - last_sent_at).total_seconds() / 3600
            if hours_since < FREQ_CAP_HOURS:
                new_send_at = last_sent_at + timedelta(hours=FREQ_CAP_HOURS)
                enrollment.next_send_at = new_send_at
                enrollment.save()
                log_action(contact, "flow", flow.id, "suppressed", RC_COOLDOWN_ACTIVE,
                           source_type=flow.name, enrollment_id=enrollment.id,
                           priority=_priority,
                           reason_detail="%.0fh frequency cap — last email %.1fh ago, rescheduled to %s"
                                         % (FREQ_CAP_HOURS, hours_since, new_send_at.strftime('%Y-%m-%d %H:%M')))
                app.logger.info(
                    "[FreqCap] Delayed '%s' step %s for %s — last email %.1fh ago, rescheduled to %s"
                    % (flow.name, enrollment.current_step, contact.email,
                       hours_since, new_send_at.strftime('%Y-%m-%d %H:%M')))
                continue

        step = (FlowStep.select()
                .where(FlowStep.flow == enrollment.flow,
                       FlowStep.step_order == enrollment.current_step)
                .first())
        if not step:
            log_action(contact, "flow", flow.id, "suppressed", RC_NO_STEP,
                       source_type=flow.name, enrollment_id=enrollment.id,
                       priority=_priority,
                       reason_detail="No step %d found, enrollment completed" % enrollment.current_step)
            enrollment.status = "completed"
            enrollment.save()
            _resume_paused_enrollments(enrollment.flow_id)
            continue

        # Note: warmup limits are enforced by the delivery_engine when actually
        # sending via SES. Flow emails should always be enqueued — the delivery
        # engine will hold them if the warmup cap is hit. Blocking here caused
        # flow emails (e.g. welcome discount codes) to be silently skipped
        # instead of queued, so the contact never received them.

        template = step.template
        _unsub = _make_unsubscribe_url(contact)

        # Map flow trigger types to discount engine purposes
        _TRIGGER_TO_DISCOUNT_PURPOSE = {
            "checkout_abandoned": "cart_abandonment",
            "browse_abandonment": "browse_abandonment",
            "no_purchase_days":   "winback",
            "contact_created":    "welcome",
            "order_placed":       "loyalty_reward",
        }
        _discount_purpose = _TRIGGER_TO_DISCOUNT_PURPOSE.get(flow.trigger_type, "welcome")

        if getattr(template, 'template_format', 'html') == 'blocks':
            # Block-based template -- render via block_registry
            from block_registry import render_template_blocks
            from discount_engine import get_or_create_discount, get_discount_display
            _discount_info = get_or_create_discount(contact.email, _discount_purpose)
            _discount_display = get_discount_display(_discount_info) if _discount_info else None
            html = render_template_blocks(template, contact, products=[], discount=_discount_display)
            html = html.replace("{{unsubscribe_url}}", _unsub)
        else:
            # Legacy HTML template -- existing path unchanged
            html = template.html_body
            html = html.replace("{{first_name}}", contact.first_name or "Friend")
            html = html.replace("{{last_name}}",  contact.last_name  or "")
            html = html.replace("{{email}}",      contact.email)
            html = html.replace("{{unsubscribe_url}}", _unsub)
            # Reuse existing discount code or create new one
            if "{{discount_code}}" in html:
                from discount_engine import get_or_create_discount
                _result = get_or_create_discount(contact.email, _discount_purpose)
                _dcode = _result.get("code", "") if isinstance(_result, dict) else ""
                html = html.replace("{{discount_code}}", _dcode)

            # Inject checkout/cart variables for abandoned checkout flows
            if flow.trigger_type == "checkout_abandoned" and ("{{cart_items}}" in html or "{{checkout_url}}" in html):
                _checkout = (AbandonedCheckout.select()
                             .where(AbandonedCheckout.contact == contact,
                                    AbandonedCheckout.recovered == False)
                             .order_by(AbandonedCheckout.created_at.desc())
                             .first())
                if _checkout:
                    import json as _json
                    try:
                        _items = _json.loads(_checkout.line_items_json)
                        _cart_html = ""
                        for _it in _items:
                            _cart_html += f'<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; {_it.get("title","")} x{_it.get("quantity",1)} — ${_it.get("price","0.00")}</p>'
                        if not _cart_html:
                            _cart_html = '<p style="margin:4px 0;font-size:14px;color:#4a5568;">Your selected items</p>'
                    except Exception:
                        _cart_html = '<p style="margin:4px 0;font-size:14px;color:#4a5568;">Your selected items</p>'
                    html = html.replace("{{cart_items}}", _cart_html)
                    html = html.replace("{{checkout_url}}", _checkout.checkout_url or "https://ldas.ca/checkout")
                else:
                    # No AbandonedCheckout record (came from viewed_cart pixel) —
                    # pull recently viewed products as cart proxy
                    _cart_html = ""
                    try:
                        import json as _json
                        _recent_views = (CustomerActivity.select()
                                         .where(CustomerActivity.contact == contact,
                                                CustomerActivity.event_type == 'viewed_product')
                                         .order_by(CustomerActivity.occurred_at.desc())
                                         .limit(4))
                        _seen = set()
                        for _rv in _recent_views:
                            try:
                                _rd = _json.loads(_rv.event_data) if isinstance(_rv.event_data, str) else _rv.event_data
                                _title = _rd.get("product_title", "")
                                _url = _rd.get("product_url", "")
                                if _title and _title not in _seen:
                                    _seen.add(_title)
                                    _cart_html += f'<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; {_title}</p>'
                            except Exception:
                                pass
                    except Exception:
                        pass
                    if not _cart_html:
                        _cart_html = '<p style="margin:4px 0;font-size:14px;color:#4a5568;">Your selected items</p>'
                    html = html.replace("{{cart_items}}", _cart_html)
                    html = html.replace("{{checkout_url}}", "https://ldas.ca/cart")

            # ── Personalization injection — pull from CustomerProfile + ContactScore ──
            try:
                _profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact)
                _cscore  = ContactScore.get_or_none(ContactScore.contact == contact)

                # Last viewed product (for browse abandonment emails)
                _last_viewed = ""
                if _profile and _profile.last_viewed_product:
                    _last_viewed = _profile.last_viewed_product
                html = html.replace("{{last_viewed_product}}", _last_viewed or "one of our popular items")

                # Recently browsed products (HTML rows from activity)
                if "{{recently_browsed_html}}" in html:
                    _browsed_html = ""
                    try:
                        _recent = (CustomerActivity.select()
                                   .where(CustomerActivity.contact == contact,
                                          CustomerActivity.event_type == 'viewed_product')
                                   .order_by(CustomerActivity.occurred_at.desc())
                                   .limit(4))
                        _seen_titles = set()
                        for _act in _recent:
                            import json as _jj
                            _d = _jj.loads(_act.event_data) if _act.event_data else {}
                            _t = _d.get("product_title", "")
                            if _t and _t not in _seen_titles:
                                _seen_titles.add(_t)
                                _p = _d.get("price", "")
                                _price_str = " — $%s" % _p if _p else ""
                                _browsed_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; <strong>%s</strong>%s</p>' % (_t, _price_str)
                    except Exception:
                        pass
                    html = html.replace("{{recently_browsed_html}}", _browsed_html or '<p style="margin:4px 0;font-size:14px;color:#4a5568;">Your recently viewed items</p>')

                # Top purchased products (for cross-sell / post-purchase emails)
                if "{{top_products_html}}" in html:
                    _top_html = ""
                    try:
                        if _profile and _profile.top_products:
                            import json as _jj2
                            _tops = _jj2.loads(_profile.top_products)[:3]
                            for _tp in _tops:
                                _top_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; %s</p>' % _tp
                    except Exception:
                        pass
                    html = html.replace("{{top_products_html}}", _top_html or "")

                # Customer stats
                html = html.replace("{{total_orders}}", str(_profile.total_orders) if _profile else "0")
                html = html.replace("{{total_spent}}", "$%.2f" % _profile.total_spent if _profile and _profile.total_spent else "$0")

                # RFM segment and lifecycle
                html = html.replace("{{rfm_segment}}", _cscore.rfm_segment if _cscore else "new")
                html = html.replace("{{lifecycle_stage}}", _profile.lifecycle_stage if _profile and hasattr(_profile, 'lifecycle_stage') else "prospect")

                # ── Learned intelligence variables ──────────────────
                # Customer type (vip/loyal/repeat/new/etc.)
                html = html.replace("{{customer_type}}", _profile.customer_type if _profile and hasattr(_profile, 'customer_type') and _profile.customer_type else "valued customer")

                # Top category affinity
                _top_cat = ""
                try:
                    if _profile and hasattr(_profile, 'category_affinity_json') and _profile.category_affinity_json:
                        import json as _cj
                        _cats = _cj.loads(_profile.category_affinity_json)
                        if isinstance(_cats, dict) and _cats:
                            _top_cat = max(_cats, key=_cats.get)
                        elif isinstance(_cats, list) and _cats:
                            _top_cat = _cats[0] if isinstance(_cats[0], str) else _cats[0].get("category", "")
                except Exception:
                    pass
                html = html.replace("{{top_category}}", _top_cat or "our top picks")

                # Days since last purchase
                _days_since = ""
                try:
                    if _profile and hasattr(_profile, 'last_order_at') and _profile.last_order_at:
                        from datetime import datetime as _dt
                        _dsp = (datetime.now() - _profile.last_order_at).days
                        _days_since = str(_dsp)
                except Exception:
                    pass
                html = html.replace("{{days_since_purchase}}", _days_since or "a while")

                # Intent level (high/medium/low bucket from intent_score 0-100)
                _intent = "medium"
                try:
                    if _profile and hasattr(_profile, 'intent_score') and _profile.intent_score is not None:
                        if _profile.intent_score >= 70:
                            _intent = "high"
                        elif _profile.intent_score >= 35:
                            _intent = "medium"
                        else:
                            _intent = "low"
                except Exception:
                    pass
                html = html.replace("{{intent_level}}", _intent)

            except Exception as _perr:
                app.logger.warning("[FlowPersonalization] Error loading profile for %s: %s", contact.email, _perr)
                # Fallback — clear any remaining personalization tags
                import re as _re
                for _tag in ["{{last_viewed_product}}", "{{recently_browsed_html}}", "{{top_products_html}}",
                             "{{total_orders}}", "{{total_spent}}", "{{rfm_segment}}", "{{lifecycle_stage}}",
                             "{{customer_type}}", "{{top_category}}", "{{days_since_purchase}}", "{{intent_level}}"]:
                    html = html.replace(_tag, "")

            # Wrap in email shell if template uses shell_version >= 1
            if getattr(template, 'shell_version', 0) >= 1:
                from email_shell import wrap_email
                html = wrap_email(html, preview_text=template.preview_text or '', unsubscribe_url=_unsub)

        flow_pixel = _make_flow_tracking_pixel_url(enrollment.id, step.id, contact.id)
        html += f'<img src="{flow_pixel}" width="1" height="1" />'

        subject = step.subject_override or template.subject
        subject = subject.replace("{{first_name}}", contact.first_name or "Friend")
        # Personalize subject with browse/purchase data
        try:
            if "{{last_viewed_product}}" in subject:
                _prof = CustomerProfile.get_or_none(CustomerProfile.contact == contact)
                _lvp = _prof.last_viewed_product if _prof and _prof.last_viewed_product else "something great"
                subject = subject.replace("{{last_viewed_product}}", _lvp)
            if "{{total_orders}}" in subject:
                _prof2 = CustomerProfile.get_or_none(CustomerProfile.contact == contact)
                subject = subject.replace("{{total_orders}}", str(_prof2.total_orders) if _prof2 else "0")
        except Exception:
            subject = subject.replace("{{last_viewed_product}}", "something great")
            subject = subject.replace("{{total_orders}}", "")

        from_email = step.from_email or os.getenv("DEFAULT_FROM_EMAIL", "")
        from_name  = step.from_name
        unsub_url = _make_unsubscribe_url(contact)

        # ── Dedup: skip if this enrollment+step is already queued ──
        _already_queued = (DeliveryQueue.select()
                           .where(DeliveryQueue.enrollment_id == enrollment.id,
                                  DeliveryQueue.step_id == step.id,
                                  DeliveryQueue.status.in_(["queued", "sending"]))
                           .exists())
        if _already_queued:
            app.logger.info("[FlowDedup] Skipping %s step %d for %s — already queued"
                            % (flow.name, enrollment.current_step, contact.email))
            sent_contacts.add(contact.id)
            continue

        # ── Log to ledger as "rendered" and enqueue ──
        ledger = log_action(contact, "flow", flow.id, "rendered", RC_OK,
                            source_type=flow.name, enrollment_id=enrollment.id,
                            step_id=step.id, template_id=template.id,
                            subject=subject, preview_text=template.preview_text or "",
                            html=html, priority=_priority)

        enqueue_email(
            contact=contact,
            email_type="flow",
            source_id=flow.id,
            enrollment_id=enrollment.id,
            step_id=step.id,
            template_id=template.id,
            from_name=from_name,
            from_email=from_email,
            subject=subject,
            html=html,
            unsubscribe_url=unsub_url,
            priority=_priority,
            ledger_id=ledger.id if ledger else 0,
        )

        sent_contacts.add(contact.id)  # mark contact as enqueued this tick

        # Real-time pipeline: refresh contact intelligence after flow email queued
        try:
            from cascade import cascade_contact
            cascade_contact(contact.id, trigger="flow_email_sent")
        except Exception:
            pass

        # Note: enrollment advancement is now handled by the delivery_engine
        # queue processor after the email is actually sent/shadowed


def _check_abandoned_checkouts():
    """Run every 15 min. Enroll contacts with 1h+ old un-recovered checkouts into checkout_abandoned flows."""
    try:
        one_hour_ago = datetime.now() - timedelta(hours=1)
        # Use abandoned_at (Shopify's actual timestamp) not created_at (DB insert time)
        # This ensures the 1-hour delay is from actual abandonment, not webhook processing
        pending = (AbandonedCheckout.select()
                   .where(AbandonedCheckout.recovered == False,
                          AbandonedCheckout.enrolled_in_flow == False,
                          AbandonedCheckout.abandoned_at <= one_hour_ago.isoformat()))

        enrolled_count = 0
        for checkout in pending:
            contact = checkout.contact
            if not contact or not contact.subscribed:
                continue

            # Enroll in checkout_abandoned flows
            checkout_flows = (Flow.select()
                              .where(Flow.is_active == True,
                                     Flow.trigger_type == "checkout_abandoned"))
            for flow in checkout_flows:
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
                        next_send_at=datetime.now() + timedelta(hours=first_step.delay_hours),
                        status="active",
                    )
                    enrolled_count += 1
                    # Phase 3: Pause lower-priority enrollments
                    _pause_lower_priority_enrollments(contact, flow)
                except Exception:
                    pass  # Already enrolled

            checkout.enrolled_in_flow = True
            checkout.save()

        if enrolled_count > 0:
            app.logger.info(f"Abandoned checkout checker: {enrolled_count} enrollments created")
    except Exception as e:
        app.logger.error(f"Abandoned checkout checker error: {e}")


def _check_passive_triggers():
    """Run every 30 min. Check no_purchase_days triggers and cancel unsubscribed enrollments.

    Uses batched processing with sleeps to avoid SQLite 'database is locked' errors.
    Total timeout: 5 minutes max per run.
    """
    import time as _time

    BATCH_SIZE = 100
    BATCH_SLEEP = 0.1  # seconds between batches
    MAX_RUNTIME = 300  # 5 minutes total timeout
    _start_time = _time.time()

    def _timed_out():
        return (_time.time() - _start_time) >= MAX_RUNTIME

    # -- Cancel enrollments for unsubscribed contacts (batched) --
    try:
        unsub_ids = list(
            Contact.select(Contact.id)
            .where(Contact.subscribed == False)
            .tuples()
        )
        unsub_ids = [r[0] for r in unsub_ids]
        app.logger.info("Passive triggers: %d unsubscribed contacts to check" % len(unsub_ids))

        for offset in range(0, len(unsub_ids), BATCH_SIZE):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during unsub cancellation at offset %d" % offset)
                break
            batch = unsub_ids[offset:offset + BATCH_SIZE]
            try:
                (FlowEnrollment.update(status="cancelled")
                    .where(FlowEnrollment.contact_id.in_(batch),
                           FlowEnrollment.status.in_(["active", "paused"]))
                    .execute())
            except Exception as e:
                app.logger.warning("Passive triggers: unsub batch error at offset %d: %s" % (offset, e))
            _time.sleep(BATCH_SLEEP)
    except Exception as e:
        app.logger.warning("Passive triggers: unsub phase error: %s" % e)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after unsub phase, skipping winback")
        return

    # -- no_purchase_days: enroll shopify contacts not yet in these flows (batched) --
    winback_flows = (Flow.select()
                     .where(Flow.is_active == True, Flow.trigger_type == "no_purchase_days"))
    for flow in winback_flows:
        if _timed_out():
            app.logger.warning("Passive triggers: timed out during winback flows")
            break
        try:
            days = int(flow.trigger_value)
        except (ValueError, TypeError):
            continue
        cutoff = datetime.now() - timedelta(days=days)

        from database import CustomerProfile
        try:
            contact_ids = list(
                Contact.select(Contact.id)
                .join(CustomerProfile, on=(CustomerProfile.contact == Contact.id))
                .where(Contact.source == "shopify",
                       Contact.subscribed == True,
                       CustomerProfile.last_order_at.is_null(False),
                       CustomerProfile.last_order_at <= cutoff)
                .tuples()
            )
            contact_ids = [r[0] for r in contact_ids]
        except Exception as e:
            app.logger.warning("Passive triggers: winback query error for flow %s: %s" % (flow.id, e))
            continue

        first_step = (FlowStep.select()
                      .where(FlowStep.flow == flow)
                      .order_by(FlowStep.step_order)
                      .first())
        if not first_step:
            continue

        app.logger.info("Passive triggers: %d candidates for winback flow %s" % (len(contact_ids), flow.id))

        for offset in range(0, len(contact_ids), BATCH_SIZE):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during winback enrollment at offset %d" % offset)
                break
            batch = contact_ids[offset:offset + BATCH_SIZE]
            for cid in batch:
                try:
                    FlowEnrollment.create(
                        flow=flow,
                        contact=cid,
                        current_step=1,
                        next_send_at=datetime.now(),
                        status="active",
                    )
                except Exception:
                    pass  # Already enrolled
            _time.sleep(BATCH_SLEEP)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after winback phase, skipping behavioural triggers")
        return

    # -- Phase G: Behavioural Trigger Detection (batched) --
    _detect_behavioural_triggers(_start_time, MAX_RUNTIME, BATCH_SIZE, BATCH_SLEEP)

    if _timed_out():
        return

    # -- Phase H: Recover pending trigger backlog (batched) --
    _recover_pending_backlog(_start_time, MAX_RUNTIME, BATCH_SIZE, BATCH_SLEEP)


def _recover_pending_backlog(_start_time=None, _max_runtime=300, _batch_size=50, _batch_sleep=0.1):
    """Safe backlog recovery: convert pending PendingTrigger rows into FlowEnrollments.

    Processes triggers in batches with:
    - Staleness check: skip triggers older than per-type threshold (unless refreshed)
    - Deduplication: skip if contact already enrolled or has ActionLedger history
    - Granular status tracking: processed, skipped_stale, skipped_duplicate,
      skipped_no_flow, skipped, failed
    """
    import time as _time
    from database import PendingTrigger, Contact, FlowStep, FlowEnrollment, CustomerActivity, ActionLedger

    if _start_time is None:
        _start_time = _time.time()

    def _timed_out():
        return (_time.time() - _start_time) >= _max_runtime

    now = datetime.now()

    # Per-type staleness thresholds
    STALE_THRESHOLDS = {
        "browse_abandonment": timedelta(days=3),
        "cart_abandonment": timedelta(days=10),
        "churn_risk_high": timedelta(days=30),
        "high_engagement_no_purchase": timedelta(days=7),
    }
    DEFAULT_STALE_THRESHOLD = timedelta(days=7)

    # Freshness window — if contact has recent activity, stale trigger is still relevant
    FRESHNESS_WINDOW = timedelta(hours=24)

    # All trigger types that can map to flows
    CONSUMABLE_TYPES = ["browse_abandonment", "cart_abandonment", "checkout_abandoned", "churn_risk_high", "high_engagement_no_purchase"]

    # Alias map: pending trigger type → flow trigger type (when names differ)
    TRIGGER_ALIASES = {
        "cart_abandonment": "checkout_abandoned",
    }

    # Build map: trigger_type → (flow, first_step) for all active flows
    flow_map = {}
    for flow in Flow.select().where(Flow.is_active == True):
        if flow.trigger_type in CONSUMABLE_TYPES:
            first_step = FlowStep.select().where(FlowStep.flow == flow).order_by(FlowStep.step_order).first()
            if first_step:
                flow_map[flow.trigger_type] = (flow, first_step)

    # Wire aliases: cart_abandonment → checkout_abandoned flow
    for alias, real_type in TRIGGER_ALIASES.items():
        if alias not in flow_map and real_type in flow_map:
            flow_map[alias] = flow_map[real_type]

    # Fetch pending triggers (oldest first)
    pending = list(
        PendingTrigger.select()
        .where(PendingTrigger.status == "pending")
        .order_by(PendingTrigger.detected_at.asc())
        .limit(500)
    )

    if not pending:
        return

    # Counters
    counts = {"processed": 0, "skipped_stale": 0, "skipped_duplicate": 0,
              "skipped_no_flow": 0, "skipped": 0, "failed": 0}

    def _set_status(trigger, status):
        trigger.status = status
        trigger.processed_at = now
        trigger.save()
        counts[status] = counts.get(status, 0) + 1

    for offset in range(0, len(pending), _batch_size):
        if _timed_out():
            app.logger.warning("Backlog recovery: timed out at offset %d" % offset)
            break
        batch = pending[offset:offset + _batch_size]

        for trigger in batch:
            if _timed_out():
                break

            try:
                # 1. No matching active flow?
                flow_entry = flow_map.get(trigger.trigger_type)
                if not flow_entry:
                    _set_status(trigger, "skipped_no_flow")
                    continue
                flow, first_step = flow_entry

                # 2. Staleness check
                threshold = STALE_THRESHOLDS.get(trigger.trigger_type, DEFAULT_STALE_THRESHOLD)
                if trigger.detected_at and trigger.detected_at < (now - threshold):
                    # Check if contact has fresh activity (still relevant)
                    has_fresh = (CustomerActivity.select()
                                 .where(CustomerActivity.email == trigger.email,
                                        CustomerActivity.occurred_at >= (now - FRESHNESS_WINDOW))
                                 .limit(1).count()) > 0
                    if not has_fresh:
                        _set_status(trigger, "skipped_stale")
                        continue

                # 3. Find contact
                try:
                    contact = Contact.get(Contact.email == trigger.email)
                except Contact.DoesNotExist:
                    _set_status(trigger, "skipped")
                    continue

                if not contact.subscribed:
                    _set_status(trigger, "skipped")
                    continue

                # 4. Dedup: already enrolled in this flow?
                existing_enrollment = FlowEnrollment.select().where(
                    FlowEnrollment.flow == flow,
                    FlowEnrollment.contact == contact,
                ).count()
                if existing_enrollment > 0:
                    _set_status(trigger, "skipped_duplicate")
                    continue

                # 5. Dedup: ActionLedger already has a processed entry for this contact+flow?
                ledger_exists = (ActionLedger.select()
                                 .where(ActionLedger.email == trigger.email,
                                        ActionLedger.trigger_type == "flow",
                                        ActionLedger.source_id == flow.id,
                                        ActionLedger.status.in_(["qualified", "queued", "rendered", "sent", "shadowed"]),
                                        ActionLedger.created_at >= trigger.detected_at)
                                 .limit(1).count()) > 0
                if ledger_exists:
                    _set_status(trigger, "skipped_duplicate")
                    continue

                # 6. Enroll
                FlowEnrollment.create(
                    flow=flow,
                    contact=contact,
                    current_step=1,
                    next_send_at=now + timedelta(hours=first_step.delay_hours),
                    status="active",
                )
                _pause_lower_priority_enrollments(contact, flow)
                trigger.enrolled_at = now
                _set_status(trigger, "processed")

            except Exception as e:
                try:
                    trigger.status = "failed"
                    trigger.processed_at = now
                    trigger.save()
                    counts["failed"] += 1
                    app.logger.warning("Backlog recovery failed for trigger #%s: %s" % (trigger.id, e))
                except Exception:
                    pass

        _time.sleep(_batch_sleep)

    total = sum(counts.values())
    if total > 0:
        app.logger.info(
            "Backlog recovery: processed=%d, stale=%d, duplicate=%d, no_flow=%d, skipped=%d, failed=%d (of %d pending)"
            % (counts["processed"], counts["skipped_stale"], counts["skipped_duplicate"],
               counts["skipped_no_flow"], counts["skipped"], counts["failed"], len(pending))
        )


def _detect_behavioural_triggers(_start_time=None, _max_runtime=300, _batch_size=100, _batch_sleep=0.1):
    """
    Scan for browse abandonment, cart abandonment, churn risk, high-intent visitors.
    Queues PendingTrigger records -- does NOT trigger email sends (sandbox safe).

    Uses batched processing to avoid SQLite locking.
    """
    from database import CustomerProfile, CustomerActivity, PendingTrigger, ShopifyOrder
    import json as _json
    import time as _time

    if _start_time is None:
        _start_time = _time.time()

    def _timed_out():
        return (_time.time() - _start_time) >= _max_runtime

    now = datetime.now()

    # -- 1. Browse Abandonment: viewed product 2+ times in last 48hrs, didn't buy --
    cutoff_48h = now - timedelta(hours=48)
    try:
        from peewee import fn
        browse_candidates = list(
            CustomerActivity.select(CustomerActivity.email, fn.COUNT(CustomerActivity.id).alias('view_count'))
            .where(CustomerActivity.event_type == 'viewed_product')
            .where(CustomerActivity.occurred_at >= cutoff_48h)
            .where(CustomerActivity.email != '')
            .group_by(CustomerActivity.email)
            .having(fn.COUNT(CustomerActivity.id) >= 2)
            .tuples()
        )
        app.logger.info("Passive triggers: %d browse abandonment candidates" % len(browse_candidates))

        for offset in range(0, len(browse_candidates), _batch_size):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during browse abandonment at offset %d" % offset)
                break
            batch = browse_candidates[offset:offset + _batch_size]
            for row_tuple in batch:
                try:
                    email = row_tuple[0]
                    existing = PendingTrigger.select().where(
                        PendingTrigger.email == email,
                        PendingTrigger.trigger_type == 'browse_abandonment',
                        PendingTrigger.detected_at >= cutoff_48h
                    ).count()
                    if existing > 0:
                        continue

                    recent_order = ShopifyOrder.select().where(
                        ShopifyOrder.email == email,
                        ShopifyOrder.created_at >= cutoff_48h
                    ).count()
                    if recent_order > 0:
                        continue

                    views = (CustomerActivity.select()
                        .where(CustomerActivity.email == email,
                               CustomerActivity.event_type == 'viewed_product',
                               CustomerActivity.occurred_at >= cutoff_48h)
                        .order_by(CustomerActivity.occurred_at.desc()))
                    products = {}
                    for v in views:
                        try:
                            data = _json.loads(v.event_data or '{}')
                            title = (data.get('product_title') or data.get('product_name')
                                     or data.get('title') or data.get('name') or '').strip()
                            if not title:
                                _url = data.get('url', '')
                                if '/products/' in _url:
                                    title = _url.split('/products/')[-1].split('?')[0].split('#')[0].replace('-', ' ').strip()
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
                    app.logger.warning("Browse abandonment error for record: %s" % _e)
            _time.sleep(_batch_sleep)
    except Exception as _e:
        app.logger.warning("Browse abandonment detection error: %s" % _e)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after browse abandonment, skipping remaining")
        return

    # -- 1b. Cart Page Viewed: viewed_cart in last 48h with no purchase and no abandoned_checkout --
    try:
        cart_view_candidates = list(
            CustomerActivity.select(CustomerActivity.email, fn.COUNT(CustomerActivity.id).alias('view_count'))
            .where(CustomerActivity.event_type == 'viewed_cart')
            .where(CustomerActivity.occurred_at >= cutoff_48h)
            .where(CustomerActivity.email != '')
            .group_by(CustomerActivity.email)
            .having(fn.COUNT(CustomerActivity.id) >= 1)
            .tuples()
        )
        app.logger.info("Passive triggers: %d viewed_cart candidates to check" % len(cart_view_candidates))

        for offset in range(0, len(cart_view_candidates), _batch_size):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during viewed_cart at offset %d" % offset)
                break
            batch = cart_view_candidates[offset:offset + _batch_size]
            for row_tuple in batch:
                try:
                    email = row_tuple[0]

                    # Skip if already has a cart_abandonment trigger in last 7 days
                    existing = PendingTrigger.select().where(
                        PendingTrigger.email == email,
                        PendingTrigger.trigger_type == 'cart_abandonment',
                        PendingTrigger.detected_at >= (now - timedelta(days=7))
                    ).count()
                    if existing > 0:
                        continue

                    # Skip if they have an abandoned_checkout event (section 2 handles those with richer data)
                    has_checkout_event = CustomerActivity.select().where(
                        CustomerActivity.email == email,
                        CustomerActivity.event_type == 'abandoned_checkout',
                        CustomerActivity.occurred_at >= cutoff_48h
                    ).count()
                    if has_checkout_event > 0:
                        continue

                    # Skip if they completed a purchase since viewing cart
                    latest_cart_view = (CustomerActivity.select()
                        .where(CustomerActivity.email == email,
                               CustomerActivity.event_type == 'viewed_cart',
                               CustomerActivity.occurred_at >= cutoff_48h)
                        .order_by(CustomerActivity.occurred_at.desc())
                        .first())
                    if latest_cart_view:
                        completed = CustomerActivity.select().where(
                            CustomerActivity.email == email,
                            CustomerActivity.event_type.in_(['completed_checkout', 'placed_order']),
                            CustomerActivity.occurred_at >= latest_cart_view.occurred_at
                        ).count()
                        if completed > 0:
                            continue

                    PendingTrigger.create(
                        email=email,
                        trigger_type='cart_abandonment',
                        trigger_data=_json.dumps({
                            'source': 'viewed_cart',
                            'checkout_id': '',
                            'products': [],
                            'total': '',
                            'item_count': 0,
                        }),
                        detected_at=now,
                        status='pending'
                    )
                except Exception as _e:
                    app.logger.warning("Viewed cart abandonment error for record: %s" % _e)
            _time.sleep(_batch_sleep)
    except Exception as _e:
        app.logger.warning("Viewed cart abandonment detection error: %s" % _e)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after viewed_cart detection, skipping remaining")
        return

    # -- 2. Cart Abandonment: abandoned_checkout with no completed order (batched) --
    cutoff_4h = now - timedelta(hours=4)
    cutoff_7d = now - timedelta(days=7)
    try:
        cart_events = list(CustomerActivity.select()
            .where(CustomerActivity.event_type == 'abandoned_checkout')
            .where(CustomerActivity.occurred_at >= cutoff_7d)
            .where(CustomerActivity.email != ''))

        app.logger.info("Passive triggers: %d cart abandonment events to check" % len(cart_events))

        for offset in range(0, len(cart_events), _batch_size):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during cart abandonment at offset %d" % offset)
                break
            batch = cart_events[offset:offset + _batch_size]
            for event in batch:
                try:
                    email = event.email
                    existing = PendingTrigger.select().where(
                        PendingTrigger.email == email,
                        PendingTrigger.trigger_type == 'cart_abandonment',
                        PendingTrigger.detected_at >= cutoff_7d
                    ).count()
                    if existing > 0:
                        continue

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

                    # Normalize variant field names for old + new data
                    _cid = (data.get('checkout_id') or data.get('checkout_token')
                            or data.get('token') or data.get('id', ''))
                    _products = data.get('products') or []
                    if not _products:
                        _items = data.get('line_items') or data.get('items') or []
                        if _items and isinstance(_items, list):
                            _products = [(i.get('title') or i.get('name') or i.get('product_title', ''))
                                         for i in _items if isinstance(i, dict)]
                            _products = [p for p in _products if p]
                    _total = (data.get('total') or data.get('total_price')
                              or data.get('subtotal_price') or data.get('amount', ''))
                    _ic = data.get('item_count') or data.get('items_count') or len(_products)

                    PendingTrigger.create(
                        email=email,
                        trigger_type='cart_abandonment',
                        trigger_data=_json.dumps({
                            'checkout_id': str(_cid),
                            'products': _products,
                            'total': _total,
                            'item_count': _ic
                        }),
                        detected_at=now,
                        status='pending'
                    )
                except Exception as _e:
                    app.logger.warning("Cart abandonment error for record: %s" % _e)
            _time.sleep(_batch_sleep)
    except Exception as _e:
        app.logger.warning("Cart abandonment detection error: %s" % _e)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after cart abandonment, skipping remaining")
        return

    # -- 3. Churn Risk High: churn_risk >= 1.5 for customers with orders (batched) --
    try:
        churn_profiles = list(CustomerProfile.select()
            .where(CustomerProfile.churn_risk >= 1.5)
            .where(CustomerProfile.total_orders > 0))

        app.logger.info("Passive triggers: %d churn risk profiles to check" % len(churn_profiles))

        for offset in range(0, len(churn_profiles), _batch_size):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during churn risk at offset %d" % offset)
                break
            batch = churn_profiles[offset:offset + _batch_size]
            for profile in batch:
                try:
                    email = profile.email
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
                    app.logger.warning("Churn risk error for record: %s" % _e)
            _time.sleep(_batch_sleep)
    except Exception as _e:
        app.logger.warning("Churn risk detection error: %s" % _e)

    if _timed_out():
        app.logger.warning("Passive triggers: timed out after churn risk, skipping remaining")
        return

    # -- 4. High Intent No Purchase: engagement > 50, 0 orders (batched) --
    try:
        intent_profiles = list(CustomerProfile.select()
            .where(CustomerProfile.website_engagement_score >= 50)
            .where(CustomerProfile.total_orders == 0))

        app.logger.info("Passive triggers: %d high intent profiles to check" % len(intent_profiles))

        for offset in range(0, len(intent_profiles), _batch_size):
            if _timed_out():
                app.logger.warning("Passive triggers: timed out during high intent at offset %d" % offset)
                break
            batch = intent_profiles[offset:offset + _batch_size]
            for profile in batch:
                try:
                    email = profile.email
                    cutoff_7d_intent = now - timedelta(days=7)
                    existing = PendingTrigger.select().where(
                        PendingTrigger.email == email,
                        PendingTrigger.trigger_type == 'high_engagement_no_purchase',
                        PendingTrigger.detected_at >= cutoff_7d_intent
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
                    app.logger.warning("High intent error for record: %s" % _e)
            _time.sleep(_batch_sleep)
    except Exception as _e:
        app.logger.warning("High intent detection error: %s" % _e)

    # Log summary
    try:
        browse_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'browse_abandonment', PendingTrigger.status == 'pending').count()
        cart_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'cart_abandonment', PendingTrigger.status == 'pending').count()
        churn_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'churn_risk_high', PendingTrigger.status == 'pending').count()
        intent_count = PendingTrigger.select().where(PendingTrigger.trigger_type == 'high_engagement_no_purchase', PendingTrigger.status == 'pending').count()
        elapsed = _time.time() - _start_time
        app.logger.info("Trigger detection complete in %.1fs: browse=%d, cart=%d, churn=%d, intent=%d" % (elapsed, browse_count, cart_count, churn_count, intent_count))
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
            priority=int(request.form.get("priority", "5")),
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
    paused   = FlowEnrollment.select().where(FlowEnrollment.flow == flow,
                                              FlowEnrollment.status == "paused").count()

    flow_emails = FlowEmail.select().join(FlowEnrollment).where(FlowEnrollment.flow == flow)
    sent   = flow_emails.where(FlowEmail.status == "sent").count()
    opened = flow_emails.where(FlowEmail.opened == True).count()
    open_rate = round(opened / sent * 100, 1) if sent > 0 else 0

    return render_template("flow_detail.html",
        flow=flow,
        steps=steps,
        templates=templates,
        enrollments=enrollments,
        total=total, active=active, completed=completed, cancelled=cancelled, paused=paused,
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


@app.route("/flows/<int:flow_id>/priority", methods=["POST"])
def flow_update_priority(flow_id):
    flow = Flow.get_by_id(flow_id)
    try:
        new_priority = int(request.form.get("priority", flow.priority))
        new_priority = max(1, min(10, new_priority))
    except (ValueError, TypeError):
        new_priority = flow.priority
    flow.priority = new_priority
    flow.save()
    flash(f"Priority updated to {new_priority}.", "success")
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
        # Update contact-level last_open_at
        try:
            Contact.update(last_open_at=datetime.now()).where(Contact.id == fe.contact_id).execute()
        except Exception as e:
            app.logger.warning("[SilentFix] Update last_open_at for flow contact %s: %s" % (fe.contact_id, e))
        # Real-time pipeline: refresh after flow email open
        try:
            from cascade import cascade_contact
            cascade_contact(fe.contact_id, trigger="flow_open_legacy")
        except Exception:
            pass
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
    from database import get_system_config
    config = {
        "aws_region":          os.getenv("AWS_REGION", ""),
        "aws_access_key":      ("*" * 16) if os.getenv("AWS_ACCESS_KEY_ID") else "",
        "shopify_store":       os.getenv("SHOPIFY_STORE_URL", ""),
        "shopify_token_set":   bool(os.getenv("SHOPIFY_ACCESS_TOKEN")),
        "from_email":          os.getenv("DEFAULT_FROM_EMAIL", ""),
    }
    system_config = get_system_config()
    return render_template("settings.html", config=config, system_config=system_config)

@app.route("/settings/delivery-mode", methods=["POST"])
def settings_delivery_mode():
    from database import get_system_config
    mode = request.form.get("delivery_mode", "shadow")
    if mode in ("live", "shadow", "sandbox"):
        cfg = get_system_config()
        cfg.delivery_mode = mode
        cfg.updated_at = datetime.now()
        cfg.save()
        flash("Delivery mode set to %s" % mode, "success")
    return redirect(url_for("settings"))

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
#  AUDIT DASHBOARD
# ─────────────────────────────────

@app.route("/audit")
def audit_dashboard():
    from action_ledger import get_today_stats, get_top_reasons, get_recent_entries
    stats = get_today_stats()
    suppression_reasons = get_top_reasons("suppressed")
    failure_reasons = get_top_reasons("failed")
    recent = get_recent_entries(page=1, per_page=50)
    return render_template("audit.html",
                           stats=stats,
                           suppression_reasons=suppression_reasons,
                           failure_reasons=failure_reasons,
                           recent=recent)

@app.route("/api/audit/stats")
def api_audit_stats():
    from action_ledger import get_today_stats
    return jsonify(get_today_stats())

@app.route("/api/audit/details")
def api_audit_details():
    from action_ledger import get_recent_entries
    page = request.args.get("page", 1, type=int)
    contact_email = request.args.get("contact", None)
    trigger_type = request.args.get("trigger_type", None)
    status = request.args.get("status", None)
    result = get_recent_entries(page=page, per_page=50,
                                contact_email=contact_email,
                                trigger_type=trigger_type,
                                status=status)
    return jsonify(result)


# ─────────────────────────────────
#  TELEMETRY DASHBOARD
# ─────────────────────────────────

@app.route("/telemetry")
def telemetry_dashboard():
    """Template & AI telemetry dashboard."""
    return render_template("telemetry.html")

@app.route("/api/telemetry/data")
def api_telemetry_data():
    """Return aggregated telemetry data."""
    from peewee import fn
    from database import AIRenderLog, ActionLedger

    # AI Usage stats
    total = AIRenderLog.select().count()
    fallback = AIRenderLog.select().where(AIRenderLog.fallback_used == True).count()
    avg_ms = AIRenderLog.select(fn.AVG(AIRenderLog.render_ms)).scalar() or 0

    ai_usage = {
        "total": total,
        "success": total - fallback,
        "fallback": fallback,
        "avg_render_ms": int(avg_ms),
        "fallback_rate": round(fallback / total * 100, 1) if total > 0 else 0,
    }

    # Family performance (from ActionLedger: emails sent per template family)
    family_stats = []
    try:
        family_rows = (ActionLedger
            .select(
                EmailTemplate.template_family,
                fn.COUNT(ActionLedger.id).alias("sent"),
            )
            .join(EmailTemplate, on=(ActionLedger.template_id == EmailTemplate.id))
            .where(
                ActionLedger.status == "sent",
                EmailTemplate.template_family != "",
            )
            .group_by(EmailTemplate.template_family))

        for row in family_rows:
            family_stats.append({
                "family": row.emailtemplate.template_family,
                "sent": row.sent,
            })
    except Exception:
        pass

    # AI field breakdown (top fields by generation count)
    field_stats = []
    try:
        field_rows = (AIRenderLog
            .select(
                AIRenderLog.field_name,
                fn.COUNT(AIRenderLog.id).alias("count"),
                fn.AVG(AIRenderLog.render_ms).alias("avg_ms"),
                fn.SUM(AIRenderLog.fallback_used.cast("int")).alias("fallback_count"),
            )
            .group_by(AIRenderLog.field_name)
            .order_by(fn.COUNT(AIRenderLog.id).desc())
            .limit(10))

        for row in field_rows:
            field_stats.append({
                "field_name": row.field_name,
                "count": row.count,
                "avg_ms": int(row.avg_ms or 0),
                "fallback_count": int(row.fallback_count or 0),
            })
    except Exception:
        pass

    return jsonify({
        "ai_usage": ai_usage,
        "family_stats": family_stats,
        "field_stats": field_stats,
    })


# ─────────────────────────────────
#  TRIGGER BACKLOG API
# ─────────────────────────────────
@app.route("/api/triggers/backlog")
def api_trigger_backlog():
    """Return PendingTrigger backlog counts grouped by status and trigger_type."""
    from peewee import fn
    from database import PendingTrigger

    rows = list(PendingTrigger
                .select(PendingTrigger.status, PendingTrigger.trigger_type,
                        fn.COUNT(PendingTrigger.id).alias("cnt"))
                .group_by(PendingTrigger.status, PendingTrigger.trigger_type)
                .dicts())

    summary = {"pending": 0, "processed": 0, "skipped_stale": 0,
               "skipped_duplicate": 0, "skipped_no_flow": 0, "skipped": 0, "failed": 0}
    by_type = {}
    for r in rows:
        s = r["status"]
        summary[s] = summary.get(s, 0) + r["cnt"]
        by_type.setdefault(r["trigger_type"], {})[s] = r["cnt"]

    return jsonify({"summary": summary, "by_type": by_type})


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

    q         = request.args.get("q", "").strip()
    tier      = request.args.get("tier", "")
    category  = request.args.get("category", "")
    lifecycle = request.args.get("lifecycle", "")
    intent    = request.args.get("intent", "")
    action    = request.args.get("action", "")
    sort      = request.args.get("sort", "spent_desc")
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
    if lifecycle:
        query = query.where(CustomerProfile.lifecycle_stage == lifecycle)
    if intent == "high":
        query = query.where(CustomerProfile.intent_score >= 70)
    elif intent == "medium":
        query = query.where(CustomerProfile.intent_score >= 30, CustomerProfile.intent_score < 70)
    elif intent == "low":
        query = query.where(CustomerProfile.intent_score < 30)

    # Sorting
    if sort == "orders_desc":
        query = query.order_by(CustomerProfile.total_orders.desc())
    elif sort == "recent":
        query = query.order_by(CustomerProfile.days_since_last_order.asc())
    elif sort == "lapsed":
        query = query.order_by(CustomerProfile.days_since_last_order.desc())
    elif sort == "name":
        query = query.order_by(Contact.first_name.asc())
    elif sort == "intent_desc":
        query = query.order_by(CustomerProfile.intent_score.desc())
    elif sort == "churn_desc":
        query = query.order_by(CustomerProfile.churn_risk_score.desc())
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
            "lifecycle_stage": getattr(p, "lifecycle_stage", "unknown"),
            "intent_score":    getattr(p, "intent_score", 0),
            "next_action":     "",
            "action_score":    0,
        })

    # Phase 2B: Bulk-load decisions for listed profiles
    try:
        from database import MessageDecision as _MD
        _cids = [r["contact_id"] for r in profiles]
        _dmap = {}
        for _md in _MD.select().where(_MD.contact.in_(_cids)):
            _dmap[_md.contact_id] = {"action_type": _md.action_type, "action_score": _md.action_score}
        for _r in profiles:
            _d = _dmap.get(_r["contact_id"], {})
            _r["next_action"] = _d.get("action_type", "")
            _r["action_score"] = _d.get("action_score", 0)
    except Exception:
        pass

    # Phase 2B: Filter by action type
    if action:
        profiles = [r for r in profiles if r.get("next_action") == action]

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
    for k, v in [("q", q), ("tier", tier), ("category", category), ("lifecycle", lifecycle), ("intent", intent), ("action", action), ("sort", sort)]:
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
        lifecycle=lifecycle, intent=intent, action=action,
        query_string=query_string,
    )


@app.route("/profiles/<int:contact_id>")
def profile_detail(contact_id):
    from database import Contact, CustomerProfile, ShopifyOrder, ShopifyOrderItem, CampaignEmail, FlowEmail, FlowStep, Campaign
    from peewee import JOIN
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

    # Email activity (last 20) — merge CampaignEmail + FlowEmail
    campaign_emails = list(
        CampaignEmail.select(
            CampaignEmail.id,
            CampaignEmail.status,
            CampaignEmail.opened,
            CampaignEmail.opened_at,
            CampaignEmail.created_at,
            Campaign.name.alias("source_name"),
        )
        .join(Campaign, on=(CampaignEmail.campaign == Campaign.id))
        .where(CampaignEmail.contact_id == contact_id)
        .order_by(CampaignEmail.created_at.desc())
        .limit(20)
        .dicts()
    )
    for ce in campaign_emails:
        ce["email_type"] = "campaign"
        ce["sent_at"] = ce["created_at"]

    flow_emails = list(
        FlowEmail.select(
            FlowEmail.id,
            FlowEmail.status,
            FlowEmail.opened,
            FlowEmail.opened_at,
            FlowEmail.sent_at,
            EmailTemplate.name.alias("source_name"),
        )
        .join(FlowStep, on=(FlowEmail.step == FlowStep.id))
        .join(EmailTemplate, on=(FlowStep.template == EmailTemplate.id))
        .where(FlowEmail.contact == contact_id)
        .order_by(FlowEmail.sent_at.desc())
        .limit(20)
        .dicts()
    )
    for fe in flow_emails:
        fe["email_type"] = "flow"

    # Auto-pilot emails
    auto_emails = list(
        AutoEmail.select(
            AutoEmail.id,
            AutoEmail.status,
            AutoEmail.opened,
            AutoEmail.opened_at,
            AutoEmail.sent_at,
            EmailTemplate.name.alias("source_name"),
        )
        .join(EmailTemplate, on=(AutoEmail.template == EmailTemplate.id), join_type=JOIN.LEFT_OUTER)
        .where(AutoEmail.contact == contact_id)
        .order_by(AutoEmail.sent_at.desc())
        .limit(20)
        .dicts()
    )
    for ae in auto_emails:
        ae["email_type"] = "auto"
        ae["source_name"] = "Auto-Pilot" + (f" \u2014 {ae['source_name']}" if ae.get("source_name") else "")

    # Merge and sort by sent_at descending, take top 20
    _all_emails = campaign_emails + flow_emails + auto_emails
    _all_emails.sort(key=lambda x: x.get("sent_at") or "", reverse=True)
    email_activity = _all_emails[:20]

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
            "Shop now: https://ldas.ca",
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

    # Phase 2A: Intelligence data
    _intelligence = {}
    if profile and profile.last_intelligence_at:
        import json as _ji
        _intelligence = {
            "lifecycle_stage": profile.lifecycle_stage,
            "customer_type": profile.customer_type,
            "intent_score": profile.intent_score,
            "reorder_likelihood": profile.reorder_likelihood,
            "churn_risk_score": profile.churn_risk_score,
            "category_affinity": _ji.loads(profile.category_affinity_json or "{}"),
            "next_purchase_category": profile.next_purchase_category,
            "preferred_send_hour": profile.preferred_send_hour,
            "preferred_send_dow": profile.preferred_send_dow,
            "channel_preference": profile.channel_preference,
            "intelligence_summary": profile.intelligence_summary,
            "discount_sensitivity": profile.discount_sensitivity,
            "confidence": {
                "lifecycle": profile.confidence_lifecycle,
                "intent": profile.confidence_intent,
                "reorder": profile.confidence_reorder,
                "category": profile.confidence_category,
                "send_window": profile.confidence_send_window,
                "channel": profile.confidence_channel,
                "discount": profile.confidence_discount,
                "churn": profile.confidence_churn,
            },
            "last_computed": profile.last_intelligence_at.strftime("%Y-%m-%d %H:%M") if profile.last_intelligence_at else "",
        }

    # Phase 2B: Next-Best-Action decision
    _decision = {}
    try:
        from database import MessageDecision, MessageDecisionHistory
        import json as _jd2
        _md = MessageDecision.get_or_none(MessageDecision.contact == contact_id)
        if _md:
            _decision = {
                "action_type": _md.action_type,
                "action_score": _md.action_score,
                "action_reason": _md.action_reason,
                "ranked_actions": _jd2.loads(_md.ranked_actions_json or "[]"),
                "rejections": _jd2.loads(_md.rejections_json or "[]"),
                "decided_at": _md.decided_at.strftime("%Y-%m-%d %H:%M") if _md.decided_at else "",
            }
        _decision_history = []
        for _dh in (MessageDecisionHistory.select()
                    .where(MessageDecisionHistory.contact == contact_id)
                    .order_by(MessageDecisionHistory.decided_at.desc())
                    .limit(14)):
            _decision_history.append({
                "date": _dh.decision_date,
                "action_type": _dh.action_type,
                "action_score": _dh.action_score,
                "was_executed": _dh.was_executed,
            })
        _decision["history"] = _decision_history
    except Exception:
        pass

    # Discount codes for this customer
    _discount_codes = []
    try:
        from database import GeneratedDiscount
        _discount_codes = list(
            GeneratedDiscount.select()
            .where(GeneratedDiscount.email == contact.email.lower())
            .order_by(GeneratedDiscount.created_at.desc())
            .limit(10)
        )
    except Exception:
        pass

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
        intelligence=_intelligence,
        decision=_decision,
        discount_codes=_discount_codes,
        now=datetime.now(),
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
    # Wrap quick email in shell
    from email_shell import wrap_email
    _unsub = f"https://mailenginehub.com/contacts/unsubscribe/{contact.email}"
    html_body = wrap_email(html_body, unsubscribe_url=_unsub)
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


# ═══════════════════════════════════════════════════════════════
# Phase 2C: Campaign Planner Routes
# ═══════════════════════════════════════════════════════════════

@app.route("/campaign-planner")
def campaign_planner_page():
    from database import SuggestedCampaign, OpportunityScanLog
    import json as _j2c
    today = datetime.now().strftime("%Y-%m-%d")
    suggestions = list(
        SuggestedCampaign.select()
        .where(SuggestedCampaign.scan_date == today)
        .order_by(SuggestedCampaign.quality_score.desc())
    )
    total_opps = len(suggestions)
    total_eligible = sum(s.segment_size for s in suggestions)
    total_predicted_revenue = sum(s.predicted_revenue for s in suggestions)
    total_predicted_profit = sum(s.net_profit for s in suggestions)
    avg_quality = round(sum(s.quality_score for s in suggestions) / max(1, total_opps), 1)
    scan_history = list(
        OpportunityScanLog.select()
        .order_by(OpportunityScanLog.created_at.desc())
        .limit(14)
    )
    for s in suggestions:
        s._warnings = _j2c.loads(s.preflight_warnings_json or "[]")
        s._products = _j2c.loads(s.top_products_json or "[]")
    return render_template("campaign_planner.html",
        suggestions=suggestions,
        total_opps=total_opps,
        total_eligible=total_eligible,
        total_predicted_revenue=total_predicted_revenue,
        total_predicted_profit=total_predicted_profit,
        avg_quality=avg_quality,
        scan_history=scan_history,
        today=today,
    )


@app.route("/api/campaign-planner/scan", methods=["POST"])
def campaign_planner_scan():
    import threading
    def _run():
        import sys as _sp; _sp.path.insert(0, APP_DIR)
        from campaign_planner import scan_opportunities
        scan_opportunities()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Opportunity scan started"})


@app.route("/api/campaign-planner/<int:sc_id>/accept", methods=["POST"])
def campaign_planner_accept(sc_id):
    try:
        import sys as _sa; _sa.path.insert(0, APP_DIR)
        from campaign_planner import accept_opportunity
        campaign_id = accept_opportunity(sc_id)
        return jsonify({"ok": True, "campaign_id": campaign_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/campaign-planner/<int:sc_id>/dismiss", methods=["POST"])
def campaign_planner_dismiss(sc_id):
    try:
        from database import SuggestedCampaign
        sc = SuggestedCampaign.get_by_id(sc_id)
        sc.status = "dismissed"
        sc.save()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/campaign-planner/<int:sc_id>/brief")
def campaign_planner_brief(sc_id):
    try:
        from database import SuggestedCampaign
        sc = SuggestedCampaign.get_by_id(sc_id)
        return jsonify({"ok": True, "brief": sc.brief_text, "campaign_name": sc.campaign_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404


# ═══════════════════════════════════════════════════════════════
# Phase 2D: Profit Dashboard Route
# ═══════════════════════════════════════════════════════════════

@app.route("/profits")
def profit_dashboard():
    from database import ProductCommercial, SuggestedCampaign, CustomerProfile, Contact
    from peewee import fn
    today = datetime.now().strftime("%Y-%m-%d")
    total_products = ProductCommercial.select().count()
    total_revenue_30d = (ProductCommercial
        .select(fn.SUM(ProductCommercial.revenue_30d))
        .scalar()) or 0
    total_profit_30d = (ProductCommercial
        .select(fn.SUM(ProductCommercial.profit_30d))
        .where(ProductCommercial.profit_30d.is_null(False))
        .scalar()) or 0
    avg_margin = (ProductCommercial
        .select(fn.AVG(ProductCommercial.margin_pct))
        .where(ProductCommercial.margin_pct.is_null(False))
        .scalar()) or 0
    products = list(
        ProductCommercial.select()
        .order_by(ProductCommercial.profitability_score.desc())
        .limit(100)
    )
    do_not_promote = list(
        ProductCommercial.select()
        .where(ProductCommercial.promotion_eligible == False)
        .order_by(ProductCommercial.product_title)
    )
    no_discount_customers = []
    try:
        _ndc_profiles = list(
            CustomerProfile.select()
            .where(
                (CustomerProfile.price_tier == "premium") &
                (CustomerProfile.discount_sensitivity < 0.2) &
                (CustomerProfile.total_orders >= 1)
            )
            .order_by(CustomerProfile.total_spent.desc())
            .limit(50)
        )
        for cp in _ndc_profiles:
            try:
                c = Contact.get_by_id(cp.contact_id)
                no_discount_customers.append({
                    "name": f"{c.first_name or ''} {c.last_name or ''}".strip() or c.email,
                    "email": c.email,
                    "price_tier": cp.price_tier,
                    "total_spent": cp.total_spent,
                    "total_orders": cp.total_orders,
                    "discount_sensitivity": cp.discount_sensitivity,
                    "reason": "Buys full price -- no discount needed",
                })
            except Exception:
                pass
    except Exception:
        pass
    campaign_forecasts = list(
        SuggestedCampaign.select()
        .where(SuggestedCampaign.scan_date == today)
        .where(SuggestedCampaign.status != "dismissed")
        .order_by(SuggestedCampaign.net_profit.desc())
    )
    return render_template("profit_dashboard.html",
        total_products=total_products,
        total_revenue_30d=total_revenue_30d,
        total_profit_30d=total_profit_30d,
        avg_margin=avg_margin,
        products=products,
        do_not_promote=do_not_promote,
        no_discount_customers=no_discount_customers,
        campaign_forecasts=campaign_forecasts,
    )


@app.route("/api/profiles/<int:contact_id>/decide", methods=["POST"])
def recompute_decision(contact_id):
    """Recompute next-best-action for a single contact on-demand."""
    try:
        from next_best_message import decide_next_action
        result = decide_next_action(contact_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profiles/<int:contact_id>/intelligence", methods=["POST"])
def recompute_intelligence(contact_id):
    """Recompute intelligence for a single contact on-demand."""
    try:
        from customer_intelligence import compute_intelligence
        result = compute_intelligence(contact_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    from database import ContactScore, AIMarketingPlan, AIDecisionLog, CustomerProfile, PendingTrigger, AIGeneratedEmail, WarmupConfig
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

    # Warmup / SES status for banner
    warmup_config = WarmupConfig.get_or_none()

    return render_template("ai_engine.html",
        segments=segments, plan=plan, recent_plans=recent_plans,
        recent_decisions=recent_decisions, total_scored=total_scored,
        churn_dist=churn_dist, revenue_at_risk=revenue_at_risk,
        revenue_on_track=revenue_on_track,
        trigger_counts=trigger_counts, total_triggers=total_triggers,
        recent_ai_emails=recent_ai_emails, total_ai_emails=total_ai_emails,
        top_recs=top_recs, warmup_config=warmup_config)



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
    import threading
    def _run():
        try:
            from ai_engine import score_all_contacts
            app.logger.info("Manual contact scoring started...")
            count = score_all_contacts()
            app.logger.info(f"Manual scoring complete: {count} contacts scored")
        except Exception as e:
            app.logger.error(f"Manual scoring failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Contact scoring started — refresh in ~30 seconds"})


# ── Self-Learning Dashboard ─────────────────────────────────────
@app.route("/learning")
def learning_dashboard():
    from database import (OutcomeLog, ContactScore, TemplatePerformance,
                          ActionPerformance, TemplateSegmentPerformance,
                          EmailTemplate, Contact, LearningConfig)
    from learning_config import get_learning_phase, get_learning_enabled

    now = datetime.now()

    # Phase info
    phase = get_learning_phase()
    enabled = get_learning_enabled()
    start_str = LearningConfig.get_val("learning_start_date", "")
    days_elapsed = 0
    if start_str:
        try:
            start = datetime.fromisoformat(start_str)
            days_elapsed = (now - start).days
        except ValueError:
            pass

    # Outcome stats
    total_tracked = OutcomeLog.select().count()
    total_opens = OutcomeLog.select().where(OutcomeLog.opened == True).count()
    total_clicks = OutcomeLog.select().where(OutcomeLog.clicked == True).count()
    total_purchases = OutcomeLog.select().where(OutcomeLog.purchased == True).count()
    total_unsubs = OutcomeLog.select().where(OutcomeLog.unsubscribed == True).count()
    total_revenue = 0.0
    for row in OutcomeLog.select(OutcomeLog.revenue):
        total_revenue += (row.revenue or 0)

    open_rate = round(total_opens / total_tracked * 100, 1) if total_tracked else 0
    click_rate = round(total_clicks / total_tracked * 100, 1) if total_tracked else 0
    conversion_rate = round(total_purchases / total_tracked * 100, 1) if total_tracked else 0
    unsub_rate = round(total_unsubs / total_tracked * 100, 1) if total_tracked else 0

    # Sunset stats
    sunset_queue = ContactScore.select().where(
        ContactScore.sunset_score >= 85,
        ContactScore.sunset_executed == False
    ).count()
    sunset_executed = ContactScore.select().where(
        ContactScore.sunset_executed == True
    ).count()

    # Template performance (top 10 by revenue_per_send)
    template_perf = []
    for tp in (TemplatePerformance.select(TemplatePerformance, EmailTemplate)
               .join(EmailTemplate)
               .order_by(TemplatePerformance.revenue_per_send.desc())
               .limit(10)):
        template_perf.append({
            "name": tp.template.name,
            "sends": tp.sends,
            "open_rate": round(tp.open_rate * 100, 1),
            "click_rate": round(tp.click_rate * 100, 1),
            "conversion_rate": round((tp.conversion_rate or 0) * 100, 1),
            "revenue_per_send": round(tp.revenue_per_send or 0, 4),
            "revenue_total": round(tp.revenue_total or 0, 2),
            "confidence": "high" if (tp.sample_size or 0) >= 50 else "learning",
        })

    # Action effectiveness
    action_perf = []
    for ap in (ActionPerformance.select()
               .order_by(ActionPerformance.conversion_rate.desc())
               .limit(20)):
        # Compute multiplier (same logic as strategy_optimizer)
        baseline = 0.02
        cr = ap.conversion_rate or 0
        if ap.sample_size < 20:
            mult = 1.0
        elif cr > baseline * 2:
            mult = 1.3
        elif cr > baseline:
            mult = 1.1
        elif cr < baseline * 0.25:
            mult = 0.7
        elif cr < baseline * 0.5:
            mult = 0.9
        else:
            mult = 1.0

        action_perf.append({
            "action_type": ap.action_type,
            "segment": ap.segment,
            "sample_size": ap.sample_size,
            "open_rate": round(ap.open_rate * 100, 1),
            "conversion_rate": round(cr * 100, 2),
            "revenue_per_send": round(ap.revenue_per_send or 0, 4),
            "multiplier": mult,
        })

    # Weekly trend (last 8 weeks)
    from datetime import timedelta
    weekly_trend = []
    for i in range(7, -1, -1):
        week_start = now - timedelta(weeks=i+1)
        week_end = now - timedelta(weeks=i)
        outcomes = list(OutcomeLog.select().where(
            OutcomeLog.sent_at >= week_start,
            OutcomeLog.sent_at < week_end,
        ))
        n = len(outcomes)
        if n > 0:
            w_opens = sum(1 for o in outcomes if o.opened)
            w_purchases = sum(1 for o in outcomes if o.purchased)
            w_revenue = sum(o.revenue or 0 for o in outcomes)
            weekly_trend.append({
                "label": week_end.strftime("%b %d"),
                "emails": n,
                "open_rate": round(w_opens / n * 100, 1),
                "conversion_rate": round(w_purchases / n * 100, 2),
                "revenue": round(w_revenue, 2),
            })
        else:
            weekly_trend.append({
                "label": week_end.strftime("%b %d"),
                "emails": 0, "open_rate": 0, "conversion_rate": 0, "revenue": 0,
            })

    # ── NEW: Audience Health — RFM Segment Distribution ──
    segment_dist = {}
    for seg in ['champion', 'loyal', 'potential', 'at_risk', 'lapsed', 'new']:
        segment_dist[seg] = ContactScore.select().where(ContactScore.rfm_segment == seg).count()
    total_scored = sum(segment_dist.values()) or 1

    # ── NEW: Message Decision Breakdown ──
    from database import MessageDecision
    from peewee import fn
    decision_dist = {}
    for row in (MessageDecision.select(MessageDecision.action_type, fn.COUNT(MessageDecision.id).alias('cnt'))
                .group_by(MessageDecision.action_type)
                .order_by(fn.COUNT(MessageDecision.id).desc())
                .limit(10)):
        decision_dist[row.action_type] = row.cnt

    # ── NEW: Frequency Optimization Stats ──
    avg_gap = ContactScore.select(fn.AVG(ContactScore.optimal_gap_hours)).where(
        ContactScore.optimal_gap_hours.is_null(False),
        ContactScore.optimal_gap_hours > 0
    ).scalar() or 0
    min_gap = ContactScore.select(fn.MIN(ContactScore.optimal_gap_hours)).where(
        ContactScore.optimal_gap_hours.is_null(False),
        ContactScore.optimal_gap_hours > 0
    ).scalar() or 16
    max_gap = ContactScore.select(fn.MAX(ContactScore.optimal_gap_hours)).where(
        ContactScore.optimal_gap_hours.is_null(False),
        ContactScore.optimal_gap_hours > 0
    ).scalar() or 336
    contacts_with_gap = ContactScore.select().where(
        ContactScore.optimal_gap_hours.is_null(False),
        ContactScore.optimal_gap_hours > 0
    ).count()

    # ── NEW: Lifecycle Stage Distribution ──
    from database import CustomerProfile
    lifecycle_dist = {}
    for row in (CustomerProfile.select(CustomerProfile.lifecycle_stage, fn.COUNT(CustomerProfile.id).alias('cnt'))
                .where(CustomerProfile.lifecycle_stage.is_null(False), CustomerProfile.lifecycle_stage != '')
                .group_by(CustomerProfile.lifecycle_stage)
                .order_by(fn.COUNT(CustomerProfile.id).desc())):
        lifecycle_dist[row.lifecycle_stage] = row.cnt

    # ── NEW: Guardrail / Regression Status ──
    from database import ActionLedger
    regression_events = list(ActionLedger.select().where(
        ActionLedger.reason_code == 'RC_LEARNING_REGRESSION'
    ).order_by(ActionLedger.created_at.desc()).limit(3))
    has_regression = len(regression_events) > 0

    return render_template("learning_dashboard.html",
        phase=phase,
        enabled=enabled,
        days_elapsed=days_elapsed,
        total_tracked=total_tracked,
        total_revenue=total_revenue,
        open_rate=open_rate,
        click_rate=click_rate,
        conversion_rate=conversion_rate,
        unsub_rate=unsub_rate,
        sunset_queue=sunset_queue,
        sunset_executed=sunset_executed,
        template_perf=template_perf,
        action_perf=action_perf,
        weekly_trend=weekly_trend,
        total_purchases=total_purchases,
        segment_dist=segment_dist,
        total_scored=total_scored,
        decision_dist=decision_dist,
        avg_gap=round(avg_gap, 1),
        min_gap=round(min_gap, 1),
        max_gap=round(max_gap, 1),
        contacts_with_gap=contacts_with_gap,
        lifecycle_dist=lifecycle_dist,
        has_regression=has_regression,
        regression_events=regression_events,
    )


@app.route("/learning/toggle", methods=["POST"])
def learning_toggle():
    from learning_config import get_learning_enabled, set_learning_enabled
    current = get_learning_enabled()
    set_learning_enabled(not current)
    new_state = "enabled" if not current else "disabled"
    flash(f"Self-learning {new_state}", "success")
    return redirect(url_for("learning_dashboard"))


@app.route("/api/learning/stats")
def api_learning_stats():
    from database import OutcomeLog, ContactScore, LearningConfig
    from learning_config import get_learning_phase, get_learning_enabled
    import json as _json

    now = datetime.now()
    phase = get_learning_phase()
    enabled = get_learning_enabled()
    start_str = LearningConfig.get_val("learning_start_date", "")
    days_elapsed = 0
    if start_str:
        try:
            days_elapsed = (now - datetime.fromisoformat(start_str)).days
        except ValueError:
            pass

    total_tracked = OutcomeLog.select().count()
    total_opens = OutcomeLog.select().where(OutcomeLog.opened == True).count()
    total_purchases = OutcomeLog.select().where(OutcomeLog.purchased == True).count()
    total_revenue = sum(r.revenue or 0 for r in OutcomeLog.select(OutcomeLog.revenue))
    sunset_queue = ContactScore.select().where(
        ContactScore.sunset_score >= 85, ContactScore.sunset_executed == False).count()

    return jsonify({
        "phase": phase,
        "enabled": enabled,
        "days_elapsed": days_elapsed,
        "total_tracked": total_tracked,
        "open_rate": round(total_opens / total_tracked * 100, 1) if total_tracked else 0,
        "conversion_rate": round(total_purchases / total_tracked * 100, 1) if total_tracked else 0,
        "revenue": round(total_revenue, 2),
        "sunset_queue": sunset_queue,
    })


# ── Auto-Pilot Dashboard ─────────────────────────────────────
@app.route("/auto-pilot")
def auto_pilot_dashboard():
    from database import DeliveryQueue, Contact, EmailTemplate, init_db
    init_db()

    now = datetime.now()

    # Build template name lookup
    _tpl_names = {}
    for _t in EmailTemplate.select(EmailTemplate.id, EmailTemplate.name):
        _tpl_names[_t.id] = _t.name

    # Pending (scheduled for future)
    pending = list(
        DeliveryQueue.select()
        .where(DeliveryQueue.email_type == "auto", DeliveryQueue.status == "queued")
        .order_by(DeliveryQueue.scheduled_at.asc())
        .limit(50)
    )
    for _p in pending:
        _p.template_name = _tpl_names.get(_p.template_id, "Unknown")

    # Recently sent (last 48h)
    from datetime import timedelta
    two_days_ago = now - timedelta(hours=48)
    sent = list(
        DeliveryQueue.select()
        .where(DeliveryQueue.email_type == "auto", DeliveryQueue.status == "sent",
               DeliveryQueue.sent_at >= two_days_ago)
        .order_by(DeliveryQueue.sent_at.desc())
        .limit(50)
    )
    for _s in sent:
        _s.template_name = _tpl_names.get(_s.template_id, "Unknown")

    # Stats
    total_scheduled_today = DeliveryQueue.select().where(
        DeliveryQueue.email_type == "auto",
        DeliveryQueue.created_at >= now.strftime("%Y-%m-%d")
    ).count()
    total_sent_today = DeliveryQueue.select().where(
        DeliveryQueue.email_type == "auto",
        DeliveryQueue.status == "sent",
        DeliveryQueue.sent_at >= now.strftime("%Y-%m-%d")
    ).count()
    total_pending = DeliveryQueue.select().where(
        DeliveryQueue.email_type == "auto",
        DeliveryQueue.status == "queued"
    ).count()

    return render_template("auto_pilot.html",
        pending=pending,
        sent=sent,
        total_scheduled_today=total_scheduled_today,
        total_sent_today=total_sent_today,
        total_pending=total_pending,
        now=now,
    )


@app.route("/api/auto-pilot/preview/<int:item_id>")
def auto_pilot_preview(item_id):
    from database import DeliveryQueue, EmailTemplate, init_db
    init_db()
    try:
        item = DeliveryQueue.get_by_id(item_id)
        _tpl_name = "Unknown"
        if item.template_id:
            try:
                _tpl = EmailTemplate.get_by_id(item.template_id)
                _tpl_name = _tpl.name
            except EmailTemplate.DoesNotExist:
                pass
        return jsonify(ok=True, html=item.html, subject=item.subject,
                       email=item.email, template_name=_tpl_name)
    except DeliveryQueue.DoesNotExist:
        return jsonify(ok=False, error="Item not found"), 404


# ═══════════════════════════════════════════════════════════════
#  AI ACCOUNT MANAGER
# ═══════════════════════════════════════════════════════════════

@app.route("/account-manager")

def account_manager_dashboard():
    from database import (AMPendingReview, ContactStrategy, Contact,
                          LearningConfig)
    filter_type = request.args.get("filter", "all")
    page = int(request.args.get("page", 1))
    per_page = 20

    # Stats
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    pending_count = AMPendingReview.select().where(AMPendingReview.status == "pending").count()
    approved_today = AMPendingReview.select().where(
        AMPendingReview.status == "approved",
        AMPendingReview.reviewed_at >= today_start).count()
    rejected_today = AMPendingReview.select().where(
        AMPendingReview.status == "rejected",
        AMPendingReview.reviewed_at >= today_start).count()

    # Rolling approval rate
    total_decided = AMPendingReview.select().where(AMPendingReview.status != "pending").count()
    total_approved_all = AMPendingReview.select().where(AMPendingReview.status == "approved").count()
    approval_rate = (total_approved_all / total_decided * 100) if total_decided > 0 else 0

    # Pending emails
    query = (AMPendingReview.select(AMPendingReview, Contact, ContactStrategy)
             .join(Contact)
             .switch(AMPendingReview)
             .join(ContactStrategy)
             .where(AMPendingReview.status == "pending")
             .order_by(AMPendingReview.created_at.desc()))

    total = query.count()
    pending_emails = query.paginate(page, per_page)

    # Enrollment stats
    enrolled_count = ContactStrategy.select().where(ContactStrategy.enrolled == True).count()
    autonomous_count = ContactStrategy.select().where(
        ContactStrategy.autonomous == True, ContactStrategy.enrolled == True).count()

    # Settings
    am_enabled = LearningConfig.get_val("am_enabled", "false")

    return render_template("account_manager.html",
        pending_emails=pending_emails,
        pending_count=pending_count,
        approved_today=approved_today,
        rejected_today=rejected_today,
        approval_rate=approval_rate,
        total_decided=total_decided,
        enrolled_count=enrolled_count,
        autonomous_count=autonomous_count,
        am_enabled=am_enabled,
        filter_type=filter_type,
        page=page,
        total_pages=(total + per_page - 1) // per_page,
        total=total)


@app.route("/account-manager/approve/<int:pending_id>", methods=["POST"])

def am_approve(pending_id):
    from account_manager import approve_email
    approve_email(pending_id)
    flash("Email approved and queued for delivery.", "success")
    return redirect(url_for("account_manager_dashboard"))


@app.route("/account-manager/reject/<int:pending_id>", methods=["POST"])

def am_reject(pending_id):
    from account_manager import reject_email
    reason = request.form.get("reason", "")
    reject_email(pending_id, reason)
    flash("Email rejected. AI will learn from your feedback.", "info")
    return redirect(url_for("account_manager_dashboard"))


@app.route("/account-manager/edit/<int:pending_id>", methods=["POST"])

def am_edit(pending_id):
    from database import AMPendingReview
    pe = AMPendingReview.get_or_none(AMPendingReview.id == pending_id)
    if not pe:
        flash("Email not found.", "error")
        return redirect(url_for("account_manager_dashboard"))

    edited_subject = request.form.get("edited_subject", "")
    edited_html = request.form.get("edited_html", "")
    if edited_subject:
        pe.edited_subject = edited_subject
    if edited_html:
        pe.edited_html = edited_html
    pe.save()

    # If user wants to save and approve directly
    if request.form.get("save_and_approve"):
        from account_manager import approve_email
        approve_email(pending_id)
        flash("Email edited and approved.", "success")
    else:
        flash("Edits saved. Review and approve when ready.", "info")
    return redirect(url_for("account_manager_dashboard"))


@app.route("/account-manager/regenerate/<int:pending_id>", methods=["POST"])

def am_regenerate(pending_id):
    from account_manager import regenerate_email
    feedback = request.form.get("feedback", "")
    result = regenerate_email(pending_id, feedback)
    if result:
        flash("Email regenerated with your feedback.", "success")
    else:
        flash("Failed to regenerate email.", "error")
    return redirect(url_for("account_manager_dashboard"))


@app.route("/account-manager/bulk-approve", methods=["POST"])

def am_bulk_approve():
    from account_manager import approve_email
    ids = request.form.getlist("pending_ids")
    count = 0
    for pid in ids:
        if approve_email(int(pid)):
            count += 1
    flash(f"{count} emails approved and queued.", "success")
    return redirect(url_for("account_manager_dashboard"))


@app.route("/account-manager/contact/<int:contact_id>")

def am_contact_detail(contact_id):
    from database import (Contact, ContactStrategy, AMPendingReview,
                          CustomerProfile, ContactScore)
    import json as _json

    contact = Contact.get_or_none(Contact.id == contact_id)
    if not contact:
        flash("Contact not found.", "error")
        return redirect(url_for("account_manager_dashboard"))

    cs = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
    profile = CustomerProfile.get_or_none(CustomerProfile.email == contact.email)
    score = ContactScore.get_or_none(ContactScore.contact == contact)

    # Parse strategy JSON
    strategy_data = {}
    if cs and cs.strategy_json and cs.strategy_json != "{}":
        try:
            strategy_data = _json.loads(cs.strategy_json)
        except Exception:
            pass

    # Email history for this contact
    email_history = (AMPendingReview.select()
                     .where(AMPendingReview.contact == contact)
                     .order_by(AMPendingReview.created_at.desc())
                     .limit(20))

    return render_template("account_manager.html",
        view="contact_detail",
        contact=contact,
        strategy=cs,
        strategy_data=strategy_data,
        profile=profile,
        score=score,
        email_history=email_history)


@app.route("/account-manager/enroll/<int:contact_id>", methods=["POST"])

def am_enroll(contact_id):
    from account_manager import enroll_contact
    enroll_contact(contact_id)
    flash("Contact enrolled in AI Account Manager.", "success")
    return redirect(request.referrer or url_for("account_manager_dashboard"))


@app.route("/account-manager/unenroll/<int:contact_id>", methods=["POST"])

def am_unenroll(contact_id):
    from account_manager import unenroll_contact
    unenroll_contact(contact_id)
    flash("Contact removed from AI Account Manager.", "info")
    return redirect(request.referrer or url_for("account_manager_dashboard"))


@app.route("/account-manager/settings", methods=["GET", "POST"])

def am_settings():
    from database import LearningConfig
    if request.method == "POST":
        LearningConfig.set_val("am_enabled", request.form.get("am_enabled", "false"))
        LearningConfig.set_val("am_max_daily_contacts", request.form.get("am_max_daily_contacts", "200"))
        LearningConfig.set_val("am_enrollment_mode", request.form.get("am_enrollment_mode", "manual"))
        flash("Account Manager settings saved.", "success")
        return redirect(url_for("am_settings"))

    return render_template("account_manager.html",
        view="settings",
        am_enabled=LearningConfig.get_val("am_enabled", "false"),
        am_max_daily_contacts=LearningConfig.get_val("am_max_daily_contacts", "200"),
        am_enrollment_mode=LearningConfig.get_val("am_enrollment_mode", "manual"))


@app.route("/account-manager/preview/<int:pending_id>")

def am_preview_email(pending_id):
    from database import AMPendingReview
    pe = AMPendingReview.get_or_none(AMPendingReview.id == pending_id)
    if not pe:
        return "Not found", 404
    html = pe.edited_html if pe.edited_html else pe.body_html
    return html


@app.route("/account-manager/prompts")

def am_prompts():
    from database import PromptVersion
    from account_manager import DEFAULT_PROMPTS, seed_default_prompts
    seed_default_prompts()

    prompt_keys = list(DEFAULT_PROMPTS.keys())
    active_prompts = {}
    for key in prompt_keys:
        pv = (PromptVersion.select()
              .where(PromptVersion.prompt_key == key, PromptVersion.is_active == True)
              .order_by(PromptVersion.version.desc())
              .first())
        if pv:
            active_prompts[key] = pv

    # Version history for each prompt
    prompt_history = {}
    for key in prompt_keys:
        versions = (PromptVersion.select()
                    .where(PromptVersion.prompt_key == key)
                    .order_by(PromptVersion.version.desc())
                    .limit(10))
        prompt_history[key] = list(versions)

    return render_template("prompt_editor.html",
        prompt_keys=prompt_keys,
        active_prompts=active_prompts,
        prompt_history=prompt_history)


@app.route("/account-manager/prompts/save", methods=["POST"])

def am_save_prompt():
    from database import PromptVersion
    prompt_key = request.form.get("prompt_key")
    content = request.form.get("content", "")
    change_note = request.form.get("change_note", "")

    # Get current max version
    max_ver = (PromptVersion.select(fn.MAX(PromptVersion.version))
               .where(PromptVersion.prompt_key == prompt_key)
               .scalar() or 0)

    # Deactivate old versions
    (PromptVersion.update(is_active=False)
     .where(PromptVersion.prompt_key == prompt_key)
     .execute())

    # Create new version
    PromptVersion.create(
        prompt_key=prompt_key,
        version=max_ver + 1,
        content=content,
        change_note=change_note,
        is_active=True,
        created_at=datetime.now()
    )

    flash(f"Prompt '{prompt_key}' saved as v{max_ver + 1}.", "success")
    return redirect(url_for("am_prompts"))


@app.route("/account-manager/prompts/revert", methods=["POST"])

def am_revert_prompt():
    from database import PromptVersion
    version_id = int(request.form.get("version_id"))

    pv = PromptVersion.get_or_none(PromptVersion.id == version_id)
    if pv:
        (PromptVersion.update(is_active=False)
         .where(PromptVersion.prompt_key == pv.prompt_key)
         .execute())
        pv.is_active = True
        pv.save()
        flash(f"Reverted to v{pv.version}.", "success")

    return redirect(url_for("am_prompts"))


@app.route("/account-manager/prompts/preview", methods=["POST"])

def am_prompt_preview():
    """Test a prompt against a specific contact — returns AI response as JSON."""
    from database import Contact, ContactStrategy
    from account_manager import (gather_contact_profile, gather_business_context,
                                 _get_anthropic_client)

    contact_email = request.form.get("contact_email", "")
    prompt_content = request.form.get("prompt_content", "")

    contact = Contact.get_or_none(Contact.email == contact_email)
    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    profile_text = gather_contact_profile(contact)
    business_ctx = gather_business_context()

    client = _get_anthropic_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system=prompt_content,
        messages=[{"role": "user", "content": f"CUSTOMER PROFILE:\n{profile_text}\n\n{business_ctx}\n\nWhat would you do for this customer today? Respond with your strategy."}]
    )

    return jsonify({"preview": response.content[0].text})


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
        site_url=SITE_URL,
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

    # Canonical identity resolution
    from identity_resolution import resolve_identity
    result = resolve_identity(
        email=email,
        session_id=session_id,
        source="pixel_identify",
        create_if_missing=True,
    )

    resp = jsonify({"ok": True, "updated": result["stitched"], "new_contact": result["created"]})
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

        # Normalize variant field names into canonical fields
        from normalize_activity import normalize_event_data
        event_data = normalize_event_data(event_type, event_data)

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
            checkout_token      = str(event_data.get("checkout_token", "") or event_data.get("checkout_id", "") or ""),
            cart_token          = str(event_data.get("cart_token", "") or ""),
            shopify_customer_id = str(event_data.get("shopify_customer_id", "") or ""),
        )
        # Real-time last_active_at for known profiles (lightweight — no full re-analysis)
        if email:
            try:
                from database import CustomerProfile
                CustomerProfile.update(last_active_at=datetime.now())                     .where(CustomerProfile.email == email)                     .execute()
            except Exception:
                pass

        # Identity resolution: stitch session when email is discovered via pixel
        if email and session_id:
            from identity_resolution import resolve_identity
            resolve_identity(email=email, session_id=session_id, source="api_track", create_if_missing=False)

        return jsonify({"ok": True}), 200, cors_headers
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400, cors_headers



@app.route("/api/subscribe", methods=["POST", "OPTIONS"])
def api_subscribe():
    """Popup subscription widget — capture email, create contact, generate discount."""
    from database import CustomerActivity, Contact, CustomerProfile
    import json as _json, re as _re

    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
    }

    if request.method == "OPTIONS":
        return "", 204, cors_headers

    try:
        payload    = request.get_json(silent=True) or {}
        email      = (payload.get("email") or "").strip().lower()
        session_id = (payload.get("session_id") or "").strip()

        # ── Validate & sanitize email ──
        from email_sanitizer import sanitize_email
        _san = sanitize_email(email)
        if not _san["valid"]:
            _msg = {
                "invalid_syntax": "Please enter a valid email address.",
                "disposable_domain": "Please use a real email address.",
                "no_mx_record": "This email domain doesn't appear to accept emails.",
                "empty": "Please enter a valid email address.",
            }
            return jsonify({"ok": False, "error": _msg.get(_san["reason"], "Invalid email.")}), 400, cors_headers
        email = _san["email"]  # use corrected email (typos fixed)

        # ── Rate limiting (simple IP-based, 5 per hour) ──
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()

        from datetime import datetime, timedelta
        one_hour_ago = datetime.now() - timedelta(hours=1)
        recent_subs = CustomerActivity.select().where(
            CustomerActivity.event_type == "popup_subscribe",
            CustomerActivity.source_ref == client_ip,
            CustomerActivity.occurred_at >= one_hour_ago
        ).count()
        if recent_subs >= 5:
            return jsonify({"ok": False, "error": "Too many requests. Please try again later."}), 429, cors_headers

        # ── Canonical identity resolution (contact + stitching + welcome) ──
        from identity_resolution import resolve_identity
        _id_result = resolve_identity(
            email=email,
            session_id=session_id,
            source="popup_subscribe",
            subscribe=True,
            create_if_missing=True,
        )
        contact = _id_result["contact"]
        created = _id_result["created"]

        if not contact:
            return jsonify({"ok": False, "error": "Contact creation failed."}), 500, cors_headers

        # ── Log the subscribe event ──
        from normalize_activity import normalize_event_data as _norm
        _sub_data = _norm("popup_subscribe", {"source": "popup_widget", "new": created})
        CustomerActivity.create(
            contact_id=contact.id,
            email=email,
            event_type="popup_subscribe",
            event_data=_json.dumps(_sub_data),
            source="popup",
            source_ref=client_ip,
            session_id=session_id,
            occurred_at=datetime.now(),
        )

        # ── Generate 10% discount code ──
        discount_code = None
        try:
            from discount_codes import generate_popup_discount
            discount_code = generate_popup_discount(contact.id, email)
        except Exception as e:
            app.logger.error(f"Popup discount generation failed for {email}: {e}")

        # Note: intelligence cascade + welcome flow enrollment are handled
        # by resolve_identity() above (runs in background thread).

        # ── Push consent to Shopify so both systems stay in sync ──
        try:
            import threading as _th_shopify
            _email_push = email
            def _push_bg():
                from shopify_sync import push_consent_to_shopify
                push_consent_to_shopify(_email_push, subscribed=True)
            _th_shopify.Thread(target=_push_bg, daemon=True).start()
        except Exception as e:
            app.logger.warning(f"Popup Shopify consent push failed for {email}: {e}")

        app.logger.info(f"Popup subscribe: {email} (new={created}, code={discount_code})")

        return jsonify({
            "ok": True,
            "discount_code": discount_code,
            "new_contact": created,
        }), 200, cors_headers

    except Exception as e:
        app.logger.error(f"Popup subscribe error: {e}")
        return jsonify({"ok": False, "error": "Something went wrong. Please try again."}), 500, cors_headers


@app.route("/activity/sync", methods=["POST"])
def activity_sync_trigger():
    """Manually trigger full activity sync in background."""
    import threading, sys as _sys
    def _run():
        _sys.path.insert(0, APP_DIR)
        import activity_sync as _sync
        _sync.run_full_activity_sync()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Activity sync started — refresh in ~30 seconds"})



def _recalculate_deliverability_scores():
    """Nightly: recalculate fatigue_score, spam_risk_score, and reset rolling counters."""
    try:
        now = datetime.now()
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)
        ninety_days_ago = now - timedelta(days=90)

        for contact in Contact.select():
            # Recount from CampaignEmail + FlowEmail
            ce_7d = CampaignEmail.select().where(
                CampaignEmail.contact == contact, CampaignEmail.status == "sent",
                CampaignEmail.created_at >= seven_days_ago).count()
            fe_7d = FlowEmail.select().where(
                FlowEmail.contact == contact, FlowEmail.status == "sent",
                FlowEmail.sent_at >= seven_days_ago).count()
            emails_7d = ce_7d + fe_7d

            ce_30d = CampaignEmail.select().where(
                CampaignEmail.contact == contact, CampaignEmail.status == "sent",
                CampaignEmail.created_at >= thirty_days_ago).count()
            fe_30d = FlowEmail.select().where(
                FlowEmail.contact == contact, FlowEmail.status == "sent",
                FlowEmail.sent_at >= thirty_days_ago).count()
            emails_30d = ce_30d + fe_30d

            # Fatigue score (0-100)
            fatigue = 0
            if emails_7d >= 5: fatigue += 40
            elif emails_7d >= 3: fatigue += 20
            elif emails_7d >= 2: fatigue += 10
            if emails_30d >= 15: fatigue += 30
            elif emails_30d >= 8: fatigue += 15
            if contact.last_open_at and contact.last_open_at < thirty_days_ago:
                fatigue += 20
            elif not contact.last_open_at and emails_30d > 0:
                fatigue += 30
            fatigue = min(fatigue, 100)

            # Spam risk score (0-100)
            spam_risk = 0
            if fatigue >= 60: spam_risk += 30
            if contact.last_open_at and contact.last_open_at < ninety_days_ago:
                spam_risk += 40
            elif not contact.last_open_at and emails_30d > 0:
                spam_risk += 50
            if emails_7d >= 4: spam_risk += 20
            spam_risk = min(spam_risk, 100)

            # Clear expired temporary suppressions
            supp_reason = contact.suppression_reason
            if supp_reason and contact.suppression_until:
                if contact.suppression_until < now:
                    supp_reason = ""

            Contact.update(
                emails_received_7d=emails_7d,
                emails_received_30d=emails_30d,
                fatigue_score=fatigue,
                spam_risk_score=spam_risk,
                suppression_reason=supp_reason,
            ).where(Contact.id == contact.id).execute()

        print("[OK] Deliverability scores recalculated")
    except Exception as e:
        print(f"[ERROR] Deliverability score recalc: {e}")


# ─────────────────────────────────
#  SYSTEM MAP
# ─────────────────────────────────

@app.route("/system-map")
def system_map():
    return render_template("system_map.html")

@app.route("/api/system-map/data")
def system_map_api():
    from system_map_data import build_system_map_nodes, build_system_map_edges
    nodes = build_system_map_nodes()
    edges = build_system_map_edges()
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("UTC")).astimezone(ZoneInfo("America/Toronto"))
    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "updated_at": now_et.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_nodes": len(nodes),
            "total_edges": len(edges)
        }
    })

# ─────────────────────────────────
#  START BACKGROUND SCHEDULER
# ─────────────────────────────────
# Guard prevents double-scheduling across Gunicorn workers using file lock.
_scheduler_lock_fd = None
def _try_scheduler_lock():
    """Try to acquire exclusive lock. Returns True if lock acquired."""
    global _scheduler_lock_fd
    try:
        _scheduler_lock_fd = open("/tmp/mailengine_scheduler.lock", "a")
        fcntl.flock(_scheduler_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _scheduler_lock_fd.write(str(os.getpid()) + "\n")
        _scheduler_lock_fd.flush()
        return True
    except (IOError, OSError):
        if _scheduler_lock_fd:
            _scheduler_lock_fd.close()
            _scheduler_lock_fd = None
        return False

def _process_delivery_queue_wrapper():
    """Wrapper to call delivery_engine.process_queue() from the scheduler."""
    try:
        from delivery_engine import process_queue
        count = process_queue()
        if count > 0:
            app.logger.info("[DeliveryQueue] Processed %d items" % count)
    except Exception as _e:
        app.logger.error("[DeliveryQueue] Error: %s" % _e)

if os.environ.get("ENABLE_SCHEDULER", "1") == "1" and not _scheduler.running and _try_scheduler_lock():
    _scheduler.add_job(_process_flow_enrollments, "interval", seconds=60,
                       id="flow_processor", replace_existing=True)
    _scheduler.add_job(_process_delivery_queue_wrapper, "interval", seconds=30,
                       id="delivery_queue", replace_existing=True)
    _scheduler.add_job(_check_passive_triggers, "interval", minutes=5,
                       id="passive_triggers", replace_existing=True)
    _scheduler.add_job(_check_abandoned_checkouts, "interval", minutes=15,
                       id="abandoned_checkout_checker", replace_existing=True)
    _scheduler.add_job(_recover_pending_backlog, "interval", minutes=10,
                       id="backlog_recovery", replace_existing=True)

    # ── Identity job processor (durable queue for enrichment/cascade/replay) ──
    def _process_identity_jobs_wrapper():
        try:
            from identity_resolution import process_identity_jobs
            process_identity_jobs()
        except Exception as _e:
            app.logger.error(f"Identity job processor error: {_e}")

    _scheduler.add_job(_process_identity_jobs_wrapper, "interval", seconds=30,
                       id="identity_job_processor", replace_existing=True)

    # ── Nightly contact scoring at 2:30am (RFM + engagement) ──
    def _run_nightly_contact_scoring():
        try:
            import sys as _sc; _sc.path.insert(0, APP_DIR)
            from ai_engine import score_all_contacts
            app.logger.info("Nightly contact scoring starting...")
            count = score_all_contacts()
            app.logger.info(f"Contact scoring complete: {count} contacts scored")
        except Exception as _e:
            app.logger.error(f"Nightly contact scoring failed: {_e}")

    _scheduler.add_job(_run_nightly_contact_scoring, "cron", hour=2, minute=30,
                       id="contact_scoring", replace_existing=True)

    # Nightly activity sync + profile enrichment at 3am
    def _run_nightly_activity_sync():
        try:
            import sys as _s; _s.path.insert(0, APP_DIR)
            from activity_sync import run_full_activity_sync
            app.logger.info("Nightly activity sync starting...")
            results = run_full_activity_sync()
            app.logger.info(f"Nightly activity sync complete: {results}")
        except Exception as _e:
            app.logger.error(f"Nightly activity sync failed: {_e}")

    def _run_nightly_intelligence():
        try:
            import sys as _si; _si.path.insert(0, APP_DIR)
            from customer_intelligence import compute_all_intelligence
            app.logger.info("Nightly intelligence computation starting...")
            count = compute_all_intelligence()
            app.logger.info(f"Nightly intelligence complete: {count} contacts scored")
        except Exception as _e:
            app.logger.error(f"Nightly intelligence failed: {_e}")

    _scheduler.add_job(_run_nightly_intelligence, "cron", hour=3, minute=30,
                       id="nightly_intelligence", replace_existing=True)
    def _run_nightly_decisions():
        try:
            import sys as _sn; _sn.path.insert(0, APP_DIR)
            from next_best_message import decide_all_contacts
            app.logger.info("Nightly decision engine starting...")
            count = decide_all_contacts()
            app.logger.info(f"Nightly decisions complete: {count} contacts processed")
        except Exception as _e:
            app.logger.error(f"Nightly decision engine failed: {_e}")

    # ── AI Account Manager: per-contact AI strategist ──
    def _run_account_manager():
        try:
            import sys as _sam; _sam.path.insert(0, APP_DIR)
            from account_manager import run_account_manager, seed_default_prompts
            seed_default_prompts()  # Ensure defaults exist
            app.logger.info("AI Account Manager starting...")
            results = run_account_manager()
            app.logger.info(f"AI Account Manager complete: {results}")
        except Exception as _e:
            app.logger.error(f"AI Account Manager failed: {_e}")

    _scheduler.add_job(_run_account_manager, "cron", hour=3, minute=40,
                       id="account_manager", replace_existing=True)
    _scheduler.add_job(_run_nightly_decisions, "cron", hour=4, minute=0,
                       id="nightly_decisions", replace_existing=True)
    def _run_nightly_opportunity_scan():
        try:
            import sys as _so; _so.path.insert(0, APP_DIR)
            from campaign_planner import scan_opportunities
            app.logger.info("Nightly opportunity scan starting...")
            opps = scan_opportunities()
            app.logger.info(f"Opportunity scan complete: {len(opps)} opportunities found")
        except Exception as _e:
            app.logger.error(f"Nightly opportunity scan failed: {_e}")

    def _run_nightly_profit_scoring():
        try:
            import sys as _sp2; _sp2.path.insert(0, APP_DIR)
            from profit_engine import sync_product_commercial_data, compute_product_scores
            app.logger.info("Nightly profit scoring starting...")
            sync_result = sync_product_commercial_data()
            app.logger.info(f"Product sync: {sync_result}")
            count = compute_product_scores()
            app.logger.info(f"Profit scoring complete: {count} products scored")
        except Exception as _e:
            app.logger.error(f"Nightly profit scoring failed: {_e}")

    _scheduler.add_job(_run_nightly_opportunity_scan, "cron", hour=4, minute=15,
                       id="opportunity_scan", replace_existing=True)

    # ── Auto-Pilot: Per-contact scheduled sends based on NBM decisions ──
    def _run_auto_scheduler():
        """Read nightly NBM decisions, map to templates, schedule at each contact's preferred hour."""
        try:
            import sys as _sas; _sas.path.insert(0, APP_DIR)
            from database import (MessageDecision, Contact, CustomerProfile, ContactScore,
                                  EmailTemplate, FlowEnrollment, DeliveryQueue, SuppressionEntry,
                                  AutoEmail, init_db, get_system_config)
            from delivery_engine import enqueue_email
            from action_ledger import log_action
            from learning_config import get_learning_enabled
            init_db()

            # Only run when learning enabled + live mode
            if not get_learning_enabled():
                app.logger.info("[AutoScheduler] Skipped — learning disabled")
                return
            cfg = get_system_config()
            if cfg.delivery_mode != "live":
                app.logger.info("[AutoScheduler] Skipped — delivery mode is %s" % cfg.delivery_mode)
                return

            # Action → template ID mapping (existing templates)
            ACTION_TEMPLATE = {
                "reorder_reminder":  12,  # Post-Purchase Review & Reorder
                "cross_sell":        11,  # Post-Purchase Thank You (cross-sell)
                "upsell":            13,  # Post-Purchase Loyalty Discount
                "new_product":       17,  # Browse Abandon Product Reminder
                "winback":           14,  # Win-Back We Miss You
                "education":          6,  # Welcome Social Proof
                "loyalty_reward":    13,  # Post-Purchase Loyalty Discount
                "discount_offer":    10,  # Checkout Recovery 10% Off
            }

            # Region → UTC offset (Canada, US states, intl)
            PROVINCE_TZ = {
                # Canada
                "BC": -8, "AB": -7, "SK": -6, "MB": -6,
                "ON": -5, "QC": -5, "NB": -4, "NS": -4,
                "PE": -4, "NL": -3.5, "YT": -7, "NT": -7, "NU": -5,
                # US states (most common in our data)
                "CA": -8, "WA": -8, "OR": -8, "NV": -8,    # Pacific
                "AZ": -7, "MT": -7, "ID": -7, "CO": -7, "NM": -7, "UT": -7, "WY": -7,  # Mountain
                "TX": -6, "IL": -6, "MN": -6, "WI": -6, "IA": -6, "MO": -6,  # Central
                "IN": -5, "OH": -5, "MI": -5, "NY": -5, "PA": -5, "NJ": -5,  # Eastern
                "VA": -5, "NC": -5, "GA": -5, "FL": -5, "MA": -5, "CT": -5, "MD": -5,
                "HI": -10, "AK": -9,  # Hawaii, Alaska
            }
            # Country → UTC offset (for contacts with country but no province)
            COUNTRY_TZ = {
                "CA": -5, "US": -5,   # default Eastern for North America
                "GB": 0, "IE": 0,     # UK/Ireland
                "DE": 1, "FR": 1, "IT": 1, "ES": 1, "NL": 1, "PL": 1, "CZ": 1, "SE": 1, "AT": 1,  # Central EU
                "AU": 10, "NZ": 12,   # Oceania
                "IN": 5.5, "AE": 4,   # South/West Asia
                "JP": 9, "KR": 9, "SG": 8, "HK": 8,  # East/SE Asia
            }
            # City → UTC offset (fallback for contacts with city but no province)
            CITY_TZ = {
                "BRAMPTON": -5, "TORONTO": -5, "MISSISSAUGA": -5, "CAMBRIDGE": -5, "LONDON": -5, "OTTAWA": -5,
                "WINNIPEG": -6, "REGINA": -6, "SASKATOON": -6,
                "CALGARY": -7, "EDMONTON": -7,
                "SURREY": -8, "ABBOTSFORD": -8, "DELTA": -8, "VANCOUVER": -8, "BURNABY": -8, "VICTORIA": -8,
                "MONTRÉAL": -5, "MONTREAL": -5, "QUEBEC": -5, "LAVAL": -5,
                "FRESNO": -8, "MANTECA": -8, "LOS ANGELES": -8, "SAN FRANCISCO": -8,
                "NEW YORK": -5, "BROOKLYN": -5, "CHICAGO": -6, "HOUSTON": -6,
                "SYDNEY": 10, "MELBOURNE": 10, "BRISBANE": 10,
            }

            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            scheduled = 0
            skipped = 0

            decisions = list(MessageDecision.select().where(
                MessageDecision.action_type != "wait",
                MessageDecision.action_type != "switch_channel",
                MessageDecision.suppression_active == False,
            ))

            for decision in decisions:
                try:
                    contact = Contact.get_by_id(decision.contact_id)

                    # Skip unsubscribed
                    if not contact.subscribed:
                        skipped += 1; continue

                    # Skip suppressed
                    if SuppressionEntry.select().where(SuppressionEntry.email == contact.email).exists():
                        skipped += 1; continue

                    # Skip sunset contacts
                    _cs = ContactScore.get_or_none(ContactScore.contact == contact)
                    if _cs and _cs.sunset_score and _cs.sunset_score >= 85:
                        skipped += 1; continue

                    # Skip contacts managed by AI Account Manager
                    from database import ContactStrategy
                    _am_cs = ContactStrategy.get_or_none(
                        ContactStrategy.contact == contact,
                        ContactStrategy.enrolled == True)
                    if _am_cs:
                        skipped += 1; continue

                    # Skip if already has a pending auto-scheduled email today
                    _existing = (DeliveryQueue.select()
                                 .where(DeliveryQueue.contact == contact,
                                        DeliveryQueue.email_type == "auto",
                                        DeliveryQueue.status == "queued",
                                        DeliveryQueue.created_at >= today_str)
                                 .exists())
                    if _existing:
                        skipped += 1; continue

                    # Skip if contact has active flow enrollment (flows take priority)
                    _active_flow = (FlowEnrollment.select()
                                    .where(FlowEnrollment.contact == contact,
                                           FlowEnrollment.status == "active")
                                    .exists())
                    if _active_flow:
                        skipped += 1; continue

                    # ── AI PERSONALIZATION ──
                    # Try per-contact AI generation first, fall back to hardcoded template
                    from database import LearningConfig
                    _ai_personalization = LearningConfig.get_val("ai_personalization_enabled", "false") == "true"
                    _ai_result = None
                    _used_ai = False

                    if _ai_personalization:
                        try:
                            from ai_engine import generate_personalized_email
                            from next_best_message import ACTION_TO_PURPOSE
                            _purpose = ACTION_TO_PURPOSE.get(decision.action_type, "")
                            if _purpose:
                                _ai_result = generate_personalized_email(
                                    contact.email, _purpose,
                                    extra_context=decision.action_reason or ""
                                )
                                import time as _t; _t.sleep(0.5)  # rate limit Claude calls
                        except Exception as _ai_err:
                            app.logger.warning("[AutoScheduler] AI gen failed for %s: %s", contact.email, _ai_err)

                    # Get template (used as fallback or when AI is disabled)
                    template_id = ACTION_TEMPLATE.get(decision.action_type)
                    template = None
                    if template_id:
                        try:
                            template = EmailTemplate.get_by_id(template_id)
                        except EmailTemplate.DoesNotExist:
                            pass

                    # If no AI result and no template, skip
                    if not _ai_result and not template:
                        skipped += 1; continue

                    # ── SEND TIME OPTIMIZATION ──
                    # 3 tiers: (1) AI-learned from contact opens, (2) global best-time
                    # curve from industry ecommerce data, (3) never falls back to fixed hour.
                    #
                    # Global curve: weighted distribution of best ecommerce send hours
                    # based on 2025-2026 Omnisend/Klaviyo/MailerLite/Brevo aggregate data.
                    # Peak windows: 10-11am (morning check), 1-2pm (lunch), 5-6pm (after work).
                    # Each contact hashes into a slot proportional to these weights.
                    _GLOBAL_SEND_CURVE = [
                        # (hour_local, weight) — weights reflect relative open+click performance
                        (9,  8),   # 9am  — early morning openers
                        (10, 15),  # 10am — peak morning window
                        (11, 14),  # 11am — late morning
                        (12, 8),   # 12pm — lunch start
                        (13, 10),  # 1pm  — lunch break peak
                        (14, 9),   # 2pm  — early afternoon
                        (15, 7),   # 3pm  — mid afternoon
                        (16, 8),   # 4pm  — late afternoon
                        (17, 10),  # 5pm  — after-work ecommerce peak
                        (18, 7),   # 6pm  — evening browse
                        (19, 4),   # 7pm  — evening wind-down
                    ]
                    _TOTAL_WEIGHT = sum(w for _, w in _GLOBAL_SEND_CURVE)

                    _profile = CustomerProfile.get_or_none(CustomerProfile.contact == contact)

                    # Tier 1: AI-learned preferred hour (from real open behavior)
                    if _profile and _profile.preferred_send_hour >= 0:
                        _pref_hour = _profile.preferred_send_hour
                    else:
                        # Tier 2: Hash contact into global best-time curve
                        # Deterministic — same contact always lands on same slot
                        _hash_val = (contact.id * 2654435761) % _TOTAL_WEIGHT  # Knuth multiplicative hash
                        _cumulative = 0
                        _pref_hour = 10  # safety fallback
                        for _hour, _weight in _GLOBAL_SEND_CURVE:
                            _cumulative += _weight
                            if _hash_val < _cumulative:
                                _pref_hour = _hour
                                break

                    # Calculate timezone offset: province → city → country → default
                    _tz_offset = None
                    _FULL_TO_CODE = {
                        "ONTARIO": "ON", "QUEBEC": "QC", "BRITISH COLUMBIA": "BC",
                        "ALBERTA": "AB", "SASKATCHEWAN": "SK", "MANITOBA": "MB",
                        "NOVA SCOTIA": "NS", "NEW BRUNSWICK": "NB",
                        "PRINCE EDWARD ISLAND": "PE", "NEWFOUNDLAND AND LABRADOR": "NL",
                        "CALIFORNIA": "CA", "NEW YORK": "NY", "INDIANA": "IN",
                        "NEW JERSEY": "NJ", "WASHINGTON": "WA", "PENNSYLVANIA": "PA",
                        "TEXAS": "TX", "VIRGINIA": "VA", "OHIO": "OH", "FLORIDA": "FL",
                        "NEW SOUTH WALES": "AU",  # map Aussie states to country
                        "VICTORIA": "AU", "QUEENSLAND": "AU",
                    }
                    # Tier 1: Province/state
                    if _profile and _profile.province:
                        _prov = _profile.province.strip().upper()
                        _prov = _FULL_TO_CODE.get(_prov, _prov[:2])
                        _tz_offset = PROVINCE_TZ.get(_prov)
                    # Tier 2: City
                    if _tz_offset is None:
                        _city = (_profile.city if _profile and _profile.city else "") or (contact.city or "")
                        if _city:
                            _tz_offset = CITY_TZ.get(_city.strip().upper())
                    # Tier 3: Country
                    if _tz_offset is None and contact.country:
                        _tz_offset = COUNTRY_TZ.get(contact.country.strip().upper())
                    # Final default: Eastern (largest segment in our data)
                    if _tz_offset is None:
                        _tz_offset = -5

                    # Convert preferred hour (contact's local) to server time (UTC)
                    _send_hour_utc = int((_pref_hour - _tz_offset) % 24)
                    # Minute jitter: spread within hour so SES doesn't get hammered at :00
                    _send_minute = (contact.id * 7) % 60

                    # Schedule for today at that hour, or tomorrow if already past
                    _sched = now.replace(hour=_send_hour_utc, minute=_send_minute, second=0, microsecond=0)
                    if _sched <= now:
                        _sched += timedelta(days=1)

                    _unsub = _make_unsubscribe_url(contact)

                    # ── RENDER EMAIL (AI path or template fallback) ──
                    if _ai_result and _ai_result.get("body_html"):
                        # AI-generated: already has product images, discount codes, full HTML
                        html = _ai_result["body_html"]
                        subject = _ai_result.get("subject", "A message from LDAS Electronics")
                        _used_ai = True
                    elif template:
                        # Fallback: existing hardcoded template path
                        html = template.html_body or ""
                        html = html.replace("{{first_name}}", contact.first_name or "Friend")
                        html = html.replace("{{last_name}}", contact.last_name or "")
                        html = html.replace("{{email}}", contact.email)
                        html = html.replace("{{unsubscribe_url}}", _unsub)

                        # Personalization from CustomerProfile
                        if _profile:
                            html = html.replace("{{last_viewed_product}}", _profile.last_viewed_product or "one of our popular items")
                            html = html.replace("{{total_orders}}", str(_profile.total_orders or 0))
                            html = html.replace("{{total_spent}}", "$%.2f" % (_profile.total_spent or 0))
                        else:
                            html = html.replace("{{last_viewed_product}}", "one of our popular items")
                            html = html.replace("{{total_orders}}", "0")
                            html = html.replace("{{total_spent}}", "$0")

                        # Discount code — reuse existing or create new
                        if "{{discount_code}}" in html:
                            try:
                                from discount_engine import get_or_create_discount
                                _purpose_map = {
                                    "discount_offer": "cart_abandonment",
                                    "winback": "winback",
                                    "loyalty_reward": "loyalty_reward",
                                }
                                _dpurpose = _purpose_map.get(decision.action_type, "welcome")
                                _result = get_or_create_discount(contact.email, _dpurpose)
                                _dcode = _result.get("code", "") if isinstance(_result, dict) else ""
                                html = html.replace("{{discount_code}}", _dcode)
                            except Exception:
                                html = html.replace("{{discount_code}}", "")

                        # Cart items — resolve from abandoned checkout or browse history
                        if "{{cart_items}}" in html:
                            _cart_html = ""
                            try:
                                from database import AbandonedCheckout
                                import json as _cj
                                _checkout = (AbandonedCheckout.select()
                                             .where(AbandonedCheckout.contact == contact,
                                                    AbandonedCheckout.recovered == False)
                                             .order_by(AbandonedCheckout.created_at.desc()).first())
                                if _checkout and _checkout.line_items_json:
                                    _items = _cj.loads(_checkout.line_items_json)
                                    for _it in _items:
                                        _cart_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; %s x%s — $%s</p>' % (
                                            _it.get("title", ""), _it.get("quantity", 1), _it.get("price", "0.00"))
                            except Exception:
                                pass
                            if not _cart_html:
                                try:
                                    from database import CustomerActivity
                                    import json as _cj2
                                    _views = list(CustomerActivity.select()
                                                  .where(CustomerActivity.contact == contact,
                                                         CustomerActivity.event_type == 'viewed_product')
                                                  .order_by(CustomerActivity.occurred_at.desc()).limit(3))
                                    _seen = set()
                                    for _v in _views:
                                        _d = _cj2.loads(_v.event_data) if _v.event_data else {}
                                        _t = _d.get("product_title", "")
                                        if _t and _t not in _seen:
                                            _seen.add(_t)
                                            _cart_html += '<p style="margin:4px 0;font-size:14px;color:#4a5568;">&bull; %s</p>' % _t
                                except Exception:
                                    pass
                            if not _cart_html:
                                _cart_html = '<p style="margin:4px 0;font-size:14px;color:#4a5568;">Your selected items from LDAS Electronics</p>'
                            html = html.replace("{{cart_items}}", _cart_html)

                        # Checkout URL fallback
                        if "{{checkout_url}}" in html:
                            _co_url = "https://ldas.ca/cart"
                            try:
                                from database import AbandonedCheckout
                                _co = (AbandonedCheckout.select(AbandonedCheckout.checkout_url)
                                       .where(AbandonedCheckout.contact == contact, AbandonedCheckout.recovered == False)
                                       .order_by(AbandonedCheckout.created_at.desc()).first())
                                if _co and _co.checkout_url:
                                    _co_url = _co.checkout_url
                            except Exception:
                                pass
                            html = html.replace("{{checkout_url}}", _co_url)

                        # Wrap in email shell (header, footer, logo)
                        from email_shell import wrap_email
                        html = wrap_email(html, preview_text=template.preview_text or '', unsubscribe_url=_unsub)

                        subject = template.subject or "A message from LDAS Electronics"
                        subject = subject.replace("{{first_name}}", contact.first_name or "Friend")
                    else:
                        skipped += 1; continue

                    from_email = os.getenv("DEFAULT_FROM_EMAIL", "news@news.ldaselectronics.com")

                    # Pre-create AutoEmail record so we have an ID for tracking tokens
                    ae = AutoEmail.create(
                        contact=contact,
                        template=template_id or 0,
                        subject=subject,
                        status="queued",
                        auto_run_date=datetime.now().date(),
                    )

                    # Add token-based tracking pixel
                    from itsdangerous import URLSafeSerializer as _USS
                    _s_open = _USS(app.secret_key, salt="auto-open")
                    _auto_token = _s_open.dumps({"aeid": ae.id})
                    _auto_pixel = "https://mailenginehub.com/track/auto-open/%s" % _auto_token
                    html += '<img src="%s" width="1" height="1" />' % _auto_pixel

                    # Wrap all links with click tracking URLs
                    import re as _re
                    from urllib.parse import quote as _quote
                    _s_click = _USS(app.secret_key, salt="auto-click")
                    _click_token = _s_click.dumps({"aeid": ae.id})

                    def _wrap_auto_link(match):
                        original_url = match.group(1)
                        if "unsubscribe" in original_url or "track/" in original_url:
                            return match.group(0)
                        wrapped = "https://mailenginehub.com/track/auto-click/%s?url=%s" % (
                            _click_token, _quote(original_url, safe=""))
                        return 'href="%s"' % wrapped

                    html = _re.sub(r'href="([^"]+)"', _wrap_auto_link, html)

                    # Log to ledger
                    _tpl_name = "AI-personalized" if _used_ai else (template.name if template else "unknown")
                    _tpl_id = template_id or 0
                    ledger = log_action(contact, "auto", 0, "rendered", "RC_AUTO_SCHEDULED",
                                        source_type="auto_scheduler",
                                        template_id=_tpl_id,
                                        subject=subject,
                                        html=html, priority=60,
                                        reason_detail="NBM action: %s → %s, scheduled: %s"
                                                      % (decision.action_type, _tpl_name, _sched.strftime("%H:%M")))

                    # Enqueue with scheduled_at, linking the pre-created AutoEmail
                    enqueue_email(
                        contact=contact,
                        email_type="auto",
                        source_id=0,
                        enrollment_id=0,
                        step_id=0,
                        template_id=_tpl_id,
                        from_name="LDAS Electronics",
                        from_email=from_email,
                        subject=subject,
                        html=html,
                        unsubscribe_url=_unsub,
                        priority=60,
                        ledger_id=ledger.id if ledger else 0,
                        scheduled_at=_sched,
                        auto_email_id=ae.id,
                    )
                    scheduled += 1

                    # Mark decision as executed
                    decision.was_executed = True
                    decision.executed_at = now
                    try:
                        decision.save()
                    except Exception:
                        pass

                except Exception as _ce:
                    skipped += 1
                    app.logger.warning("[AutoScheduler] Error for contact %s: %s" % (decision.contact_id, _ce))

            app.logger.info("[AutoScheduler] Done: %d scheduled, %d skipped" % (scheduled, skipped))

        except Exception as _e:
            app.logger.error("[AutoScheduler] Failed: %s" % _e)

    _scheduler.add_job(_run_auto_scheduler, "cron", hour=4, minute=30,
                       id="auto_scheduler", replace_existing=True)

    _scheduler.add_job(_recalculate_deliverability_scores, "cron", hour=3, minute=45,
                       id="deliverability_scores", replace_existing=True)
    _scheduler.add_job(_run_nightly_profit_scoring, "cron", hour=4, minute=45,
                       id="profit_scoring", replace_existing=True)
    _scheduler.add_job(_run_nightly_activity_sync, "cron", hour=3, minute=0,
                       id="activity_sync_nightly", replace_existing=True)

    # Incremental Shopify order sync every 2 hours
    def _run_incremental_shopify_sync():
        try:
            import sys as _ss2; _ss2.path.insert(0, APP_DIR)
            from shopify_enrichment import sync_new_orders
            app.logger.info("Incremental Shopify sync starting...")
            result = sync_new_orders()
            if result.get("new_orders", 0) > 0:
                app.logger.info(f"Shopify sync: {result['new_orders']} new orders, "
                                f"{result.get('profiles_refreshed', 0)} profiles refreshed")
            else:
                app.logger.info("Shopify sync: no new orders")
        except Exception as _e:
            app.logger.error(f"Incremental Shopify sync failed: {_e}")

    _scheduler.add_job(_run_incremental_shopify_sync, "interval", hours=2,
                       id="shopify_sync_incremental", replace_existing=True)

    # Full Shopify backfill at 2:00am as safety net
    def _run_nightly_shopify_sync():
        try:
            import sys as _ss; _ss.path.insert(0, APP_DIR)
            from shopify_enrichment import run_shopify_backfill
            app.logger.info("Nightly Shopify full sync starting...")
            count = run_shopify_backfill()
            app.logger.info(f"Shopify full sync complete: {count} profiles rebuilt")
        except Exception as _e:
            app.logger.error(f"Nightly Shopify sync failed: {_e}")

    _scheduler.add_job(_run_nightly_shopify_sync, "cron", hour=2, minute=0,
                       id="shopify_sync_nightly", replace_existing=True)

    # Nightly knowledge enrichment at 4:30am
    def _run_nightly_knowledge_enrichment():
        try:
            import sys as _sk; _sk.path.insert(0, APP_DIR)
            from knowledge_scraper import run_knowledge_enrichment
            app.logger.info("Nightly knowledge enrichment starting...")
            run_knowledge_enrichment()
            app.logger.info("Knowledge enrichment complete")
        except Exception as _e:
            app.logger.error(f"Knowledge enrichment failed: {_e}")

    _scheduler.add_job(_run_nightly_knowledge_enrichment, "cron", hour=4, minute=30,
                       id="knowledge_enrichment", replace_existing=True)

    def _run_outcome_tracker():
        try:
            import sys as _sk; _sk.path.insert(0, APP_DIR)
            from outcome_tracker import run_outcome_tracker
            app.logger.info("Outcome tracker starting...")
            run_outcome_tracker()
            app.logger.info("Outcome tracker complete")
        except Exception as _e:
            app.logger.error(f"Outcome tracker failed: {_e}")

    def _run_learning_engine():
        try:
            import sys as _sk; _sk.path.insert(0, APP_DIR)
            from learning_engine import run_learning_engine
            app.logger.info("Learning engine starting...")
            run_learning_engine()
            app.logger.info("Learning engine complete")
        except Exception as _e:
            app.logger.error(f"Learning engine failed: {_e}")

    def _run_strategy_optimizer():
        try:
            import sys as _sk; _sk.path.insert(0, APP_DIR)
            from strategy_optimizer import run_strategy_optimizer
            app.logger.info("Strategy optimizer starting...")
            run_strategy_optimizer()
            app.logger.info("Strategy optimizer complete")
        except Exception as _e:
            app.logger.error(f"Strategy optimizer failed: {_e}")

    # ── Self-Learning Pipeline (5:00 → 5:30 → 6:00 AM) ──
    _scheduler.add_job(_run_outcome_tracker, "cron", hour=5, minute=0,
                       id="outcome_tracker", replace_existing=True)
    _scheduler.add_job(_run_learning_engine, "cron", hour=5, minute=30,
                       id="learning_engine", replace_existing=True)
    _scheduler.add_job(_run_strategy_optimizer, "cron", hour=6, minute=0,
                       id="strategy_optimizer", replace_existing=True)

    # ── Google Postmaster Tools fetch (6:30 AM) ──
    def _run_postmaster_fetch():
        try:
            from postmaster_tools import fetch_postmaster_metrics
            count = fetch_postmaster_metrics(days_back=7)
            if count:
                app.logger.info(f"[Postmaster] Fetched {count} new metric days")
        except Exception as e:
            app.logger.error(f"[Postmaster] Fetch failed: {e}")

    _scheduler.add_job(_run_postmaster_fetch, "cron", hour=6, minute=30,
                       id="postmaster_fetch", replace_existing=True)

    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))
    sys.stderr.write("[OK] Background scheduler started (ENABLE_SCHEDULER=1, pid=%d)\n" % os.getpid())
else:
    if os.environ.get("ENABLE_SCHEDULER", "1") != "1":
        sys.stderr.write("[INFO] Scheduler disabled (ENABLE_SCHEDULER != 1)\n")
    elif _scheduler.running:
        sys.stderr.write("[INFO] Scheduler already running\n")
    else:
        sys.stderr.write("[INFO] Scheduler skipped (another worker has lock, pid=%d)\n" % os.getpid())

if __name__ == "__main__":
    init_db()
    # Seed AI Account Manager default prompts
    try:
        from account_manager import seed_default_prompts
        seed_default_prompts()
    except Exception as e:
        app.logger.warning(f"Could not seed AM prompts: {e}")
    print("\n" + "="*50)
    print("  Email Marketing Platform Running!")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
