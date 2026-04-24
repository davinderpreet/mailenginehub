"""
test_condition_engine.py — Unit tests for template family registry + validators.

Tests:
  1. TestTemplateFamilies — allowed_blocks / required_blocks invariants
  2. TestFamilyBlockCompatibility — specific family/block combinations that
     caused production bugs (regression guard)

Run:  python -m pytest tests/test_condition_engine.py -v
"""

import os
import sys

# Add project root to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


class TestTemplateFamilies:
    """Invariants that must hold across all template families."""

    def test_all_families_have_required_keys(self):
        """Every family must define allowed_blocks, required_blocks, max_blocks, label."""
        from condition_engine import TEMPLATE_FAMILIES
        for family_key, family in TEMPLATE_FAMILIES.items():
            assert "allowed_blocks" in family, "%s missing allowed_blocks" % family_key
            assert "required_blocks" in family, "%s missing required_blocks" % family_key
            assert "max_blocks" in family, "%s missing max_blocks" % family_key
            assert "label" in family, "%s missing label" % family_key

    def test_required_blocks_are_allowed(self):
        """Every required_block must also appear in allowed_blocks for that family."""
        from condition_engine import TEMPLATE_FAMILIES
        for family_key, family in TEMPLATE_FAMILIES.items():
            allowed = set(family["allowed_blocks"])
            for req in family["required_blocks"]:
                assert req in allowed, (
                    "%s family: required block %r not in allowed_blocks" % (family_key, req)
                )


class TestFamilyBlockCompatibility:
    """Regression guards for specific family/block combinations that bit us in production."""

    def test_browse_recovery_allows_driver_testimonial(self):
        """Regression: template #18 'Browse Abandon — Social Proof' uses driver_testimonial
        and was getting 29 active enrollments stuck in a cancellation loop because
        browse_recovery didn't allow it. Driver testimonials are social proof — they
        belong in browse_recovery just like they already do in welcome/post_purchase/winback.
        """
        from condition_engine import TEMPLATE_FAMILIES
        allowed = set(TEMPLATE_FAMILIES["browse_recovery"]["allowed_blocks"])
        assert "driver_testimonial" in allowed, (
            "browse_recovery must allow driver_testimonial — see "
            "'Cancelled enrollment ... driver_testimonial is not allowed' log errors"
        )

    def test_driver_testimonial_allowed_in_social_proof_families(self):
        """driver_testimonial should be usable in any family that leans on social proof."""
        from condition_engine import TEMPLATE_FAMILIES
        # Families where social proof testimonials make editorial sense
        social_proof_families = [
            "welcome", "browse_recovery", "post_purchase",
            "winback", "high_intent_browse", "promo",
        ]
        for family_key in social_proof_families:
            allowed = set(TEMPLATE_FAMILIES[family_key]["allowed_blocks"])
            assert "driver_testimonial" in allowed, (
                "%s should allow driver_testimonial for social-proof content" % family_key
            )
