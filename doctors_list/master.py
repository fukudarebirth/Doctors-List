"""Canonical-content reconciliation; retain removed records as holds, not current entries."""
from . import SPREADSHEET_ID
from .snapshot import inspect_snapshot, snapshot_hash
from .storage import digest, load_json, save_private_json
from .legacy_master import import_library_master, bind_library_rows

SCHEMA_VERSION = 1


def load_master(path):
    value = load_json(path)
    if isinstance(value, dict) and 'authoritative_source' in value and 'people' in value:
        value = import_library_master(value)
    if not isinstance(value, dict) or value.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('除外マスターの形式が未対応です。原本を保持し、文書の移行手順で変換してください。')
    if value.get('spreadsheet_id') != SPREADSHEET_ID:
        raise ValueError('除外マスターの参照元IDが正本と異なります。')
    if 'legacy_archive' in value and digest(value['legacy_archive']) != value.get('legacy_archive_sha256'):
        raise ValueError('保存済みLibrary原本の内容確認に失敗しました。')
    for field in ('historical_name_keys', 'import_review_issues'):
        if field in value and (not isinstance(value[field], list) or any(not isinstance(s, str) for s in value[field])):
            raise ValueError('除外マスターの補助情報形式が不正です。')
    if not isinstance(value.get('entries'), list) or not isinstance(value.get('removed', []), list):
        raise ValueError('除外マスターの記録形式が不正です。')
    for record in value['entries'] + value.get('removed', []):
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ('id', 'name', 'email')):
            raise ValueError('除外マスターの索引が不正です。')
        for key in ('aliases', 'other_emails', 'previous_affiliations'):
            if key in record and (not isinstance(record[key], list) or any(not isinstance(s, str) for s in record[key])):
                raise ValueError('除外マスターの別表記が不正です。')
    return value


def reconcile(snapshot, old=None):
    inspected = inspect_snapshot(snapshot)
    if inspected['issues']:
        raise ValueError('未分類シート・列構成・不完全行を解消するまでマスター更新を停止します。')
    old = old or {'entries': [], 'removed': []}
    if 'legacy_archive' in old and digest(old['legacy_archive']) != old.get('legacy_archive_sha256'):
        raise ValueError('保存済みLibrary原本の内容確認に失敗しました。')
    old = {**old, 'entries': bind_library_rows(old['entries'], snapshot)}
    old_by_cells = {}
    for record in old['entries']:
        key = (record.get('sheet_id'), digest(record.get('raw_cells', record.get('cells', []))))
        old_by_cells.setdefault(key, []).append(record)
    entries, reused = [], 0
    for row in inspected['records']:
        key = (row['sheet_id'], digest(row['raw_cells']))
        priors = old_by_cells.get(key, [])
        prior = dict(priors[0]) if priors else None
        if len(priors) > 1:
            # Exact duplicate rows can carry different historical aliases. Keep
            # every search key; do not arbitrarily pick one identity claim.
            for field in ('aliases', 'other_emails', 'previous_affiliations'):
                prior[field] = list(dict.fromkeys(x for p in priors for x in p.get(field, [])))
            for field in ('identity_key', 'identity_evidence', 'identity_reviewed'):
                prior.pop(field, None)
        # Reuse only exact content and same sheet, never merely the old row position.
        if prior and prior.get('sheet_id') == row['sheet_id']:
            entries.append({**prior, **row})
            reused += 1
        else:
            entries.append(row)
    current = {(e['sheet_id'], digest(e['raw_cells'])) for e in entries}
    removed = list(old.get('removed', []))
    for e in old['entries']:
        if (e.get('sheet_id'), digest(e.get('raw_cells', e.get('cells', [])))) not in current:
            if digest(e) not in {digest(r) for r in removed}:
                removed.append(e)
    result = {'schema_version': SCHEMA_VERSION, 'spreadsheet_id': SPREADSHEET_ID,
              'snapshot_hash': snapshot_hash(snapshot), 'fetched_at': snapshot['fetched_at'],
              'entries': entries, 'removed': removed,
              'matching_version': 1}
    for key in ('legacy_archive', 'legacy_archive_sha256', 'imported_format',
                'historical_name_keys', 'import_review_issues'):
        if key in old:
            result[key] = old[key]
    return result, {'content_equal': old.get('snapshot_hash') == result['snapshot_hash'],
                    'reused_records': reused, 'rebuilt_records': len(entries)-reused,
                    'removed_or_changed_records': len(removed)}


def sync_master(snapshot, destination, old=None):
    if snapshot.get('source') != 'google':
        raise ValueError('マスター保存は今回Googleから正常取得した正本だけで実行できます。')
    result, diff = reconcile(snapshot, old)
    verification = save_private_json(destination, result)
    return {'diff': diff, 'save_verification': verification}
