"""Prepare an experimental index input without silently truncating oversized passages."""
import argparse
import hashlib
import json
from pathlib import Path

from transformers import AutoTokenizer

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
parser.add_argument('--revision',required=True)
args=parser.parse_args()
tokenizer=AutoTokenizer.from_pretrained('BAAI/bge-m3',revision=args.revision)
args.output.mkdir(parents=True,exist_ok=False)
accepted=excluded=0
with (args.source/'passages.jsonl').open() as source, (args.output/'passages.jsonl').open('w') as target, (args.output/'excluded.jsonl').open('w') as rejected:
    for line in source:
        row=json.loads(line)
        count=len(tokenizer(row['canonical_text'],truncation=False)['input_ids'])
        if count>1024:
            rejected.write(json.dumps({'passage_id':row['passage_id'],'reason':'exceeds_embedding_limit', 'tokens':count})+'\n')
            excluded+=1
        else:
            target.write(line)
            accepted+=1
manifest={'status':'experimental','accepted':accepted,'excluded_oversized':excluded,
          'embedding_revision':args.revision,'max_seq_length':1024,
          'source_sha256':hashlib.sha256((args.source/'passages.jsonl').read_bytes()).hexdigest()}
(args.output/'preflight.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest))
