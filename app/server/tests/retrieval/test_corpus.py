import hashlib
import json

import pytest
from rahbar.retrieval.corpus import normalize, prepare


def test_read_only_deterministic_and_quarantine(tmp_path):
    root = tmp_path / 'raw'
    root.mkdir()
    path = root / 'input.json'
    path.write_text(json.dumps({'Relay': [
        {'transcript': 'ریلے current کو switch کرتا ہے۔', 'qa_pairs': [None]},
        {'transcript': 'ریلے current کو switch کرتا ہے۔'},
        {'transcript': '%PDF-1.7 binary endstream'},
    ]}))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    first = prepare(root, tmp_path / 'one')
    second = prepare(root, tmp_path / 'two')
    assert first == second
    assert first['passages'] == 1
    assert first['quarantined'] == 2
    assert first['reviewed'] == 0
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    passage = json.loads((tmp_path / 'one/passages.jsonl').read_text())
    assert len(passage['provenance']) == 2
    assert not passage['publication_eligible']
    with pytest.raises(ValueError):
        prepare(root, root / 'derived')


def test_preserve_terms_numbers_and_negation():
    assert normalize('  كيا  12V relay نہیں  ') == 'کیا 12V relay نہیں'
