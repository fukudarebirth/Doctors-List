"""Official OAuth and strictly read-only access to the canonical spreadsheet.

Google client libraries are imported only when OAuth is requested. No write API
is exposed by this module. Returned row_count is grid capacity, not people count.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unicodedata

SPREADSHEET_ID = "1mcimCk9NVrQmaVgDvddiD0kB5PTMDFpnViUr_LMZWVs"
MAIN_SHEET_ID = 1997875360
MAIN_SHEET_TITLE = "都内・公開メール医師"
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SCOPES = (READONLY_SCOPE,)
HEADERS = ("追加日", "配信状況", "氏名（ふりがな）", "勤務先＆役職", "メールアドレス", "出典", "備考")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReadError(RuntimeError):
    """A safe, non-credential-bearing error suitable for display to the user."""


def _outside_checkout(path: str | Path) -> Path:
    original = Path(path).expanduser()
    if original.is_symlink():
        raise ReadError("認証ファイルにシンボリックリンクは使用できません。")
    result = original.resolve()
    if result == PROJECT_ROOT or PROJECT_ROOT in result.parents:
        raise ReadError("認証情報とトークンはリポジトリの外へ保存してください。")
    return result


def _save_token(path: Path, value: str) -> None:
    """Replace a token atomically without exposing it in a partial file or logs."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def build_service(client_path: str | Path, token_path: str | Path, *, interactive: bool = False):
    """Build a read-only Sheets service using a Desktop OAuth client.

    Interactive consent is performed only when explicitly requested. Never pass
    tokens via CLI arguments or place either file in the repository. On Windows,
    use a private user directory with an appropriate NTFS access control list.
    """
    client = _outside_checkout(client_path)
    token = _outside_checkout(token_path)
    if client == token:
        raise ReadError("OAuthクライアントとトークンには異なるファイルを指定してください。")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise ReadError("Google認証用の依存関係をインストールしてください。") from None

    try:
        credentials = None
        if token.exists():
            # Do not silently reuse credentials with a previous write grant.
            info = json.loads(token.read_text(encoding="utf-8"))
            scopes = info.get("scopes", [])
            if isinstance(scopes, str):
                scopes = scopes.split()
            if not isinstance(scopes, list) or set(scopes) != set(SCOPES):
                raise ReadError("このトークンは読み取り専用ではありません。別のトークンファイルで再認証してください。")
            credentials = Credentials.from_authorized_user_info(info, scopes=list(SCOPES))
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_token(token, credentials.to_json())
        if not credentials or not credentials.valid:
            if not interactive:
                raise ReadError("有効なOAuthトークンがありません。Windowsで明示的にauthを実行してください。")
            if not client.is_file():
                raise ReadError("Google Cloudのデスクトップアプリ用OAuthクライアントJSONが必要です。")
            client_info = json.loads(client.read_text(encoding="utf-8"))
            if not isinstance(client_info, dict) or "installed" not in client_info:
                raise ReadError("デスクトップアプリ用の公式OAuthクライアントを指定してください。")
            flow = InstalledAppFlow.from_client_config(client_info, scopes=list(SCOPES))
            # Suppress the callback server's authorization URL in console logs.
            credentials = flow.run_local_server(port=0, authorization_prompt_message="", success_message="認証を完了しました。この画面を閉じてください。")
            granted = getattr(credentials, "granted_scopes", None)
            if granted is not None and set(granted) != set(SCOPES):
                raise ReadError("読み取り専用以外の権限が付与されたため停止しました。")
            _save_token(token, credentials.to_json())
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except ReadError:
        raise
    except Exception:
        # OAuth/HTTP errors may contain tokens or private server responses.
        raise ReadError("Google認証に失敗しました。クライアント設定、権限、通信状態を確認してください。認証情報は表示していません。") from None


def _header(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def read_snapshot(service, person_sheet_ids: set[int] | None = None,
                  non_person_sheet_ids: set[int] | None = None) -> dict:
    """Read metadata and every tab; unknown tabs stay visible and unclassified.

    A timestamp alone does not prove freshness. Consumers must distinguish live
    acquisition from imported snapshots and re-read before any future write.
    """
    people = set(person_sheet_ids or ()) | {MAIN_SHEET_ID}
    non_people = set(non_person_sheet_ids or ())
    if people & non_people:
        raise ReadError("人物シートと非人物シートの指定が重複しています。")
    try:
        spreadsheets = service.spreadsheets()
        metadata = spreadsheets.get(
            spreadsheetId=SPREADSHEET_ID,
            fields="spreadsheetId,properties(timeZone),sheets(properties(sheetId,title,sheetType,gridProperties(rowCount,columnCount)))",
            includeGridData=False,
        ).execute()
        if metadata.get("spreadsheetId") != SPREADSHEET_ID:
            raise ReadError("正本とは異なるスプレッドシートです。")
        properties = [s["properties"] for s in metadata.get("sheets", [])]
        ids = [p["sheetId"] for p in properties]
        if len(ids) != len(set(ids)):
            raise ReadError("シートIDが重複した応答です。")
        main = next((p for p in properties if p["sheetId"] == MAIN_SHEET_ID), None)
        if main is None or main.get("title") != MAIN_SHEET_TITLE:
            raise ReadError("指定のメインシートIDまたは名称を確認できません。")
        if (people | non_people) - set(ids):
            raise ReadError("設定に正本に存在しないシートIDがあります。全シート分類を確認してください。")
        # Full-tab ranges read beyond A:G too; apostrophes must be escaped in A1.
        ranges = ["'" + p["title"].replace("'", "''") + "'" for p in properties]
        response = spreadsheets.values().batchGet(
            spreadsheetId=SPREADSHEET_ID, ranges=ranges,
            majorDimension="ROWS", valueRenderOption="FORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
        if response.get("spreadsheetId") != SPREADSHEET_ID:
            raise ReadError("値の応答が正本と一致しません。")
        values = response.get("valueRanges", [])
        if len(values) != len(properties):
            raise ReadError("全シートの値を取得できませんでした。")
        result = []
        for props, value_range in zip(properties, values):
            raw_rows = value_range.get("values", [])
            if not isinstance(raw_rows, list) or any(not isinstance(r, list) for r in raw_rows):
                raise ReadError("シートの値の形式が不正です。")
            rows = [["" if c is None else str(c) for c in row] for row in raw_rows]
            sheet_id = props["sheetId"]
            kind = "person" if sheet_id in people else "non_person" if sheet_id in non_people else "unknown"
            if kind == "person":
                if not rows or tuple(_header(x) for x in rows[0][:7]) != tuple(_header(x) for x in HEADERS):
                    raise ReadError("人物シートのA〜G列ヘッダーが仕様と一致しません。列対応を確認してください。")
            result.append({"sheet_id": sheet_id, "title": props["title"], "kind": kind,
                           "rows": rows, "row_count": props.get("gridProperties", {}).get("rowCount", 0),
                           "header_row": 1})
        return {"source": "google", "spreadsheet_id": SPREADSHEET_ID,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "timezone": metadata.get("properties", {}).get("timeZone"), "sheets": result}
    except ReadError:
        raise
    except Exception:
        raise ReadError("正本の全シート取得に失敗しました。通信、閲覧権限、シート構成を確認してください。部分的なデータでは判定しません。") from None
