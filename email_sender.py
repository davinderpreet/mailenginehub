"""
Email Sender — Amazon SES via boto3
MIME-based sending with RFC 8058 compliance, deliverability headers,
and suppression list enforcement.

$0.10 per 1,000 emails. Way cheaper than Klaviyo.
"""

import boto3
from botocore.exceptions import ClientError
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from token_utils import create_token


# ── SES Configuration Set name (created in AWS Console / CLI) ────
SES_CONFIG_SET = "mailenginehub-production"


def _get_ses_client():
    return boto3.client(
        "ses",
        region_name           = os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id     = os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def _inject_tracking_params(html_body, to_email):
    """Append signed tracking token to all LDAS Electronics store links."""
    token = create_token({"p": "click", "e": to_email})
    store_domains = ['ldas-electronics', 'ldas.ca', 'ldaselectronics']

    def add_param(m):
        href = m.group(1)
        if not any(d in href for d in store_domains):
            return m.group(0)
        sep = '&' if '?' in href else '?'
        return f'href="{href}{sep}meh_t={token}"'

    return re.sub(r'href="(https?://[^"]+)"', add_param, html_body)


def _check_suppression(email):
    """Check if an email is in the suppression list. Returns (suppressed: bool, reason: str)."""
    try:
        from database import SuppressionEntry
        entry = SuppressionEntry.get_or_none(SuppressionEntry.email == email.lower().strip())
        if entry:
            return True, f"suppressed:{entry.reason}"
        return False, None
    except Exception:
        # If table doesn't exist yet (pre-migration), allow sending
        return False, None


def send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body,
                        unsubscribe_url=None, campaign_id=None):
    """
    Send a single email via Amazon SES using raw MIME for full header control.
    Includes RFC 8058 one-click unsubscribe, Feedback-ID, and Precedence headers.

    Returns: (success: bool, error_message: str | None)
    """
    # ── Suppression check ──────────────────────────────────────────
    suppressed, reason = _check_suppression(to_email)
    if suppressed:
        return False, reason, ""

    # ── Inject activity tracking params into store links ───────────
    html_body = _inject_tracking_params(html_body, to_email)

    # ── Build the unsubscribe URL if not provided ──────────────────
    if not unsubscribe_url:
        unsubscribe_url = f"https://mailenginehub.com/contacts/unsubscribe/{to_email}"

    # ── Build MIME message ─────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = formataddr((from_name, from_email))
    msg["To"]      = formataddr((to_name or to_email, to_email))
    msg["Reply-To"] = from_email

    # ── Deliverability headers ─────────────────────────────────────
    # RFC 8058: One-click unsubscribe (MANDATORY for Gmail/Yahoo 2024+)
    msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Google Postmaster Tools tracking
    cid = campaign_id if campaign_id else "general"
    msg["Feedback-ID"] = f"{cid}:mailenginehub:mailenginehub"

    # Identify as bulk marketing email
    msg["Precedence"] = "bulk"
    msg["X-Mailer"] = "MailEngineHub/1.0"

    # ── Attach plain text + HTML parts ─────────────────────────────
    plain_text = _html_to_text(html_body)
    part_text = MIMEText(plain_text, "plain", "utf-8")
    part_html = MIMEText(html_body, "html", "utf-8")
    msg.attach(part_text)
    msg.attach(part_html)

    # ── Send via SES raw API ───────────────────────────────────────
    try:
        client = _get_ses_client()
        send_args = {
            "Source": formataddr((from_name, from_email)),
            "Destinations": [to_email],
            "RawMessage": {"Data": msg.as_string()},
        }
        # Attach configuration set for event tracking
        send_args["ConfigurationSetName"] = SES_CONFIG_SET

        # SES message tags for bounce/complaint attribution
        tags = []
        if campaign_id:
            tags.append({"Name": "campaign_id", "Value": str(campaign_id)})
        try:
            template_id = campaign_id  # Will be overridden by caller if available
        except Exception:
            pass
        if tags:
            send_args["Tags"] = tags

        response = client.send_raw_email(**send_args)
        message_id = response.get("MessageId", "")
        return True, None, message_id
    except ClientError as e:
        error = e.response["Error"]["Message"]
        # If config set doesn't exist yet, retry without it
        if "ConfigurationSetDoesNotExist" in str(e):
            try:
                del send_args["ConfigurationSetName"]
                response = client.send_raw_email(**send_args)
                message_id = response.get("MessageId", "")
                return True, None, message_id
            except ClientError as e2:
                error = e2.response["Error"]["Message"]
                print(f"SES Error sending to {to_email}: {error}")
                return False, error, ""
        print(f"SES Error sending to {to_email}: {error}")
        return False, error, ""
    except Exception as e:
        print(f"Unexpected error sending to {to_email}: {str(e)}")
        return False, str(e), ""


def test_ses_connection(test_email):
    """Send a test email to verify SES is configured correctly."""
    try:
        client = _get_ses_client()

        # Verify credentials by listing verified identities
        identities = client.list_verified_email_addresses()
        verified   = identities.get("VerifiedEmailAddresses", [])

        if not verified:
            return False, "No verified email addresses found in SES. Please verify your sending email in AWS SES console."

        from_email = os.getenv("DEFAULT_FROM_EMAIL") or verified[0]

        success, error, _msg_id = send_campaign_email(
            to_email   = test_email,
            to_name    = "Test",
            from_email = from_email,
            from_name  = "Email Platform Test",
            subject    = "Your Email Platform is Working!",
            html_body  = """
            <div style="font-family:Arial;padding:40px;max-width:600px;margin:0 auto;">
                <h2 style="color:#7c3aed;">Your Email Platform is Working!</h2>
                <p>Your in-house email marketing platform is successfully connected to Amazon SES.</p>
                <p><strong>What this means:</strong></p>
                <ul>
                    <li>You can send emails at $0.10 per 1,000 emails</li>
                    <li>No monthly software fees</li>
                    <li>Full control over your customer data</li>
                </ul>
                <p style="color:#888;font-size:12px;">Sent from MailEngineHub</p>
            </div>
            """,
            campaign_id = 0
        )
        return success, error

    except ClientError as e:
        return False, e.response["Error"]["Message"]
    except Exception as e:
        return False, str(e)


def _html_to_text(html):
    """
    Convert HTML email to clean plain text for the text/plain MIME part.
    Preserves links, lists, and paragraph structure instead of just stripping tags.
    Better plain text = better deliverability score.
    """
    text = html

    # Remove <head>, <style>, <script> blocks entirely
    text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <br> and <br/> to newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Convert block-level elements to newlines
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h[1-6][^>]*>', '\n', text, flags=re.IGNORECASE)

    # Convert list items to bullet points
    text = re.sub(r'<li[^>]*>', '\n  - ', text, flags=re.IGNORECASE)

    # Convert links: <a href="url">text</a> → text (url)
    def link_replace(m):
        href = m.group(1)
        link_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if link_text and link_text.lower() != href.lower():
            return f"{link_text} ({href})"
        return href
    text = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', link_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <img> with alt text
    text = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*/?>', r'[\1]', text, flags=re.IGNORECASE)

    # Convert horizontal rules
    text = re.sub(r'<hr[^>]*/?>', '\n' + '-' * 40 + '\n', text, flags=re.IGNORECASE)

    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode HTML entities
    import html as html_module
    text = html_module.unescape(text)

    # Clean up whitespace
    text = re.sub(r'[ \t]+', ' ', text)             # collapse horizontal whitespace
    text = re.sub(r'\n[ \t]+', '\n', text)           # strip leading whitespace from lines
    text = re.sub(r'[ \t]+\n', '\n', text)           # strip trailing whitespace from lines
    text = re.sub(r'\n{3,}', '\n\n', text)           # max 2 consecutive newlines
    text = text.strip()

    return text
