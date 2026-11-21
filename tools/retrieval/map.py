"""Join prepared passages to exact curriculum keys; optionally apply recorded reviews."""
import argparse
import json
from pathlib import Path

from rahbar.retrieval.mapping import apply_reviews, map_passages

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('corpus',type=Path)
parser.add_argument('curriculum',type=Path)
parser.add_argument('output',type=Path)
parser.add_argument('--reviews',type=Path)
args=parser.parse_args()
records=[json.loads(line) for line in (args.corpus/'passages.jsonl').read_text().splitlines()]
rows,report=map_passages(records,json.loads(args.curriculum.read_text()))
if args.reviews:
    rows=apply_reviews(rows,[json.loads(line) for line in args.reviews.read_text().splitlines()])
args.output.mkdir(parents=True,exist_ok=False)
(args.output/'passages.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows))
(args.output/'mapping-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='unresolved_topics'}))
