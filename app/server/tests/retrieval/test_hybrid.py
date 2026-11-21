import json

import numpy as np
from rahbar.retrieval import hybrid


class Encoder:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    def encode(self, text, **kwargs):
        self.calls += 1
        def vector(value):
            result = np.zeros(1024, dtype=np.float32)
            result[0 if 'relay' in value or 'ریلے' in value else 1] = 1
            return result
        return np.array([vector(v) for v in text]) if isinstance(text, list) else vector(text)


def test_embedded_qdrant_build_reopen_and_hybrid_search(tmp_path, monkeypatch):
    monkeypatch.setattr(hybrid, 'SentenceTransformer', Encoder)
    records = [{'passage_id': str(i), 'canonical_text': text} for i, text in enumerate([
        'A relay is an electrical switch.', 'The battery stores energy.',
        'A fuse protects a circuit.', 'An alternator charges the battery.'])]
    (tmp_path / 'passages.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    index = hybrid.HybridIndex(tmp_path, 'fixture-revision')
    try:
        index.build(batch_size=2)
        assert index.search('ریلے', 1)[0]['passage_id'] == '0'
        calls = index.encoder.calls
        index.build(batch_size=2)
        assert index.encoder.calls == calls  # resumed from embedding cache
    finally:
        index.client.close()
    reopened = hybrid.HybridIndex(tmp_path, 'fixture-revision')
    try:
        assert reopened.search('relay', 1)[0]['passage_id'] == '0'
    finally:
        reopened.client.close()


def test_scope_and_review_filter_apply_before_candidate_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(hybrid, 'SentenceTransformer', Encoder)
    records=[dict(passage_id='draft',canonical_text='relay relay relay', unit_ids=['u'],publication_eligible=False),
             dict(passage_id='approved',canonical_text='relay is a switch',unit_ids=['u'],publication_eligible=True),
             dict(passage_id='foreign',canonical_text='relay elsewhere',unit_ids=['other'],publication_eligible=True)]
    (tmp_path/'passages.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    index=hybrid.HybridIndex(tmp_path,'fixture')
    try:
        index.build()
        assert [h['passage_id'] for h in index.search('relay',unit_id='u',reviewed_only=True)]==['approved']
        assert index.search('relay',unit_id='missing')==[]
        assert index.search('')==[]
    finally:
        index.client.close()
