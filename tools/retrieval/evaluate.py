"""Evaluate reviewed retrieval gold; optionally use the runtime conversation planner."""
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx
from rahbar.config import load_retrieval_settings
from rahbar.retrieval.answer import AnswerService
from rahbar.retrieval.evaluation import score, validate_gold
from rahbar.retrieval.gemini import GeminiRouter
from rahbar.retrieval.hybrid import HybridIndex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('gold', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--revision', required=True)
parser.add_argument('--split', choices=['development', 'test'], default='development')
parser.add_argument('--url')
parser.add_argument('--mode', choices=['hybrid', 'dense', 'bm25'], default='hybrid')
parser.add_argument('--planned', action='store_true', help='Use runtime follow-up and facet planning')
parser.add_argument('--project-root', type=Path)
parser.add_argument('--free-tier-confirmed', action='store_true')
args = parser.parse_args()
if args.planned and (not args.project_root or not args.free_tier_confirmed):
    parser.error('--planned requires --project-root and --free-tier-confirmed')
rows = [json.loads(line) for line in args.gold.read_text().splitlines() if line.strip()]
validate_gold(rows)
index = HybridIndex(args.directory, args.revision, args.url)


async def run():
    async with httpx.AsyncClient() as client:
        service = None
        if args.planned:
            settings = load_retrieval_settings(args.project_root)
            service = AnswerService(GeminiRouter(client, settings.gemini_api_key,
                                                list(settings.models)), index.search)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as output:
            for row in rows:
                if row['split'] != args.split:
                    continue
                start = perf_counter()
                queries, model, clarification = [row['question']], None, False
                if service:
                    plan, model = await service.plan(row['question'], row.get('history', []))
                    clarification = bool(plan.clarification)
                    queries = list(dict.fromkeys([row['question'], plan.standalone, *plan.subquestions]))
                results = [] if clarification else [
                    index.search(query, 10, mode=args.mode) for query in queries]
                # Match runtime's round-robin facet ordering before its publication gate.
                ids = list(dict.fromkeys(hit['passage_id'] for rank in range(10)
                           for hits in results for hit in hits[rank:rank+1]))[:10]
                result = {'id': row['id'], 'language': row['language'],
                          'family_id': row['family_id'], 'retrieved_ids': ids,
                          'latency_ms': (perf_counter()-start)*1000,
                          'collection': index.collection, 'mode': args.mode,
                          'embedding_revision': args.revision,
                          'query_strategy': 'runtime_planner' if service else 'raw_question_ablation',
                          'planner_model': model, 'queries': queries,
                          'clarification': clarification}
                if row['expected_outcome'] == 'answered':
                    result['at_5'] = score(ids, row['evidence_groups'], 5)
                    result['at_10'] = score(ids, row['evidence_groups'], 10)
                output.write(json.dumps(result, ensure_ascii=False) + '\n')
                output.flush()


try:
    asyncio.run(run())
finally:
    index.client.close()
