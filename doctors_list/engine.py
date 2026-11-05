from datetime import datetime
from zoneinfo import ZoneInfo
import re
from . import MAIN_SHEET_ID, SHEET_URL
from .snapshot import inspect_snapshot, snapshot_hash
from .matching import compare, normalize_name
from .master import reconcile
from .validation import conditions, reviewed, validate_candidates
from .storage import digest


def reiwa_day(moment):
    if moment.tzinfo is None:
        raise ValueError('タイムゾーン付き日時が必要です。')
    today = moment.astimezone(ZoneInfo('Asia/Tokyo')).date()
    if today.year < 2019 or (today.year == 2019 and today.month < 5):
        raise ValueError('令和以前の日付は未対応です。')
    return today, f'R{today.year-2018}/{today.month}/{today.day}'


def _identity_safe(record):
    record = dict(record)
    record['identity_reviewed'] = reviewed(record.get('identity_evidence'))
    return record


def _resolution(candidate, other, hit, canonical_hash):
    for r in candidate.get('resolutions', []):
        if (isinstance(r, dict) and r.get('record_id') == other['id']
                and r.get('snapshot_hash') == canonical_hash and reviewed(r.get('review'))):
            if r.get('decision') == 'same_person':
                return {'level': 'confirmed', 'reasons': ['根拠付きの本人照合で同一人物と確認']}
            if r.get('decision') == 'different_person' and hit and hit['level'] != 'confirmed':
                return None
    return hit


def _date_present(value, today, label):
    normalized = str(value).strip().replace('令和', 'R').replace('年', '/').replace('月', '/').replace('日', '')
    normalized = re.sub(r'\s', '', normalized)
    if normalized == label:
        return True
    try:
        m = re.fullmatch(r'R(\d+)/(\d+)/(\d+)', normalized)
        if m:
            return (int(m[1])+2018, int(m[2]), int(m[3])) == (today.year, today.month, today.day)
        m = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', normalized)
        return bool(m and tuple(map(int, m.groups())) == (today.year, today.month, today.day))
    except ValueError:
        return False


def evaluate(snapshot, candidates, master=None, now=None):
    validate_candidates(candidates)
    now = now or datetime.now().astimezone()
    today, label = reiwa_day(now)
    inspection = inspect_snapshot(snapshot)
    fingerprint = snapshot_hash(snapshot)
    blockers = list(inspection['issues'])
    source = snapshot.get('source', 'offline')
    if source not in ('google', 'fixture'):
        blockers.append('オフライン評価：最新の正本を今回取得していないため登録判定は保留')
    if source == 'google':
        try:
            fetched = datetime.fromisoformat(snapshot['fetched_at'].replace('Z', '+00:00'))
            if fetched.tzinfo is None or not -60 <= (now - fetched).total_seconds() <= 900:
                blockers.append('正本取得から時間が経過しています。再取得が必要です。')
        except (ValueError, TypeError, KeyError):
            blockers.append('正本の取得時刻を検証できません。')
    if master is None:
        blockers.append('保存済み除外マスターが未照合（削除履歴等が未確認）')
    blockers.extend((master or {}).get('import_review_issues', []))
    rebuilt, master_diff = (None, None)
    if not inspection['issues']:
        rebuilt, master_diff = reconcile(snapshot, master)
    existing = rebuilt['entries'] if rebuilt else inspection['records']
    history = rebuilt['removed'] if rebuilt else (master or {}).get('removed', [])
    normalized = [_identity_safe(c) for c in candidates]
    legacy_names = {normalize_name(n) for n in (master or {}).get('historical_name_keys', [])}
    assessments = []
    for c in normalized:
        validation = conditions(c, today)
        hold = validation['hold'] + blockers
        excluded = list(validation['exclude'])
        matches = []
        candidate_names = [c.get(k, '') for k in ('name', 'kana', 'romanized')] + c.get('aliases', [])
        if any(normalize_name(n) in legacy_names for n in candidate_names if n):
            hold.append('保存済み氏名索引に一致：正本と過去記録の対応を確認してください')
        for record in existing:
            hit = compare(c, _identity_safe(record))
            hit = _resolution(c, record, hit, fingerprint)
            if hit:
                matches.append({'target': record['id'], 'scope': 'canonical', **hit})
        for record in history:
            hit = compare(c, _identity_safe(record))
            if hit:
                hold.append('削除・変更履歴に一致：経緯の確認が必要')
                matches.append({'target': record['id'], 'scope': 'history',
                                'level': 'suspected', 'reasons': hit['reasons']})
        for other in normalized:
            if c['id'] != other['id']:
                hit = compare(c, other)
                if hit:
                    matches.append({'target': other['id'], 'scope': 'candidate_batch', **hit})
        confirmed = any(m['level'] == 'confirmed' for m in matches)
        suspected = any(m['level'] == 'suspected' for m in matches)
        if suspected:
            hold.append('重複疑い：同一人物か別人かの根拠確認が必要')
        if confirmed:
            status = 'duplicate'
        elif excluded:
            status = 'excluded'
        elif hold:
            status = 'hold'
        else:
            status = 'eligible'
        assessments.append({'id': c['id'], 'name': c['name'], 'status': status,
                            'reasons': excluded + list(dict.fromkeys(hold)),
                            'matches': matches, 'age_note': validation['age_note']})
    selected = {a['id']: a for a in assessments if a['status'] == 'eligible'}
    main = next(s for s in snapshot['sheets'] if s['sheet_id'] == MAIN_SHEET_ID)
    has_date = any(r and _date_present(r[0], today, label) for r in main['rows'][1:])
    rows = []
    for c in candidates:
        if c['id'] not in selected:
            continue
        evidence = c['evidence']
        sources = list(dict.fromkeys(u for part in ('email', 'age', 'affiliation') for u in evidence[part]['urls']))
        notes = [selected[c['id']]['age_note']]
        for part, title in [('email', '本人メール'), ('age', '年齢根拠'), ('affiliation', '現職')]:
            e = evidence[part]
            notes.append(f"{title}: {e['summary']}（確認: {e['reviewed_at']} / {e['reviewer']}）")
        if c.get('old_affiliation_email'):
            notes.append('旧所属のメール。現職と資料掲載時の所属・時点を区別: ' + c['old_email_review']['summary'])
        if c.get('notes'):
            notes.append(c['notes'])
        name = c['name'] + (f"（{c['kana']}）" if c.get('kana') else '')
        rows.append(['' if has_date or rows else label, '', name, c['affiliation'], c['email'],
                     '\n'.join(sources), '\n'.join(notes)])
    counts = {status: sum(a['status'] == status for a in assessments)
              for status in ('eligible', 'duplicate', 'excluded', 'hold')}
    planned = {p: sum(c.get('profession') == p and c['id'] in selected for c in candidates)
               for p in ('doctor', 'dentist')}
    before = {k: inspection[k] for k in ('registered_rows', 'observed_named_rows', 'actual_people',
                                        'actual_people_reason', 'last_data_row')}
    return {'mode': 'dry_run', 'production_authorized': False, 'source': source,
            'assessment_scope': 'synthetic_reference_only' if source == 'fixture' else 'read_only_review',
            'canonical_url': SHEET_URL, 'snapshot_hash': fingerprint, 'fetched_at': snapshot['fetched_at'],
            'generated_at': now.isoformat(), 'date_timezone': 'Asia/Tokyo',
            'sheet_timezone_unchanged': snapshot.get('timezone'), 'sheet_inventory': inspection['sheets'],
            'before': before, 'after': dict(before),
            'newly_added': {'doctor': 0, 'dentist': 0, 'total': 0},
            'planned_additions': planned, 'candidate_counts': counts,
            'duplicate_excluded_people': 0 if not counts['duplicate'] else None,
            'count_note': '候補件数と実人数は別です。重複候補の人数も未同定なら不明です。',
            'assessments': assessments, 'blockers': blockers,
            'master_diff': master_diff, 'master_saved': False,
            'proposed_change': {'sheet_id': MAIN_SHEET_ID, 'start_row': inspection['last_data_row']+1,
                                'rows_A_G': rows, 'new_row_height_px': 20,
                                'preserve_existing_format_and_validation': True,
                                'format_status': '設計のみ・書式変更は未実装',
                                'operation_id': digest({'snapshot': fingerprint, 'rows': rows})},
            'post_save_verification': 'not_run_read_only',
            'limitations': ['本番書き込み機能は未実装。承認・保存検証は次段階。',
                            '資料の真偽は自動保証しません。人による根拠確認が必要です。',
                            '別表記・旧姓は明示入力が必要。機械照合だけで未登録を保証しません。']}
