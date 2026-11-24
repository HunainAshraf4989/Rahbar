"""Narrow eligibility policy for prepared conceptual Q&A; no procedure/spec approval."""
import re
from typing import Any

PROCEDURAL = re.compile(
    r'\b(?:connect(?:ing)?|disconnect|remov(?:e|ing|al)|install(?:ing|ation)?|tighten|loosen|'
    r'must|always|never|pin|terminal|steps?|touch|danger|safety|should|procedure|test(?:ing|er)?|'
    r'check(?:ing)?|press|insert|replace|measure|select|sandpaper|silicone)\b', re.I)


def prepared_fact_eligible(row: dict[str, Any]) -> bool:
    if not row.get('unit_ids') or not row.get('provenance') or row.get('review_flags'):
        return False
    if row.get('review_status') == 'withheld':
        return False
    text = row['canonical_text']
    body = text.split('\n', 1)[-1] if text.startswith('Source topic:') else text
    match = re.match(r'(?:what (?:is|are)|why).+?[?؟]\s*(.+)$', body, re.I | re.S)
    if not match or not 5 <= len(match[1].split()) <= 80:
        return False
    # Numeric specifications and procedural claims require separate evidence review.
    return not re.search(r'\d', body) and not PROCEDURAL.search(body)


def supplied_content_eligible(row: dict[str, Any]) -> bool:
    """Use the supplied cleaned corpus without claiming expert review."""
    sources = row.get('provenance')
    return bool(row.get('passage_id') and str(row.get('canonical_text', '')).strip()
        and row.get('review_status') != 'withheld' and not row.get('exclusion_reason')
        and isinstance(sources, list) and sources
        and isinstance(sources[0], dict)
        and sources[0].get('location') and sources[0].get('source_hash'))
