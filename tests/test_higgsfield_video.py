import hashlib
import json
import signal
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import higgsfield_video


class HiggsfieldVideoAdapterTests(unittest.TestCase):
    def schema(self):
        return {
            "job_type": "seedance_2_0_mini",
            "type": "video",
            "params": [
                {"name": "prompt", "type": "string", "required": True},
                {
                    "name": "aspect_ratio",
                    "type": "string",
                    "required": False,
                    "enum": ["9:16", "1:1"],
                },
                {"name": "duration", "type": "integer", "required": False},
                {"name": "generate_audio", "type": "boolean", "required": False},
            ],
        }

    def test_discovered_schema_accepts_only_real_params_and_enums(self):
        errors = higgsfield_video.validate_params(
            self.schema(),
            {
                "prompt": "A calm sunrise",
                "aspect_ratio": "9:16",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(errors, [])
        self.assertTrue(
            any(
                "invented" in error
                for error in higgsfield_video.validate_params(
                    self.schema(), {"prompt": "x", "invented": "value"}
                )
            )
        )
        self.assertTrue(
            any(
                "aspect_ratio" in error
                for error in higgsfield_video.validate_params(
                    self.schema(), {"prompt": "x", "aspect_ratio": "4:5"}
                )
            )
        )

    def test_generation_command_requires_explicit_spend_confirmation(self):
        params = {"prompt": "A calm sunrise", "duration": 5}

        with self.assertRaisesRegex(ValueError, "confirm-spend"):
            higgsfield_video.build_create_command(
                "seedance_2_0_mini", params, confirm_spend=False
            )

        command = higgsfield_video.build_create_command(
            "seedance_2_0_mini", params, confirm_spend=True
        )
        self.assertEqual(command[:4], ["higgsfield", "generate", "create", "seedance_2_0_mini"])
        self.assertIn("--duration", command)

    def test_cost_command_never_creates_a_job(self):
        command = higgsfield_video.build_cost_command(
            "seedance_2_0_mini", {"prompt": "A calm sunrise"}
        )

        self.assertEqual(command[:4], ["higgsfield", "generate", "cost", "seedance_2_0_mini"])
        self.assertNotIn("create", command)

    def test_asset_receipt_hashes_prompt_schema_and_output_but_stays_rights_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "shot.mp4"
            output.write_bytes(b"mock-mp4-for-receipt")

            receipt = higgsfield_video.build_asset_receipt(
                model="seedance_2_0_mini",
                job_id="job-123",
                prompt="private creative prompt",
                schema=self.schema(),
                output=output,
                cost_estimate={"credits": 2.5},
            )

        self.assertEqual(receipt["job_id"], "job-123")
        self.assertEqual(
            receipt["prompt_sha256"],
            hashlib.sha256(b"private creative prompt").hexdigest(),
        )
        self.assertNotIn("prompt", receipt)
        self.assertEqual(receipt["rights"]["status"], "pending_provider_terms")
        self.assertFalse(receipt["rights"]["commercial_ads"])
        self.assertNotIn("cost", receipt)
        self.assertEqual(receipt["cost_status"], "estimate_only_unreconciled")

    def test_rights_basis_is_recorded_as_operator_confirmed_not_auto_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "shot.mp4"
            output.write_bytes(b"mock-mp4")

            receipt = higgsfield_video.build_asset_receipt(
                model="seedance_2_0_mini",
                job_id="job-123",
                prompt="prompt",
                schema=self.schema(),
                output=output,
                cost_estimate={"credits": 1},
                commercial_rights_basis="terms snapshot reviewed by operator",
            )

        self.assertEqual(receipt["rights"]["status"], "operator_confirmed")
        self.assertNotEqual(receipt["rights"]["status"], "cleared")

    def quote(self, params=None):
        return higgsfield_video.build_quote_receipt(
            model="seedance_2_0_mini",
            params=params or {"prompt": "A calm sunrise", "duration": 5},
            amount=2.5,
            unit="credits",
            expires_at="2099-01-01T00:00:00+00:00",
            max_amount=3.0,
            provider_cost={"credits": 2.5},
        )

    def test_quote_digest_binds_model_params_cost_unit_expiration_and_cap(self):
        params = {"prompt": "A calm sunrise", "duration": 5}
        quote = self.quote(params)

        higgsfield_video.validate_quote_receipt(
            quote,
            model="seedance_2_0_mini",
            params=params,
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

        for field, value in (
            ("model", "other-model"),
            ("expires_at", "2098-01-01T00:00:00+00:00"),
        ):
            with self.subTest(field=field):
                tampered = json.loads(json.dumps(quote))
                tampered[field] = value
                with self.assertRaisesRegex(ValueError, "digest"):
                    higgsfield_video.validate_quote_receipt(
                        tampered,
                        model="seedance_2_0_mini",
                        params=params,
                        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
                    )

        tampered = json.loads(json.dumps(quote))
        tampered["estimate"]["amount"] = 2.75
        with self.assertRaisesRegex(ValueError, "digest"):
            higgsfield_video.validate_quote_receipt(
                tampered,
                model="seedance_2_0_mini",
                params=params,
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, "params"):
            higgsfield_video.validate_quote_receipt(
                quote,
                model="seedance_2_0_mini",
                params={"prompt": "changed", "duration": 5},
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )

    def test_quote_rejects_expiration_and_cost_above_cap(self):
        expired = higgsfield_video.build_quote_receipt(
            model="seedance_2_0_mini",
            params={"prompt": "A calm sunrise"},
            amount=2.5,
            unit="credits",
            expires_at="2026-07-09T00:00:00+00:00",
            max_amount=3.0,
            provider_cost={"credits": 2.5},
        )
        with self.assertRaisesRegex(ValueError, "expirou"):
            higgsfield_video.validate_quote_receipt(
                expired,
                model="seedance_2_0_mini",
                params={"prompt": "A calm sunrise"},
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, "teto"):
            higgsfield_video.build_quote_receipt(
                model="seedance_2_0_mini",
                params={"prompt": "A calm sunrise"},
                amount=3.5,
                unit="credits",
                expires_at="2099-01-01T00:00:00+00:00",
                max_amount=3.0,
                provider_cost={"credits": 3.5},
            )

        higgsfield_video.validate_quote_receipt(
            expired,
            model="seedance_2_0_mini",
            params={"prompt": "A calm sunrise"},
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            allow_expired=True,
        )

    def test_quote_from_provider_estimate_seals_raw_response(self):
        provider_cost = {"data": {"estimated_credits": 2.5}}

        quote = higgsfield_video.build_quote_from_estimate(
            model="seedance_2_0_mini",
            params={"prompt": "A calm sunrise"},
            provider_cost=provider_cost,
            unit="credits",
            expires_at="2099-01-01T00:00:00+00:00",
            max_amount=3.0,
        )

        self.assertEqual(quote["estimate"], {"amount": 2.5, "unit": "credits"})
        self.assertEqual(
            quote["provider_cost_sha256"],
            higgsfield_video.canonical_sha256(provider_cost),
        )
        self.assertEqual(quote["provider_cost"], provider_cost)

        tampered = json.loads(json.dumps(quote))
        tampered["provider_cost"]["data"]["estimated_credits"] = 1.0
        sealed = {
            key: value for key, value in tampered.items() if key != "quote_sha256"
        }
        tampered["quote_sha256"] = higgsfield_video.canonical_sha256(sealed)
        with self.assertRaisesRegex(ValueError, "provider cost digest"):
            higgsfield_video.validate_quote_receipt(
                tampered,
                model="seedance_2_0_mini",
                params={"prompt": "A calm sunrise"},
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )

    def test_quote_requires_provider_estimate_and_matching_amount(self):
        common = {
            "model": "seedance_2_0_mini",
            "params": {"prompt": "A calm sunrise"},
            "amount": 2.5,
            "unit": "credits",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "max_amount": 3.0,
        }
        with self.assertRaisesRegex(ValueError, "provider cost"):
            higgsfield_video.build_quote_receipt(**common)

        with self.assertRaisesRegex(ValueError, "diverge"):
            higgsfield_video.build_quote_receipt(
                **common, provider_cost={"credits": 1.0}
            )

    def test_paid_generation_is_fail_closed_before_any_provider_call(self):
        params = {"prompt": "A calm sunrise", "duration": 5}
        quote = self.quote(params)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(higgsfield_video, "run_json") as provider:
                with self.assertRaisesRegex(RuntimeError, "não suportada"):
                    higgsfield_video.generate(
                        model="seedance_2_0_mini",
                        params=params,
                        output=root / "shot.mp4",
                        receipt_path=root / "receipt.json",
                        checkpoint_path=root / "job.json",
                        quote=quote,
                        confirm_spend=True,
                    )
            provider.assert_not_called()

    def test_checkpoint_write_failure_exposes_sealed_job_recovery_payload(self):
        params = {"prompt": "A calm sunrise", "duration": 5}
        quote = self.quote(params)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "job.json"
            with mock.patch.object(
                higgsfield_video,
                "write_json_atomic",
                side_effect=OSError("disk unavailable"),
            ):
                with self.assertRaises(
                    higgsfield_video.CheckpointPersistenceError
                ) as raised:
                    higgsfield_video.persist_created_job_checkpoint(
                        checkpoint_path=checkpoint_path,
                        model="seedance_2_0_mini",
                        params=params,
                        quote=quote,
                        job_id="job-123",
                    )

        recovery = raised.exception.recovery
        self.assertEqual(recovery["checkpoint"]["job_id"], "job-123")
        self.assertEqual(
            higgsfield_video.validate_job_checkpoint(
                recovery["checkpoint"],
                model="seedance_2_0_mini",
                params=params,
                quote=quote,
            ),
            "job-123",
        )
        self.assertNotIn("A calm sunrise", json.dumps(recovery))

    def test_checkpoint_digest_rejects_job_id_tampering(self):
        params = {"prompt": "A calm sunrise", "duration": 5}
        quote = self.quote(params)
        checkpoint = higgsfield_video.build_job_checkpoint(
            model="seedance_2_0_mini",
            params=params,
            quote=quote,
            job_id="job-123",
        )
        checkpoint["job_id"] = "attacker-job"

        with self.assertRaisesRegex(ValueError, "digest"):
            higgsfield_video.validate_job_checkpoint(
                checkpoint,
                model="seedance_2_0_mini",
                params=params,
                quote=quote,
            )

    def test_download_rejects_non_https_and_private_or_loopback_hosts(self):
        for url in (
            "http://example.com/video.mp4",
            "https://127.0.0.1/video.mp4",
            "https://10.0.0.1/video.mp4",
            "https://[::1]/video.mp4",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                higgsfield_video.validate_download_url(url)

    def test_remote_download_is_blocked_before_any_network_or_output_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            with self.assertRaisesRegex(RuntimeError, "DNS rebinding"):
                higgsfield_video.download_atomic(
                    "https://93.184.216.34/video.mp4", output
                )

            self.assertFalse(output.exists())

    def test_provider_and_ffprobe_calls_have_external_timeouts(self):
        completed = subprocess.CompletedProcess(
            ["tool"], 0, stdout='{"ok": true}', stderr=""
        )
        with mock.patch.object(
            higgsfield_video, "_run_process_group", return_value=completed
        ) as runner:
            self.assertEqual(higgsfield_video.run_json(["tool"]), {"ok": True})
            runner.assert_called_once_with(
                ["tool"], timeout_seconds=higgsfield_video.CLI_TIMEOUT_SECONDS
            )

        probe_result = subprocess.CompletedProcess(
            ["ffprobe"],
            0,
            stdout=json.dumps({"streams": [{"codec_type": "video"}]}),
            stderr="",
        )
        with mock.patch.object(
            higgsfield_video, "_run_process_group", return_value=probe_result
        ) as runner:
            higgsfield_video.probe_mp4(Path("video.mp4"))
            self.assertEqual(
                runner.call_args.kwargs["timeout_seconds"],
                higgsfield_video.FFPROBE_TIMEOUT_SECONDS,
            )

    def test_timeout_terminates_the_provider_process_group(self):
        process = mock.Mock(pid=4242, returncode=None)
        timeout = subprocess.TimeoutExpired(["higgsfield"], 1)
        process.communicate.side_effect = [timeout, ("", "")]

        with mock.patch.object(
            higgsfield_video.subprocess, "Popen", return_value=process
        ) as popen, mock.patch.object(higgsfield_video.os, "killpg") as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                higgsfield_video._run_process_group(
                    ["higgsfield"], timeout_seconds=1
                )

        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(4242, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
