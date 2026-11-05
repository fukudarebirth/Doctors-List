"""Read-only CLI. Reports containing personal data are saved outside the checkout."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .engine import evaluate
from .google_reader import ReadError, build_service, read_snapshot
from .master import load_master, sync_master
from .storage import load_json, private_path, save_private_json


def _parser():
    parser = argparse.ArgumentParser(description="医師リスト照合（読み取り専用）", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    dry = commands.add_parser("dry-run", help="候補を照合し、書き込みなしでレポート保存", allow_abbrev=False)
    dry.add_argument("--candidates", required=True)
    source = dry.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", help="取得済みのJSON。常にオフライン参考評価")
    source.add_argument("--live", action="store_true", help="今回Googleから全シートを読み取り")
    dry.add_argument("--config", default="config/settings.example.json")
    dry.add_argument("--master")
    dry.add_argument("--output", required=True)
    dry.add_argument("--fixture", action="store_true", help="架空テストデータ専用（--snapshotと併用）")
    auth = commands.add_parser("auth", help="公式OAuthで読み取り専用認証", allow_abbrev=False)
    auth.add_argument("--client", required=True)
    auth.add_argument("--token", required=True)
    fetch = commands.add_parser("fetch", help="正本の全シートを読み取り、外部フォルダーへ保存", allow_abbrev=False)
    fetch.add_argument("--config", required=True)
    fetch.add_argument("--output", required=True)
    sync = commands.add_parser("sync-master", help="正本を再取得し、除外マスターの更新版を別名保存", allow_abbrev=False)
    sync.add_argument("--config", required=True)
    sync.add_argument("--master", required=True)
    sync.add_argument("--output", required=True)
    check = commands.add_parser("check-master", help="除外マスターの形式確認（内容を表示せず変更しない）", allow_abbrev=False)
    check.add_argument("--master", required=True)
    return parser


def _config(path):
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("設定はJSONオブジェクトにしてください。")
    for key in ("client_secrets_path", "token_path"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("OAuthファイルの絶対パスを設定してください。")
        expanded = Path(os.path.expandvars(value[key])).expanduser()
        if not expanded.is_absolute():
            raise ValueError("OAuthファイルには絶対パスを指定してください。")
        value[key] = str(private_path(expanded))
    if value["client_secrets_path"] == value["token_path"]:
        raise ValueError("OAuthクライアントとトークンには別のパスが必要です。")
    for key in ("person_sheet_ids", "non_person_sheet_ids"):
        ids = value.get(key, [])
        if not isinstance(ids, list) or any(type(i) is not int or i < 0 for i in ids):
            raise ValueError("シートIDは0以上の整数の配列にしてください。")
        value[key] = ids
    return value


def _live(config):
    service = build_service(config["client_secrets_path"], config["token_path"], interactive=False)
    return read_snapshot(service, set(config["person_sheet_ids"]), set(config["non_person_sheet_ids"]))


def _output(path, protected):
    output = private_path(path)
    for original in protected:
        if original is not None and output == Path(original).expanduser().resolve():
            raise ValueError("入力ファイルと出力ファイルには異なるパスを指定してください。")
    return output


def _summary(report):
    # Deliberate allowlist: never print names, emails, source URLs, or evidence.
    return {"mode": report["mode"], "source": report["source"],
            "production_authorized": report["production_authorized"],
            "candidate_counts": report["candidate_counts"],
            "before_registered_rows": report["before"]["registered_rows"],
            "before_actual_people": report["before"]["actual_people"],
            "planned_additions": report["planned_additions"],
            "newly_added": report["newly_added"],
            "post_save_verification": report["post_save_verification"],
            "report_saved": True}


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return 0 if error.code == 0 else 1
    try:
        if args.command == "check-master":
            private_path(args.master)
            value = load_master(args.master)
            print(json.dumps({"master_loaded": True, "source_id_verified": True,
                              "format": value.get("imported_format", "native_v1"),
                              "stored_active_records": len(value["entries"]),
                              "stored_history_records": len(value.get("removed", [])),
                              "review_issue_count": len(value.get("import_review_issues", [])),
                              "canonical_read_this_run": False, "file_changed": False}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "auth":
            build_service(args.client, args.token, interactive=True)
            print("読み取り専用OAuth認証を完了しました。Googleシートは変更していません。")
            return 0
        if args.command == "dry-run":
            if args.fixture and args.live:
                raise ValueError("--fixtureは--snapshotと組み合わせてください。")
            if not args.fixture:
                private_path(args.candidates)
                if args.snapshot:
                    private_path(args.snapshot)
                if args.master:
                    private_path(args.master)
            config = _config(args.config) if args.live else None
            protected = [args.candidates, args.snapshot, args.master, args.config]
            if config:
                protected += [config["client_secrets_path"], config["token_path"]]
            destination = _output(args.output, protected)
            candidates = load_json(args.candidates)
            master = load_master(args.master) if args.master else None
            snapshot = _live(config) if args.live else load_json(args.snapshot)
            if not isinstance(snapshot, dict):
                raise ValueError("スナップショットの形式が不正です。")
            if not args.live:
                snapshot = dict(snapshot)
                # A serialized source label cannot establish live acquisition.
                snapshot["source"] = "fixture" if args.fixture else "offline"
            report = evaluate(snapshot, candidates, master)
            save_private_json(destination, report)
            print(json.dumps(_summary(report), ensure_ascii=False, indent=2))
            return 0
        config = _config(args.config)
        protected = [args.config, config["client_secrets_path"], config["token_path"]]
        if args.command == "sync-master":
            private_path(args.master)
            protected.append(args.master)
        destination = _output(args.output, protected)
        # Check local input before authentication or any network request.
        old = load_master(args.master) if args.command == "sync-master" else None
        snapshot = _live(config)
        if args.command == "fetch":
            verification = save_private_json(destination, snapshot)
            result = {"snapshot_saved": True, "sheet_count": len(snapshot["sheets"]),
                      "source": "google", "save_verified": verification["verified"]}
        else:
            result = sync_master(snapshot, destination, old)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReadError as error:
        print(str(error), file=sys.stderr)  # Adapter errors are explicitly redacted.
    except FileNotFoundError:
        print("必要な入力ファイルが見つかりません。指定パスと設定を確認してください。", file=sys.stderr)
    except PermissionError:
        print("ファイルを読み書きできません。保存先とアクセス権を確認してください。", file=sys.stderr)
    except json.JSONDecodeError as error:
        print(f"JSON形式が不正です（行{error.lineno}、列{error.colno}）。内容は表示していません。", file=sys.stderr)
    except (ValueError, TypeError, KeyError):
        print("入力・設定を検証できませんでした。JSON形式、必須項目、シート構成、保存先を確認してください。入力の上書きは許可されません。", file=sys.stderr)
    except Exception:
        print("処理を安全停止しました。入力と通信・保存状態を確認してください。Googleシートへの書き込みは行っていません。", file=sys.stderr)
    return 1
