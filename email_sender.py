"""
Email Sender — Amazon SES via boto3
$0.10 per 1,000 emails. Way cheaper than Klaviyo.
"""

import boto3
from botocore.exceptions import ClientError
import os


def _get_ses_client():
    return boto3.client(
        "ses",
        region_name          = os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id    = os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key= os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def _inject_tracking_params(html_body, to_email):
    """Append ?meh_e=EMAIL to all LDAS Electronics store links so we can identify visitors from email clicks."""
    import re, urllib.parse
    encoded = urllib.parse.quote(to_email, safe='')
    store_domains = ['ldas-electronics', 'ldas.ca', 'ldaselectronics']

    def add_param(m):
        href = m.group(1)
        # Only modify links to the Shopify store
        if not any(d in href for d in store_domains):
            return m.group(0)
        sep = '&' if '?' in href else '?'
        return f'href="{href}{sep}meh_e={encoded}"'

    return re.sub(r'href="(https?://[^"]+)"', add_param, html_body)


def send_campaign_email(to_email, to_name, from_email, from_name, subject, html_body):
    """
    Send a single email via Amazon SES.
    Returns: (success: bool, error_message: str | None)
    """
    # Inject activity tracking params into store links
    html_body = _inject_tracking_params(html_body, to_email)
    try:
        client = _get_ses_client()
        response = client.send_email(
            Source      = f"{from_name} <{from_email}>",
            Destination = {"ToAddresses": [f"{to_name} <{to_email}>"]},
            Message     = {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                    "Text": {"Data": _html_to_text(html_body), "Charset": "UTF-8"},
                },
            },
            ReplyToAddresses = [from_email],
        )
        return True, None
    except ClientError as e:
        error = e.response["Error"]["Message"]
        print(f"SES Error sending to {to_email}: {error}")
        return False, error
    except Exception as e:
        print(f"Unexpected error sending to {to_email}: {str(e)}")
        return False, str(e)


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

        success, error = send_campaign_email(
            to_email   = test_email,
            to_name    = "Test",
            from_email = from_email,
            from_name  = "Email Platform Test",
            subject    = "✅ Your Email Platform is Working!",
            html_body  = """
            <div style="font-family:Arial;padding:40px;max-width:600px;margin:0 auto;">
                <h2 style="color:#6366f1;">✅ Amazon SES is Working!</h2>
                <p>Your in-house email marketing platform is successfully connected to Amazon SES.</p>
                <p><strong>What this means:</strong></p>
                <ul>
                    <li>You can send emails at $0.10 per 1,000 emails</li>
                    <li>No monthly software fees</li>
                    <li>Full control over your customer data</li>
                </ul>
                <p style="color:#888;font-size:12px;">Sent from your Email Marketing Platform</p>
            </div>
            """
        )
        return success, error

    except ClientError as e:
        return False, e.response["Error"]["Message"]
    except Exception as e:
        return False, str(e)


def _html_to_text(html):
    """Very basic HTML to plain text (strips tags)."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text
