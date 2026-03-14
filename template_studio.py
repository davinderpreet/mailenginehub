"""
template_studio.py -- Orchestrator for the AI Email Template Studio pipeline.

TemplateStudio runs the skill pipeline:
    select_block_sequence -> compose blocks -> compose_subject_line -> validate_and_fix

It also handles candidate approval/rejection and knowledge-base scoring.
"""

import json
import logging
from datetime import datetime

from database import (
    KnowledgeEntry, AIModelConfig, StudioJob, TemplateCandidate,
    TemplatePerformance, EmailTemplate, db
)
from ai_provider import get_provider
from studio_skills import (
    select_block_sequence, compose_hero, compose_text,
    compose_generic_block, compose_subject_line, validate_and_fix
)

log = logging.getLogger(__name__)


class TemplateStudio:
    """Orchestrates the AI skill pipeline for email template generation."""

    # ── Public API ────────────────────────────────────────────────────────

    def generate(self, family, product_focus="", tone="", model_config_id=None):
        """
        Run the full skill pipeline and return the StudioJob instance.

        Steps:
        1. Create StudioJob row (status=running)
        2. Get AI provider
        3. Build context (knowledge + performance data)
        4. Run skills: block selection -> compose each block -> subject line -> validate
        5. Save TemplateCandidate
        6. Mark job done (or error on failure)
        """
        # 1. Create job
        config = None
        if model_config_id:
            try:
                config = AIModelConfig.get_by_id(model_config_id)
            except AIModelConfig.DoesNotExist:
                config = None

        job = StudioJob.create(
            job_type="generate_template",
            status="running",
            family=family,
            input_json=json.dumps({
                "product_focus": product_focus,
                "tone": tone,
            }),
            model_config=config,
        )

        try:
            # 2. Get provider
            provider = get_provider(config)

            # 3. Build context
            context = self._build_context(family, product_focus, tone)

            # 4. Run skill pipeline
            context = select_block_sequence(context, provider)

            for block_type in context["block_sequence"]:
                if block_type == "hero":
                    context = compose_hero(context, provider)
                elif block_type == "text":
                    context = compose_text(context, provider)
                else:
                    context = compose_generic_block(block_type, context, provider)

            context = compose_subject_line(context, provider)
            context = validate_and_fix(context, provider)

            # 5. Save candidate
            TemplateCandidate.create(
                job=job,
                blocks_json=json.dumps(context.get("blocks", [])),
                subject_line=context.get("subject", ""),
                preview_text=context.get("preview_text", ""),
                reasoning=context.get("reasoning", ""),
                status="pending",
            )

            # 6. Mark done
            job.status = "done"
            job.completed_at = datetime.now()
            job.save()

        except Exception as e:
            log.exception("TemplateStudio.generate failed for family=%s", family)
            job.status = "error"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            job.save()

        return job

    def approve_candidate(self, candidate_id):
        """
        Convert an approved TemplateCandidate into a standard EmailTemplate.

        Returns the new EmailTemplate instance.
        """
        candidate = TemplateCandidate.get_by_id(candidate_id)
        job = candidate.job

        template = EmailTemplate.create(
            name="Studio: %s - %s" % (job.family, datetime.now().strftime("%Y-%m-%d %H:%M")),
            subject=candidate.subject_line,
            preview_text=candidate.preview_text,
            html_body="",
            template_format="blocks",
            blocks_json=candidate.blocks_json,
            template_family=job.family,
        )

        candidate.status = "approved"
        candidate.approved_at = datetime.now()
        candidate.template = template
        candidate.save()

        return template

    def reject_candidate(self, candidate_id, reason=""):
        """Mark a candidate as rejected, storing the reason in metadata_json."""
        candidate = TemplateCandidate.get_by_id(candidate_id)
        candidate.status = "rejected"

        # Merge reason into existing metadata
        try:
            meta = json.loads(candidate.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["rejection_reason"] = reason
        meta["rejected_at"] = datetime.now().isoformat()
        candidate.metadata_json = json.dumps(meta)
        candidate.save()

    def get_intelligence_score(self):
        """
        Calculate the knowledge-base intelligence score (0-100).

        Returns a dict with score, breakdown per category, and suggestions.
        """
        # Count entries by type
        counts = {}
        for entry_type in ("product_catalog", "brand_copy", "testimonial",
                           "blog_post", "competitor_intel", "faq"):
            counts[entry_type] = (
                KnowledgeEntry
                .select()
                .where(
                    KnowledgeEntry.entry_type == entry_type,
                    KnowledgeEntry.is_active == True,  # noqa: E712
                )
                .count()
            )

        # Count tracked templates
        perf_count = TemplatePerformance.select().count()

        # Scoring rules: (entry_type, pts_per_entry, max_pts, label)
        rules = [
            ("product_catalog",  5, 25, "Product catalog"),
            ("brand_copy",       7, 20, "Brand copy"),
            ("testimonial",      3, 15, "Testimonials"),
            ("blog_post",        3, 10, "Blog posts"),
            ("competitor_intel", 5, 10, "Competitor intel"),
            ("faq",              2, 10, "FAQs"),
        ]

        breakdown = {}
        total = 0

        for entry_type, pts_per, max_pts, label in rules:
            count = counts.get(entry_type, 0)
            points = min(count * pts_per, max_pts)
            total += points
            breakdown[entry_type] = {
                "count": count,
                "points": points,
                "max": max_pts,
            }

        # Performance data: 2 pts per tracked template, cap 10
        perf_points = min(perf_count * 2, 10)
        total += perf_points
        breakdown["performance_data"] = {
            "count": perf_count,
            "points": perf_points,
            "max": 10,
        }

        # Build suggestions
        suggestions = []
        for entry_type, pts_per, max_pts, label in rules:
            info = breakdown[entry_type]
            if info["points"] < max_pts:
                needed = (max_pts - info["points"] + pts_per - 1) // pts_per
                suggestions.append(
                    "Add %d more %s entries to reach %d/%d"
                    % (needed, label.lower(), max_pts, max_pts)
                )

        if perf_points < 10:
            needed = (10 - perf_points + 1) // 2
            suggestions.append(
                "Track %d more template(s) to reach 10/10 performance points"
                % needed
            )

        return {
            "score": total,
            "breakdown": breakdown,
            "suggestions": suggestions,
        }

    # ── Private ───────────────────────────────────────────────────────────

    def _build_context(self, family, product_focus="", tone=""):
        """
        Gather knowledge entries and performance data into a context dict
        for the skill pipeline.
        """
        knowledge = []

        # Product focus: filter product_catalog entries by title
        if product_focus:
            product_entries = (
                KnowledgeEntry
                .select()
                .where(
                    KnowledgeEntry.entry_type == "product_catalog",
                    KnowledgeEntry.is_active == True,  # noqa: E712
                    KnowledgeEntry.title.contains(product_focus),
                )
            )
            for e in product_entries:
                knowledge.append({
                    "type": e.entry_type,
                    "title": e.title,
                    "content": e.content,
                })

        # Always include brand copy
        brand_entries = (
            KnowledgeEntry
            .select()
            .where(
                KnowledgeEntry.entry_type == "brand_copy",
                KnowledgeEntry.is_active == True,  # noqa: E712
            )
        )
        for e in brand_entries:
            knowledge.append({
                "type": e.entry_type,
                "title": e.title,
                "content": e.content,
            })

        # Journey-specific additions
        _TESTIMONIAL_FAMILIES = {"welcome", "high_intent_browse"}
        _FAQ_FAMILIES = {"checkout_recovery", "cart_recovery"}
        _COMPETITOR_FAMILIES = {"winback"}

        if family in _TESTIMONIAL_FAMILIES:
            for e in KnowledgeEntry.select().where(
                KnowledgeEntry.entry_type == "testimonial",
                KnowledgeEntry.is_active == True,  # noqa: E712
            ):
                knowledge.append({
                    "type": e.entry_type,
                    "title": e.title,
                    "content": e.content,
                })

        if family in _FAQ_FAMILIES:
            for e in KnowledgeEntry.select().where(
                KnowledgeEntry.entry_type == "faq",
                KnowledgeEntry.is_active == True,  # noqa: E712
            ):
                knowledge.append({
                    "type": e.entry_type,
                    "title": e.title,
                    "content": e.content,
                })

        if family in _COMPETITOR_FAMILIES:
            for e in KnowledgeEntry.select().where(
                KnowledgeEntry.entry_type == "competitor_intel",
                KnowledgeEntry.is_active == True,  # noqa: E712
            ):
                knowledge.append({
                    "type": e.entry_type,
                    "title": e.title,
                    "content": e.content,
                })

        # Always include blog posts (up to 3)
        for e in (
            KnowledgeEntry
            .select()
            .where(
                KnowledgeEntry.entry_type == "blog_post",
                KnowledgeEntry.is_active == True,  # noqa: E712
            )
            .order_by(KnowledgeEntry.created_at.desc())
            .limit(3)
        ):
            knowledge.append({
                "type": e.entry_type,
                "title": e.title,
                "content": e.content,
            })

        # Performance data: top 3 templates by open_rate for this family
        top_templates = []
        perf_query = (
            TemplatePerformance
            .select(TemplatePerformance, EmailTemplate)
            .join(EmailTemplate)
            .where(EmailTemplate.template_family == family)
            .order_by(TemplatePerformance.open_rate.desc())
            .limit(3)
        )
        for p in perf_query:
            top_templates.append({
                "name": p.template.name,
                "family": p.template.template_family,
                "open_rate": p.open_rate,
                "click_rate": p.click_rate,
            })

        performance = {"top_templates": top_templates}

        return {
            "family": family,
            "product_focus": product_focus,
            "tone": tone or "confident",
            "knowledge": knowledge,
            "performance": performance,
            "reasoning": "",
        }
