"""Exact curriculum joins and explicit, provenance-backed publication review."""
from collections import defaultdict
from typing import Any


def map_passages(records: list[dict[str, Any]], curriculum: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in curriculum['units']:
        by_key[unit['content_key']].append(unit)
    mapped: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    for record in records:
        units = {unit['unit_id']: unit for topic in record['topics'] for unit in by_key.get(topic, [])}
        unresolved.update(topic for topic in record['topics'] if topic not in by_key)
        mapped.append(dict(record, unit_ids=sorted(units), track_ids=sorted({
            track for unit in units.values() for track in unit.get('track_ids', [])}),
            mapping_status='mapped' if units else 'unresolved', publication_eligible=False))
    return mapped, {'passages': len(mapped), 'mapped': sum(bool(r['unit_ids']) for r in mapped),
                    'unresolved_topics': sorted(unresolved), 'curriculum_version': curriculum['version']}


def apply_reviews(records: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {r['passage_id'] for r in records}
    by_id = {r['passage_id']: r for r in reviews}
    if len(by_id) != len(reviews) or set(by_id) - known:
        raise ValueError('Duplicate or unknown passage review')
    result = []
    for record in records:
        row = dict(record, publication_eligible=False)
        review = by_id.get(record['passage_id'])
        if review:
            if review.get('status') not in {'reviewed', 'withheld'}:
                raise ValueError('Review must explicitly approve or withhold a passage')
            if not review.get('reviewed_by') or not review.get('review_reference'):
                raise ValueError('Human reviewer identity and review reference required')
            if review.get('safety') not in {'ordinary', 'safety', 'procedure'}:
                raise ValueError('Explicit safety classification required')
            if review['status'] == 'reviewed' and (not record.get('unit_ids') or not record.get('provenance')):
                raise ValueError('Unmapped or untraceable passage cannot be published')
            row.update(review_status=review['status'], reviewed_by=review['reviewed_by'],
                       review_reference=review['review_reference'], safety=review['safety'],
                       publication_eligible=review['status'] == 'reviewed')
        result.append(row)
    return result
