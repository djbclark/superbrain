#!/usr/bin/env python3
"""Unit tests for config-driven category taxonomy and classifier validation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.classifier import (
    aggregate_suggestions,
    classify_content,
    validate_classification,
)
from core.taxonomy import (
    TaxonomyError,
    build_effective_taxonomy,
    clear_taxonomy_cache,
    load_taxonomy,
    CategoryDef,
)


OPERATOR_TOML = """
[taxonomy]
use_default_categories = false
allow_multiple_categories = false
fallback_category = "Other"
confidence_threshold = 0.55
suggestion_min_count = 5

[[categories]]
name = "Sysadmin"
precedence = 1
guidance = "Actionable tools."

[[categories]]
name = "Science"
precedence = 2
guidance = "Science."

[[categories]]
name = "Technology"
precedence = 3
guidance = "Non-actionable tech news."

[[categories]]
name = "History"
precedence = 4
guidance = "History."

[[categories]]
name = "Humanities"
precedence = 5
guidance = "Humanities."

[[categories]]
name = "Politics"
precedence = 6
guidance = "Politics."

[[categories]]
name = "Other"
precedence = 7
guidance = "Fallback."
"""


class TestTaxonomyConfig(unittest.TestCase):
    def setUp(self):
        clear_taxonomy_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "categories.toml"

    def tearDown(self):
        clear_taxonomy_cache()
        self.tmp.cleanup()

    def test_load_operator_taxonomy(self):
        self.path.write_text(OPERATOR_TOML, encoding="utf-8")
        tax = load_taxonomy(self.path)
        self.assertFalse(tax.use_default_categories)
        self.assertFalse(tax.allow_multiple_categories)
        self.assertEqual(tax.fallback_category, "Other")
        self.assertEqual(
            tax.names,
            ["Sysadmin", "Science", "Technology", "History", "Humanities", "Politics", "Other"],
        )
        self.assertEqual(tax.pick_single(["technology", "Sysadmin"]), "Sysadmin")

    def test_rejects_duplicate_precedence(self):
        bad = OPERATOR_TOML.replace("precedence = 2", "precedence = 1", 1)
        self.path.write_text(bad, encoding="utf-8")
        with self.assertRaises(TaxonomyError):
            load_taxonomy(self.path)

    def test_rejects_missing_fallback(self):
        bad = OPERATOR_TOML.replace('fallback_category = "Other"', 'fallback_category = "Nope"')
        self.path.write_text(bad, encoding="utf-8")
        with self.assertRaises(TaxonomyError):
            load_taxonomy(self.path)

    def test_rejects_empty_effective_taxonomy(self):
        with self.assertRaises(TaxonomyError):
            build_effective_taxonomy(
                use_default_categories=False,
                allow_multiple_categories=False,
                fallback_category="Other",
                confidence_threshold=0.55,
                suggestion_min_count=5,
                user_categories=[],
                config_path=None,
                loaded_from_file=True,
            )

    def test_user_category_overrides_default_same_name(self):
        user = [
            CategoryDef(
                name="other",
                precedence=1,
                guidance="User other wins",
                source="user",
            )
        ]
        tax = build_effective_taxonomy(
            use_default_categories=True,
            allow_multiple_categories=False,
            fallback_category="other",
            confidence_threshold=0.55,
            suggestion_min_count=5,
            user_categories=user,
            config_path=None,
            loaded_from_file=True,
        )
        resolved = tax.by_name_lower["other"]
        self.assertEqual(resolved.source, "user")
        self.assertEqual(resolved.precedence, 1)


class TestClassifierValidation(unittest.TestCase):
    def setUp(self):
        clear_taxonomy_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "categories.toml"
        self.path.write_text(OPERATOR_TOML, encoding="utf-8")
        self.tax = load_taxonomy(self.path)

    def tearDown(self):
        clear_taxonomy_cache()
        self.tmp.cleanup()

    def test_malformed_falls_back(self):
        result = validate_classification(None, self.tax)
        self.assertEqual(result.category, "Other")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.fallback_reason, "malformed_or_empty_model_output")

    def test_unknown_primary_falls_back(self):
        result = validate_classification(
            {"primary_category": "Cooking", "confidence": 0.9, "rationale": "x"},
            self.tax,
        )
        self.assertEqual(result.category, "Other")
        self.assertEqual(result.fallback_reason, "primary_not_in_taxonomy")

    def test_low_confidence_falls_back(self):
        result = validate_classification(
            {
                "primary_category": "Science",
                "confidence": 0.2,
                "rationale": "weak",
                "out_of_taxonomy_suggestions": ["Biology podcast"],
            },
            self.tax,
        )
        self.assertEqual(result.category, "Other")
        self.assertEqual(result.fallback_reason, "low_confidence")
        self.assertEqual(result.suggestions, ["Biology podcast"])

    def test_precedence_resolves_ambiguity(self):
        result = validate_classification(
            {
                "primary_category": "Technology",
                "additional_categories": ["Sysadmin"],
                "confidence": 0.8,
                "rationale": "both",
            },
            self.tax,
        )
        self.assertEqual(result.category, "Sysadmin")
        self.assertEqual(result.source, "model")

    def test_suggestions_exclude_configured_names(self):
        result = validate_classification(
            {
                "primary_category": "Politics",
                "confidence": 0.9,
                "rationale": "ok",
                "out_of_taxonomy_suggestions": ["Politics", "Local elections"],
            },
            self.tax,
        )
        self.assertEqual(result.suggestions, ["Local elections"])

    def test_classify_content_uses_router_json(self):
        router = MagicMock()
        router.generate_text.return_value = json.dumps(
            {
                "primary_category": "Sysadmin",
                "confidence": 0.91,
                "rationale": "Install guide for nginx",
                "out_of_taxonomy_suggestions": ["DevOps"],
            }
        )
        result = classify_content(
            title="Nginx reverse proxy setup",
            summary="How to configure nginx with certbot",
            tags=["#nginx", "#linux"],
            taxonomy=self.tax,
            router=router,
        )
        self.assertEqual(result.category, "Sysadmin")
        self.assertEqual(result.suggestions, ["DevOps"])
        self.assertEqual(result.source, "model")

    def test_aggregate_suggestions_threshold(self):
        lists = [["DevOps"], ["devops"], ["DevOps"], ["Cooking"], ["Cooking"]]
        out = aggregate_suggestions(lists, self.tax, min_count=3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["count"], 3)


class TestRecategorizeDryRunNoWrites(unittest.TestCase):
    def setUp(self):
        clear_taxonomy_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        self.cfg = Path(self.tmp.name) / "categories.toml"
        self.cfg.write_text(OPERATOR_TOML, encoding="utf-8")
        from core.database import Database

        self.db = Database(self.db_path)
        self.db.save_analysis(
            shortcode="YT_abc",
            url="https://youtube.com/watch?v=abc",
            username="ch",
            title="Install WireGuard on Debian",
            summary="Step by step WireGuard VPN setup",
            tags=["#wireguard", "#linux"],
            music="",
            category="software",
            content_type="youtube",
        )

    def tearDown(self):
        self.db.close()
        clear_taxonomy_cache()
        self.tmp.cleanup()

    def test_apply_updates_only_category_metadata(self):
        from core.database import Database

        ok = self.db.update_category_metadata(
            "YT_abc",
            category="Sysadmin",
            category_source="migration",
            category_confidence=0.88,
            category_rationale="VPN setup guide",
            category_suggestions_json=["Networking"],
            category_taxonomy_version="testhash",
        )
        self.assertTrue(ok)
        row = self.db.get_by_shortcode("YT_abc")
        self.assertEqual(row["category"], "Sysadmin")
        self.assertEqual(row["category_source"], "migration")
        self.assertEqual(row["title"], "Install WireGuard on Debian")
        self.assertEqual(row["summary"], "Step by step WireGuard VPN setup")
        suggestions = json.loads(row["category_suggestions_json"])
        self.assertEqual(suggestions, ["Networking"])


if __name__ == "__main__":
    unittest.main()
