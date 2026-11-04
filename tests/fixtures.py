"""All records in this module are fictional; no live personal information."""
from copy import deepcopy
from datetime import datetime, timezone
from doctors_list import HEADERS, MAIN_SHEET_ID, SPREADSHEET_ID

NOW = datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)


def review(**extra):
    return {'confirmed': True, 'reviewer': '架空確認担当',
            'reviewed_at': '2026-11-04T09:00:00+09:00',
            'summary': '架空資料の本人記載を目視確認',
            'urls': ['https://fictional.example.invalid/evidence'], **extra}


def candidate(identifier='candidate-1', name='架空若葉', email='wakaba@example.invalid', **extra):
    return {'id': identifier, 'name': name, 'email': email, 'profession': 'doctor',
            'affiliation': '架空第一病院 医師',
            'evidence': {
                'email': review(personal_public=True),
                'age': review(method='birth_date', birth_date='1990-06-03'),
                'affiliation': review(current=True),
            }, **extra}


def snapshot(rows=None, **extra):
    return {'spreadsheet_id': SPREADSHEET_ID, 'fetched_at': NOW.isoformat(),
            'source': 'fixture', 'timezone': 'America/Los_Angeles',
            'sheets': [
                {'sheet_id': MAIN_SHEET_ID, 'title': '都内・公開メール医師',
                 'kind': 'person', 'row_count': 1000, 'rows': [deepcopy(HEADERS), *(rows or [])]},
                {'sheet_id': 1046147091, 'title': 'LP・小冊子',
                 'kind': 'non_person', 'row_count': 100, 'rows': [['資料名', 'リンク'], ['架空小冊子', 'https://example.invalid/']]},
            ], **extra}


def row(name='架空既存', email='existing@example.invalid', date='R8/11/3'):
    return [date, '', name, '架空既存病院 医師', email, 'https://example.invalid/', '架空データ']
