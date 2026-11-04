import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doctors_list.cli import main


def report():
    return {"mode": "dry_run", "source": "offline", "production_authorized": False,
            "candidate_counts": {"hold": 1}, "before": {"registered_rows": 3, "actual_people": None},
            "planned_additions": {"doctor": 0, "dentist": 0},
            "newly_added": {"doctor": 0, "dentist": 0, "total": 0},
            "post_save_verification": "not_run_read_only",
            "assessments": [{"name": "架空秘密医師", "email": "secret@example.invalid"}]}


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.candidates = self.directory / "candidates.json"
        self.candidates.write_text("[]")
        self.snapshot = self.directory / "snapshot.json"
        self.snapshot.write_text('{"source":"google"}')
        self.output = self.directory / "report.json"
        self.config = self.directory / "config.json"
        self.config.write_text(json.dumps({"client_secrets_path": str(self.directory / "client.json"),
                                           "token_path": str(self.directory / "token.json"),
                                           "person_sheet_ids": [], "non_person_sheet_ids": []}))

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(arguments)
        return code, out.getvalue(), err.getvalue()

    def dry_args(self):
        return ["dry-run", "--candidates", str(self.candidates), "--snapshot", str(self.snapshot),
                "--output", str(self.output)]

    def test_offline_snapshot_cannot_claim_google_source_and_console_has_no_person_data(self):
        with patch("doctors_list.cli.evaluate", return_value=report()) as evaluate, patch("doctors_list.cli.build_service") as service:
            code, out, err = self.invoke(self.dry_args())
        self.assertEqual(code, 0, err)
        self.assertEqual(evaluate.call_args.args[0]["source"], "offline")
        self.assertNotIn("架空秘密医師", out)
        self.assertNotIn("secret@example.invalid", out)
        self.assertEqual(json.loads(self.output.read_text())["assessments"][0]["name"], "架空秘密医師")
        service.assert_not_called()

    def test_fixture_has_explicit_fixture_source(self):
        with patch("doctors_list.cli.evaluate", return_value=report()) as evaluate:
            self.assertEqual(self.invoke(self.dry_args() + ["--fixture"])[0], 0)
        self.assertEqual(evaluate.call_args.args[0]["source"], "fixture")

    def test_live_fixture_combination_is_rejected_before_auth(self):
        with patch("doctors_list.cli.build_service") as service:
            result = self.invoke(["dry-run", "--live", "--fixture", "--candidates", str(self.candidates), "--output", str(self.output)])
        self.assertEqual(result[0], 1)
        service.assert_not_called()

    def test_input_overwrite_rejected_before_evaluation(self):
        arguments = self.dry_args()
        arguments[-1] = str(self.candidates)
        with patch("doctors_list.cli.evaluate") as evaluate:
            self.assertEqual(self.invoke(arguments)[0], 1)
        evaluate.assert_not_called()
        self.assertEqual(self.candidates.read_text(), "[]")

    def test_auth_is_only_interactive_command(self):
        with patch("doctors_list.cli.build_service") as service:
            code, _, _ = self.invoke(["auth", "--client", str(self.directory / "client.json"), "--token", str(self.directory / "token.json")])
        self.assertEqual(code, 0)
        self.assertTrue(service.call_args.kwargs["interactive"])

    def test_fetch_uses_noninteractive_auth_and_saves_outside_repo(self):
        with patch("doctors_list.cli.build_service") as service, patch("doctors_list.cli.read_snapshot", return_value={"sheets": [], "source": "google"}):
            code, out, err = self.invoke(["fetch", "--config", str(self.config), "--output", str(self.output)])
        self.assertEqual(code, 0, err)
        self.assertFalse(service.call_args.kwargs["interactive"])
        self.assertTrue(json.loads(out)["save_verified"])

    def test_sync_requires_new_destination(self):
        master = self.directory / "master.json"
        master.write_text("{}")
        with patch("doctors_list.cli.build_service") as service:
            code, _, _ = self.invoke(["sync-master", "--config", str(self.config), "--master", str(master), "--output", str(master)])
        self.assertEqual(code, 1)
        service.assert_not_called()

    def test_secret_exception_is_redacted(self):
        with patch("doctors_list.cli.evaluate", side_effect=RuntimeError("access_token=VERY_SECRET")):
            code, out, err = self.invoke(self.dry_args())
        self.assertEqual(code, 1)
        self.assertNotIn("VERY_SECRET", out + err)
        self.assertFalse(self.output.exists())

    def test_no_apply_or_approve_command(self):
        for command in ("apply", "approve"):
            self.assertEqual(self.invoke([command])[0], 1)


if __name__ == "__main__":
    unittest.main()
