from datetime import date, datetime
import re
from urllib.parse import urlsplit
from .matching import normalize_email

SHARED = {'info', 'contact', 'office', 'admin', 'support', 'reception', 'reserve',
          'reservation', 'yoyaku', 'iryoku', 'ikyoku', 'secretary', 'inquiry', 'mail'}


def reviewed(evidence):
    if not isinstance(evidence, dict) or evidence.get('confirmed') is not True:
        return False
    if not all(isinstance(evidence.get(k), str) and evidence[k].strip()
               for k in ('reviewer', 'reviewed_at', 'summary')):
        return False
    try:
        stamp = datetime.fromisoformat(evidence['reviewed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return False
    except ValueError:
        return False
    urls = evidence.get('urls')
    return isinstance(urls, list) and bool(urls) and all(
        isinstance(u, str) and urlsplit(u).scheme in ('https', 'http') and bool(urlsplit(u).hostname)
        for u in urls)


def validate_candidates(candidates):
    if not isinstance(candidates, list):
        raise ValueError('候補ファイルは候補オブジェクトの配列にしてください。')
    ids = set()
    for c in candidates:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k].strip() for k in ('id', 'name', 'email')):
            raise ValueError('各候補には文字列の id, name, email が必要です。')
        if c['id'] in ids:
            raise ValueError('候補IDが重複しています。')
        ids.add(c['id'])
        for k in ('aliases', 'other_emails', 'previous_affiliations'):
            if k in c and (not isinstance(c[k], list) or any(not isinstance(x, str) for x in c[k])):
                raise ValueError('別表記リストの形式が不正です。')
        if 'evidence' in c and not isinstance(c['evidence'], dict):
            raise ValueError('根拠オブジェクトの形式が不正です。')


def conditions(candidate, today):
    """Mechanical assessment of human-reviewed evidence, never web truth verification."""
    hold, exclude = [], []
    email = normalize_email(candidate['email'])
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        hold.append('メール文字列が未確定・形式不正')
    if email.split('@')[0].split('+')[0] in SHARED or candidate.get('shared_email') is True:
        exclude.append('代表・共通メール')
    evidence = candidate.get('evidence', {})
    for key, label in [('email', '本人公開個人メール'), ('age', '年齢'), ('affiliation', '現在の所属')]:
        if not reviewed(evidence.get(key)):
            hold.append(f'{label}の根拠・確認者・確認日時が不足')
    mail = evidence.get('email', {})
    if not isinstance(mail, dict):
        mail = {}
    if mail.get('personal_public') is not True:
        hold.append('本人による公開個人メールであることが未確認')
    if mail.get('inferred') is True or mail.get('ocr_only') is True:
        hold.append('推測・OCRのみのメールを確定できません')
    if candidate.get('old_affiliation_email') is True:
        decision = candidate.get('old_email_review', {})
        if not reviewed(decision) or decision.get('decision') != 'accept':
            hold.append('旧所属メールの採用可否を資料に基づいて個別確認')
    affiliation = evidence.get('affiliation', {})
    if not isinstance(affiliation, dict) or affiliation.get('current') is not True or not candidate.get('affiliation'):
        hold.append('医師・歯科医師としての現在の勤務・所属が未確認')
    if candidate.get('profession') not in ('doctor', 'dentist'):
        hold.append('医師・歯科医師の区分が未確認')
    if candidate.get('conflicts'):
        hold.append('資料間に未解消の不一致')
    age = evidence.get('age', {})
    if not isinstance(age, dict):
        age = {}
    age_text = ''
    if today.year != 2026:
        hold.append('運用基準年2026年の変更をユーザーに確認してください')
    try:
        if age.get('method') == 'birth_date':
            born = date.fromisoformat(age['birth_date'])
            maximum = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            if born > today:
                raise ValueError()
            age_text = f'{maximum}歳（生年月日根拠、確認基準日{today}）'
        elif age.get('method') == 'birth_year':
            year = age['birth_year']
            if type(year) is not int or not 1900 <= year <= today.year:
                raise ValueError()
            maximum = today.year - year
            age_text = f'{maximum-1}〜{maximum}歳（生年根拠、{today.year}年）'
        elif age.get('method') == 'career':
            maximum = age['max_age']
            if (type(maximum) is not int or maximum < 0 or age.get('basis_year') != today.year
                    or age.get('sufficient') is not True or not age.get('calculation')
                    or age.get('nonstandard_history_checked') is not True):
                raise ValueError()
            age_text = f'年齢推定：{maximum}歳以下（{age["basis_year"]}年、{age["calculation"]}）'
        else:
            raise ValueError()
        if maximum > 45:
            hold.append('45歳以下と確定できない・境界例を含む')
    except (KeyError, TypeError, ValueError):
        hold.append('年齢の計算根拠が不足')
    return {'hold': hold, 'exclude': exclude, 'age_note': age_text}
