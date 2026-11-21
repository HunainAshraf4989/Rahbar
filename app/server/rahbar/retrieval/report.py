"""Aggregate retrieval results by language with scenario-cluster bootstrap intervals."""
import json
import random
from collections import defaultdict
from typing import Any


def summarize(rows: list[dict[str, Any]], seed: int = 20261117) -> dict[str, Any]:
    # Fallback models and retrieval ablations are different experimental conditions.
    conditions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fields = ('collection', 'mode', 'embedding_revision', 'query_strategy', 'planner_model')
    for row in rows:
        key = json.dumps({field: row.get(field) for field in fields}, sort_keys=True)
        conditions[key].append(row)
    if len(conditions) > 1:
        return {'conditions': [dict(configuration=json.loads(key), report=summarize(items, seed))
                               for key, items in sorted(conditions.items())]}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if 'at_10' in row:
            groups['overall'].append(row)
            groups[row['language']].append(row)
    result: dict[str, Any] = {}
    for name, items in groups.items():
        families: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            families[row['family_id']].append(row)
        keys = sorted(families)
        metrics = {}
        for metric in ['recall', 'complete_evidence', 'mrr', 'ndcg']:
            rng = random.Random(seed)
            samples = []
            for _ in range(1000):
                draw = [r for key in rng.choices(keys, k=len(keys)) for r in families[key]]
                samples.append(sum(r['at_10'][metric] for r in draw) / len(draw))
            samples.sort()
            metrics[metric] = {'mean': sum(r['at_10'][metric] for r in items)/len(items),
                               'ci95': [samples[24], samples[974]]}
        times = sorted(r['latency_ms'] for r in items)
        result[name] = {'questions':len(items), 'families':len(keys), 'metrics_at_10':metrics,
                        'latency_ms_median':times[len(times)//2],
                        'latency_ms_p90':times[min(len(times)-1, int(.9*len(times)))]}
    return {'bootstrap_seed':seed, 'bootstrap_unit':'scenario_family', 'slices':result}
