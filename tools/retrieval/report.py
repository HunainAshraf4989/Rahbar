"""Summarize a saved retrieval evaluation JSONL run."""
import argparse
import json
from pathlib import Path

from rahbar.retrieval.report import summarize

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('results', type=Path)
args = parser.parse_args()
print(json.dumps(summarize([json.loads(line) for line in args.results.read_text().splitlines()]),
                 indent=2))
