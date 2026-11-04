from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doctors_list.master import load_master, reconcile, sync_master
from doctors_list.storage import save_private_json
from fixtures import row, snapshot


class MasterTests(unittest.TestCase):
    def test_same_content_reuses_index_and_ignores_fetch_time(self):
        book = snapshot([row()])
        old = reconcile(book)[0]
        old['entries'][0]['aliases'] = ['架空旧姓']
        book['fetched_at'] = '2026-11-04T06:00:00+00:00'
        new, diff = reconcile(book, old)
        self.assertTrue(diff['content_equal'])
        self.assertEqual(diff['reused_records'], 1)
        self.assertEqual(new['entries'][0]['aliases'], ['架空旧姓'])

    def test_same_count_different_content_rebuilt_not_considered_equal(self):
        old = reconcile(snapshot([row()]))[0]
        changed = snapshot([row(email='new-mail@example.invalid')])
        new, diff = reconcile(changed, old)
        self.assertFalse(diff['content_equal'])
        self.assertEqual(diff['rebuilt_records'], 1)
        self.assertEqual(len(new['removed']), 1)
        self.assertEqual(new['removed'][0]['email'], 'existing@example.invalid')

    def test_deleted_record_retained_as_tombstone_and_not_current_entry(self):
        old = reconcile(snapshot([row(), row('架空二郎', 'second@example.invalid')]))[0]
        new, diff = reconcile(snapshot([row()]), old)
        self.assertEqual(len(new['entries']), 1)
        self.assertEqual(len(new['removed']), 1)
        again = reconcile(snapshot([row()]), new)[0]
        self.assertEqual(len(again['removed']), 1)

    def test_reordering_updates_locations_preserves_aliases(self):
        a, b = row(), row('架空二郎', 'second@example.invalid')
        old = reconcile(snapshot([a, b]))[0]
        old['entries'][0]['aliases'] = ['架空旧姓']
        new, diff = reconcile(snapshot([b, a]), old)
        self.assertEqual(diff['reused_records'], 2)
        self.assertEqual(new['entries'][1]['row'], 3)
        self.assertEqual(new['entries'][1]['aliases'], ['架空旧姓'])
        self.assertEqual(new['removed'], [])

    def test_same_row_content_on_two_person_tabs_keeps_both_indexes(self):
        book = snapshot([row()])
        secondary = deepcopy(book['sheets'][0])
        secondary.update(sheet_id=123456, title='架空分類シート')
        book['sheets'].append(secondary)
        old = reconcile(book)[0]
        old['entries'][0]['aliases'] = ['架空主表の旧姓']
        old['entries'][1]['aliases'] = ['架空分類表の旧姓']
        new, diff = reconcile(book, old)
        self.assertEqual(diff['reused_records'], 2)
        self.assertEqual(new['entries'][0]['aliases'], ['架空主表の旧姓'])
        self.assertEqual(new['entries'][1]['aliases'], ['架空分類表の旧姓'])

    def test_duplicate_rows_on_same_sheet_retain_union_of_aliases(self):
        book = snapshot([row(), row()])
        old = reconcile(book)[0]
        old['entries'][0].update(aliases=['架空旧姓一', '架空共通別名'], other_emails=['old-one@example.invalid'],
                                 previous_affiliations=['架空旧第一病院'], identity_key='fictional-identity-one',
                                 identity_reviewed=True)
        old['entries'][1].update(aliases=['架空旧姓二', '架空共通別名'], other_emails=['old-two@example.invalid'],
                                 previous_affiliations=['架空旧第二病院'], identity_key='fictional-identity-two',
                                 identity_reviewed=True)
        new, diff = reconcile(book, old)
        self.assertEqual(diff['reused_records'], 2)
        for entry in new['entries']:
            self.assertEqual(set(entry['aliases']), {'架空旧姓一', '架空共通別名', '架空旧姓二'})
            self.assertEqual(len(entry['aliases']), 3)
            self.assertEqual(set(entry['other_emails']), {'old-one@example.invalid', 'old-two@example.invalid'})
            self.assertEqual(set(entry['previous_affiliations']), {'架空旧第一病院', '架空旧第二病院'})
            self.assertNotIn('identity_key', entry)
            self.assertNotIn('identity_reviewed', entry)
        self.assertEqual(new['removed'], [])

    def test_change_outside_a_to_g_changes_snapshot(self):
        a = row()
        old = reconcile(snapshot([a]))[0]
        new, diff = reconcile(snapshot([a + ['extra fictional note']]), old)
        self.assertFalse(diff['content_equal'])
        self.assertEqual(diff['rebuilt_records'], 1)

    def test_unknown_sheets_prevent_master_sync(self):
        book = snapshot([row()])
        book['sheets'][1]['kind'] = 'unknown'
        with self.assertRaises(ValueError):
            reconcile(book)

    def test_fixture_never_saved_as_authoritative_master(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'master.json'
            with self.assertRaises(ValueError):
                sync_master(snapshot([row()]), target)
            self.assertFalse(target.exists())

    def test_live_snapshot_local_save_verified_and_repeatable(self):
        # Simulated Google marker; no API is invoked by this test.
        book = snapshot([row()], source='google')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'master.json'
            result = sync_master(book, target)
            self.assertTrue(result['save_verification']['verified'])
            restored = load_master(target)
            second = sync_master(book, target, restored)
            self.assertTrue(second['diff']['content_equal'])
            self.assertTrue(second['save_verification']['verified'])

    def test_save_error_propagates_without_success_report(self):
        book = snapshot([row()], source='google')
        with patch('doctors_list.master.save_private_json', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                sync_master(book, '/tmp/not-written-master.json')

    def test_foreign_or_unsupported_master_rejected(self):
        good = reconcile(snapshot([row()]))[0]
        cases = [[], {'schema_version': 99}, {**good, 'spreadsheet_id': 'wrong-source'},
                 {**good, 'entries': 'not-array'}, {**good, 'entries': [{'id': 'bad'}]}]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'master.json'
            for value in cases:
                with self.subTest(value=value):
                    save_private_json(target, value)
                    with self.assertRaises(ValueError):
                        load_master(target)

    def test_invalid_aliases_rejected_on_import(self):
        old = reconcile(snapshot([row()]))[0]
        old['entries'][0]['aliases'] = 'not-an-array'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'master.json'
            save_private_json(target, old)
            with self.assertRaises(ValueError):
                load_master(target)

    def test_reconcile_does_not_mutate_old_records(self):
        old = reconcile(snapshot([row()]))[0]
        before = deepcopy(old)
        reconcile(snapshot([row('架空変更', 'changed@example.invalid')]), old)
        self.assertEqual(old, before)


if __name__ == '__main__':
    unittest.main()
