import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from doctors_list.google_reader import (
    HEADERS, MAIN_SHEET_ID, MAIN_SHEET_TITLE, PROJECT_ROOT, READONLY_SCOPE,
    SCOPES, SPREADSHEET_ID, ReadError, _outside_checkout, _save_token,
    build_service, read_snapshot,
)


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeSheets:
    def __init__(self):
        self.calls = []
        self.meta = {"spreadsheetId": SPREADSHEET_ID, "properties": {"timeZone": "America/Los_Angeles"}, "sheets": [
            {"properties": {"sheetId": MAIN_SHEET_ID, "title": MAIN_SHEET_TITLE, "gridProperties": {"rowCount": 1000}}},
            {"properties": {"sheetId": 2, "title": "O'Brien", "gridProperties": {"rowCount": 999}}},
            {"properties": {"sheetId": 3, "title": "集計"}},
        ]}
        self.data = {"spreadsheetId": SPREADSHEET_ID, "valueRanges": [
            {"values": [list(HEADERS), ["R8/11/4", "", "架空医師", "架空病院", "doctor@example.invalid", "", ""], [], ["", "", "", "", "", "", "", "列Hも読む"]]},
            {"values": [list(HEADERS)]}, {"values": [["人数", "10"]]},
        ]}

    def spreadsheets(self):
        return self

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Request(self.meta)

    def values(self):
        return self

    def batchGet(self, **kwargs):
        self.calls.append(("batchGet", kwargs))
        return Request(self.data)


class ReaderTests(unittest.TestCase):
    def test_all_sheets_full_columns_and_unknown_classification(self):
        fake = FakeSheets()
        snapshot = read_snapshot(fake)
        self.assertEqual([s["kind"] for s in snapshot["sheets"]], ["person", "unknown", "unknown"])
        self.assertEqual(snapshot["timezone"], "America/Los_Angeles")
        self.assertEqual(snapshot["sheets"][0]["row_count"], 1000)
        self.assertEqual(len(snapshot["sheets"][0]["rows"]), 4)
        self.assertEqual(snapshot["sheets"][0]["rows"][3][7], "列Hも読む")
        self.assertEqual(fake.calls[1][1]["ranges"], ["'都内・公開メール医師'", "'O''Brien'", "'集計'"])
        self.assertEqual(fake.calls[1][1]["valueRenderOption"], "FORMATTED_VALUE")
        self.assertTrue(snapshot["fetched_at"].endswith("+00:00"))
        self.assertEqual([c[0] for c in fake.calls], ["get", "batchGet"])

    def test_explicit_classification(self):
        snapshot = read_snapshot(FakeSheets(), {2}, {3})
        self.assertEqual([s["kind"] for s in snapshot["sheets"]], ["person", "person", "non_person"])

    def test_overlap_and_nonexistent_ids(self):
        for people, other in [({2}, {2}), (set(), {MAIN_SHEET_ID}), ({42}, set())]:
            with self.subTest(people=people, other=other), self.assertRaises(ReadError):
                read_snapshot(FakeSheets(), people, other)

    def test_missing_main_and_wrong_title(self):
        for modification in ("missing", "renamed"):
            fake = FakeSheets()
            if modification == "missing":
                fake.meta["sheets"].pop(0)
            else:
                fake.meta["sheets"][0]["properties"]["title"] = "別シート"
            with self.assertRaises(ReadError):
                read_snapshot(fake)

    def test_wrong_book_metadata_or_values(self):
        for target in ("meta", "data"):
            fake = FakeSheets()
            getattr(fake, target)["spreadsheetId"] = "wrong"
            with self.assertRaises(ReadError):
                read_snapshot(fake)

    def test_headers_required(self):
        fake = FakeSheets()
        fake.data["valueRanges"][0]["values"][0][4] = "電話番号"
        with self.assertRaises(ReadError):
            read_snapshot(fake)

    def test_whitespace_width_header_normalization(self):
        fake = FakeSheets()
        fake.data["valueRanges"][0]["values"][0][3] = " 勤務先&役職 "
        self.assertEqual(read_snapshot(fake)["sheets"][0]["kind"], "person")

    def test_partial_response_fails_closed(self):
        fake = FakeSheets()
        fake.data["valueRanges"].pop()
        with self.assertRaises(ReadError):
            read_snapshot(fake)

    def test_api_error_is_redacted_and_not_retried(self):
        fake = FakeSheets()
        fake.data = RuntimeError("sensitive-secret")
        with self.assertRaises(ReadError) as error:
            read_snapshot(fake)
        self.assertNotIn("sensitive-secret", str(error.exception))
        self.assertEqual(len(fake.calls), 2)

    def test_scope_is_only_readonly(self):
        self.assertEqual(SCOPES, (READONLY_SCOPE,))
        self.assertTrue(READONLY_SCOPE.endswith("spreadsheets.readonly"))

    def test_credentials_cannot_be_inside_repository(self):
        with self.assertRaises(ReadError):
            _outside_checkout(PROJECT_ROOT / "private.json")
        with self.assertRaises(ReadError):
            build_service(PROJECT_ROOT / "client.json", "/tmp/token.json")


    def _google_modules(self, credential=None):
        modules = {name: types.ModuleType(name) for name in (
            "google", "google.auth", "google.auth.transport", "google.auth.transport.requests",
            "google.oauth2", "google.oauth2.credentials", "google_auth_oauthlib",
            "google_auth_oauthlib.flow", "googleapiclient", "googleapiclient.discovery",
        )}
        modules["google.auth.transport.requests"].Request = MagicMock()
        credentials = MagicMock()
        credentials.from_authorized_user_info.return_value = credential
        modules["google.oauth2.credentials"].Credentials = credentials
        modules["google_auth_oauthlib.flow"].InstalledAppFlow = MagicMock()
        modules["googleapiclient.discovery"].build = MagicMock(return_value="readonly-service")
        return modules

    def test_broader_cached_token_is_rejected(self):
        modules = self._google_modules()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            token = Path(directory) / "token.json"
            token.write_text(json.dumps({"scopes": ["https://www.googleapis.com/auth/spreadsheets"]}))
            with self.assertRaises(ReadError):
                build_service(Path(directory) / "client.json", token)
            modules["google.oauth2.credentials"].Credentials.from_authorized_user_info.assert_not_called()
            modules["google_auth_oauthlib.flow"].InstalledAppFlow.from_client_config.assert_not_called()

    def test_missing_token_never_starts_interactive_login_by_default(self):
        modules = self._google_modules()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            with self.assertRaises(ReadError):
                build_service(Path(directory) / "client.json", Path(directory) / "token.json")
            modules["google_auth_oauthlib.flow"].InstalledAppFlow.from_client_config.assert_not_called()

    def test_valid_cached_readonly_token_builds_service(self):
        credential = types.SimpleNamespace(expired=False, valid=True)
        modules = self._google_modules(credential)
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            token = Path(directory) / "token.json"
            token.write_text(json.dumps({"scopes": list(SCOPES)}))
            service = build_service(Path(directory) / "client.json", token)
            self.assertEqual(service, "readonly-service")
            modules["googleapiclient.discovery"].build.assert_called_once_with(
                "sheets", "v4", credentials=credential, cache_discovery=False,
            )

    def test_write_scope_requires_explicit_mode_and_dedicated_token(self):
        from doctors_list.google_reader import WRITE_SCOPE
        credential = types.SimpleNamespace(expired=False, valid=True)
        modules = self._google_modules(credential)
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', modules):
            client = Path(directory) / 'client.json'
            token = Path(directory) / 'write.json'
            token.write_text(json.dumps({'scopes': list(SCOPES)}))
            with self.assertRaises(ReadError): build_service(client, token, writable=True)
            token.write_text(json.dumps({'scopes': [WRITE_SCOPE]}))
            with self.assertRaises(ReadError): build_service(client, token)
            build_service(client, token, writable=True)
            modules['google.oauth2.credentials'].Credentials.from_authorized_user_info.assert_called_once_with(
                {'scopes': [WRITE_SCOPE]}, scopes=[WRITE_SCOPE])

    def test_atomic_token_save_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "token.json"
            _save_token(path, '{"synthetic":true}')
            self.assertEqual(json.loads(path.read_text()), {"synthetic": True})
            _save_token(path, '{"synthetic":false}')
            self.assertEqual(json.loads(path.read_text()), {"synthetic": False})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["token.json"])


if __name__ == "__main__":
    unittest.main()
