"""Contact matching (Algorithm B). Pure logic: no Flask, no database.

Order: exact match first, then fuzzy. The result NEVER picks silently between similar
names: 'many' means "ask the user which one". Even a single match is only a proposal,
the caller must still get a spoken "yes" before anything happens.
"""
import unicodedata

from rapidfuzz import fuzz

from app.models.contact import name_key

STRONG = 85          # fuzzy score (0-100) needed to count as a match at all
CLEAR_LEAD = 10      # the best match must beat the runner-up by this much to stand alone
MAX_CHOICES = 3

# Bangla case endings that speech adds to a name: "ছেলেকে" (to son), "রহিমের" (Rahim's).
_SUFFIXES = [unicodedata.normalize('NFC', s) for s in ('দেরকে', 'কে', 'য়ের', 'ের', 'র')]
_Y = unicodedata.normalize('NFC', 'য়')


def _variants(spoken):
    """The spoken name, plus the same name with one Bangla case ending removed."""
    key = name_key(spoken)
    out = [key]
    for suffix in _SUFFIXES:
        if key.endswith(suffix) and len(key) - len(suffix) >= 2:
            out.append(key[:-len(suffix)])
    return out


def _names(contact):
    return [name_key(contact['name'])] + [name_key(a) for a in contact.get('aliases', [])]


def match_contact(contacts, spoken):
    """Return {'result': 'none' | 'one' | 'many', 'matches': [contact, ...]}."""
    if not isinstance(spoken, str) or not spoken.strip():
        return {'result': 'none', 'matches': []}
    variants = _variants(spoken)

    exact = [c for c in contacts if any(v in _names(c) for v in variants)]
    if len(exact) == 1:
        return {'result': 'one', 'matches': exact}
    if len(exact) > 1:
        return {'result': 'many', 'matches': exact[:MAX_CHOICES]}

    scored = []
    for contact in contacts:
        score = max(fuzz.ratio(v, n) for v in variants for n in _names(contact))
        if score >= STRONG:
            scored.append((score, contact))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return {'result': 'none', 'matches': []}
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= CLEAR_LEAD:
        return {'result': 'one', 'matches': [scored[0][1]]}
    return {'result': 'many', 'matches': [c for _, c in scored[:MAX_CHOICES]]}
