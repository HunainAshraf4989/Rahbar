"""Build or query an experimental index; a pinned Hugging Face revision is required."""
import argparse
import json
from pathlib import Path

from rahbar.retrieval.hybrid import HybridIndex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--revision', required=True)
parser.add_argument('--url')
parser.add_argument('--query')
args = parser.parse_args()
index = HybridIndex(args.directory, args.revision, args.url)
try:
    if args.query:
        print(json.dumps(index.search(args.query), ensure_ascii=False, indent=2))
    else:
        index.build()
finally:
    index.client.close()
