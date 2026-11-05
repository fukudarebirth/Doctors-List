"""Fictional, in-memory integration tests. Never contacts Google."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from fixtures import NOW, candidate, row, snapshot
from doctors_list import MAIN_SHEET_ID, SPREADSHEET_ID
from doctors_list import writer
from doctors_list.master import reconcile
from doctors_list.sheets_write import audit_model, append_requests, append_spec, GoogleBackend
from doctors_list.storage import load_json, digest
from doctors_list.google_reader import ReadError
from doctors_list.workflow import write_token_path

SID = str(MAIN_SHEET_ID)


def input_candidate():
    c = candidate(email='fictional@fictional-clinic.edu')
    for e in c['evidence'].values():
        e['urls'] = ['https://fictional-clinic.edu/fictional-evidence']
    return c


class MemoryBackend:
    def __init__(self):
        self.snapshot = snapshot([row()], source='google')
        raw = {'spreadsheetId': SPREADSHEET_ID, 'properties': {'timeZone': 'America/Los_Angeles'}, 'sheets': []}
        for s in self.snapshot['sheets']:
            raw['sheets'].append({'properties': {'sheetId': s['sheet_id'], 'title': s['title'], 'gridProperties': {'rowCount': s['row_count'], 'columnCount': 7}},
                'data': [{'rowData': [{'values': [{'userEnteredValue': {'stringValue': v},
                     'userEnteredFormat': {'textFormat': {'fontFamily': 'Arial'}, 'horizontalAlignment': 'LEFT'}} for v in r]} for r in s['rows']],
                     'rowMetadata': [{'pixelSize': 21} for _ in s['rows']], 'columnMetadata': [{'pixelSize': 110} for _ in range(7)]}]})
        self.model = audit_model(raw)
        self.model['sheets'][SID]['cells']['1:1']['dataValidation'] = {'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': '配信済み'}]}, 'strict': True}
        self.append_calls = 0
        self.read_calls = 0
        self.on_read = None
        self.before_append = None
        self.after_append = None
        self.fail_before = False
        self.fail_after = False

    def read(self):
        self.read_calls += 1
        if self.on_read:
            self.on_read(self)
        return deepcopy(self.snapshot), deepcopy(self.model)

    def append(self, spec):
        self.append_calls += 1
        if self.before_append:
            self.before_append(self)
        if self.fail_before:
            raise TimeoutError('fake transport failure')
        # Simulate server mutations independently of expected_model.
        sheet = self.model['sheets'][SID]
        main = self.snapshot['sheets'][0]
        main['row_count'] = max(main['row_count'], spec['end'])
        sheet['properties']['gridProperties']['rowCount'] = main['row_count']
        while len(main['rows']) < spec['end']:
            main['rows'].append([])
        for r, values in enumerate(spec['rows'], spec['first']):
            main['rows'][r] = list(values)
            for c, value in enumerate(values):
                cell = deepcopy(sheet['cells'].get(f'{r}:{c}', {}))
                template = sheet['cells'].get(f'{spec["first"]-1}:{c}', {})
                for field in ('userEnteredFormat', 'dataValidation'):
                    cell.pop(field, None)
                    if template.get(field):
                        cell[field] = deepcopy(template[field])
                cell.pop('textFormatRuns', None)
                cell.pop('userEnteredValue', None)
                if value:
                    cell['userEnteredValue'] = {'stringValue': value}
                if cell:
                    sheet['cells'][f'{r}:{c}'] = cell
                else:
                    sheet['cells'].pop(f'{r}:{c}', None)
            sheet['row_sizes'].setdefault(str(r), {})['pixelSize'] = 20
        if self.after_append:
            self.after_append(self)
        if self.fail_after:
            raise TimeoutError('fake response loss after commit')


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = patch('doctors_list.writer.utcnow', return_value=NOW)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.backend = MemoryBackend()
        self.master = reconcile(self.backend.snapshot)[0]
        self.candidates = [input_candidate()]
        self.plan = writer.prepare(self.backend, self.candidates, self.master, NOW)

    def apply(self, plan=None):
        plan = plan or self.plan
        return writer.apply(self.backend, plan, self.root, writer.approval_phrase(plan), True, NOW)

    def journal(self):
        return load_json(self.root / self.plan['plan_id'] / 'journal.json')

    def test_prepare_never_mutates_and_unknown_people_stays_unknown(self):
        self.assertEqual(self.backend.append_calls, 0)
        self.assertIsNone(self.plan['payload']['report']['before']['actual_people'])
        self.assertEqual(self.plan['payload']['spec']['first'], 2)

    def test_requires_exact_approval_and_single_writer(self):
        for approval, single in [('yes', True), (writer.approval_phrase(self.plan), False)]:
            with self.assertRaises(writer.WriteStopped):
                writer.apply(self.backend, self.plan, self.root, approval, single, NOW)
        self.assertEqual(self.backend.append_calls, 0)

    def test_plan_tampering_rejected(self):
        self.plan['payload']['spec']['rows'][0][2] = '改変'
        with self.assertRaises(writer.WriteStopped):
            self.apply()
        self.assertEqual(self.backend.append_calls, 0)

    def test_expired_and_future_plan_rejected(self):
        for delta in (-1, 901):
            with self.assertRaises(writer.WriteStopped):
                writer.apply(self.backend, self.plan, self.root, writer.approval_phrase(self.plan), True, NOW + timedelta(seconds=delta))
        self.assertEqual(self.backend.append_calls, 0)

    def test_changed_value_or_format_requires_new_approval(self):
        for kind in ('value', 'width'):
            with self.subTest(kind=kind):
                b = deepcopy(self.backend)
                if kind == 'value':
                    b.snapshot['sheets'][0]['rows'][1][6] = '編集'
                else:
                    b.model['sheets'][SID]['column_sizes']['0']['pixelSize'] = 123
                with self.assertRaises(writer.WriteStopped):
                    writer.apply(b, self.plan, self.root, writer.approval_phrase(self.plan), True, NOW)
                self.assertEqual(b.append_calls, 0)

    def test_final_read_change_aborts_without_blocking_new_plan(self):
        def change(b):
            if b.read_calls == 3:
                b.model['sheets'][SID]['column_sizes']['0']['pixelSize'] = 130
        self.backend.on_read = change
        with self.assertRaises(writer.WriteStopped): self.apply()
        self.assertEqual(self.backend.append_calls, 0)
        self.assertEqual(self.journal()['state'], 'not_sent_changed')
        new = writer.prepare(self.backend, self.candidates, self.master, NOW)
        self.assertEqual(self.apply(new)['newly_added']['total'], 1)

    def test_backup_failure_sends_nothing(self):
        with patch('doctors_list.writer.save_private_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.backend.append_calls, 0)

    def test_durable_backup_before_single_append_and_replay(self):
        before = deepcopy(self.backend.model)
        def inspect(b):
            folder = self.root / self.plan['plan_id']
            self.assertEqual(load_json(folder / 'backup.json')['plan'], self.plan)
            self.assertEqual(load_json(folder / 'journal.json')['state'], 'submitted')
        self.backend.before_append = inspect
        report = self.apply()
        self.assertEqual(report['newly_added'], {'doctor': 1, 'dentist': 0, 'total': 1})
        self.assertEqual(report['after']['registered_rows'], 2)
        self.assertEqual(report['after']['last_data_row'], 3)
        self.assertIsNone(report['after']['actual_people'])
        self.assertEqual(self.journal()['state'], 'verified')
        main = self.backend.model['sheets'][SID]
        self.assertEqual(main['row_sizes']['2']['pixelSize'], 20)
        self.assertEqual(main['cells']['2:1']['dataValidation'], before['sheets'][SID]['cells']['1:1']['dataValidation'])
        self.assertEqual(main['column_sizes'], before['sheets'][SID]['column_sizes'])
        self.assertEqual(self.backend.model['properties'], before['properties'])
        self.assertTrue(self.apply()['already_completed'])
        self.assertEqual(self.backend.append_calls, 1)
        # A new preparation rechecks current rows and cannot add the same candidate.
        with self.assertRaises(writer.WriteStopped):
            writer.prepare(self.backend, self.candidates, self.master, NOW)

    def test_timeout_after_commit_verifies_without_retry(self):
        self.backend.fail_after = True
        self.assertEqual(self.apply()['post_save_verification'], 'verified')
        self.assertTrue(self.journal()['response_uncertain'])
        self.assertEqual(self.backend.append_calls, 1)

    def test_timeout_before_commit_blocks_replay_and_other_operations(self):
        self.backend.fail_before = True
        with self.assertRaises(writer.WriteStopped): self.apply()
        with self.assertRaises(writer.WriteStopped): self.apply()
        with self.assertRaises(writer.WriteStopped): writer.recover(self.backend, self.root, self.plan['plan_id'])
        other = input_candidate(); other.update(id='other', name='架空別候補', email='another@fictional-clinic.edu')
        plan = writer.prepare(self.backend, [other], self.master, NOW)
        with self.assertRaises(writer.WriteStopped): self.apply(plan)
        self.assertEqual(self.backend.append_calls, 1)

    def test_read_failure_after_commit_recovers_without_mutation(self):
        def read(b):
            if b.append_calls:
                raise ReadError('fake read error')
        self.backend.on_read = read
        with self.assertRaises(writer.WriteStopped): self.apply()
        self.backend.on_read = None
        report = writer.recover(self.backend, self.root, self.plan['plan_id'])
        self.assertEqual(report['post_save_verification'], 'verified')
        self.assertEqual(self.backend.append_calls, 1)

    def test_post_save_detects_wrong_value_existing_format_width_and_height(self):
        for kind in ('value', 'existing', 'width', 'height', 'validation'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                b = MemoryBackend()
                def corrupt(b):
                    main = b.model['sheets'][SID]
                    if kind == 'value': main['cells']['2:2']['userEnteredValue'] = {'stringValue': '不一致'}
                    if kind == 'existing': main['cells']['1:2']['userEnteredFormat'] = {'textFormat': {'bold': True}}
                    if kind == 'width': main['column_sizes']['0']['pixelSize'] = 20
                    if kind == 'height': main['row_sizes']['2']['pixelSize'] = 21
                    if kind == 'validation': main['cells']['2:1'].pop('dataValidation')
                b.after_append = corrupt
                with self.assertRaises(writer.WriteStopped):
                    writer.apply(b, self.plan, tmp, writer.approval_phrase(self.plan), True, NOW)
                self.assertEqual(b.append_calls, 1)
                self.assertFalse((Path(tmp) / self.plan['plan_id'] / 'master-updated.json').exists())

    def test_same_day_does_not_repeat_date(self):
        self.backend.snapshot['sheets'][0]['rows'][1][0] = 'R8/11/4'
        plan = writer.prepare(self.backend, self.candidates, self.master, NOW)
        self.assertEqual(plan['payload']['spec']['rows'][0][0], '')

    def test_multiple_professions_contiguous_rows_one_date_and_blank_delivery(self):
        second = input_candidate()
        second.update(id='dentist', name='架空歯科候補', email='dentist@fictional-clinic.edu', profession='dentist')
        plan = writer.prepare(self.backend, self.candidates + [second], self.master, NOW)
        report = self.apply(plan)
        self.assertEqual(report['newly_added'], {'doctor': 1, 'dentist': 1, 'total': 2})
        rows = self.backend.snapshot['sheets'][0]['rows']
        self.assertEqual(rows[2][0], 'R8/11/4')
        self.assertEqual(rows[3][0], '')
        self.assertEqual([r[1] for r in rows[2:]], ['', ''])
        self.assertEqual(report['after']['last_data_row'], 4)
        self.assertEqual(self.backend.model['sheets'][SID]['row_sizes']['3']['pixelSize'], 20)

    def test_fictional_sample_and_offline_cannot_prepare_for_production(self):
        with self.assertRaises(writer.WriteStopped):
            writer.prepare(self.backend, [candidate()], self.master, NOW)
        self.backend.snapshot['source'] = 'fixture'
        with self.assertRaises(writer.WriteStopped):
            writer.prepare(self.backend, self.candidates, self.master, NOW)

    def test_local_lock_prevents_other_process_session(self):
        with writer.local_writer_lock(self.root):
            with self.assertRaises(writer.WriteStopped):
                with writer.local_writer_lock(self.root): pass

    def test_unsafe_template_or_hidden_formula_stops(self):
        for kind in ('formula', 'merged', 'validation', 'other_person'):
            with self.subTest(kind=kind):
                model = deepcopy(self.plan['payload']['before_model'])
                report = deepcopy(self.plan['payload']['report'])
                main = model['sheets'][SID]
                if kind == 'formula': main['cells']['10:0'] = {'userEnteredValue': {'formulaValue': '=""'}}
                if kind == 'merged': main['merges'] = [{'sheetId': MAIN_SHEET_ID, 'startRowIndex': 1, 'endRowIndex': 3}]
                if kind == 'validation': main['cells']['1:1']['dataValidation']['condition']['type'] = 'CUSTOM_FORMULA'
                if kind == 'other_person': report['sheet_inventory'].append({'sheet_id': 5, 'kind': 'person'})
                with self.assertRaises(ValueError): append_spec(model, report)

    def test_batch_only_new_cells_and_rows_raw_string_no_retry(self):
        spec = deepcopy(self.plan['payload']['spec']); spec['rows'][0][6] = '=IMPORTXML("fake")'
        service = MagicMock()
        GoogleBackend(service, {}, writable=True).append(spec)
        call = service.spreadsheets().batchUpdate.call_args.kwargs
        self.assertEqual(call['spreadsheetId'], SPREADSHEET_ID)
        req = call['body']['requests']
        self.assertEqual(len(req), 4)
        self.assertEqual(req[2]['updateCells']['fields'], 'userEnteredValue')
        self.assertEqual(req[2]['updateCells']['rows'][0]['values'][6]['userEnteredValue'], {'stringValue': spec['rows'][0][6]})
        self.assertEqual(req[2]['updateCells']['range']['startRowIndex'], 2)
        self.assertEqual(req[3]['updateDimensionProperties']['properties'], {'pixelSize': 20})
        service.spreadsheets().batchUpdate().execute.assert_called_once_with(num_retries=0)
        service.reset_mock()
        with self.assertRaises(ReadError): GoogleBackend(service, {}).append(spec)
        service.spreadsheets.assert_not_called()

    def test_capacity_growth_rows_only(self):
        spec = deepcopy(self.plan['payload']['spec']); spec['old_grid_rows'] = 2
        req = append_requests(spec)
        self.assertEqual(req[0], {'appendDimension': {'sheetId': MAIN_SHEET_ID, 'dimension': 'ROWS', 'length': 1}})

    def test_separate_write_token(self):
        config = {'token_path': str(self.root / 'token.json'), 'client_secrets_path': str(self.root / 'client.json')}
        self.assertEqual(write_token_path(config), self.root / 'token-write.json')
        for key in ('token_path', 'client_secrets_path'):
            with self.assertRaises(ValueError): write_token_path({**config, 'write_token_path': config[key]})

    def test_google_backend_detects_changes_during_backup_read(self):
        service = MagicMock()
        changed = deepcopy(self.backend.snapshot)
        changed['sheets'][0]['rows'][1][6] = 'concurrent change'
        with patch('doctors_list.sheets_write.read_snapshot', side_effect=[self.backend.snapshot, changed]):
            with self.assertRaises(ReadError):
                GoogleBackend(service, {'person_sheet_ids': [], 'non_person_sheet_ids': []}).read()
        service.spreadsheets().batchUpdate.assert_not_called()

    def test_date_or_expiry_during_backup_aborts_without_send(self):
        with patch('doctors_list.writer.utcnow', return_value=NOW + timedelta(days=1)):
            with self.assertRaises(writer.WriteStopped): self.apply()
        self.assertEqual(self.backend.append_calls, 0)
        self.assertEqual(self.journal()['state'], 'not_sent_changed')

    def test_master_disk_failure_after_append_recovers_without_retry(self):
        original = writer.save_private_json
        def fail_master(path, value):
            if Path(path).name == 'master-updated.json': raise OSError('disk full')
            return original(path, value)
        with patch('doctors_list.writer.save_private_json', side_effect=fail_master):
            with self.assertRaises(writer.WriteStopped): self.apply()
        self.assertEqual(self.backend.append_calls, 1)
        self.assertEqual(self.journal()['state'], 'needs_review')
        self.assertEqual(writer.recover(self.backend, self.root, self.plan['plan_id'])['newly_added']['total'], 1)
        self.assertEqual(self.backend.append_calls, 1)


if __name__ == '__main__': unittest.main()
