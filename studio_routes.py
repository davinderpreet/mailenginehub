"""
studio_routes.py -- Flask Blueprint for the AI Email Template Studio.

Routes for knowledge base management, AI template generation,
job/candidate review, model configuration, and preview.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from database import (
    KnowledgeEntry, AIModelConfig, StudioJob, TemplateCandidate,
    TemplatePerformance, EmailTemplate, ScrapeSource, ScrapeLog, RejectionLog, db
)
from template_studio import TemplateStudio
from condition_engine import TEMPLATE_FAMILIES
import json
import threading
from datetime import datetime

studio_bp = Blueprint("studio", __name__, url_prefix="/studio")


# ─────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────

@studio_bp.route("")
def dashboard():
    """Studio dashboard — intelligence score, recent jobs, quick stats."""
    studio = TemplateStudio()
    score_data = studio.get_intelligence_score()

    recent_jobs = (
        StudioJob
        .select()
        .order_by(StudioJob.created_at.desc())
        .limit(10)
    )

    stats = {
        "total_jobs": StudioJob.select().count(),
        "total_candidates": TemplateCandidate.select().count(),
        "approved": TemplateCandidate.select().where(
            TemplateCandidate.status == "approved"
        ).count(),
        "knowledge_entries": KnowledgeEntry.select().where(
            KnowledgeEntry.is_active == True  # noqa: E712
        ).count(),
    }

    pending_count = KnowledgeEntry.select().where(
        (KnowledgeEntry.is_active == False) &  # noqa: E712
        (KnowledgeEntry.is_rejected == False)  # noqa: E712
    ).count()

    # Source health: any active sources with >50% rejection rate
    unhealthy_sources = []
    for src in ScrapeSource.select().where(ScrapeSource.is_active == True):  # noqa: E712
        total_staged = KnowledgeEntry.select().where(
            KnowledgeEntry.metadata_json.contains(f'"scrape_source_id": {src.id}')
        ).count()
        total_rejected = RejectionLog.select().where(
            RejectionLog.source == src
        ).count()
        total = total_staged + total_rejected
        if total > 0 and (total_rejected / total) > 0.5:
            unhealthy_sources.append({"source": src, "rejection_rate": round(total_rejected / total * 100)})

    return render_template(
        "studio/dashboard.html",
        score=score_data,
        recent_jobs=list(recent_jobs),
        total_jobs=stats["total_jobs"],
        total_candidates=stats["total_candidates"],
        total_approved=stats["approved"],
        pending_count=pending_count,
        unhealthy_sources=unhealthy_sources,
    )


# ─────────────────────────────────
#  KNOWLEDGE BASE
# ─────────────────────────────────

# Knowledge section definitions — controls UI grouping
KNOWLEDGE_SECTIONS = [
    {
        "key": "products",
        "label": "LDAS Products",
        "icon": "fas fa-box",
        "color": "var(--purple)",
        "types": ["product_catalog"],
        "description": "Core product catalog — Shopify sync only",
    },
    {
        "key": "brand",
        "label": "Brand & Blog",
        "icon": "fas fa-feather-alt",
        "color": "var(--pink)",
        "types": ["brand_copy", "blog_post"],
        "description": "Our voice, our content",
    },
    {
        "key": "competitors",
        "label": "Competitor Intel",
        "icon": "fas fa-binoculars",
        "color": "var(--amber)",
        "types": ["competitor_intel"],
        "description": "Jabra, Poly/HP, BlueParrott product intelligence",
    },
    {
        "key": "email_intel",
        "label": "Email Design Intel",
        "icon": "fas fa-envelope-open-text",
        "color": "var(--cyan)",
        "types": ["email_design_intel"],
        "description": "Template best practices, deliverability, trends",
    },
    {
        "key": "social_proof",
        "label": "Testimonials & FAQs",
        "icon": "fas fa-star",
        "color": "var(--green)",
        "types": ["testimonial", "faq"],
        "description": "Customer proof and answers — with images for email blocks",
    },
]


@studio_bp.route("/knowledge")
def knowledge_list():
    """Knowledge base list, organized by section."""
    section = request.args.get("section", "")
    entry_type = request.args.get("type", "")

    query = (
        KnowledgeEntry.select()
        .where(KnowledgeEntry.is_active == True)  # noqa: E712
        .order_by(KnowledgeEntry.updated_at.desc())
    )

    # Filter by section or specific type
    if section:
        sec = next((s for s in KNOWLEDGE_SECTIONS if s["key"] == section), None)
        if sec:
            query = query.where(KnowledgeEntry.entry_type.in_(sec["types"]))
    elif entry_type:
        query = query.where(KnowledgeEntry.entry_type == entry_type)

    entries = list(query)

    # Count per section for badges
    section_counts = {}
    for sec in KNOWLEDGE_SECTIONS:
        section_counts[sec["key"]] = (
            KnowledgeEntry.select()
            .where(
                KnowledgeEntry.is_active == True,  # noqa: E712
                KnowledgeEntry.entry_type.in_(sec["types"]),
            )
            .count()
        )

    entry_types = [
        "product_catalog", "brand_copy", "testimonial",
        "blog_post", "competitor_intel", "email_design_intel", "faq",
    ]

    studio = TemplateStudio()
    score = studio.get_intelligence_score()

    return render_template(
        "studio/knowledge.html",
        entries=entries,
        entry_types=entry_types,
        current_type=entry_type,
        current_section=section,
        sections=KNOWLEDGE_SECTIONS,
        section_counts=section_counts,
        score=score,
    )


@studio_bp.route("/knowledge/add", methods=["POST"])
def knowledge_add():
    """Add a new knowledge entry."""
    metadata = {}
    image_url = request.form.get("image_url", "").strip()
    if image_url:
        metadata["image_urls"] = [image_url]
    metadata["source_name"] = "Manual"

    KnowledgeEntry.create(
        entry_type=request.form.get("entry_type", "faq"),
        title=request.form.get("title", "").strip(),
        content=request.form.get("content", "").strip(),
        metadata_json=json.dumps(metadata),
    )
    flash("Knowledge entry added.", "success")
    return redirect(url_for("studio.knowledge_list"))


@studio_bp.route("/knowledge/<int:id>/edit", methods=["POST"])
def knowledge_edit(id):
    """Update an existing knowledge entry."""
    entry = KnowledgeEntry.get_by_id(id)
    entry.title = request.form.get("title", entry.title).strip()
    entry.content = request.form.get("content", entry.content).strip()
    entry.is_active = request.form.get("is_active", "0") in ("1", "on", "true")
    entry.updated_at = datetime.now()
    entry.save()
    flash("Knowledge entry updated.", "success")
    return redirect(url_for("studio.knowledge_list"))


@studio_bp.route("/knowledge/<int:id>/delete", methods=["POST"])
def knowledge_delete(id):
    """Delete a knowledge entry."""
    KnowledgeEntry.delete_by_id(id)
    flash("Knowledge entry deleted.", "success")
    return redirect(url_for("studio.knowledge_list"))


# ─────────────────────────────────
#  GENERATION
# ─────────────────────────────────

@studio_bp.route("/generate", methods=["GET"])
def generate_form():
    """Show the generation form — family, product focus, tone, model."""
    models = list(
        AIModelConfig
        .select()
        .where(AIModelConfig.is_active == True)  # noqa: E712
        .order_by(AIModelConfig.display_name)
    )
    return render_template(
        "studio/generate.html",
        families=TEMPLATE_FAMILIES,
        models=models,
    )


@studio_bp.route("/generate", methods=["POST"])
def generate_run():
    """Trigger AI template generation and redirect to job detail."""
    family = request.form.get("family", "")
    product_focus = request.form.get("product_focus", "").strip()
    tone = request.form.get("tone", "").strip()
    model_config_id = request.form.get("model_config_id") or None

    if model_config_id:
        model_config_id = int(model_config_id)

    studio = TemplateStudio()
    job = studio.generate(
        family=family,
        product_focus=product_focus,
        tone=tone,
        model_config_id=model_config_id,
    )

    flash("Template generation started. Review candidates below.", "success")
    return redirect(url_for("studio.job_detail", id=job.id))


# ─────────────────────────────────
#  JOBS
# ─────────────────────────────────

@studio_bp.route("/jobs")
def jobs_list():
    """All jobs, newest first, paginated 20 per page."""
    page = int(request.args.get("page", 1))
    per_page = 20

    total = StudioJob.select().count()
    jobs = list(
        StudioJob
        .select()
        .order_by(StudioJob.created_at.desc())
        .paginate(page, per_page)
    )

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "studio/jobs.html",
        jobs=jobs,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@studio_bp.route("/jobs/<int:id>")
def job_detail(id):
    """Job detail with all candidates."""
    job = StudioJob.get_by_id(id)
    candidates = list(
        TemplateCandidate
        .select()
        .where(TemplateCandidate.job == job)
        .order_by(TemplateCandidate.created_at)
    )
    return render_template(
        "studio/job.html",
        job=job,
        candidates=candidates,
    )


# ─────────────────────────────────
#  CANDIDATES
# ─────────────────────────────────

@studio_bp.route("/candidates/<int:id>/approve", methods=["POST"])
def candidate_approve(id):
    """Approve a candidate — creates an EmailTemplate."""
    candidate = TemplateCandidate.get_by_id(id)
    studio = TemplateStudio()
    template = studio.approve_candidate(id)
    flash(
        f'Candidate approved! <a href="/templates/{template.id}/edit">View template #{template.id}</a>',
        "success",
    )
    return redirect(url_for("studio.job_detail", id=candidate.job_id))


@studio_bp.route("/candidates/<int:id>/reject", methods=["POST"])
def candidate_reject(id):
    """Reject a candidate with optional reason."""
    candidate = TemplateCandidate.get_by_id(id)
    reason = request.form.get("reason", "")
    studio = TemplateStudio()
    studio.reject_candidate(id, reason=reason)
    flash("Candidate rejected.", "success")
    return redirect(url_for("studio.job_detail", id=candidate.job_id))


@studio_bp.route("/candidates/<int:id>/preview")
def candidate_preview(id):
    """Render candidate blocks as a full HTML email preview (raw HTML, no base.html)."""
    from block_registry import _BLOCK_RENDERERS, BLOCK_TYPES
    from email_shell import wrap_email

    candidate = TemplateCandidate.get_by_id(id)
    blocks = json.loads(candidate.blocks_json)

    inner_html = ""
    for block in blocks:
        bt = block.get("block_type", "")
        renderer = _BLOCK_RENDERERS.get(bt)
        if renderer:
            html = renderer(block.get("content", {}))
            if html:
                inner_html += html

    return wrap_email(inner_html)


# ─────────────────────────────────
#  MODEL CONFIGURATION
# ─────────────────────────────────

@studio_bp.route("/models")
def models_list():
    """List AI model configs with add form."""
    models = list(AIModelConfig.select().order_by(AIModelConfig.created_at.desc()))
    return render_template("studio/models.html", models=models)


@studio_bp.route("/models/add", methods=["POST"])
def models_add():
    """Add a new AI model config."""
    AIModelConfig.create(
        provider=request.form.get("provider", "").strip(),
        model_id=request.form.get("model_id", "").strip(),
        display_name=request.form.get("display_name", "").strip(),
        api_key_env=request.form.get("api_key_env", "").strip(),
        max_tokens=int(request.form.get("max_tokens", 2048)),
        is_default=request.form.get("is_default", "0") in ("1", "on", "true"),
    )
    flash("Model configuration added.", "success")
    return redirect(url_for("studio.models_list"))


# ─────────────────────────────────
#  API
# ─────────────────────────────────

@studio_bp.route("/api/intelligence-score")
def api_intelligence_score():
    """Return JSON intelligence score."""
    studio = TemplateStudio()
    return jsonify(studio.get_intelligence_score())


# ─────────────────────────────────
#  PENDING REVIEW
# ─────────────────────────────────

@studio_bp.route("/knowledge/pending")
def knowledge_pending():
    """Show staged entries awaiting human review, grouped by section."""
    section_filter = request.args.get("section", "")

    query = (
        KnowledgeEntry.select()
        .where(
            (KnowledgeEntry.is_active == False) &  # noqa: E712
            (KnowledgeEntry.is_rejected == False)   # noqa: E712
        )
        .order_by(KnowledgeEntry.created_at.desc())
    )

    all_entries = list(query)

    # Build type-to-section mapping
    type_to_section = {}
    for sec in KNOWLEDGE_SECTIONS:
        for t in sec["types"]:
            type_to_section[t] = sec["key"]

    # Group entries by section
    grouped = {}
    for sec in KNOWLEDGE_SECTIONS:
        grouped[sec["key"]] = []
    grouped["other"] = []

    for entry in all_entries:
        sec_key = type_to_section.get(entry.entry_type, "other")
        grouped[sec_key].append(entry)

    # Count per section for filter tabs
    section_counts = {sec["key"]: len(grouped[sec["key"]]) for sec in KNOWLEDGE_SECTIONS}

    # If filtering by section, only show that section's entries
    if section_filter:
        filtered_entries = grouped.get(section_filter, [])
    else:
        filtered_entries = all_entries

    return render_template(
        "studio/pending.html",
        entries=filtered_entries,
        all_count=len(all_entries),
        sections=KNOWLEDGE_SECTIONS,
        section_counts=section_counts,
        current_section=section_filter,
    )


@studio_bp.route("/knowledge/<int:id>/approve", methods=["POST"])
def knowledge_approve(id):
    """Approve a staged entry — sets is_active=True."""
    entry = KnowledgeEntry.get_by_id(id)
    entry.is_active = True
    entry.updated_at = datetime.now()
    entry.save()
    flash("Entry approved and added to the knowledge base.", "success")
    return redirect(url_for("studio.knowledge_pending"))


@studio_bp.route("/knowledge/<int:id>/reject", methods=["POST"])
def knowledge_reject(id):
    """Reject a staged entry — creates a RejectionLog and marks as rejected."""
    entry = KnowledgeEntry.get_by_id(id)

    # Parse metadata to extract source info and content hash
    try:
        meta = json.loads(entry.metadata_json or "{}")
    except (ValueError, TypeError):
        meta = {}

    scrape_source_id = meta.get("scrape_source_id")
    content_hash = meta.get("raw_content_hash", "")
    source_url = meta.get("source_url", "")

    # Resolve source FK (may be None for manually-added entries)
    source_obj = None
    if scrape_source_id:
        try:
            source_obj = ScrapeSource.get_by_id(scrape_source_id)
        except ScrapeSource.DoesNotExist:
            source_obj = None

    RejectionLog.create(
        original_entry_type=entry.entry_type,
        source=source_obj,
        title=entry.title,
        content_snippet=entry.content[:200],
        source_url=source_url,
        content_hash=content_hash,
    )

    entry.is_rejected = True
    entry.updated_at = datetime.now()
    entry.save()
    flash("Entry rejected and logged.", "success")
    return redirect(url_for("studio.knowledge_pending"))


# ─────────────────────────────────
#  SOURCE MANAGEMENT
# ─────────────────────────────────

@studio_bp.route("/sources")
def sources_list():
    """List all ScrapeSource rows with rejection rates and last log."""
    sources = list(ScrapeSource.select().order_by(ScrapeSource.created_at.desc()))

    source_data = []
    for src in sources:
        # Last scrape log
        try:
            last_log = (
                ScrapeLog
                .select()
                .where(ScrapeLog.source == src)
                .order_by(ScrapeLog.started_at.desc())
                .get()
            )
        except ScrapeLog.DoesNotExist:
            last_log = None

        # Rejection rate
        total_staged = KnowledgeEntry.select().where(
            KnowledgeEntry.metadata_json.contains(f'"scrape_source_id": {src.id}')
        ).count()
        total_rejected = RejectionLog.select().where(
            RejectionLog.source == src
        ).count()
        total = total_staged + total_rejected
        rejection_rate = round(total_rejected / total * 100) if total > 0 else 0

        source_data.append({
            "source": src,
            "last_log": last_log,
            "rejection_rate": rejection_rate,
            "high_rejection": rejection_rate > 50,
        })

    return render_template("studio/sources.html", source_data=source_data)


@studio_bp.route("/sources/add", methods=["POST"])
def sources_add():
    """Create a new ScrapeSource."""
    ScrapeSource.create(
        source_type=request.form.get("source_type", "web").strip(),
        source_name=request.form.get("source_name", "").strip(),
        url=request.form.get("url", "").strip(),
        scrape_frequency=request.form.get("scrape_frequency", "weekly").strip(),
        config_json=request.form.get("config_json", "{}").strip() or "{}",
    )
    flash("Scrape source added.", "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/<int:id>/toggle", methods=["POST"])
def sources_toggle(id):
    """Toggle is_active on a ScrapeSource."""
    src = ScrapeSource.get_by_id(id)
    src.is_active = not src.is_active
    src.save()
    state = "enabled" if src.is_active else "disabled"
    flash(f'Source "{src.source_name}" {state}.', "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/<int:id>/run", methods=["POST"])
def sources_run(id):
    """Trigger a background scrape for a single source."""
    src = ScrapeSource.get_by_id(id)

    def _run():
        try:
            import sys as _sys
            import os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from knowledge_scraper import run_single_source
            run_single_source(src.id)
        except Exception as _e:
            import logging
            logging.getLogger(__name__).error(f"Manual source run failed for {src.id}: {_e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    flash(f'Scrape started for "{src.source_name}". Check the log in a few moments.', "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/fix", methods=["POST"])
def sources_fix():
    """Apply source URL/selector fixes and add new sources."""
    from knowledge_scraper import fix_scrape_sources
    count = fix_scrape_sources()
    flash(f"Source fix migration applied: {count} changes.", "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/run-all", methods=["POST"])
def sources_run_all():
    """Trigger a background scrape for ALL active sources."""
    def _run():
        try:
            import sys as _sys
            import os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from knowledge_scraper import run_knowledge_enrichment
            run_knowledge_enrichment()
        except Exception as _e:
            import logging
            logging.getLogger(__name__).error(f"Run-all enrichment failed: {_e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    flash("Enrichment started for all active sources. Check the scrape log.", "success")
    return redirect(url_for("studio.sources_list"))


# ─────────────────────────────────
#  SCRAPE LOG
# ─────────────────────────────────

@studio_bp.route("/scrape-log")
def scrape_log():
    """Show the last 50 ScrapeLog entries."""
    logs = list(
        ScrapeLog
        .select(ScrapeLog, ScrapeSource)
        .join(ScrapeSource)
        .order_by(ScrapeLog.started_at.desc())
        .limit(50)
    )
    return render_template("studio/scrape_log.html", logs=logs)
