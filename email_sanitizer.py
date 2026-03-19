"""
Email Sanitizer — centralized email validation and correction.

Used by: popup subscribe, identity resolution, Shopify sync, CSV import,
         bulk contact cleanup.

sanitize_email(email) -> {
    "email":    str,       # corrected email (typos fixed)
    "valid":    bool,      # False = reject / suppress
    "reason":   str,       # "valid" | "invalid_syntax" | "disposable_domain" | "no_mx_record" | "empty"
    "warnings": [str],     # non-blocking: "role_based", "typo_corrected:gmial.com→gmail.com"
}
"""
import re

# ── Common typo corrections (domain misspellings) ──────────────────
_TYPO_CORRECTIONS = {
    # Gmail
    "gmial.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.cm": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.om": "gmail.com",
    "gmaill.com": "gmail.com",
    "gmil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmail.cim": "gmail.com",
    "gmail.vom": "gmail.com",
    "gmail.comm": "gmail.com",
    "gmaik.com": "gmail.com",
    "gmsil.com": "gmail.com",
    "gmailcom": "gmail.com",
    # Yahoo
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "yahoo.co": "yahoo.com",
    "yahoo.cm": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "yahoo.om": "yahoo.com",
    "yahoo.comm": "yahoo.com",
    "yhaoo.com": "yahoo.com",
    "yhoo.com": "yahoo.com",
    "yaoo.com": "yahoo.com",
    "yahho.com": "yahoo.com",
    # Hotmail / Outlook
    "hotmal.com": "hotmail.com",
    "hotmial.com": "hotmail.com",
    "hotmail.co": "hotmail.com",
    "hotmail.con": "hotmail.com",
    "hotamil.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "hotmaill.com": "hotmail.com",
    "hotmil.com": "hotmail.com",
    "hotmail.cm": "hotmail.com",
    "outlok.com": "outlook.com",
    "outllook.com": "outlook.com",
    "outlook.co": "outlook.com",
    "outlook.con": "outlook.com",
    "outlool.com": "outlook.com",
    # iCloud
    "icloud.co": "icloud.com",
    "icloud.con": "icloud.com",
    "icoud.com": "icloud.com",
    "iclould.com": "icloud.com",
    # AOL
    "aol.co": "aol.com",
    "aol.con": "aol.com",
    # Common TLD typos for any domain
    # (handled by _fix_tld_typo below)
}

# ── Disposable / temporary email domains ────────────────────────────
_DISPOSABLE_DOMAINS = {
    # Original list
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
    # Extended list
    "mailnator.com", "guerrillamail.de", "guerrillamail.biz", "mailforspam.com",
    "spamfree24.org", "trashymail.com", "bugmenot.com", "devnullmail.com",
    "dodgeit.com", "e4ward.com", "emailwarden.com", "enterto.com",
    "firemailbox.club", "getairmail.com", "guerrillamail.com",
    "imgof.com", "imstations.com", "incognitomail.org", "inboxalias.com",
    "mailblocks.com", "mailcatch.com", "mailchop.com", "mailmoat.com",
    "mailshell.com", "mailsiphon.com", "mailzilla.com", "nomail.xl.cx",
    "nowmymail.com", "pookmail.com", "proxymail.eu", "rcpt.at",
    "reallymymail.com", "recode.me", "regbypass.com", "rmqkr.net",
    "royal.net", "sharklasers.com", "shieldedmail.com", "sigmund.no",
    "slaskpost.se", "slipry.net", "spambob.net", "spambox.us",
    "spamcannon.com", "spamcannon.net", "spamcowboy.com", "spamherelots.com",
    "spamhole.com", "spaml.de", "spamspot.com", "spamthis.co.uk",
    "speed.1s.fr", "tempinbox.com", "thankyou2010.com", "thisisnotmyrealemail.com",
    "throwam.com", "tittbit.in", "tradermail.info", "trash2009.com",
    "turual.com", "twinmail.de", "uggsrock.com", "upliftnow.com",
    "venompen.com", "veryreallytempmail.com", "viditag.com", "viewcastmedia.com",
    "watchfull.net", "webemail.me", "wig.waw.pl", "wuzup.net",
    "xagloo.com", "yapped.net", "yomail.info", "zippymail.info",
    "tempmailo.com", "emailondeck.com", "mailtemp.info", "burnermail.io",
    "guerrillamailblock.com", "mailhazard.com", "mailscrap.com",
}

# ── Role-based local parts (low engagement, often shared inboxes) ──
_ROLE_BASED_PREFIXES = {
    "info", "admin", "administrator", "webmaster", "postmaster",
    "hostmaster", "noreply", "no-reply", "no.reply",
    "support", "help", "abuse", "sales", "marketing",
    "contact", "enquiry", "enquiries", "office", "billing",
    "accounts", "hr", "jobs", "careers", "press", "media",
    "legal", "compliance", "security", "mailer-daemon",
    "newsletter", "unsubscribe", "feedback", "service",
}

_EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def sanitize_email(email):
    """
    Validate and sanitize an email address.

    Returns dict:
        email:    str   — cleaned/corrected email
        valid:    bool  — False means reject or suppress
        reason:   str   — "valid", "invalid_syntax", "disposable_domain", "no_mx_record", "empty"
        warnings: list  — non-blocking warnings like "role_based", "typo_corrected:old→new"
    """
    warnings = []

    # ── Normalize ──
    if not email:
        return {"email": "", "valid": False, "reason": "empty", "warnings": []}

    email = email.strip().lower()

    # Strip mailto: prefix if present
    if email.startswith("mailto:"):
        email = email[7:]

    # Remove surrounding angle brackets or quotes
    email = email.strip("<>\"' ")

    # ── Syntax check ──
    if not _EMAIL_REGEX.match(email):
        return {"email": email, "valid": False, "reason": "invalid_syntax", "warnings": []}

    local, domain = email.rsplit("@", 1)

    # ── Typo correction ──
    original_domain = domain
    if domain in _TYPO_CORRECTIONS:
        domain = _TYPO_CORRECTIONS[domain]
        email = "%s@%s" % (local, domain)
        warnings.append("typo_corrected:%s→%s" % (original_domain, domain))

    # ── Disposable domain check ──
    if domain in _DISPOSABLE_DOMAINS:
        return {"email": email, "valid": False, "reason": "disposable_domain", "warnings": warnings}

    # ── Role-based detection (warning, not rejection) ──
    if local in _ROLE_BASED_PREFIXES:
        warnings.append("role_based")

    # ── MX record check (verify domain accepts email) ──
    try:
        import dns.resolver
        try:
            dns.resolver.resolve(domain, "MX")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            return {"email": email, "valid": False, "reason": "no_mx_record", "warnings": warnings}
        except dns.resolver.LifetimeTimeout:
            pass  # Timeout = assume valid, don't block on slow DNS
        except Exception:
            pass  # Any other DNS error = assume valid
    except ImportError:
        pass  # dnspython not installed — skip MX check

    return {"email": email, "valid": True, "reason": "valid", "warnings": warnings}


def bulk_sanitize_contacts():
    """
    Scan all contacts, validate emails, suppress invalid ones.
    Returns summary dict.
    """
    from database import Contact, SuppressionEntry
    from datetime import datetime

    stats = {
        "total_checked": 0,
        "valid": 0,
        "invalid": 0,
        "already_suppressed": 0,
        "typos_found": 0,
        "role_based": 0,
        "details": {
            "invalid_syntax": 0,
            "disposable_domain": 0,
            "no_mx_record": 0,
        },
    }

    for contact in Contact.select():
        stats["total_checked"] += 1

        # Skip already suppressed
        if contact.suppression_reason:
            stats["already_suppressed"] += 1
            continue

        result = sanitize_email(contact.email)

        if not result["valid"]:
            stats["invalid"] += 1
            reason = result["reason"]
            if reason in stats["details"]:
                stats["details"][reason] += 1

            # Suppress the contact
            contact.suppression_reason = "invalid_email"
            contact.suppression_source = "sanitizer"
            contact.subscribed = False
            contact.save()

            # Add to global suppression list
            SuppressionEntry.get_or_create(
                email=contact.email,
                defaults={
                    "reason": "invalid_email",
                    "source": "sanitizer",
                    "detail": result["reason"],
                }
            )
        else:
            stats["valid"] += 1
            for w in result["warnings"]:
                if w.startswith("typo_corrected:"):
                    stats["typos_found"] += 1
                elif w == "role_based":
                    stats["role_based"] += 1

    return stats
