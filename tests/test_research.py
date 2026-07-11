import unittest
from datetime import datetime, timezone

from scripts import research


class ResearchContractTests(unittest.TestCase):
    def valid_research(self):
        return {
            "version": 1,
            "app": "sunrise-demo",
            "observed_at": "2026-07-07T12:00:00Z",
            "expires_at": "2026-08-06T12:00:00Z",
            "creatives": [
                {
                    "id": "meta-123",
                    "platform": "meta",
                    "advertiser": "StrideCo",
                    "market": "BR",
                    "active_since": "2025-12-01",
                    "format": "video",
                    "angle": "21-day challenge",
                    "hook": "Jejum de Daniel",
                    "source_url": "https://www.facebook.com/ads/library/?id=123",
                    "evidence_level": "longevity_proxy",
                    "lineage": "competitor_pattern",
                }
            ],
        }

    def test_research_requires_fresh_source_provenance(self):
        data = self.valid_research()
        data["creatives"][0].pop("source_url")

        result = research.audit_research(data)

        self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_research_cannot_label_longevity_as_proven_roas(self):
        data = self.valid_research()
        data["creatives"][0]["evidence_level"] = "proven_roas"

        result = research.audit_research(data)

        self.assertTrue(any("proven_roas" in error for error in result["errors"]))

    def test_research_source_url_must_be_valid_http_or_https(self):
        for source_url in ("facebook.com/ads/123", "ftp://example.com/ad", "https:///missing-host"):
            with self.subTest(source_url=source_url):
                data = self.valid_research()
                data["creatives"][0]["source_url"] = source_url

                result = research.audit_research(data)

                self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_malformed_http_hosts_are_blocking_without_crashing_the_audit(self):
        for source_url in ("https://[", "https://bad host/path", "http://."):
            with self.subTest(source_url=source_url):
                data = self.valid_research()
                data["creatives"][0]["source_url"] = source_url

                try:
                    result = research.audit_research(data)
                except ValueError as error:
                    self.fail(f"audit_research lançou ValueError: {error}")

                self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_terminal_dots_in_http_hosts_are_blocking(self):
        for source_url in (
            "https://example.com.",
            "https://example.com../path",
            "https://example.com.../path",
        ):
            with self.subTest(source_url=source_url):
                data = self.valid_research()
                data["creatives"][0]["source_url"] = source_url

                result = research.audit_research(data)

                self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_malformed_http_ports_are_blocking_without_crashing_the_audit(self):
        for source_url in ("https://example.com:not-a-port", "https://example.com:70000"):
            with self.subTest(source_url=source_url):
                data = self.valid_research()
                data["creatives"][0]["source_url"] = source_url

                try:
                    result = research.audit_research(data)
                except ValueError as error:
                    self.fail(f"audit_research lançou ValueError: {error}")

                self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_requested_app_must_match_research_app(self):
        result = research.audit_research(self.valid_research(), expected_app="demo-app-c")

        self.assertTrue(any("demo-app-c" in error and "sunrise-demo" in error for error in result["errors"]))

    def test_supported_lineage_types_are_accepted(self):
        for lineage in (
            "competitor_pattern",
            "own_winner",
            "customer_insight",
            "trend",
            "exploratory",
        ):
            with self.subTest(lineage=lineage):
                data = self.valid_research()
                data["creatives"][0]["lineage"] = lineage
                if lineage == "own_winner":
                    data["creatives"][0]["evidence_level"] = "performance_data"
                    data["creatives"][0]["performance_metrics"] = {"spend": 100, "revenue": 150}

                result = research.audit_research(data)

                self.assertFalse(any("lineage" in error for error in result["errors"]))

    def test_unknown_lineage_is_blocking(self):
        data = self.valid_research()
        data["creatives"][0]["lineage"] = "proven_competitor_winner"

        result = research.audit_research(data)

        self.assertTrue(any("lineage" in error for error in result["errors"]))

    def test_longevity_proxy_cannot_claim_own_winner_lineage(self):
        data = self.valid_research()
        data["creatives"][0]["lineage"] = "own_winner"

        result = research.audit_research(data)

        self.assertTrue(any("own_winner" in error for error in result["errors"]))

    def test_legacy_research_gets_explicit_competitor_pattern_migration_warning(self):
        data = self.valid_research()
        data["creatives"][0].pop("lineage")

        result = research.audit_research(data)

        self.assertFalse(any("lineage" in error for error in result["errors"]))
        self.assertTrue(any("competitor_pattern" in warning for warning in result["warnings"]))

    def test_expired_research_blocks_generation(self):
        result = research.audit_research(
            self.valid_research(),
            now=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )

        self.assertTrue(any("expirada" in error for error in result["errors"]))

    def test_longer_running_observed_creative_scores_above_newer_proxy(self):
        data = self.valid_research()
        newer = dict(data["creatives"][0])
        newer.update({"id": "meta-456", "active_since": "2026-07-01"})
        data["creatives"].append(newer)

        ranked = research.rank_creatives(data, as_of=datetime(2026, 7, 7, tzinfo=timezone.utc))

        self.assertEqual(ranked[0]["id"], "meta-123")
        self.assertEqual(ranked[0]["evidence_level"], "longevity_proxy")


if __name__ == "__main__":
    unittest.main()
