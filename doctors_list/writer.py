"""Approval-bound append transaction, durable uncertainty and read-only recovery."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.parse import urlsplit

from . import MAIN_SHEET_ID
from .engine import evaluate, reiwa_day
from .master import reconcile
from .snapshot import inspect_snapshot, snapshot_hash
from .sheets_write import append_spec, expected_model
from .storage import digest, load_json, private_path, save_private_json


class WriteStopped(RuntimeError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def ensure_real_candidates(candidates, report):
    eligible = {a['id'] for a in report['assessments'] if a['status'] == 'eligible'}
    for c in candidates:
        if c['id'] not in eligible:
            continue
        hosts = [c['email'].rsplit('@', 1)[-1]]
        hosts += [urlsplit(u).hostname or '' for v in c['evidence'].values() if isinstance(v, dict) for u in v.get('urls', [])]
        if c.get('synthetic') or any(h.lower().endswith(('.invalid', '.test', '.example', '.localhost', '.example.com', '.example.org', '.example.net')) or h.lower() in ('localhost', 'example.com', 'example.org', 'example.net') for h in hosts):
            raise WriteStopped('架空・サンプルの候補は本番の追記承認対象にできません。Dry Runだけで使用してください。')


def prepare(backend, candidates, master, now=None):
    now = now or utcnow()
    snapshot, model = backend.read()
    if snapshot.get('source') != 'google':
        raise WriteStopped('追記準備には最新の正本の取得が必要です。')
    report = evaluate(snapshot, candidates, master, now)
    if report['blockers']:
        raise WriteStopped('正本・マスターに未確認事項があります。Dry Runの理由を確認してください。')
    ensure_real_candidates(candidates, report)
    try:
        spec = append_spec(model, report)
    except ValueError as error:
        raise WriteStopped(str(error)) from None
    payload = {'version': 1, 'created_at': now.isoformat(), 'jst_day': str(reiwa_day(now)[0]),
               'candidates': deepcopy(candidates), 'master': deepcopy(master), 'snapshot': snapshot,
               'before_model': model, 'report': report, 'spec': spec}
    return {'plan_id': digest(payload), 'payload': payload}


def validate_plan(plan):
    if (not isinstance(plan, dict) or not isinstance(plan.get('payload'), dict)
            or plan.get('plan_id') != digest(plan['payload']) or plan['payload'].get('version') != 1):
        raise WriteStopped('承認対象ファイルの内容が変わったか、形式が不正です。')
    return plan['payload']


def approval_phrase(plan):
    validate_plan(plan)
    return '承認 ' + plan['plan_id'][:12]


@contextmanager
def local_writer_lock(directory):
    """OS releases lock after crash. This cannot lock other machines/Google editors."""
    directory = private_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / 'writer.lock').open('a+b')
    locked = False
    try:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise WriteStopped('このPCで別の追記処理が動作中です。') from None
        yield directory
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _journal_path(root, plan_id):
    return root / plan_id / 'journal.json'


def _pending(root, excluding=None):
    for file in root.glob('*/journal.json'):
        if file == excluding:
            continue
        data = load_json(file)
        if data.get('state') not in ('verified', 'not_sent_changed'):
            raise WriteStopped('保存結果が未確認の操作があります。「保存状態を確認」で照合してください。')


def _check_result(payload, after, model):
    spec = payload['spec']
    if digest(model) != digest(expected_model(payload['before_model'], spec)):
        raise WriteStopped('予定値・書式・入力規則・行高・既存範囲の検証が一致しません。自動再追記はしません。')
    before = inspect_snapshot(payload['snapshot'])
    inspected = inspect_snapshot(after)
    if inspected['issues'] or inspected['registered_rows'] != before['registered_rows'] + len(spec['rows']):
        raise WriteStopped('保存後の登録行数・シート構成を検証できません。')
    main = next(s for s in after['sheets'] if s['sheet_id'] == MAIN_SHEET_ID)
    actual = [(r + [''] * 7)[:7] for r in main['rows'][spec['first']:spec['end']]]
    if actual != spec['rows'] or inspected['last_data_row'] != spec['end']:
        raise WriteStopped('保存後の行位置・値・最終行が一致しません。')
    # Pre-write duplicate recheck + exact whole-sheet post-image proves that no
    # unplanned extra rows were introduced. Unknown existing people counts stay null.
    report = deepcopy(payload['report'])
    report.update(mode='applied', production_authorized=True,
                  assessment_scope='approved_append_verified',
                  after={k: inspected[k] for k in ('registered_rows', 'observed_named_rows', 'actual_people', 'actual_people_reason', 'last_data_row')},
                  post_save_verification='verified', verified_at=utcnow().isoformat())
    report['newly_added'] = {**report['planned_additions'], 'total': len(spec['rows'])}
    report['proposed_change']['format_status'] = '値・既存範囲・書式・入力規則・列幅・追加行高20pxを検証済み'
    return report


def _finish(backend, root, plan, journal, journal_path):
    payload = validate_plan(plan)
    after, model = backend.read()
    report = _check_result(payload, after, model)
    new_master, diff = reconcile(after, payload['master'])
    folder = journal_path.parent
    # All output belongs to this operation; original inputs are never overwritten.
    save_private_json(folder / 'master-updated.json', new_master)
    report['master_saved'] = True
    report['master_diff'] = diff
    report['plan_id'] = plan['plan_id']
    save_private_json(folder / 'report.json', report)
    journal.update(state='verified', report_hash=digest(report), master_hash=digest(new_master))
    save_private_json(journal_path, journal)
    return report


def apply(backend, plan, state_dir, approval, single_writer=False, now=None):
    payload = validate_plan(plan)
    if approval != approval_phrase(plan) or single_writer is not True:
        raise WriteStopped('差分に対する明示承認と、他の編集を停止した単一担当運用の確認が必要です。')
    with local_writer_lock(state_dir) as root:
        journal_path = _journal_path(root, plan['plan_id'])
        if journal_path.exists():
            journal = load_json(journal_path)
            if journal.get('state') == 'not_sent_changed':
                raise WriteStopped('この操作は送信前に中止しました。差分を新しく準備して承認してください。')
            if journal.get('state') == 'verified':
                report = load_json(journal_path.parent / 'report.json')
                if digest(report) != journal.get('report_hash'):
                    raise WriteStopped('保存済みレポートの内容が一致しません。')
                return {**report, 'already_completed': True}
            raise WriteStopped('この操作は送信済み、または成否未確認です。再送せず「保存状態を確認」を実行してください。')
        _pending(root)
        now = now or utcnow()
        try:
            age = (now - datetime.fromisoformat(payload['created_at'])).total_seconds()
        except (ValueError, TypeError):
            raise WriteStopped('追記準備の日時が不正です。') from None
        if not 0 <= age <= 900 or str(reiwa_day(now)[0]) != payload['jst_day']:
            raise WriteStopped('日付が変わったか、準備から15分経過しました。もう一度差分を確認してください。')
        # Re-read all tabs, re-evaluate candidate/batch collisions immediately before backup.
        fresh = prepare(backend, payload['candidates'], payload['master'], now)
        current = fresh['payload']
        if (snapshot_hash(current['snapshot']) != snapshot_hash(payload['snapshot'])
                or digest(current['before_model']) != digest(payload['before_model'])
                or current['spec'] != payload['spec']):
            raise WriteStopped('正本または書式・追記条件が変わりました。承認を取り直してください。')
        folder = journal_path.parent
        save_private_json(folder / 'backup.json', {'plan': plan, 'saved_at': now.isoformat()})
        # Journal is durable BEFORE sending. Even a crash before send requires recovery.
        journal = {'state': 'submitted', 'plan_id': plan['plan_id'], 'started_at': now.isoformat()}
        save_private_json(journal_path, journal)
        # One final read after disk I/O, before the only write operation.
        final_snapshot, final_model = backend.read()
        final_now = utcnow()
        if (snapshot_hash(final_snapshot) != snapshot_hash(payload['snapshot'])
                or digest(final_model) != digest(payload['before_model'])
                or str(reiwa_day(final_now)[0]) != payload['jst_day']
                or not 0 <= (final_now - datetime.fromisoformat(payload['created_at'])).total_seconds() <= 900):
            journal['state'] = 'not_sent_changed'
            save_private_json(journal_path, journal)
            raise WriteStopped('送信直前に正本または日付が変わりました。追記していません。差分を新しく準備してください。')
        try:
            backend.append(payload['spec'])
        except Exception:
            # Response loss can mean fully committed. Read back, never retry the write.
            journal['response_uncertain'] = True
            save_private_json(journal_path, journal)
        try:
            return _finish(backend, root, plan, journal, journal_path)
        except Exception:
            journal['state'] = 'needs_review'
            save_private_json(journal_path, journal)
            raise WriteStopped('保存状態または保存後の検証が未完了です。再追記せず「保存状態を確認」を実行してください。') from None


def recover(backend, state_dir, plan_id):
    """Read-only toward Google. Never retries an append or rolls back existing rows."""
    if len(plan_id) != 64 or any(c not in '0123456789abcdef' for c in plan_id):
        raise WriteStopped('操作IDが不正です。')
    with local_writer_lock(state_dir) as root:
        path = _journal_path(root, plan_id)
        journal = load_json(path)
        backup = load_json(path.parent / 'backup.json')
        plan = backup['plan']
        validate_plan(plan)
        if plan['plan_id'] != plan_id or journal.get('plan_id') != plan_id:
            raise WriteStopped('操作記録が一致しません。')
        # Even if absent now, a timed-out server request might still complete later.
        # Do not clear the pending journal automatically or allow an unguarded retry.
        try:
            return _finish(backend, root, plan, journal, path)
        except Exception:
            raise WriteStopped('保存完了を確認できません。未保存・一部保存・他編集の可能性があります。再送せず管理担当者が確認してください。') from None
