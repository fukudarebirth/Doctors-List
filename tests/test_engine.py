from copy import deepcopy
from datetime import datetime, timezone
import unittest

from doctors_list import MAIN_SHEET_ID
from doctors_list.engine import evaluate, reiwa_day
from doctors_list.master import reconcile
from doctors_list.snapshot import inspect_snapshot, snapshot_hash
from doctors_list.validation import conditions, reviewed
from fixtures import NOW, candidate, review, row, snapshot


class EngineTests(unittest.TestCase):
    def assess(self, candidates, book=None, master=None, now=NOW):
        book = book or snapshot([row()])
        if master is None:
            master = reconcile(book)[0]
        return evaluate(book, candidates, master, now=now)

    def test_normal_new_candidate_and_dentist_plans_without_saving(self):
        c = candidate()
        d = candidate('c2', '架空桜子', 'sakura@example.invalid', profession='dentist')
        out = self.assess([c, d])
        self.assertEqual(out['candidate_counts']['eligible'], 2)
        self.assertEqual(out['planned_additions'], {'doctor': 1, 'dentist': 1})
        self.assertEqual(out['newly_added'], {'doctor': 0, 'dentist': 0, 'total': 0})
        self.assertFalse(out['production_authorized'])
        self.assertEqual(out['before'], out['after'])
        self.assertEqual(out['post_save_verification'], 'not_run_read_only')

    def test_primary_email_duplicate_excluded_from_plan(self):
        out = self.assess([candidate(email='existing@example.invalid')])
        self.assertEqual(out['assessments'][0]['status'], 'duplicate')
        self.assertEqual(out['proposed_change']['rows_A_G'], [])
        self.assertIsNone(out['duplicate_excluded_people'])

    def test_same_person_another_email_not_automatically_new(self):
        out = self.assess([candidate(name='架空既存')])
        self.assertEqual(out['assessments'][0]['status'], 'hold')
        self.assertEqual(out['assessments'][0]['matches'][0]['level'], 'suspected')

    def test_old_surname_and_old_kanji_both_require_review(self):
        out = self.assess([candidate(name='架空新姓', aliases=['高崎花子'])], snapshot([row(name='髙﨑 花子')]))
        self.assertEqual(out['assessments'][0]['status'], 'hold')

    def test_homonym_different_person_evidence_bound_to_snapshot(self):
        book = snapshot([row()])
        c = candidate(name='架空既存')
        c['resolutions'] = [{'record_id': f'{MAIN_SHEET_ID}:2', 'snapshot_hash': snapshot_hash(book),
                             'decision': 'different_person', 'review': review(summary='架空の別生年月日を確認')}]
        self.assertEqual(self.assess([c], book)['assessments'][0]['status'], 'eligible')
        c['resolutions'][0]['snapshot_hash'] = 'stale'
        self.assertEqual(self.assess([c], book)['assessments'][0]['status'], 'hold')

    def test_exact_email_cannot_be_dismissed_by_homonym_resolution(self):
        book = snapshot([row()])
        c = candidate(email='existing@example.invalid')
        c['resolutions'] = [{'record_id': f'{MAIN_SHEET_ID}:2', 'snapshot_hash': snapshot_hash(book),
                             'decision': 'different_person', 'review': review()}]
        self.assertEqual(self.assess([c], book)['assessments'][0]['status'], 'duplicate')

    def test_shared_email_excluded_even_with_reviewed_evidence(self):
        out = self.assess([candidate(email='info@example.invalid')])
        self.assertEqual(out['assessments'][0]['status'], 'excluded')
        self.assertIn('代表・共通メール', out['assessments'][0]['reasons'])

    def test_explicit_shared_email_excluded(self):
        out = self.assess([candidate(shared_email=True)])
        self.assertEqual(out['assessments'][0]['status'], 'excluded')

    def test_missing_age_evidence_held(self):
        c = candidate()
        del c['evidence']['age']
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')

    def test_url_alone_does_not_establish_truth(self):
        c = candidate()
        c['evidence']['email'] = {'urls': ['https://example.invalid/'], 'personal_public': True}
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')

    def test_old_affiliation_mail_is_individual_review_not_blanket_exclusion(self):
        c = candidate(old_affiliation_email=True)
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')
        c['old_email_review'] = review(decision='accept', summary='旧所属の資料発表年と現在の所属を個別確認')
        out = self.assess([c])
        self.assertEqual(out['assessments'][0]['status'], 'eligible')
        self.assertIn('旧所属のメール', out['proposed_change']['rows_A_G'][0][6])

    def test_career_estimate_requires_sufficient_basis_and_history_check(self):
        c = candidate()
        c['evidence']['age'] = review(method='career', max_age=40, basis_year=2026,
                                     sufficient=True, calculation='架空卒業年と確認した学歴から算出',
                                     nonstandard_history_checked=True)
        out = self.assess([c])
        self.assertEqual(out['assessments'][0]['status'], 'eligible')
        self.assertIn('年齢推定', out['proposed_change']['rows_A_G'][0][6])
        c['evidence']['age']['nonstandard_history_checked'] = False
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')

    def test_46_year_boundary_held(self):
        c = candidate()
        c['evidence']['age'] = review(method='birth_year', birth_year=1980)
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')

    def test_45_year_old_eligible(self):
        c = candidate()
        c['evidence']['age'] = review(method='birth_date', birth_date='1981-01-01')
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'eligible')

    def test_future_dob_and_future_reference_year_hold(self):
        c = candidate()
        c['evidence']['age'] = review(method='birth_date', birth_date='2027-01-01')
        self.assertEqual(self.assess([c])['assessments'][0]['status'], 'hold')
        self.assertEqual(self.assess([candidate()], now=datetime(2027, 1, 1, tzinfo=timezone.utc))['assessments'][0]['status'], 'hold')

    def test_duplicate_batch_candidates_both_blocked(self):
        a = candidate()
        b = candidate('candidate-2', '架空別表記', a['email'])
        out = self.assess([a, b])
        self.assertEqual(out['candidate_counts']['duplicate'], 2)
        self.assertEqual(out['planned_additions']['doctor'], 0)
        self.assertTrue(all(a['matches'][0]['scope'] == 'candidate_batch' for a in out['assessments']))

    def test_suspected_batch_same_person_other_email_both_held(self):
        a = candidate()
        b = candidate('candidate-2', a['name'], 'other@example.invalid')
        self.assertEqual(self.assess([a, b])['candidate_counts']['hold'], 2)

    def test_empty_rows_not_people_and_append_after_last_real_row(self):
        book = snapshot([row(), [], ['', '', ''], row('架空二郎', 'second@example.invalid'), [], []])
        out = self.assess([candidate()], book)
        self.assertEqual(out['before']['registered_rows'], 2)
        self.assertEqual(out['before']['last_data_row'], 5)
        self.assertEqual(out['proposed_change']['start_row'], 6)
        self.assertNotEqual(out['before']['last_data_row'], 1000)
        self.assertIsNone(out['before']['actual_people'])

    def test_nonempty_beyond_g_not_overwritten_by_plan(self):
        book = snapshot([row(), [], ['', '', '', '', '', '', '', '架空の注記']])
        out = evaluate(book, [candidate()], None, NOW)
        self.assertEqual(out['before']['last_data_row'], 4)
        self.assertEqual(out['proposed_change']['start_row'], 5)
        self.assertEqual(out['assessments'][0]['status'], 'hold')

    def test_zero_value_beyond_g_is_not_an_empty_row(self):
        book = snapshot([row(), [], ['', '', '', '', '', '', '', 0]])
        inspection = inspect_snapshot(book)
        self.assertEqual(inspection['last_data_row'], 4)

    def test_same_day_existing_date_not_repeated(self):
        out = self.assess([candidate()], snapshot([row(date='R8/11/4')]))
        self.assertEqual(out['proposed_change']['rows_A_G'][0][0], '')

    def test_first_candidate_only_gets_current_japan_date(self):
        a = candidate()
        b = candidate('c2', '架空桜子', 'sakura@example.invalid')
        out = self.assess([a, b])
        self.assertEqual([r[0] for r in out['proposed_change']['rows_A_G']], ['R8/11/4', ''])
        self.assertTrue(all(r[1] == '' for r in out['proposed_change']['rows_A_G']))

    def test_japan_day_boundary(self):
        out = self.assess([candidate()], now=datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(out['proposed_change']['rows_A_G'][0][0], 'R8/11/4')
        self.assertEqual(out['sheet_timezone_unchanged'], 'America/Los_Angeles')
        self.assertEqual(reiwa_day(datetime(2026, 11, 3, 14, 59, tzinfo=timezone.utc))[1], 'R8/11/3')

    def test_existing_formatted_gregorian_date_does_not_duplicate_day(self):
        out = self.assess([candidate()], snapshot([row(date='2026/11/4')]))
        self.assertEqual(out['proposed_change']['rows_A_G'][0][0], '')

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            self.assess([candidate()], now=datetime(2026, 11, 4))

    def test_unknown_sheet_and_changed_header_block(self):
        for changed in ('kind', 'header'):
            book = snapshot([row()])
            if changed == 'kind':
                book['sheets'][1]['kind'] = 'unknown'
            else:
                book['sheets'][0]['rows'][0][4] = '異なる列'
            out = evaluate(book, [candidate()], None, NOW)
            self.assertEqual(out['assessments'][0]['status'], 'hold')
            self.assertTrue(out['blockers'])
            self.assertIsNone(out['before']['registered_rows'])

    def test_non_person_tab_not_counted_as_people(self):
        out = self.assess([], snapshot([row()]))
        self.assertEqual(out['before']['registered_rows'], 1)
        self.assertEqual(len(out['sheet_inventory']), 2)
        self.assertIsNone(out['sheet_inventory'][1]['registered_rows'])

    def test_no_master_blocks_eligible_status(self):
        out = evaluate(snapshot([row()]), [candidate()], now=NOW)
        self.assertEqual(out['assessments'][0]['status'], 'hold')

    def test_removed_master_record_never_readded_unconditionally(self):
        old_book = snapshot([row(), row('架空若葉', 'wakaba@example.invalid')])
        old = reconcile(old_book)[0]
        out = self.assess([candidate()], snapshot([row()]), old)
        self.assertEqual(out['assessments'][0]['status'], 'hold')
        self.assertTrue(any(m['scope'] == 'history' for m in out['assessments'][0]['matches']))

    def test_rerun_is_stable_and_does_not_mutate_data(self):
        book, cs = snapshot([row()]), [candidate()]
        master = reconcile(book)[0]
        before = deepcopy((book, cs, master))
        a = self.assess(cs, book, master)
        b = self.assess(cs, book, master)
        self.assertEqual(a, b)
        self.assertEqual((book, cs, master), before)
        self.assertEqual(a['proposed_change']['new_row_height_px'], 20)
        self.assertTrue(a['proposed_change']['preserve_existing_format_and_validation'])
        self.assertIn('未実装', a['proposed_change']['format_status'])

    def test_google_snapshot_older_than_fifteen_minutes_is_held(self):
        book = snapshot([row()], source='google', fetched_at='2026-11-04T04:44:59+00:00')
        out = self.assess([candidate()], book)
        self.assertEqual(out['assessments'][0]['status'], 'hold')
        self.assertEqual(out['proposed_change']['rows_A_G'], [])
        self.assertTrue(any('再取得' in reason for reason in out['blockers']))
        book['fetched_at'] = '2026-11-04T04:45:00+00:00'
        self.assertEqual(self.assess([candidate()], book)['assessments'][0]['status'], 'eligible')

    def test_offline_snapshot_with_valid_master_still_held(self):
        book = snapshot([row()], source='offline')
        out = self.assess([candidate()], book)
        self.assertEqual(out['assessments'][0]['status'], 'hold')
        self.assertEqual(out['proposed_change']['rows_A_G'], [])
        self.assertTrue(any('オフライン' in reason for reason in out['blockers']))

    def test_non_person_tab_containing_person_headers_blocks_all_candidates(self):
        book = snapshot([row()])
        old_master = reconcile(book)[0]
        book['sheets'][1]['rows'].extend([deepcopy(book['sheets'][0]['rows'][0]), row('架空分類', 'secondary@example.invalid')])
        candidates = [candidate(), candidate('second', '架空桜子', 'sakura@example.invalid')]
        out = evaluate(book, candidates, old_master, NOW)
        self.assertEqual(out['candidate_counts']['hold'], 2)
        self.assertEqual(out['proposed_change']['rows_A_G'], [])
        self.assertTrue(any('非人物シートに人物表' in reason for reason in out['blockers']))
        self.assertIsNone(out['before']['registered_rows'])

    def test_candidate_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.assess([candidate(), candidate()])

    def test_invalid_candidate_array_rejected(self):
        with self.assertRaises(ValueError):
            self.assess({'not': 'array'})

    def test_no_truthy_strings_as_review(self):
        self.assertFalse(reviewed(review(confirmed='true')))
        self.assertFalse(reviewed(review(reviewed_at='2026-11-04')))
        self.assertFalse(reviewed(review(urls=['javascript:alert(1)'])))


if __name__ == '__main__':
    unittest.main()
