"""
Campaign Preflight Check System
Runs PASS / WARN / BLOCK checks before any campaign send.
Returns a structured result the dashboard can display.
"""

from datetime import datetime, timedelta
from database import (db, Contact, Campaign, CampaignEmail, EmailTemplate,
                      WarmupConfig, BounceLog, SuppressionEntry, get_warmup_config)


class PreflightCheck:
    """One individual check result."""
    def __init__(self, name, status, message, details=None):
        self.name = name          # e.g. "Warmup Compliance"
        self.status = status      # "PASS" | "WARN" | "BLOCK"
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


class PreflightResult:
    """Overall preflight result with list of checks."""
    def __init__(self):
        self.checks = []
        self.overall = "PASS"  # Degrades to WARN or BLOCK

    def add(self, check):
        self.checks.append(check)
        if check.status == "BLOCK":
            self.overall = "BLOCK"
        elif check.status == "WARN" and self.overall != "BLOCK":
            self.overall = "WARN"

    def to_dict(self):
        return {
            "overall": self.overall,
            "checks": [c.to_dict() for c in self.checks],
            "timestamp": datetime.now().isoformat(),
        }


# ── Warmup phases (duplicated from app.py to avoid circular import) ──
_WARMUP_PHASES = {
    1: {"label": "Ignition",      "daily_limit": 50},
    2: {"label": "Spark",         "daily_limit": 150},
    3: {"label": "Gaining Trust", "daily_limit": 350},
    4: {"label": "Building",      "daily_limit": 750},
    5: {"label": "Momentum",      "daily_limit": 1500},
    6: {"label": "Scaling",       "daily_limit": 3000},
    7: {"label": "High Volume",   "daily_limit": 7000},
    8: {"label": "Full Send",     "daily_limit": 999999},
}


def run_preflight(campaign_id):
    """
    Run all preflight checks for a campaign.
    Returns PreflightResult with overall PASS/WARN/BLOCK.
    """
    result = PreflightResult()
    campaign = Campaign.get_by_id(campaign_id)
    template = EmailTemplate.get_by_id(campaign.template_id)
    warmup = get_warmup_config()

    # Build recipient list
    contacts = _get_eligible_contacts(campaign)
    total_recipients = len(contacts)

    # 10 checks
    result.add(_check_warmup_compliance(warmup, total_recipients))
    result.add(_check_complaint_rate())
    result.add(_check_bounce_rate())
    result.add(_check_suppression_coverage(contacts))
    result.add(_check_content_compliance(template))
    result.add(_check_authentication(warmup))
    result.add(_check_list_quality(contacts))
    result.add(_check_domain_risk(contacts))
    result.add(_check_fatigue(contacts))
    result.add(_check_volume_safety(warmup, total_recipients))

    return result


def _get_eligible_contacts(campaign):
    """Get subscribed contacts matching segment."""
    query = Contact.select().where(Contact.subscribed == True)
    if campaign.segment_filter and campaign.segment_filter != "all":
        query = query.where(Contact.tags.contains(campaign.segment_filter))
    return list(query)


# ═══════════════════════════════════════════════════════════
#  Individual Check Functions
# ═══════════════════════════════════════════════════════════

def _check_warmup_compliance(warmup, send_count):
    """Check if sending volume is within daily warmup limit."""
    if not warmup.is_active:
        return PreflightCheck("Warmup Compliance", "WARN",
                              "Warmup mode is disabled. Daily limits are not enforced.")

    phase_info = _WARMUP_PHASES.get(warmup.current_phase, _WARMUP_PHASES[8])
    daily_limit = phase_info["daily_limit"]
    remaining = max(0, daily_limit - warmup.emails_sent_today)

    if send_count > remaining:
        return PreflightCheck("Warmup Compliance", "WARN",
            f"Campaign targets {send_count} recipients but only {remaining} "
            f"sends remain in today's limit ({daily_limit}). Campaign will pause at limit.",
            {"daily_limit": daily_limit, "remaining": remaining, "requested": send_count})

    return PreflightCheck("Warmup Compliance", "PASS",
        f"Volume ({send_count}) within today's limit ({remaining} remaining of {daily_limit}).")


def _check_complaint_rate():
    """Check current complaint rate against Google/Yahoo thresholds."""
    cutoff = datetime.now() - timedelta(days=14)
    total_sent = CampaignEmail.select().where(
        CampaignEmail.status == "sent",
        CampaignEmail.created_at >= cutoff
    ).count()

    if total_sent == 0:
        return PreflightCheck("Complaint Rate", "PASS", "No sends in last 14 days to measure.")

    try:
        complaints = BounceLog.select().where(
            BounceLog.event_type == "Complaint",
            BounceLog.timestamp >= cutoff
        ).count()
    except Exception:
        complaints = 0

    rate = complaints / total_sent * 100
    if rate >= 0.3:
        return PreflightCheck("Complaint Rate", "BLOCK",
            f"Complaint rate is {rate:.2f}% (hard limit: 0.3%). Stop sending and investigate.",
            {"rate": round(rate, 3), "complaints": complaints, "sent": total_sent})
    elif rate >= 0.1:
        return PreflightCheck("Complaint Rate", "WARN",
            f"Complaint rate is {rate:.2f}% (target: <0.1%).",
            {"rate": round(rate, 3)})

    return PreflightCheck("Complaint Rate", "PASS",
        f"Complaint rate is {rate:.3f}% — well under thresholds.")


def _check_bounce_rate():
    """Check current bounce rate."""
    cutoff = datetime.now() - timedelta(days=14)
    total_sent = CampaignEmail.select().where(
        CampaignEmail.status.in_(["sent", "bounced"]),
        CampaignEmail.created_at >= cutoff
    ).count()

    if total_sent == 0:
        return PreflightCheck("Bounce Rate", "PASS", "No recent sends to measure.")

    bounced = CampaignEmail.select().where(
        CampaignEmail.status == "bounced",
        CampaignEmail.created_at >= cutoff
    ).count()

    rate = bounced / total_sent * 100
    if rate >= 5:
        return PreflightCheck("Bounce Rate", "BLOCK",
            f"Bounce rate is {rate:.1f}% (threshold: 5%). Clean your list before sending.",
            {"rate": round(rate, 1), "bounced": bounced})
    elif rate >= 2:
        return PreflightCheck("Bounce Rate", "WARN",
            f"Bounce rate is {rate:.1f}% (caution at 2%).",
            {"rate": round(rate, 1)})

    return PreflightCheck("Bounce Rate", "PASS", f"Bounce rate is {rate:.1f}%.")


def _check_suppression_coverage(contacts):
    """Check that known-bad addresses are suppressed."""
    leaks = [c for c in contacts if c.suppression_reason and c.subscribed]
    if leaks:
        return PreflightCheck("Suppression Coverage", "WARN",
            f"{len(leaks)} contacts have suppression flags but are still subscribed.",
            {"leaked_count": len(leaks)})
    return PreflightCheck("Suppression Coverage", "PASS",
        "All suppressed contacts are excluded from sending.")


def _check_content_compliance(template):
    """Check template has unsubscribe link."""
    html = template.html_body.lower()
    if "{{unsubscribe_url}}" not in template.html_body and "unsubscribe" not in html:
        return PreflightCheck("Content Compliance", "BLOCK",
            "Template is missing an unsubscribe link. Add {{unsubscribe_url}} to the template.")
    return PreflightCheck("Content Compliance", "PASS",
        "Template contains an unsubscribe link.")


def _check_authentication(warmup):
    """Check SPF/DKIM/DMARC status from checklist."""
    missing = []
    if not warmup.check_spf:
        missing.append("SPF")
    if not warmup.check_dkim:
        missing.append("DKIM")
    if not warmup.check_dmarc:
        missing.append("DMARC")

    if missing:
        status = "BLOCK" if len(missing) >= 2 else "WARN"
        return PreflightCheck("Authentication", status,
            f"Missing authentication: {', '.join(missing)}. Configure these in your DNS.",
            {"missing": missing})
    return PreflightCheck("Authentication", "PASS",
        "SPF, DKIM, and DMARC all configured.")


def _check_list_quality(contacts):
    """What % of recipients have no engagement in 90+ days?"""
    if not contacts:
        return PreflightCheck("List Quality", "PASS", "No recipients.")

    cutoff_90d = datetime.now() - timedelta(days=90)
    no_engagement = 0
    for c in contacts:
        last_open = getattr(c, 'last_open_at', None)
        if not last_open or last_open < cutoff_90d:
            no_engagement += 1

    pct = no_engagement / len(contacts) * 100
    if pct >= 50:
        return PreflightCheck("List Quality", "WARN",
            f"{pct:.0f}% of recipients ({no_engagement}) have no engagement in 90+ days. "
            f"Consider sending to engaged contacts only.",
            {"no_engagement_count": no_engagement, "percentage": round(pct, 1)})

    return PreflightCheck("List Quality", "PASS",
        f"{pct:.0f}% of recipients have no recent engagement.")


def _check_domain_risk(contacts):
    """Check for recipient domains with historically high bounce rates."""
    try:
        cursor = db.execute_sql("""
            SELECT recipient_domain, COUNT(*) as cnt
            FROM bounce_log
            WHERE event_type = 'Bounce' AND recipient_domain != ''
            GROUP BY recipient_domain
            HAVING cnt >= 3
            ORDER BY cnt DESC
            LIMIT 10
        """)
        risky_domains = {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        risky_domains = {}

    if not risky_domains:
        return PreflightCheck("Domain Risk", "PASS", "No historically risky domains found.")

    risky_count = sum(1 for c in contacts
                      if c.email.split("@")[-1] in risky_domains)

    if risky_count > 0:
        domain_list = ", ".join(list(risky_domains.keys())[:5])
        return PreflightCheck("Domain Risk", "WARN",
            f"{risky_count} recipients are on domains with high bounce history: {domain_list}",
            {"risky_domains": risky_domains, "risky_recipient_count": risky_count})

    return PreflightCheck("Domain Risk", "PASS", "No recipients on historically risky domains.")


def _check_fatigue(contacts):
    """How many recipients have high fatigue scores?"""
    if not contacts:
        return PreflightCheck("Fatigue Check", "PASS", "No recipients.")

    high_fatigue = 0
    multi_send = 0
    for c in contacts:
        fatigue = getattr(c, 'fatigue_score', 0) or 0
        r7d = getattr(c, 'emails_received_7d', 0) or 0
        if fatigue >= 60:
            high_fatigue += 1
        if r7d >= 2:
            multi_send += 1

    pct_fatigued = high_fatigue / len(contacts) * 100

    if pct_fatigued >= 20:
        return PreflightCheck("Fatigue Check", "WARN",
            f"{high_fatigue} recipients ({pct_fatigued:.0f}%) have high fatigue scores. "
            f"{multi_send} received 2+ emails this week.",
            {"fatigued_count": high_fatigue, "multi_send_count": multi_send})

    return PreflightCheck("Fatigue Check", "PASS",
        f"{high_fatigue} recipients have high fatigue ({pct_fatigued:.0f}%).")


def _check_volume_safety(warmup, send_count):
    """Is total send count safe for current warmup phase?"""
    if not warmup.is_active:
        return PreflightCheck("Volume Safety", "WARN",
            "Warmup disabled. No volume limits enforced.")

    phase_info = _WARMUP_PHASES.get(warmup.current_phase, _WARMUP_PHASES[8])
    daily_limit = phase_info["daily_limit"]

    if send_count > daily_limit * 1.5:
        return PreflightCheck("Volume Safety", "WARN",
            f"Campaign targets {send_count} which exceeds 150% of today's limit ({daily_limit}). "
            f"Campaign will pause and span multiple days.",
            {"send_count": send_count, "daily_limit": daily_limit})

    return PreflightCheck("Volume Safety", "PASS",
        f"Send volume ({send_count}) is safe for current phase limit ({daily_limit}).")
