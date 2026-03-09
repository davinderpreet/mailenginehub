"""
email_shell.py — Universal Email Shell (Header + Footer)

Every email sent by MailEngineHub gets wrapped in this shell.
- Brand header: LDAS logo on dark background (matches ldas.ca)
- CAN-SPAM compliant footer: physical address, unsubscribe link, reason text
- Responsive: 600px container, mobile stacking, dark mode support
- Table-based: Full email client compatibility

Usage:
    from email_shell import wrap_email
    full_html = wrap_email(body_html, preview_text="Inbox preview", unsubscribe_url=url)
"""

import html as html_mod

# ── Brand Constants (LDAS Electronics) ───────────────────────
BRAND_NAME = "LDAS Electronics"
BRAND_URL = "https://ldas-electronics.com"
BRAND_COLOR = "#063cff"           # LDAS primary blue
BRAND_COLOR_DARK = "#0532d4"      # Darker blue
HEADER_BG = "#0a0a0a"            # Dark header (matches ldas.ca)
TEXT_DARK = "#1a1a2e"
TEXT_MID = "#4a5568"
TEXT_LIGHT = "#718096"
BG_OUTER = "#f4f4f8"
BG_BODY = "#ffffff"
LOGO_URL = "https://ldas.ca/cdn/shop/files/Untitled_design_Logo.png?v=1758142321&width=300"

# CAN-SPAM physical mailing address (REQUIRED)
PHYSICAL_ADDRESS = "23 Westmore Dr #5b Unit #209, Etobicoke, ON M9W 0C3, Canada"


def wrap_email(body_html, preview_text="", unsubscribe_url="{{unsubscribe_url}}"):
    """
    Wrap body HTML in the universal LDAS Electronics email shell.

    Args:
        body_html: HTML table rows (<tr>...</tr>) for the email body
        preview_text: Inbox preview text (will be HTML-escaped)
        unsubscribe_url: One-click unsubscribe URL

    Returns:
        str: Complete HTML email document ready to send
    """
    safe_preheader = html_mod.escape(preview_text) if preview_text else ""

    return '''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>''' + BRAND_NAME + '''</title>
<style type="text/css">
  @media only screen and (max-width: 620px) {
    .email-container { width: 100% !important; max-width: 100% !important; }
    .stack-col { display: block !important; width: 100% !important; max-width: 100% !important; }
    .stack-col img { width: 100% !important; }
    .mobile-pad { padding: 20px 16px !important; }
    .mobile-center { text-align: center !important; }
    .mobile-full { width: 100% !important; display: block !important; }
    .hide-mobile { display: none !important; }
  }
  @media (prefers-color-scheme: dark) {
    .email-outer { background-color: #1a1a2e !important; }
    .email-body { background-color: #16162a !important; }
    .dark-invert { color: #e2e8f0 !important; }
    .dark-bg { background-color: #1e1e3a !important; }
  }
</style>
</head>
<body style="margin:0;padding:0;background:''' + BG_OUTER + ''';font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<!-- Preheader (inbox preview text) -->
<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:''' + BG_OUTER + ''';">
  ''' + safe_preheader + '''
  &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847; &#847;
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="email-outer" style="background:''' + BG_OUTER + ''';padding:0;">
<tr><td align="center" style="padding:24px 8px;">

  <!-- Container -->
  <table role="presentation" class="email-container" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:''' + BG_BODY + ''';border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

    <!-- Header: LDAS Brand — Dark background with white logo -->
    <tr>
      <td style="background:''' + HEADER_BG + ''';padding:22px 30px;text-align:center;">
        <a href="''' + BRAND_URL + '''" style="text-decoration:none;">
          <img src="''' + LOGO_URL + '''" alt="''' + BRAND_NAME + '''" width="140" style="display:inline-block;max-width:140px;height:auto;" />
        </a>
      </td>
    </tr>

    <!-- Body Content -->
    ''' + body_html + '''

    <!-- Footer: CAN-SPAM Compliant -->
    <tr>
      <td style="background:#f8f8fc;padding:28px 30px;text-align:center;border-top:1px solid #eeeef2;">
        <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:''' + TEXT_MID + ''';">
          ''' + BRAND_NAME + '''
        </p>
        <p style="margin:0 0 10px;font-size:12px;color:''' + TEXT_LIGHT + ''';">
          ''' + html_mod.escape(PHYSICAL_ADDRESS) + '''
        </p>
        <p style="margin:0 0 10px;font-size:12px;color:''' + TEXT_LIGHT + ''';">
          <a href="''' + BRAND_URL + '''" style="color:''' + BRAND_COLOR + ''';text-decoration:none;">Shop</a>
          &nbsp;&bull;&nbsp;
          <a href="''' + unsubscribe_url + '''" style="color:''' + TEXT_LIGHT + ''';text-decoration:underline;">Unsubscribe</a>
        </p>
        <p style="margin:0;font-size:11px;color:#a0aec0;">
          You received this email because you subscribed at ldas.ca.
        </p>
      </td>
    </tr>

  </table>

</td></tr>
</table>
</body>
</html>'''
