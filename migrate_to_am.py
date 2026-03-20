"""
migrate_to_am.py — One-time migration: enroll all non-flow contacts into AI Account Manager.

Ensures every subscribed contact is under one of two umbrellas:
  1. Flows (active/paused enrollment) — new customers, automated sequences
  2. AI Account Manager (ContactStrategy.enrolled) — everything else

Run once: python migrate_to_am.py
Safe to re-run — skips already-enrolled contacts.
"""

import os, sys, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (Contact, ContactStrategy, FlowEnrollment, FlowEmail,
                      SuppressionEntry, ContactScore, init_db)

init_db()


def migrate():
    print("=" * 50)
    print("  Migrate all non-flow contacts to AI Account Manager")
    print("=" * 50)

    # Get all subscribed contacts
    contacts = Contact.select().where(Contact.subscribed == True)
    total = contacts.count()
    print(f"\nTotal subscribed contacts: {total}")

    enrolled = 0
    skipped_in_flow = 0
    skipped_already_am = 0
    skipped_suppressed = 0
    skipped_sunset = 0

    for contact in contacts:
        # Skip suppressed
        if SuppressionEntry.select().where(SuppressionEntry.email == contact.email).exists():
            skipped_suppressed += 1
            continue

        # Skip high sunset score (disengaged)
        cscore = ContactScore.get_or_none(ContactScore.contact == contact)
        if cscore and cscore.sunset_score and cscore.sunset_score >= 85:
            skipped_sunset += 1
            continue

        # Check if in active/paused flow
        active_flows = (FlowEnrollment.select()
                        .where(FlowEnrollment.contact == contact,
                               FlowEnrollment.status.in_(["active", "paused"]))
                        .count())
        if active_flows > 0:
            skipped_in_flow += 1
            # Make sure flow tags are set
            for fe in FlowEnrollment.select().where(
                    FlowEnrollment.contact == contact,
                    FlowEnrollment.status.in_(["active", "paused"])):
                try:
                    from account_manager import add_flow_tag
                    add_flow_tag(contact, fe.flow.name, fe.status)
                except Exception:
                    pass
            continue

        # Check if already in AM
        existing = ContactStrategy.get_or_none(ContactStrategy.contact == contact)
        if existing and existing.enrolled:
            skipped_already_am += 1
            continue

        # Build flow history for this contact
        flow_history = []
        completed_flows = (FlowEnrollment.select()
                           .where(FlowEnrollment.contact == contact,
                                  FlowEnrollment.status.in_(["completed", "cancelled"]))
                           .order_by(FlowEnrollment.enrolled_at.desc())
                           .limit(10))
        for fe in completed_flows:
            try:
                flow_name = fe.flow.name
            except Exception:
                flow_name = "Unknown"
            emails_in_flow = (FlowEmail.select()
                              .where(FlowEmail.enrollment == fe,
                                     FlowEmail.status == "sent")
                              .count())
            flow_history.append({
                "flow": flow_name,
                "status": fe.status,
                "enrolled": fe.enrolled_at.strftime("%Y-%m-%d") if fe.enrolled_at else "",
                "emails_sent": emails_in_flow
            })
            # Tag completed/cancelled flows
            try:
                from account_manager import add_flow_tag
                add_flow_tag(contact, flow_name, fe.status)
            except Exception:
                pass

        # Enroll in AM
        cs, created = ContactStrategy.get_or_create(
            contact=contact,
            defaults={
                "enrolled": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
        )
        if not created:
            cs.enrolled = True
            cs.updated_at = datetime.now()

        # Store flow graduation context
        strategy = {}
        if flow_history:
            strategy["flow_graduation"] = {
                "graduated_at": datetime.now().strftime("%Y-%m-%d"),
                "completed_flows": flow_history
            }
        cs.strategy_json = json.dumps(strategy)
        cs.save()

        # Tag as AM managed
        existing_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
        if "am_managed" not in existing_tags:
            existing_tags.append("am_managed")
            contact.tags = ",".join(existing_tags)
            contact.save()

        enrolled += 1

    print(f"\n  Enrolled in AM:        {enrolled}")
    print(f"  Already in AM:         {skipped_already_am}")
    print(f"  Still in flows:        {skipped_in_flow}")
    print(f"  Suppressed:            {skipped_suppressed}")
    print(f"  Sunset (disengaged):   {skipped_sunset}")
    print(f"\n  Every subscribed contact is now in Flows or AM.")
    print("=" * 50)


if __name__ == "__main__":
    migrate()
