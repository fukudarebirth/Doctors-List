"""Import the Library people/source_cells format as data, never executable code."""
from copy import deepcopy
from . import SPREADSHEET_ID
from .storage import digest


def import_library_master(value):
    source = value.get('authoritative_source')
    if not isinstance(source, dict) or source.get('spreadsheet_id') != SPREADSHEET_ID:
        raise ValueError('除外マスターの参照元IDが正本と異なります。')
    if not isinstance(value.get('schema_version'), str) or not value['schema_version']:
        raise ValueError('Libraryマスターの形式識別子がありません。')
    issues = []

    def records(field):
        items = value.get(field, [])
        if not isinstance(items, list):
            raise ValueError('Libraryマスターの人物配列が不正です。')
        result = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Libraryマスターの人物記録が不正です。')
            for key in ('person_id', 'sheet', 'name', 'primary_name', 'primary_email', 'current_affiliation_position'):
                if not isinstance(item.get(key), str):
                    raise ValueError('Libraryマスターの人物項目が不足・形式不正です。')
            if not item['person_id'] or not item['name'].strip() or type(item.get('row')) is not int or item['row'] < 1:
                raise ValueError('Libraryマスターの人物識別・行位置が不正です。')
            cells = item.get('source_cells')
            if not isinstance(cells, list) or len(cells) != 7 or any(c is not None and not isinstance(c, (str, int, float, bool)) for c in cells):
                raise ValueError('LibraryマスターのA〜G記録が不正です。')
            aliases = [item['name'], item['primary_name']]
            contexts = []
            for key, target in (('explicit_aliases', aliases), ('affiliation_contexts', contexts)):
                extra = item.get(key, [])
                if not isinstance(extra, list):
                    raise ValueError('Libraryマスターの別表記配列が不正です。')
                fields = ({'value', 'label', 'origin'} if key == 'explicit_aliases'
                          else {'text', 'origin'})
                text_field = 'value' if key == 'explicit_aliases' else 'text'
                for part in extra:
                    if isinstance(part, str):
                        target.append(part)
                    elif (isinstance(part, dict) and set(part) == fields
                          and all(isinstance(part[field], str) for field in fields)):
                        # Extract the supplied text only. label/origin remain in
                        # legacy_archive; neither is an identity approval or code.
                        target.append(part[text_field])
                    else:
                        # Keep unfamiliar structures in the archive, block eligibility.
                        issues.append('Libraryマスターの別表記・旧所属に未対応の構造があります。')
            result.append({'id': 'library:' + digest({'kind': field, 'record': item}),
                           'legacy_person_id': item['person_id'], 'legacy_sheet_title': item['sheet'],
                           'sheet_id': None, 'row': item['row'],
                           'name': item['primary_name'] or item['name'],
                           'email': item['primary_email'], 'affiliation': item['current_affiliation_position'],
                           'aliases': list(dict.fromkeys(a for a in aliases if a.strip())),
                           'other_emails': [item['primary_email']] if item['primary_email'] and item['primary_email'] != cells[4] else [],
                           'previous_affiliations': contexts,
                           'cells': ['' if c is None else str(c) for c in cells],
                           'raw_cells': ['' if c is None else str(c) for c in cells]})
        return result

    if 'people' not in value:
        raise ValueError('Libraryマスターの人物一覧がありません。')
    entries, removed = records('people'), records('retired_people')
    annotations = value.get('external_identity_annotations', [])
    if not isinstance(annotations, list):
        raise ValueError('Libraryマスターの本人照合注記が不正です。')
    for note in annotations:
        if not isinstance(note, dict):
            raise ValueError('Libraryマスターの本人照合注記が不正です。')
        names = [note.get(k) for k in ('official_name', 'official_kana', 'master_name') if note.get(k)]
        if any(not isinstance(n, str) for n in names) or not names:
            issues.append('Libraryマスターの本人照合注記に未確認の構造があります。')
            continue
        email = note.get('email', '')
        if not isinstance(email, str):
            raise ValueError('Libraryマスターの注記メール形式が不正です。')
        targets = [r for r in entries + removed if r.get('legacy_person_id') and r.get('legacy_person_id') == note.get('person_id')]
        if not targets:
            # Detached annotations remain review signals, never an active registration.
            target = {'id': 'library-annotation:' + digest(note), 'name': names[0],
                      'email': '', 'aliases': [], 'other_emails': []}
            removed.append(target)
            targets = [target]
        for record in targets:
            record['aliases'] = list(dict.fromkeys(record.get('aliases', []) + names))
            record['other_emails'] = list(dict.fromkeys(record.get('other_emails', []) + ([email] if email else [])))
    name_index = value.get('name_alias_index', {})
    if not isinstance(name_index, dict):
        raise ValueError('Libraryマスターの氏名索引が不正です。')
    return {'schema_version': 1, 'spreadsheet_id': SPREADSHEET_ID,
            'snapshot_hash': None, 'entries': entries, 'removed': removed,
            'matching_version': 1, 'imported_format': 'library_people',
            'legacy_archive': deepcopy(value), 'legacy_archive_sha256': digest(value),
            'historical_name_keys': list(name_index),
            'import_review_issues': list(dict.fromkeys(issues))}


def bind_library_rows(records, snapshot):
    """Map title to current ID only; content equality is still checked by reconcile."""
    def cell_key(cells):
        cells = list(cells)
        while cells and cells[-1] == '':
            cells.pop()
        return tuple(cells)

    by_title = {}
    rows_by_sheet = {}
    for sheet in snapshot['sheets']:
        by_title.setdefault(sheet['title'], []).append(sheet['sheet_id'])
        rows_by_sheet[sheet['sheet_id']] = {cell_key(r): r for r in sheet['rows'][1:]}
    bound = deepcopy(records)
    for record in bound:
        if record.get('sheet_id') is None and record.get('legacy_sheet_title'):
            ids = by_title.get(record['legacy_sheet_title'], [])
            if len(ids) == 1:
                record['sheet_id'] = ids[0]
                # Sheets omits empty trailing cells. Treat these as empty, but
                # preserve substantive H+ cells and require full content equality.
                raw = rows_by_sheet[ids[0]].get(cell_key(record.get('raw_cells', [])))
                if raw is not None:
                    record['raw_cells'] = list(raw)
    return bound
