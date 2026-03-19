"""
Google Postmaster Tools integration.
Fetches daily spam rate, reputation, auth stats for our sending domain.

Setup:
1. Enable "Gmail Postmaster Tools API" in Google Cloud Console
2. Create OAuth2 credentials (Desktop app type)
3. Download client_secret.json to /var/www/mailengine/postmaster_credentials.json
4. Run `python postmaster_tools.py --auth` once to complete OAuth flow
5. After that, nightly fetch runs automatically
"""

import os
import json
import pickle
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(APP_DIR, "postmaster_credentials.json")
TOKEN_FILE = os.path.join(APP_DIR, "postmaster_token.pickle")
DOMAIN = "news.ldaselectronics.com"
SCOPES = ["https://www.googleapis.com/auth/postmaster.readonly"]


def _get_service():
    """Build authenticated Postmaster Tools API service."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                logger.error("[Postmaster] No credentials file at %s", CREDENTIALS_FILE)
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("gmailpostmastertools", "v1beta1", credentials=creds)


def fetch_postmaster_metrics(days_back=7):
    """Fetch Postmaster Tools metrics for the last N days and store in DB."""
    from database import PostmasterMetric, init_db
    init_db()

    service = _get_service()
    if not service:
        logger.error("[Postmaster] Could not authenticate. Run: python postmaster_tools.py --auth")
        return 0

    domain_name = f"domains/{DOMAIN}"
    stored = 0

    for day_offset in range(1, days_back + 1):
        target_date = (datetime.now() - timedelta(days=day_offset)).strftime("%Y%m%d")
        date_obj = datetime.strptime(target_date, "%Y%m%d").date()

        # Skip if already fetched
        existing = PostmasterMetric.get_or_none(
            PostmasterMetric.date == date_obj,
            PostmasterMetric.domain == DOMAIN
        )
        if existing:
            continue

        try:
            # Fetch traffic stats
            stats_name = f"{domain_name}/trafficStats/{target_date}"
            stats = service.domains().trafficStats().get(name=stats_name).execute()

            metric = PostmasterMetric.create(
                date=date_obj,
                domain=DOMAIN,
                spam_rate=stats.get("userReportedSpamRatio", 0.0),
                ip_reputation=_rep_to_str(stats.get("ipReputations", [])),
                domain_reputation=_rep_to_str(stats.get("domainReputation", [])),
                spf_success_rate=stats.get("spfSuccessRatio", 0.0),
                dkim_success_rate=stats.get("dkimSuccessRatio", 0.0),
                dmarc_success_rate=stats.get("dmarcSuccessRatio", 0.0),
                inbound_encryption_rate=stats.get("inboundEncryptionRatio", 0.0),
                outbound_encryption_rate=stats.get("outboundEncryptionRatio", 0.0),
                delivery_error_rate=_compute_delivery_error_rate(stats.get("deliveryErrors", [])),
                raw_json=json.dumps(stats),
            )
            stored += 1
            logger.info("[Postmaster] Stored metrics for %s", target_date)

        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "notFound" in error_str:
                # No data for this date (low volume or too old)
                logger.debug("[Postmaster] No data for %s", target_date)
            else:
                logger.error("[Postmaster] Error fetching %s: %s", target_date, e)

    return stored


def _rep_to_str(rep_data):
    """Convert reputation data to readable string."""
    if isinstance(rep_data, str):
        return rep_data
    if isinstance(rep_data, list):
        # IP reputation comes as list of {reputation, ipCount}
        if rep_data:
            # Return the dominant reputation
            best = max(rep_data, key=lambda x: x.get("ipCount", 0)) if rep_data else {}
            return best.get("reputation", "UNKNOWN")
    return "UNKNOWN"


def _compute_delivery_error_rate(errors):
    """Compute overall delivery error rate from error breakdown."""
    if not errors:
        return 0.0
    total = sum(e.get("errorRatio", 0.0) for e in errors)
    return min(total, 1.0)


def get_latest_metrics():
    """Get the most recent PostmasterMetric for display."""
    from database import PostmasterMetric, init_db
    init_db()
    return (PostmasterMetric
            .select()
            .where(PostmasterMetric.domain == DOMAIN)
            .order_by(PostmasterMetric.date.desc())
            .first())


def get_metrics_trend(days=14):
    """Get last N days of metrics for trend charts."""
    from database import PostmasterMetric, init_db
    init_db()
    cutoff = (datetime.now() - timedelta(days=days)).date()
    return list(
        PostmasterMetric
        .select()
        .where(PostmasterMetric.domain == DOMAIN,
               PostmasterMetric.date >= cutoff)
        .order_by(PostmasterMetric.date.asc())
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if "--auth" in sys.argv:
        print("Starting OAuth flow for Google Postmaster Tools...")
        print(f"Credentials file: {CREDENTIALS_FILE}")
        svc = _get_service()
        if svc:
            print("Authentication successful! Token saved.")
            # Verify domain access
            try:
                domains = svc.domains().list().execute()
                print(f"Accessible domains: {domains.get('domains', [])}")
            except Exception as e:
                print(f"Could not list domains: {e}")
                print(f"Make sure {DOMAIN} is verified in Google Postmaster Tools UI")
        else:
            print("Authentication failed.")
    else:
        count = fetch_postmaster_metrics(days_back=14)
        print(f"Stored {count} new metric rows")
