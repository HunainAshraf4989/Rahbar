"""Usage: python tools/retrieval/prepare.py CORPUS OUTPUT."""
import argparse
import json
from pathlib import Path

from rahbar.retrieval.corpus import prepare

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("corpus", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
print(json.dumps(prepare(args.corpus, args.output), indent=2))
