"""Google adapter and deterministic audit model. No service is opened at import."""
from copy import deepcopy
from . import MAIN_SHEET_ID, SPREADSHEET_ID
from .google_reader import read_snapshot, ReadError
from .snapshot import snapshot_hash
from .storage import digest

# Only stable user-authored state, not volatile formula results or effective styles.
AUDIT_FIELDS = ('spreadsheetId,properties,namedRanges,sheets(properties,merges,conditionalFormats,'
                'protectedRanges,basicFilter,filterViews,bandedRanges,data(startRow,startColumn,'
                'rowData(values(userEnteredValue,userEnteredFormat,dataValidation,note,textFormatRuns,'
                'pivotTable,dataSourceFormula,dataSourceTable)),rowMetadata(pixelSize,hiddenByUser),'
                'columnMetadata(pixelSize,hiddenByUser)))')


def clean_cell(cell):
    value = deepcopy(cell)
    if value.get('userEnteredValue') == {'stringValue': ''}:
        value.pop('userEnteredValue', None)
    return {k: v for k, v in value.items() if v not in ({}, [], None)}


def audit_model(raw):
    if raw.get('spreadsheetId') != SPREADSHEET_ID:
        raise ReadError('バックアップの正本IDが一致しません。')
    result = {k: deepcopy(v) for k, v in raw.items() if k != 'sheets'}
    result['sheets'] = {}
    for sheet in raw.get('sheets', []):
        sid = str(sheet['properties']['sheetId'])
        if sid in result['sheets']:
            raise ReadError('バックアップのシートIDが重複しています。')
        item = {k: deepcopy(v) for k, v in sheet.items() if k != 'data'}
        item.update(cells={}, row_sizes={}, column_sizes={})
        for block in sheet.get('data', []):
            sr, sc = block.get('startRow', 0), block.get('startColumn', 0)
            for r, row in enumerate(block.get('rowData', []), sr):
                for c, cell in enumerate(row.get('values', []), sc):
                    cleaned = clean_cell(cell)
                    if cleaned:
                        item['cells'][f'{r}:{c}'] = cleaned
            for field, target, start in [('rowMetadata', 'row_sizes', sr), ('columnMetadata', 'column_sizes', sc)]:
                for index, dimensions in enumerate(block.get(field, []), start):
                    if dimensions:
                        item[target][str(index)] = deepcopy(dimensions)
        result['sheets'][sid] = item
    if str(MAIN_SHEET_ID) not in result['sheets']:
        raise ReadError('バックアップにメインシートがありません。')
    return result


def _overlap(region, first, end):
    return (region.get('sheetId') == MAIN_SHEET_ID
            and region.get('startRowIndex', 0) < end
            and region.get('endRowIndex', end) > first
            and region.get('startColumnIndex', 0) < 7
            and region.get('endColumnIndex', 7) > 0)


def append_spec(model, report):
    """Require reviewable template, unused destination and uncomplicated main-only workflow."""
    change = report['proposed_change']
    rows, first = change['rows_A_G'], change['start_row'] - 1
    if not rows or first < 2 or any(len(row) != 7 or any(not isinstance(v, str) for v in row) for row in rows):
        raise ValueError('追記対象または既存データ行の書式見本を確認できません。')
    # Additional person sheets need an explicitly implemented propagation policy.
    if any(s['kind'] == 'person' and s['sheet_id'] != MAIN_SHEET_ID for s in report['sheet_inventory']):
        raise ValueError('分類・統合シートへの反映手順の実装が必要です。追記を停止します。')
    main = model['sheets'][str(MAIN_SHEET_ID)]
    if main['properties'].get('sheetType', 'GRID') != 'GRID':
        raise ValueError('標準グリッド以外のシートへの追記は未対応です。')
    end = first + len(rows)
    if any(_overlap(m, first-1, end) for m in main.get('merges', [])):
        raise ValueError('見本行または追記先に結合セルがあります。')
    # A formula displaying an empty string is still existing content.
    for address, cell in main['cells'].items():
        r, _ = map(int, address.split(':'))
        if r >= first and any(k in cell for k in ('userEnteredValue', 'pivotTable', 'dataSourceFormula', 'dataSourceTable')):
            raise ValueError('表示上の末尾より下に値・数式があります。追記位置を確認してください。')
    if not main['cells'].get(f'{first-1}:2', {}).get('userEnteredValue'):
        raise ValueError('書式見本の行に氏名がありません。')
    for c in range(7):
        validation = main['cells'].get(f'{first-1}:{c}', {}).get('dataValidation', {})
        if validation.get('condition', {}).get('type') in ('CUSTOM_FORMULA', 'ONE_OF_RANGE'):
            raise ValueError('見本行に相対参照を含み得る独自の入力規則があります。対応確認が必要です。')
    return {'first': first, 'end': end, 'rows': rows,
            'old_grid_rows': main['properties']['gridProperties']['rowCount']}


def expected_model(before, spec):
    expected = deepcopy(before)
    sheet = expected['sheets'][str(MAIN_SHEET_ID)]
    sheet['properties']['gridProperties']['rowCount'] = max(spec['old_grid_rows'], spec['end'])
    for r, row in enumerate(spec['rows'], spec['first']):
        for c, text in enumerate(row):
            key = f'{r}:{c}'
            cell = deepcopy(sheet['cells'].get(key, {}))
            template = before['sheets'][str(MAIN_SHEET_ID)]['cells'].get(f'{spec["first"]-1}:{c}', {})
            for field in ('userEnteredFormat', 'dataValidation'):
                cell.pop(field, None)
                if field in template:
                    cell[field] = deepcopy(template[field])
            cell['userEnteredValue'] = {'stringValue': text}
            cell.pop('textFormatRuns', None)
            cell = clean_cell(cell)
            if cell:
                sheet['cells'][key] = cell
            else:
                sheet['cells'].pop(key, None)
        dimensions = sheet['row_sizes'].setdefault(str(r), {})
        dimensions['pixelSize'] = 20
    return expected


def append_requests(spec):
    first, end = spec['first'], spec['end']
    dest = {'sheetId': MAIN_SHEET_ID, 'startRowIndex': first, 'endRowIndex': end,
            'startColumnIndex': 0, 'endColumnIndex': 7}
    source = {**dest, 'startRowIndex': first-1, 'endRowIndex': first}
    requests = []
    if end > spec['old_grid_rows']:
        requests.append({'appendDimension': {'sheetId': MAIN_SHEET_ID, 'dimension': 'ROWS', 'length': end-spec['old_grid_rows']}})
    for kind in ('PASTE_FORMAT', 'PASTE_DATA_VALIDATION'):
        requests.append({'copyPaste': {'source': source, 'destination': dest, 'pasteType': kind, 'pasteOrientation': 'NORMAL'}})
    requests.append({'updateCells': {'range': dest, 'rows': [
        {'values': [{'userEnteredValue': {'stringValue': text}} for text in row]} for row in spec['rows']],
        'fields': 'userEnteredValue'}})
    requests.append({'updateDimensionProperties': {'range': {'sheetId': MAIN_SHEET_ID, 'dimension': 'ROWS',
        'startIndex': first, 'endIndex': end}, 'properties': {'pixelSize': 20}, 'fields': 'pixelSize'}})
    return requests


class GoogleBackend:
    def __init__(self, service, config, writable=False):
        self.service, self.config, self.writable = service, config, writable

    def read(self):
        config = self.config
        before = read_snapshot(self.service, set(config['person_sheet_ids']), set(config['non_person_sheet_ids']))
        try:
            raw = self.service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID,
                includeGridData=True, fields=AUDIT_FIELDS).execute(num_retries=0)
        except Exception:
            raise ReadError('値・書式のバックアップ用読み取りに失敗しました。') from None
        after = read_snapshot(self.service, set(config['person_sheet_ids']), set(config['non_person_sheet_ids']))
        if snapshot_hash(before) != snapshot_hash(after):
            raise ReadError('取得中に正本が変わりました。再確認してください。')
        return after, audit_model(raw)

    def append(self, spec):
        if not self.writable:
            raise ReadError('読み取り専用の接続では書き込めません。')
        # One atomic API batch, no automatic retry after an uncertain response.
        self.service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID,
            body={'requests': append_requests(spec)}).execute(num_retries=0)
