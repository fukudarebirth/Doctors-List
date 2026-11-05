"""Fictional fixtures matching the supplied Library field layout; no real records."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doctors_list import SPREADSHEET_ID
from doctors_list.cli import main
from doctors_list.engine import evaluate
from doctors_list.legacy_master import import_library_master
from doctors_list.master import load_master, reconcile, sync_master
from doctors_list.storage import digest, save_private_json
from fixtures import NOW, candidate, row, snapshot


def person(identifier='fictional-1', cells=None):
    cells = cells or row()
    return {'person_id': identifier, 'sheet': '都内・公開メール医師', 'row': 2,
            'name': cells[2], 'primary_name': cells[2], 'primary_email': cells[4],
            'current_affiliation_position': cells[3], 'explicit_aliases': ['架空旧姓'],
            'affiliation_contexts': ['架空旧病院'], 'source_cells': cells}


def library():
    return {'schema_version': 'fictional-library-format', 'matching_rules_version': 'fictional-rules',
            'authoritative_source': {'spreadsheet_id': SPREADSHEET_ID},
            'verified_snapshot': {'registered_rows': 777, 'actual_people': 777, 'content_sha256': 'not-current'},
            'people': [person()], 'retired_people': [], 'external_identity_annotations': [],
            'name_alias_index': {}, 'email_mention_index': {},
            'maintenance_builder_python': "raise RuntimeError('NEVER EXECUTE')",
            'maintenance_append_sync_python': "raise RuntimeError('NEVER EXECUTE')",
            'sheet_snapshots': [{'title': '都内・公開メール医師', 'header': [], 'rows': []}],
            'research_log_history': [{'checked_date_jst': 'fictional', 'unadded_review_records': []}]}


class LegacyTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'master.json'
            save_private_json(path, value)
            before = path.read_bytes()
            result = load_master(path)
            self.assertEqual(path.read_bytes(), before)
            return result

    def test_import_retains_complete_archive_without_executing_code(self):
        original = library()
        value = self.load(original)
        self.assertEqual(value['legacy_archive'], original)
        self.assertEqual(value['legacy_archive_sha256'], digest(original))
        self.assertIsNone(value['snapshot_hash'])
        self.assertEqual(value['entries'][0]['aliases'], ['架空既存', '架空旧姓'])
        self.assertEqual(value['entries'][0]['previous_affiliations'], ['架空旧病院'])

    def test_import_does_not_mutate_object(self):
        original = library()
        before = deepcopy(original)
        result = import_library_master(original)
        result['legacy_archive']['people'].clear()
        self.assertEqual(original, before)

    def test_foreign_source_rejected(self):
        original = library()
        original['authoritative_source']['spreadsheet_id'] = 'another-book'
        with self.assertRaises(ValueError):
            self.load(original)

    def test_invalid_record_shapes_rejected(self):
        for field, value in [('source_cells', ['short']), ('primary_email', []), ('row', True),
                             ('explicit_aliases', 'not-list'), ('name', '')]:
            original = library()
            original['people'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(original)

    def test_unknown_nested_alias_kept_and_eligibility_blocked(self):
        original = library()
        original['people'][0]['explicit_aliases'] = [{'unknown_label': '架空別名'}]
        value = self.load(original)
        report = evaluate(snapshot([row()]), [candidate()], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')
        self.assertEqual(value['legacy_archive'], original)
        self.assertTrue(report['blockers'])

    def test_structured_aliases_and_contexts_mixed_with_strings(self):
        original = library()
        record = original['people'][0]
        record['explicit_aliases'] += [{'value': '架空別姓', 'label': '旧姓', 'origin': '架空資料'}]
        record['affiliation_contexts'] += [{'text': '架空過去大学', 'origin': '架空経歴'}]
        value = self.load(original)
        self.assertEqual(value['import_review_issues'], [])
        self.assertEqual(value['entries'][0]['aliases'], ['架空既存', '架空旧姓', '架空別姓'])
        self.assertEqual(value['entries'][0]['previous_affiliations'], ['架空旧病院', '架空過去大学'])
        self.assertEqual(value['legacy_archive'], original)

    def test_structured_alias_is_suspicion_not_confirmed_identity(self):
        original = library()
        original['people'][0]['explicit_aliases'] = [{'value': '架空別姓', 'label': '確認済み', 'origin': '架空資料'}]
        report = evaluate(snapshot([row()]), [candidate(name='架空別姓')], self.load(original), NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')
        self.assertEqual(report['assessments'][0]['matches'][0]['level'], 'suspected')

    def test_structured_retired_alias_preserved_as_history(self):
        original = library()
        record = person('retired', row('架空引退', 'retired@example.invalid'))
        record['explicit_aliases'] = [{'value': '架空旧々姓', 'label': '別名', 'origin': '架空資料'}]
        original['retired_people'].append(record)
        value = self.load(original)
        self.assertEqual(value['import_review_issues'], [])
        report = evaluate(snapshot([row()]), [candidate(name='架空旧々姓')], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')
        self.assertTrue(any(m['scope'] == 'history' for m in report['assessments'][0]['matches']))

    def test_bad_structured_metadata_types_or_unknown_keys_stay_blocked(self):
        cases = [
            ('explicit_aliases', {'value': ['架空別姓'], 'label': '旧姓', 'origin': '架空資料'}),
            ('explicit_aliases', {'value': '架空別姓', 'label': {}, 'origin': '架空資料'}),
            ('explicit_aliases', {'value': '架空別姓', 'label': '旧姓', 'origin': None}),
            ('explicit_aliases', {'value': '架空別姓', 'origin': '架空資料'}),
            ('affiliation_contexts', {'text': {}, 'origin': '架空資料'}),
            ('affiliation_contexts', {'text': '架空所属', 'origin': '架空資料', 'unrecognized': True}),
        ]
        for field, item in cases:
            with self.subTest(field=field, item=item):
                original = library(); original['people'][0][field] = [item]
                value = self.load(original)
                self.assertTrue(value['import_review_issues'])
                self.assertEqual(value['legacy_archive'], original)
                report = evaluate(snapshot([row()]), [candidate()], value, NOW)
                self.assertEqual(report['assessments'][0]['status'], 'hold')

    def test_structured_origin_is_inert_and_survives_save_and_reload(self):
        original = library()
        origin = "raise RuntimeError('DO NOT EXECUTE')"
        original['people'][0]['explicit_aliases'] = [{'value': '架空別姓', 'label': '架空別名', 'origin': origin}]
        original['people'][0]['affiliation_contexts'] = [{'text': '架空過去大学', 'origin': origin}]
        value = self.load(original)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'synced.json'
            sync_master(snapshot([row()], source='google'), output, value)
            saved = load_master(output)
            self.assertEqual(saved['legacy_archive'], original)
            self.assertIn('架空別姓', saved['entries'][0]['aliases'])
            self.assertIn('架空過去大学', saved['entries'][0]['previous_affiliations'])
            self.assertEqual(saved['import_review_issues'], [])

    def test_check_master_reports_supported_structures_without_person_values(self):
        original = library()
        original['people'][0]['explicit_aliases'] = [{'value': '架空別姓', 'label': '旧姓', 'origin': '架空資料'}]
        original['people'][0]['affiliation_contexts'] = [{'text': '架空過去大学', 'origin': '架空経歴'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'; save_private_json(path, original)
            before = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('doctors_list.cli.build_service') as service:
                self.assertEqual(main(['check-master', '--master', str(path)]), 0)
            service.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['review_issue_count'], 0)
            self.assertNotIn('架空', output.getvalue())
            self.assertNotIn('@', output.getvalue())
            self.assertEqual(path.read_bytes(), before)

    def test_matching_content_reuses_aliases_and_current_position(self):
        original = self.load(library())
        book = snapshot([[], row()])
        result, diff = reconcile(book, original)
        self.assertEqual(diff['reused_records'], 1)
        self.assertEqual(result['entries'][0]['row'], 3)
        self.assertEqual(result['entries'][0]['id'], '1997875360:3')
        self.assertIn('架空旧姓', result['entries'][0]['aliases'])
        self.assertEqual(result['removed'], [])
        self.assertEqual(result['legacy_archive'], original['legacy_archive'])

    def test_empty_trailing_cells_reused(self):
        cells = row(); cells[-1] = ''
        original = library(); original['people'] = [person(cells=cells)]
        result, diff = reconcile(snapshot([cells[:-1]]), self.load(original))
        self.assertEqual(diff['reused_records'], 1)
        self.assertEqual(result['removed'], [])

    def test_content_change_and_added_columns_do_not_reuse(self):
        for cells in (row() + ['new H value'], row(email='changed@example.invalid')):
            result, diff = reconcile(snapshot([cells]), self.load(library()))
            self.assertEqual(diff['reused_records'], 0)
            self.assertEqual(len(result['removed']), 1)

    def test_renamed_sheet_never_binds_by_row_number(self):
        original = library(); original['people'][0]['sheet'] = '架空旧タブ名'
        result, diff = reconcile(snapshot([row()]), self.load(original))
        self.assertEqual(diff['reused_records'], 0)
        self.assertEqual(len(result['removed']), 1)

    def test_retired_people_remain_history_and_hold(self):
        original = library()
        original['retired_people'] = [person('retired', row('架空引退', 'retired@example.invalid'))]
        value = self.load(original)
        report = evaluate(snapshot([row()]), [candidate(name='架空引退', email='retired@example.invalid')], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')
        self.assertTrue(any(m['scope'] == 'history' for m in report['assessments'][0]['matches']))

    def test_deleted_current_person_is_not_silently_reintroduced(self):
        value = self.load(library())
        report = evaluate(snapshot(), [candidate(name='架空既存', email='existing@example.invalid')], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')

    def test_annotations_are_search_clues_not_verified_identity(self):
        original = library()
        original['external_identity_annotations'] = [{'person_id': 'fictional-1', 'official_name': '架空新姓',
            'official_kana': 'かくうしんせい', 'email': 'alternate@example.invalid',
            'identity_evidence': 'fictional', 'master_change_applied': True}]
        value = self.load(original)
        result, _ = reconcile(snapshot([row()]), value)
        record = result['entries'][0]
        self.assertIn('架空新姓', record['aliases'])
        self.assertIn('alternate@example.invalid', record['other_emails'])
        self.assertNotIn('identity_reviewed', record)
        report = evaluate(snapshot([row()]), [candidate(name='架空新姓', email='alternate@example.invalid')], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')

    def test_multiple_detached_annotations_preserved_without_crash(self):
        original = library()
        original['external_identity_annotations'] = [
            {'person_id': 'missing-1', 'official_name': '架空注記一', 'email': 'one@example.invalid'},
            {'person_id': 'missing-2', 'official_name': '架空注記二', 'email': 'two@example.invalid'}]
        value = self.load(original)
        self.assertEqual(len(value['removed']), 2)
        self.assertTrue(all(r['email'] == '' for r in value['removed']))

    def test_same_content_across_tabs_preserves_separate_aliases(self):
        original = library()
        extra = person('other'); extra['sheet'] = '架空分類'; extra['explicit_aliases'] = ['分類旧姓']
        original['people'].append(extra)
        book = snapshot([row()])
        sheet = deepcopy(book['sheets'][0]); sheet.update(sheet_id=123, title='架空分類')
        book['sheets'].append(sheet)
        result, diff = reconcile(book, self.load(original))
        self.assertEqual(diff['reused_records'], 2)
        self.assertIn('架空旧姓', result['entries'][0]['aliases'])
        self.assertIn('分類旧姓', result['entries'][1]['aliases'])

    def test_alias_name_index_is_only_a_hold(self):
        original = library(); original['name_alias_index'] = {'架空索引のみ': [{'opaque': 'ignored'}]}
        report = evaluate(snapshot([row()]), [candidate(name='架空索引のみ')], self.load(original), NOW)
        self.assertEqual(report['assessments'][0]['status'], 'hold')

    def test_email_mentions_never_establish_person_contact(self):
        original = library(); original['email_mention_index'] = {'wakaba@example.invalid': [{'coauthor': 'fictional'}]}
        value = self.load(original)
        report = evaluate(snapshot([row()]), [candidate()], value, NOW)
        self.assertEqual(report['assessments'][0]['status'], 'eligible')
        self.assertEqual(value['legacy_archive']['email_mention_index'], original['email_mention_index'])

    def test_current_counts_ignore_legacy_claims(self):
        report = evaluate(snapshot([row()]), [], self.load(library()), NOW)
        self.assertEqual(report['before']['registered_rows'], 1)
        self.assertIsNone(report['before']['actual_people'])
        self.assertEqual(report['newly_added']['total'], 0)

    def test_sync_save_reload_retains_archive_and_is_repeatable(self):
        value = self.load(library())
        book = snapshot([row()], source='google')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'updated.json'
            self.assertTrue(sync_master(book, output, value)['save_verification']['verified'])
            saved = load_master(output)
            self.assertEqual(saved['legacy_archive'], library())
            self.assertTrue(sync_master(book, output, saved)['diff']['content_equal'])
            self.assertEqual(load_master(output)['removed'], [])

    def test_archive_integrity_mismatch_rejected(self):
        value = self.load(library())
        value['legacy_archive']['people'].clear()
        with self.assertRaises(ValueError):
            self.load(value)
        with self.assertRaises(ValueError):
            reconcile(snapshot(), value)

    def test_check_master_console_only_safe_metadata_no_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.json'; save_private_json(path, library())
            before = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('doctors_list.cli.build_service') as service:
                code = main(['check-master', '--master', str(path)])
            self.assertEqual(code, 0)
            service.assert_not_called()
            self.assertNotIn('existing@example.invalid', output.getvalue())
            self.assertNotIn('架空', output.getvalue())
            summary = json.loads(output.getvalue())
            self.assertTrue(summary['master_loaded'])
            self.assertFalse(summary['canonical_read_this_run'])
            self.assertFalse(summary['file_changed'])
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
