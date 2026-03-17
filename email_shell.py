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
BRAND_URL = "https://ldas.ca"
BRAND_COLOR = "#063cff"           # LDAS primary blue
BRAND_COLOR_DARK = "#0532d4"      # Darker blue
HEADER_BG = "#080a16"            # Deep dark header
TEXT_DARK = "#1a1a2e"
TEXT_MID = "#888888"
TEXT_LIGHT = "#555555"
BG_OUTER = "#060810"             # Near-black outer
BG_BODY = "#0d1020"              # Deep navy body — unified with block_registry DESIGN
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
    .email-outer { background-color: #060810 !important; }
    .email-body { background-color: #0d1020 !important; }
    .dark-invert { color: #e2e8f0 !important; }
    .dark-bg { background-color: #0d1020 !important; }
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
<tr><td align="center" style="padding:0;">

  <!-- Container — unified dark gradient, edge-to-edge -->
  <table role="presentation" class="email-container" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#0d1020;">

    <!-- Header: LDAS Brand — subtle blue ambient glow behind logo -->
    <tr>
      <td style="background:''' + HEADER_BG + ''';padding:32px 30px 28px;text-align:center;background-image:radial-gradient(ellipse at 50% 140%, rgba(6,60,255,0.12) 0%, transparent 65%);">
        <a href="''' + BRAND_URL + '''" style="text-decoration:none;">
          <img src="''' + LOGO_URL + '''" alt="''' + BRAND_NAME + '''" width="140" style="display:inline-block;max-width:140px;height:auto;" />
        </a>
      </td>
    </tr>

    <!-- Body Content -->
    ''' + body_html + '''

    <!-- Footer: CAN-SPAM Compliant — Dark theme -->
    <tr>
      <td style="background:#0a0d1a;padding:32px 30px 28px;text-align:center;border-top:1px solid rgba(255,255,255,0.05);">
        <!-- Social links row -->
        <p style="margin:0 0 16px;font-size:12px;">
          <a href="https://www.instagram.com/ldas.ca/" style="color:#888888;text-decoration:none;">Instagram</a>
          &nbsp;<span style="color:#333333;">&nbsp;|&nbsp;</span>&nbsp;
          <a href="https://www.facebook.com/ldas.ca/" style="color:#888888;text-decoration:none;">Facebook</a>
          &nbsp;<span style="color:#333333;">&nbsp;|&nbsp;</span>&nbsp;
          <a href="https://www.youtube.com/@ldas_electronics" style="color:#888888;text-decoration:none;">YouTube</a>
        </p>
        <p style="margin:0 0 6px;font-size:12px;font-weight:600;color:''' + TEXT_MID + ''';">
          ''' + BRAND_NAME + '''
        </p>
        <p style="margin:0 0 10px;font-size:11px;color:''' + TEXT_LIGHT + ''';">
          ''' + html_mod.escape(PHYSICAL_ADDRESS) + '''
        </p>
        <p style="margin:0 0 10px;font-size:11px;color:''' + TEXT_LIGHT + ''';">
          <a href="''' + BRAND_URL + '''" style="color:''' + BRAND_COLOR + ''';text-decoration:none;">Shop ldas.ca</a>
          &nbsp;<span style="color:#333333;">&bull;</span>&nbsp;
          <a href="''' + unsubscribe_url + '''" style="color:#555555;text-decoration:underline;">Unsubscribe</a>
        </p>
        <p style="margin:0;font-size:10px;color:#333333;">
          You received this email because you subscribed at ldas.ca.
        </p>
      </td>
    </tr>

  </table>

</td></tr>
</table>
</body>
</html>'''
