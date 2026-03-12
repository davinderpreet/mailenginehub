"""Diagnostic: Check every identified visitor in last 24h for system cracks."""
import sys, os
sys.path.insert(0, "/var/www/mailengine")
os.chdir("/var/www/mailengine")
from dotenv import load_dotenv
load_dotenv()
from database import *
init_db()
from datetime import datetime, timedelta
from peewee import fn
import json

cutoff = datetime.now() - timedelta(hours=24)
now = datetime.now()

# Get all emails with activity in last 24h
emails_rows = list(CustomerActivity
    .select(CustomerActivity.email)
    .where(CustomerActivity.occurred_at >= cutoff,
           CustomerActivity.email.is_null(False),
           fn.LENGTH(CustomerActivity.email) > 0)
    .group_by(CustomerActivity.email)
    .order_by(fn.MAX(CustomerActivity.occurred_at).desc()))

# Filter out obvious junk (typos with extra chars)
emails = [r.email for r in emails_rows if "@" in r.email and "." in r.email.split("@")[1]]

print("=" * 80)
print("SYSTEM DIAGNOSTIC - %d identified visitors in last 24h" % len(emails))
print("Run at: %s" % now.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 80)

results = []

for email in emails:
    issues = []
    info = []

    # Contact
    try:
        c = Contact.get(Contact.email == email)
    except Contact.DoesNotExist:
        c = None

    # Activity in last 24h
    activities = list(CustomerActivity.select()
        .where(CustomerActivity.email == email, CustomerActivity.occurred_at >= cutoff)
        .order_by(CustomerActivity.occurred_at.asc()))

    event_types = [a.event_type for a in activities]
    has_popup = "popup_subscribe" in event_types
    has_product_view = "viewed_product" in event_types
    has_cart_view = "viewed_cart" in event_types
    has_email_activity = "email_activity" in event_types
    product_view_count = event_types.count("viewed_product")
    cart_view_count = event_types.count("viewed_cart")

    # Contact status
    if not c:
        issues.append("NO CONTACT RECORD - activity tracked but no contact created")
    else:
        if not c.subscribed:
            info.append("unsubscribed")

        # FlowEnrollment
        enrollments = list(FlowEnrollment.select().where(FlowEnrollment.contact == c))
        enrollment_flows = []
        for e in enrollments:
            f = Flow.get_by_id(e.flow_id)
            enrollment_flows.append((f.name, f.trigger_type, e.status))

        # Check: should have Welcome Series?
        if has_popup and c.subscribed:
            has_welcome = any(ft == "contact_created" for _, ft, _ in enrollment_flows)
            if not has_welcome:
                issues.append("NO WELCOME EMAIL - subscribed via popup but never enrolled in Welcome Series")

        # Check: should have Browse Abandonment?
        if product_view_count >= 2 and c.subscribed:
            has_browse = any(ft == "browse_abandonment" for _, ft, _ in enrollment_flows)
            pending_browse = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == "browse_abandonment").count()
            if not has_browse and pending_browse == 0:
                issues.append("NO BROWSE TRIGGER - viewed %d products but no browse abandonment detected" % product_view_count)
            elif pending_browse > 0 and not has_browse:
                pt = PendingTrigger.get(PendingTrigger.email == email, PendingTrigger.trigger_type == "browse_abandonment")
                if pt.status == "pending":
                    info.append("browse trigger pending (not yet consumed)")
                elif pt.status in ("skipped_stale", "skipped_duplicate", "skipped_no_flow", "skipped", "failed"):
                    issues.append("BROWSE TRIGGER %s - detected but skipped (%s)" % (pt.status.upper(), pt.status))
                else:
                    info.append("browse trigger %s" % pt.status)
            elif has_browse:
                enr = [(fn_name, ft, st) for fn_name, ft, st in enrollment_flows if ft == "browse_abandonment"][0]
                info.append("browse flow enrolled (%s)" % enr[2])

        # Check: should have Cart Abandonment?
        if cart_view_count >= 1 and c.subscribed:
            has_cart_flow = any(ft == "cart_abandonment" for _, ft, _ in enrollment_flows)
            pending_cart = PendingTrigger.select().where(
                PendingTrigger.email == email,
                PendingTrigger.trigger_type == "cart_abandonment").count()
            if not has_cart_flow and pending_cart == 0:
                # Check if they have an abandoned_checkout event (handled separately)
                has_abandoned_checkout = CustomerActivity.select().where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type == "abandoned_checkout"
                ).count()
                # Check if they completed purchase since cart view
                latest_cv = CustomerActivity.select().where(
                    CustomerActivity.email == email,
                    CustomerActivity.event_type == "viewed_cart",
                    CustomerActivity.occurred_at >= cutoff
                ).order_by(CustomerActivity.occurred_at.desc()).first()
                purchased_after = False
                if latest_cv:
                    purchased_after = CustomerActivity.select().where(
                        CustomerActivity.email == email,
                        CustomerActivity.event_type.in_(["completed_checkout", "placed_order"]),
                        CustomerActivity.occurred_at >= latest_cv.occurred_at
                    ).count() > 0

                if has_abandoned_checkout:
                    info.append("cart view + abandoned_checkout present (handled by checkout detection)")
                elif purchased_after:
                    info.append("cart view but purchased since - correctly skipped")
                else:
                    issues.append("NO CART TRIGGER - viewed cart %d times but no cart abandonment detected" % cart_view_count)
            elif pending_cart > 0 and not has_cart_flow:
                pt = PendingTrigger.get(PendingTrigger.email == email, PendingTrigger.trigger_type == "cart_abandonment")
                info.append("cart trigger %s" % pt.status)
            elif has_cart_flow:
                enr = [(fn_name, ft, st) for fn_name, ft, st in enrollment_flows if ft == "cart_abandonment"][0]
                info.append("cart flow enrolled (%s)" % enr[2])

        # ActionLedger
        ledger = list(ActionLedger.select().where(ActionLedger.email == email))
        if ledger:
            for l in ledger:
                info.append("ledger: %s (%s)" % (l.status, l.reason_code or "ok"))

        # DeliveryQueue
        dq = list(DeliveryQueue.select().where(DeliveryQueue.email == email))
        if dq:
            for d in dq:
                info.append("queue: %s (%s)" % (d.status, d.queue_type))

        # Enrollment summary
        if enrollment_flows:
            for fn_name, ft, st in enrollment_flows:
                info.append("enrolled: %s (%s)" % (fn_name, st))

        # Check if any email was ever actually sent
        flow_emails = list(FlowEmail.select().where(FlowEmail.contact == c))
        sent_count = sum(1 for fe in flow_emails if fe.status == "sent")
        shadowed_count = sum(1 for fe in flow_emails if fe.status == "shadowed")
        if sent_count > 0:
            info.append("%d emails sent" % sent_count)
        if shadowed_count > 0:
            info.append("%d emails shadowed (not actually sent)" % shadowed_count)
        if not flow_emails and not dq and not ledger:
            if enrollments:
                info.append("enrolled but NO emails sent/queued yet")

    # Build event summary
    event_counts = {}
    for et in event_types:
        event_counts[et] = event_counts.get(et, 0) + 1
    event_str = ", ".join(["%s:%d" % (k, v) for k, v in event_counts.items()])

    results.append({
        "email": email,
        "events": len(activities),
        "event_str": event_str,
        "issues": issues,
        "info": info,
        "source": c.source if c else "no contact",
        "subscribed": c.subscribed if c else False,
    })

# Print results grouped by severity
critical = [r for r in results if any("NO WELCOME" in i for i in r["issues"])]
cart_gap = [r for r in results if any("NO CART" in i for i in r["issues"])]
browse_gap = [r for r in results if any("NO BROWSE" in i for i in r["issues"])]
no_contact = [r for r in results if any("NO CONTACT" in i for i in r["issues"])]
clean = [r for r in results if not r["issues"]]

print()
print("=" * 80)
print("CRACK #1: NO WELCOME EMAIL (%d people)" % len(critical))
print("=" * 80)
for r in critical:
    print()
    print("  %s (%d events: %s)" % (r["email"], r["events"], r["event_str"]))
    for i in r["issues"]:
        print("    [ISSUE] %s" % i)
    for i in r["info"]:
        print("    [info]  %s" % i)

print()
print("=" * 80)
print("CRACK #2: CART PAGE VISITED BUT NO CART TRIGGER (%d people)" % len(cart_gap))
print("=" * 80)
for r in cart_gap:
    print()
    print("  %s (%d events: %s)" % (r["email"], r["events"], r["event_str"]))
    for i in r["issues"]:
        print("    [ISSUE] %s" % i)
    for i in r["info"]:
        print("    [info]  %s" % i)

print()
print("=" * 80)
print("CRACK #3: BROWSED PRODUCTS BUT NO TRIGGER (%d people)" % len(browse_gap))
print("=" * 80)
for r in browse_gap:
    print()
    print("  %s (%d events: %s)" % (r["email"], r["events"], r["event_str"]))
    for i in r["issues"]:
        print("    [ISSUE] %s" % i)
    for i in r["info"]:
        print("    [info]  %s" % i)

print()
print("=" * 80)
print("NO CONTACT RECORD (%d people)" % len(no_contact))
print("=" * 80)
for r in no_contact:
    print()
    print("  %s (%d events: %s)" % (r["email"], r["events"], r["event_str"]))
    for i in r["issues"]:
        print("    [ISSUE] %s" % i)

print()
print("=" * 80)
print("WORKING CORRECTLY (%d people)" % len(clean))
print("=" * 80)
for r in clean:
    print()
    print("  %s (%d events: %s)" % (r["email"], r["events"], r["event_str"]))
    for i in r["info"]:
        print("    [info]  %s" % i)

print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print("Total identified visitors (24h):  %d" % len(results))
print("Missing Welcome Email:            %d" % len(critical))
print("Cart page but no cart trigger:    %d" % len(cart_gap))
print("Browsed but no browse trigger:    %d" % len(browse_gap))
print("No contact record:                %d" % len(no_contact))
print("Working correctly:                %d" % len(clean))
