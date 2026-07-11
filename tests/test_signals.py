import unittest
from datetime import datetime, timezone

from scripts import signals


class SignalContractTests(unittest.TestCase):
    def valid_snapshot(self):
        return {
            "version": 1,
            "app": "sunrise-demo",
            "observed_at": "2026-07-09T18:00:00Z",
            "expires_at": "2026-07-16T18:00:00Z",
            "sources": [
                {
                    "kind": "posthog",
                    "project_id": 999999,
                    "window_days": 30,
                    "production_filters": ["not_testflight", "not_emulator", "app_name=Sunrise Walks"],
                },
                {"kind": "paid_media", "status": "unavailable"},
            ],
            "countries": {
                "US": {
                    "opened_users": 40,
                    "first_value_users": 6,
                    "ritual_users": 6,
                    "retained_users": 0,
                    "paywall_users": 35,
                    "purchase_started_users": 12,
                    "purchase_users": 0,
                    "trial_users": 0,
                    "renewal_users": 6,
                },
                "BR": {
                    "opened_users": 27,
                    "first_value_users": 20,
                    "ritual_users": 22,
                    "retained_users": 2,
                    "paywall_users": 20,
                    "purchase_started_users": 4,
                    "purchase_users": 2,
                    "trial_users": 2,
                    "renewal_users": 1,
                },
            },
        }

    def test_stale_snapshot_is_a_blocking_error(self):
        snapshot = self.valid_snapshot()

        result = signals.audit_signals(
            snapshot,
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertTrue(any("expirado" in error for error in result["errors"]))

    def test_posthog_source_requires_production_filters_and_project(self):
        snapshot = self.valid_snapshot()
        snapshot["sources"][0].pop("project_id")
        snapshot["sources"][0]["production_filters"] = ["not_testflight"]

        result = signals.audit_signals(snapshot)

        self.assertTrue(any("project_id" in error for error in result["errors"]))
        self.assertTrue(any("not_emulator" in error for error in result["errors"]))

    def test_missing_paid_spend_warns_instead_of_fabricating_roas(self):
        result = signals.audit_signals(self.valid_snapshot())

        self.assertTrue(any("ROAS indisponível" in warning for warning in result["warnings"]))

    def test_requested_app_must_match_signals_app(self):
        result = signals.audit_signals(self.valid_snapshot(), expected_app="demo-app-c")

        self.assertTrue(any("demo-app-c" in error and "sunrise-demo" in error for error in result["errors"]))

    def test_posthog_app_name_filter_must_match_the_expected_product(self):
        snapshot = self.valid_snapshot()
        snapshot["sources"][0]["production_filters"] = [
            "not_testflight",
            "not_emulator",
            "app_name=OtherApp",
        ]

        result = signals.audit_signals(
            snapshot,
            expected_app="sunrise-demo",
            expected_app_name="Sunrise Walks",
        )

        self.assertTrue(
            any("app_name=OtherApp" in error and "Sunrise Walks" in error for error in result["errors"])
        )

    def test_posthog_rejects_multiple_conflicting_app_name_filters(self):
        snapshot = self.valid_snapshot()
        snapshot["sources"][0]["production_filters"].append("app_name=OtherApp")

        result = signals.audit_signals(snapshot, expected_app_name="Sunrise Walks")

        self.assertTrue(any("app_name" in error and "exatamente" in error for error in result["errors"]))

    def test_ranking_prefers_current_purchase_and_activation_quality(self):
        ranking = signals.rank_countries(self.valid_snapshot())

        self.assertEqual(ranking[0]["country"], "BR")
        self.assertGreater(ranking[0]["score"], ranking[1]["score"])
        self.assertEqual(ranking[0]["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
