"""Deterministic retrieval metrics; gold families must not cross data splits."""
from __future__ import annotations

import math
from typing import Any


def validate_gold(rows: list[dict[str, Any]]) -> None:
    families: dict[str, str] = {}
    ids: set[str] = set()
    for row in rows:
        if row['id'] in ids:
            raise ValueError('Duplicate question ID')
        ids.add(row['id'])
        if row['split'] not in {'development', 'test'}:
            raise ValueError('Invalid split')
        previous = families.setdefault(row['family_id'], row['split'])
        if previous != row['split']:
            raise ValueError('Scenario family leaks across splits')
        if row.get('review_status') != 'reviewed' or not row.get('reviewers'):
            raise ValueError('Gold questions require recorded human review')
        if row['expected_outcome'] == 'answered' and not row['evidence_groups']:
            raise ValueError('Answerable question has no gold evidence')


def score(retrieved: list[str], groups: list[list[str]], k: int = 10) -> dict[str, float]:
    if not groups or any(not group for group in groups):
        raise ValueError('Recall requires nonempty evidence groups')
    ranking = list(dict.fromkeys(retrieved))[:k]
    relevant = set().union(*(set(group) for group in groups))
    covered = [bool(set(group).intersection(ranking)) for group in groups]
    # Recall is fact-group based; nDCG uses binary passage relevance.
    first = next((rank for rank, identifier in enumerate(ranking, 1)
                  if identifier in relevant), 0)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    dcg = sum(1 / math.log2(i + 2) for i, identifier in enumerate(ranking)
              if identifier in relevant)
    return {'recall': sum(covered) / len(covered), 'complete_evidence': float(all(covered)),
            'mrr': 1 / first if first else 0., 'ndcg': min(dcg / ideal, 1.)}
