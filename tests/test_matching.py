import unittest

from doctors_list.matching import compare, normalize_email, normalize_name


class MatchingTests(unittest.TestCase):
    def person(self, **kw):
        return {"name": "架空 花子", "email": "fictional.a@example.invalid", **kw}

    def test_exact_email_is_collision_not_identity_claim(self):
        result = compare(self.person(), self.person(name="架空 別人"))
        self.assertEqual(result, {"level": "confirmed", "reasons": ["primary_email_exact"]})

    def test_same_name_other_email_requires_review(self):
        self.assertEqual(compare(self.person(), self.person(email="fictional.b@example.invalid"))["level"], "suspected")

    def test_reviewed_identity_same_person_different_email(self):
        a = self.person(identity_key="fictional-person-1", identity_reviewed=True)
        b = self.person(name="別表記", email="other@example.invalid", identity_key="fictional-person-1", identity_reviewed=True)
        self.assertEqual(compare(a, b)["reasons"], ["reviewed_same_identity"])

    def test_homonyms_can_be_different_reviewed_people(self):
        a = self.person(identity_key="fictional-person-1", identity_reviewed=True)
        b = self.person(email="other@example.invalid", identity_key="fictional-person-2", identity_reviewed=True)
        self.assertIsNone(compare(a, b))

    def test_distinct_keys_do_not_override_shared_primary_address(self):
        a = self.person(identity_key="fictional-person-1", identity_reviewed=True)
        b = self.person(identity_key="fictional-person-2", identity_reviewed=True)
        self.assertIn("email_identity_conflict", compare(a, b)["reasons"])

    def test_distinct_reviewed_identity_keys_do_not_hide_alternate_email_conflict(self):
        a = self.person(identity_key='fictional-person-1', identity_reviewed=True,
                        other_emails=['former@example.invalid'])
        b = self.person(name='架空別人', email='different@example.invalid',
                        identity_key='fictional-person-2', identity_reviewed=True,
                        other_emails=['former@example.invalid'])
        hit = compare(a, b)
        self.assertEqual(hit['level'], 'suspected')
        self.assertIn('alternate_email_overlap', hit['reasons'])
        self.assertIn('email_identity_conflict', hit['reasons'])
        self.assertEqual(hit, compare(b, a))

    def test_unreviewed_identity_not_trusted(self):
        a = self.person(identity_key="fictional-person-1")
        b = self.person(email="other@example.invalid", identity_key="fictional-person-2")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_old_surname_alias(self):
        a = self.person(name="架空 花子", aliases=["試験 花子"])
        b = self.person(name="試験花子", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_traditional_characters(self):
        a = self.person(name="髙﨑 齋子")
        b = self.person(name="高崎斉子", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_kana_width_spaces_and_script(self):
        a = self.person(kana="ｶｸｳ ﾊﾅｺ")
        b = self.person(name="別の表記", kana="かくう　はなこ", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_latin_explicit_romanized_form(self):
        a = self.person(romanized="KAKUU HANAKO")
        b = self.person(name="Kakuu Hanako", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_column_c_reading_parentheses(self):
        a = self.person(name="架空 花子（かくう はなこ）")
        b = self.person(name="架空花子", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_changed_affiliation_does_not_prove_new_person(self):
        a = self.person(affiliation="架空旧病院")
        b = self.person(email="other@example.invalid", affiliation="架空新病院", previous_affiliations=["架空旧病院"])
        result = compare(a, b)
        self.assertEqual(result["level"], "suspected")
        self.assertIn("affiliation_context_overlap", result["reasons"])

    def test_affiliation_only_is_not_identity(self):
        a = self.person(affiliation="架空病院")
        b = self.person(name="試験 太郎", email="other@example.invalid", affiliation="架空病院")
        self.assertIsNone(compare(a, b))

    def test_alternate_email_needs_identity_review(self):
        a = self.person(other_emails=["old@example.invalid"])
        b = self.person(name="別表記", email="old@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_coauthor_email_in_notes_not_scanned(self):
        a = self.person(notes="共同著者 other@example.invalid", source="other@example.invalid")
        b = self.person(name="試験 太郎", email="other@example.invalid")
        self.assertIsNone(compare(a, b))

    def test_fuzzy_name_not_confirmed(self):
        a = self.person(name="Fictional Hanako")
        b = self.person(name="Fictional Hanko", email="other@example.invalid")
        self.assertEqual(compare(a, b)["level"], "suspected")

    def test_partial_short_name_not_matched(self):
        a = self.person(name="架空")
        b = self.person(name="架空太郎", email="other@example.invalid")
        self.assertIsNone(compare(a, b))

    def test_empty_fields_never_match(self):
        self.assertIsNone(compare({"name": "", "email": ""}, {"name": "", "email": ""}))

    def test_normalizations(self):
        self.assertEqual(normalize_name("髙﨑　ﾊﾅｺ"), normalize_name("高崎 はなこ"))
        self.assertEqual(normalize_email(" Ａ@EXAMPLE.INVALID "), "a@example.invalid")
        self.assertNotEqual(normalize_email("a.b@example.invalid"), normalize_email("ab@example.invalid"))
        self.assertNotEqual(normalize_email("a+x@example.invalid"), normalize_email("a@example.invalid"))

    def test_symmetry_for_candidate_pairs(self):
        a, b = self.person(), self.person(email="other@example.invalid")
        self.assertEqual(compare(a, b), compare(b, a))


if __name__ == "__main__":
    unittest.main()
