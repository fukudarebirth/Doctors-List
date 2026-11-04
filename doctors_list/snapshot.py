import re
import unicodedata
from . import HEADERS, MAIN_SHEET_ID, SPREADSHEET_ID
from .storage import digest


def headers_match(row):
    clean = lambda value: ''.join(unicodedata.normalize('NFKC', str(value)).split())
    return [clean(c) for c in row[:7]] == [clean(c) for c in HEADERS]


def snapshot_hash(snapshot):
    # Ignore acquisition time, include sheet identities, classification, headers and ALL cells.
    return digest({'spreadsheet_id': snapshot['spreadsheet_id'],
                   'timezone': snapshot.get('timezone'), 'sheets': snapshot['sheets']})


def inspect_snapshot(snapshot):
    if snapshot.get('spreadsheet_id') != SPREADSHEET_ID:
        raise ValueError('指定の正本IDと一致しません。')
    sheets = snapshot.get('sheets')
    if not isinstance(sheets, list) or not sheets:
        raise ValueError('全シートの取得結果がありません。')
    ids = [s['sheet_id'] for s in sheets]
    if len(set(ids)) != len(ids) or MAIN_SHEET_ID not in ids:
        raise ValueError('シート構成が不正、またはメインシートがありません。')
    records, issues, inventory = [], [], []
    main_last = 1
    for sheet in sheets:
        rows = sheet.get('rows', [])
        if not isinstance(rows, list) or any(not isinstance(r, list) for r in rows):
            raise ValueError('シート行形式が不正です。')
        if any(not isinstance(cell, (str, int, float, bool)) and cell is not None for r in rows for cell in r):
            raise ValueError('セル形式が不正です。')
        is_main = sheet['sheet_id'] == MAIN_SHEET_ID
        kind = sheet.get('kind', 'unknown')
        if is_main and (sheet['title'] != '都内・公開メール医師' or kind != 'person'):
            raise ValueError('メインシートの名前・分類が指定と異なります。')
        last = max((i for i, r in enumerate(rows, 1) if any(c is not None and str(c).strip() for c in r)), default=0)
        if is_main:
            main_last = max(1, last)
        if kind not in ('person', 'non_person'):
            issues.append(f"シート分類が未確認: {sheet['sheet_id']}")
        if kind == 'non_person' and any(headers_match(r) for r in rows):
            issues.append(f"非人物シートに人物表を検出。分類を再確認: {sheet['sheet_id']}")
        count = 0
        if kind == 'person':
            if not rows or not headers_match(rows[0]):
                issues.append(f"人物シートの列構成を確認してください: {sheet['sheet_id']}")
                inventory.append({'sheet_id': sheet['sheet_id'], 'title': sheet['title'], 'kind': kind,
                                  'last_data_row': last, 'registered_rows': None})
                continue
            for number, raw in enumerate(rows[1:], 2):
                cells = [str(c) if c is not None else '' for c in raw] + [''] * 7
                if not any(v.strip() for v in cells):
                    continue
                if not cells[2].strip():
                    issues.append(f"氏名なしの非空行: {sheet['sheet_id']}:{number}")
                    continue
                count += 1
                name = cells[2].strip()
                parts = re.match(r'^(.*?)\s*[（(]([^()（）]+)[）)]\s*$', name)
                records.append({'id': f"{sheet['sheet_id']}:{number}", 'sheet_id': sheet['sheet_id'],
                                'row': number, 'name': parts[1].strip() if parts else name,
                                'kana': parts[2] if parts else '', 'email': cells[4].strip(),
                                'affiliation': cells[3], 'cells': cells[:7], 'raw_cells': raw})
        inventory.append({'sheet_id': sheet['sheet_id'], 'title': sheet['title'], 'kind': kind,
                          'grid_rows': sheet.get('row_count'), 'last_data_row': last,
                          'registered_rows': count if kind == 'person' else None})
    return {'records': records, 'issues': issues, 'sheets': inventory,
            'registered_rows': len(records) if not issues else None,
            'observed_named_rows': len(records),
            # No identity audit was supplied for existing A-G rows. Email != person ID.
            'actual_people': 0 if not records and not issues else None,
            'actual_people_reason': '既存全行の本人同定が未確認のため実人数を確定しません。' if records or issues else '',
            'last_data_row': main_last}
