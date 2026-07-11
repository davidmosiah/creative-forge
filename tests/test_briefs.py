import unittest

from scripts import briefs


class BriefContractTests(unittest.TestCase):
    def valid_brief(self):
        return {
            "version": 1,
            "id": "2026-07-pilot",
            "app": "sunrise-demo",
            "status": "approved",
            "approved_by": "demo-operator",
            "objective": "Acquire qualified first-time users",
            "primary_kpi": "first_open_rate",
            "markets": ["br"],
            "destination": {
                "type": "app_store",
                "url": "https://apps.apple.com/app/id0000000000",
            },
            "hypothesis": {
                "action": "Lead with a calm morning ritual",
                "expected_result": "Increase qualified installs",
                "reason": "The cited competitor patterns repeatedly frame the walk as a ritual",
            },
            "test_design": {
                "isolated_variable": "hook_angle",
                "constants": ["offer", "destination", "audience"],
            },
            "measurement": {
                "observation_window_hours": 72,
                "attribution_window": {"click_days": 7, "view_days": 1},
                "currency": "BRL",
            },
            "concepts": [
                {
                    "id": "morning-relief",
                    "lineage": "competitor_pattern",
                    "research_refs": ["meta-1"],
                    "agent_rationale": "Test a specific pain-to-relief opening.",
                }
            ],
        }

    def test_valid_agent_authored_brief_passes(self):
        result = briefs.audit_brief(
            self.valid_brief(),
            expected_app="sunrise-demo",
            known_research_refs={"meta-1"},
            supported_markets={"br", "us"},
        )

        self.assertEqual(result["errors"], [])

    def test_hypothesis_requires_action_result_and_reason(self):
        brief = self.valid_brief()
        brief["hypothesis"].pop("reason")

        result = briefs.audit_brief(brief)

        self.assertTrue(any("hypothesis.reason" in error for error in result["errors"]))

    def test_brief_requires_kpi_isolated_variable_and_destination(self):
        brief = self.valid_brief()
        brief.pop("primary_kpi")
        brief["test_design"].pop("isolated_variable")
        brief["destination"] = {}
        brief.pop("measurement")

        result = briefs.audit_brief(brief)

        self.assertTrue(any("primary_kpi" in error for error in result["errors"]))
        self.assertTrue(any("isolated_variable" in error for error in result["errors"]))
        self.assertTrue(any("destination" in error for error in result["errors"]))
        self.assertTrue(any("measurement" in error for error in result["errors"]))

    def test_concept_lineage_and_research_refs_are_fail_closed(self):
        brief = self.valid_brief()
        brief["concepts"][0]["lineage"] = "proven_winner"
        brief["concepts"][0]["research_refs"] = ["missing"]

        result = briefs.audit_brief(brief, known_research_refs={"meta-1"})

        self.assertTrue(any("lineage" in error for error in result["errors"]))
        self.assertTrue(any("missing" in error for error in result["errors"]))

    def test_recipe_must_bind_to_existing_concept_in_declared_brief(self):
        recipe = {
            "brief_ref": "2026-07-pilot",
            "concept_id": "not-there",
            "target_markets": ["br"],
        }

        errors = briefs.recipe_binding_errors(recipe, self.valid_brief(), "recipe-a")

        self.assertTrue(any("not-there" in error for error in errors))

    def test_brief_markets_must_be_supported_by_the_app(self):
        brief = self.valid_brief()
        brief["markets"] = ["br", "unknown"]

        result = briefs.audit_brief(brief, supported_markets={"br", "us"})

        self.assertTrue(any("unknown" in error for error in result["errors"]))

    def test_brief_and_recipe_market_ids_must_be_strings(self):
        brief = self.valid_brief()
        brief["markets"] = [{"id": "br"}]

        result = briefs.audit_brief(brief, supported_markets={"br"})
        recipe_errors = briefs.recipe_binding_errors(
            {
                "brief_ref": brief["id"],
                "concept_id": "morning-relief",
                "research_refs": ["meta-1"],
                "target_markets": [{"id": "br"}],
            },
            self.valid_brief(),
            "recipe-a",
        )

        self.assertTrue(any("markets" in error for error in result["errors"]))
        self.assertTrue(any("target_markets" in error for error in recipe_errors))

    def test_observation_window_must_be_finite(self):
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid=invalid):
                brief = self.valid_brief()
                brief["measurement"]["observation_window_hours"] = invalid

                result = briefs.audit_brief(brief)

                self.assertTrue(
                    any("observation_window_hours" in error for error in result["errors"])
                )

    def test_recipe_target_markets_must_be_nonempty_subset_of_brief(self):
        brief = self.valid_brief()
        brief["markets"] = ["br"]
        missing = {
            "brief_ref": brief["id"],
            "concept_id": "morning-relief",
            "research_refs": ["meta-1"],
        }
        outside = {**missing, "target_markets": ["us"]}

        missing_errors = briefs.recipe_binding_errors(missing, brief, "recipe-a")
        outside_errors = briefs.recipe_binding_errors(outside, brief, "recipe-a")

        self.assertTrue(any("target_markets" in error for error in missing_errors))
        self.assertTrue(any("us" in error for error in outside_errors))


if __name__ == "__main__":
    unittest.main()
