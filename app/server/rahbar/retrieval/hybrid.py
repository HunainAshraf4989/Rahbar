"""Experimental multilingual dense/BM25 retrieval with reproducible local artifacts."""
import hashlib
import json
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from rahbar.retrieval.corpus import normalize


def tokens(text: str) -> list[str]:
    import re
    return re.findall(r"[^\W_]+", normalize(text).casefold())


class HybridIndex:
    def __init__(self, directory: Path, model_revision: str, url: str | None = None):
        self.directory = directory
        self.search_lock = threading.Lock()
        self.revision = model_revision
        self.client = QdrantClient(url=url) if url else QdrantClient(path=str(directory / 'qdrant'))
        self.encoder = SentenceTransformer('BAAI/bge-m3', revision=model_revision, device='cpu')
        self.encoder.max_seq_length = 1024
        self.records = [json.loads(line) for line in (directory / 'passages.jsonl').read_text().splitlines()]
        self.lexical = BM25Okapi([tokens(r['canonical_text']) for r in self.records])
        self.by_id = {r['passage_id']: r for r in self.records}
        fingerprint = hashlib.sha256((directory / 'passages.jsonl').read_bytes()).hexdigest()
        self.collection = 'rahbar_' + hashlib.sha256((fingerprint + model_revision + ':maxlen1024:normalized:cosine:v1').encode()).hexdigest()[:24]

    def build(self, batch_size: int = 8) -> None:
        started = perf_counter()
        if batch_size < 1:
            raise ValueError("Batch size must be positive")
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=1024, distance=models.Distance.COSINE))
        cache = self.directory / 'embeddings'
        cache.mkdir(exist_ok=True)
        for start in range(0, len(self.records), batch_size):
            batch = self.records[start:start + batch_size]
            texts = [r['canonical_text'] for r in batch]
            key = hashlib.sha256(json.dumps([self.revision, texts]).encode()).hexdigest()
            target = cache / (key + '.npy')
            if target.exists():
                vectors = np.load(target, allow_pickle=False)
            else:
                if hasattr(self.encoder, 'tokenizer'):
                    lengths = self.encoder.tokenizer(texts, truncation=False, padding=False)['input_ids']
                    if any(len(ids) > self.encoder.max_seq_length for ids in lengths):
                        raise ValueError('Passage exceeds encoder limit; rechunk before indexing')
                vectors = self.encoder.encode(texts, normalize_embeddings=True, batch_size=batch_size)
                temporary = target.with_suffix('.tmp')
                with temporary.open('wb') as handle:
                    np.save(handle, vectors, allow_pickle=False)
                temporary.replace(target)
            self.client.upsert(self.collection, points=[models.PointStruct(
                id=start+i, vector=vector.tolist(), payload={'passage_id': record['passage_id'],
                    'unit_ids': record.get('unit_ids', []),
                    'publication_eligible': bool(record.get('publication_eligible', False))})
                for i, (record, vector) in enumerate(zip(batch, vectors, strict=True))])
            progress = {'completed': start + len(batch), 'total': len(self.records),
                        'elapsed_seconds': perf_counter() - started, 'collection': self.collection}
            pending = self.directory / 'progress.pending.json'
            pending.write_text(json.dumps(progress))
            pending.replace(self.directory / 'progress.json')
        temporary_manifest = self.directory / 'index.pending.json'
        temporary_manifest.write_text(json.dumps({
            'collection': self.collection, 'model': 'BAAI/bge-m3', 'revision': self.revision,
            'count': len(self.records), 'status': 'experimental', 'max_seq_length': 1024}))
        temporary_manifest.replace(self.directory / 'index.json')

    def search(self, question: str, k: int = 10, *, unit_id: str | None = None,
               reviewed_only: bool = False, mode: str = 'hybrid') -> list[dict[str, Any]]:
        if mode not in {'hybrid', 'dense', 'bm25'} or not 1 <= k <= 100:
            raise ValueError('Invalid retrieval mode or result limit')
        if not normalize(question):
            return []
        manifest = json.loads((self.directory / 'index.json').read_text())
        if manifest['collection'] != self.collection:
            raise ValueError('Index manifest mismatch; rebuild required')
        eligible = {i for i, record in enumerate(self.records)
                    if (unit_id is None or unit_id in record.get('unit_ids', []))
                    and (not reviewed_only or record.get('publication_eligible', False))}
        if not eligible:
            return []
        conditions: list[models.Condition] = []
        if unit_id is not None:
            conditions.append(models.FieldCondition(key='unit_ids', match=models.MatchValue(value=unit_id)))
        if reviewed_only:
            conditions.append(models.FieldCondition(key='publication_eligible', match=models.MatchValue(value=True)))
        rankings = []
        # The local Qdrant client and shared encoder are serialized across worker threads.
        with self.search_lock:
            if mode != 'bm25':
                vector = self.encoder.encode(normalize(question), normalize_embeddings=True)
                dense = self.client.query_points(self.collection, query=vector.tolist(), limit=30,
                    query_filter=models.Filter(must=conditions) if conditions else None).points
                rankings.append([int(p.id) for p in dense if int(p.id) in eligible])
            if mode != 'dense':
                scores = self.lexical.get_scores(tokens(question))
                rankings.append([int(i) for i in np.argsort(-scores, kind='stable')
                                 if int(i) in eligible and scores[i] > 0][:30])
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, identifier in enumerate(ranking, 1):
                fused[identifier] = fused.get(identifier, 0) + 1 / (60 + rank)
        return [dict(self.records[i], rrf_score=fused[i])
                for i in sorted(fused, key=lambda i: (-fused[i], i))[:k]]
