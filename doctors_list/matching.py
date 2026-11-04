"""Conservative identity matching, independent of Google and source research.

``confirmed`` means a registration collision, not necessarily proven identity:
identical primary contact addresses are collisions even for two different people.
Only independently reviewed identity keys establish person identity here.  Name,
spelling, alias, reading, or similarity matches always remain ``suspected``.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

# This is a deliberately bounded search-key map, not a claim that names coincide.
_VARIANTS = str.maketrans({
    "髙": "高", "﨑": "崎", "邊": "辺", "邉": "辺", "齋": "斎",
    "齊": "斎", "斉": "斎", "濱": "浜", "澤": "沢", "櫻": "桜",
    "廣": "広", "國": "国", "龍": "竜", "瀨": "瀬", "德": "徳",
})


def normalize_name(value: str) -> str:
    """Produce a search key, including width, kana, case, and selected variants.

    Katakana is folded to hiragana; no pronunciation or romanization is guessed.
    Returned equality is a reason to review, never proof of the same person.
    """
    if not isinstance(value, str):
        raise TypeError("Name must be a string")
    value = unicodedata.normalize("NFKC", value).casefold().translate(_VARIANTS)
    value = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)
    return "".join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith(("P", "Z")))


def normalize_email(value: str) -> str:
    """Normalize spelling only. Never remove dots or plus-tags or infer addresses."""
    if not isinstance(value, str):
        raise TypeError("Email must be a string")
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [s for s in value if isinstance(s, str) and s.strip()]
    return []


def _name_keys(record: dict) -> set[str]:
    values: list[str] = []
    for field in ("name", "kana", "romanized", "aliases"):
        values.extend(_strings(record.get(field)))
    # Column C commonly contains "氏名（ふりがな）". Keep both explicit parts;
    # this does not generate a reading or infer a surname.
    for value in list(values):
        normalized = unicodedata.normalize("NFKC", value)
        match = re.fullmatch(r"\s*([^()]+?)\s*\(([^()]+)\)\s*", normalized)
        if match:
            values.extend(match.groups())
    return {key for value in values if (key := normalize_name(value))}


def _reviewed_identity(record: dict) -> str | None:
    key = record.get("identity_key")
    if record.get("identity_reviewed") is True and isinstance(key, str) and key.strip():
        return key.strip()
    return None


def compare(a: dict, b: dict) -> dict | None:
    """Return a confirmed/suspected collision with auditable reason codes.

    Explicit ``other_emails`` are possible previous personal addresses, producing
    a review signal unless independently reviewed identity keys also match.
    Source/notes text is intentionally never searched for email addresses, because
    it can mention other authors. Previous/current affiliation differences never
    negate a name match; affiliation alone never identifies somebody.

    Distinct independently reviewed keys can resolve a homonym; they cannot
    override a shared primary address. Review evidence must be validated by the
    caller before setting ``identity_reviewed=True``.
    """
    primary_a = normalize_email(a.get("email", ""))
    primary_b = normalize_email(b.get("email", ""))
    identity_a, identity_b = _reviewed_identity(a), _reviewed_identity(b)
    if primary_a and primary_a == primary_b:
        reasons = ["primary_email_exact"]
        if identity_a and identity_b and identity_a != identity_b:
            reasons.append("email_identity_conflict")
        return {"level": "confirmed", "reasons": reasons}
    if identity_a and identity_b:
        if identity_a == identity_b:
            return {"level": "confirmed", "reasons": ["reviewed_same_identity"]}

    reasons: list[str] = []
    emails_a = {normalize_email(s) for s in _strings(a.get("other_emails"))}
    emails_b = {normalize_email(s) for s in _strings(b.get("other_emails"))}
    if primary_a:
        emails_a.add(primary_a)
    if primary_b:
        emails_b.add(primary_b)
    if (emails_a & emails_b) - {""}:
        reasons.append("alternate_email_overlap")
    if identity_a and identity_b and identity_a != identity_b:
        return ({"level": "suspected", "reasons": reasons + ["email_identity_conflict"]}
                if reasons else None)

    names_a, names_b = _name_keys(a), _name_keys(b)
    if names_a & names_b:
        reasons.append("name_or_explicit_alias_overlap")
    elif any(len(x) >= 4 and len(y) >= 4 and SequenceMatcher(None, x, y).ratio() >= 0.88
             for x in names_a for y in names_b):
        reasons.append("name_similarity_review_required")

    if not reasons:
        return None
    # These fields explain a possible change; they never strengthen a guess into
    # a confirmed match or make different workplaces prove different identities.
    affiliations_a = {normalize_name(s) for s in _strings(a.get("affiliation")) + _strings(a.get("previous_affiliations"))}
    affiliations_b = {normalize_name(s) for s in _strings(b.get("affiliation")) + _strings(b.get("previous_affiliations"))}
    if affiliations_a & affiliations_b:
        reasons.append("affiliation_context_overlap")
    return {"level": "suspected", "reasons": reasons}
