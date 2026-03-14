"""
studio_routes.py -- Flask Blueprint for the AI Email Template Studio.

Routes for knowledge base management, AI template generation,
job/candidate review, model configuration, and preview.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from database import (
    KnowledgeEntry, AIModelConfig, StudioJob, TemplateCandidate,
    TemplatePerformance, EmailTemplate, db
)
from template_studio import TemplateStudio
from condition_engine import TEMPLATE_FAMILIES
import json
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

    return render_template(
        "studio/dashboard.html",
        score=score_data,
        recent_jobs=list(recent_jobs),
        total_jobs=stats["total_jobs"],
        total_candidates=stats["total_candidates"],
        total_approved=stats["approved"],
    )


# ─────────────────────────────────
#  KNOWLEDGE BASE
# ─────────────────────────────────

@studio_bp.route("/knowledge")
def knowledge_list():
    """Knowledge base list, filterable by entry_type."""
    entry_type = request.args.get("type", "")
    query = KnowledgeEntry.select().order_by(KnowledgeEntry.updated_at.desc())
    if entry_type:
        query = query.where(KnowledgeEntry.entry_type == entry_type)
    entries = list(query)

    entry_types = [
        "product_catalog", "brand_copy", "testimonial",
        "blog_post", "competitor_intel", "faq",
    ]

    studio = TemplateStudio()
    score = studio.get_intelligence_score()

    return render_template(
        "studio/knowledge.html",
        entries=entries,
        entry_types=entry_types,
        current_type=entry_type,
        score=score,
    )


@studio_bp.route("/knowledge/add", methods=["POST"])
def knowledge_add():
    """Add a new knowledge entry."""
    KnowledgeEntry.create(
        entry_type=request.form.get("entry_type", "faq"),
        title=request.form.get("title", "").strip(),
        content=request.form.get("content", "").strip(),
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
