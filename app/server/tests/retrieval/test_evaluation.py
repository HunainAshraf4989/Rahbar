import pytest
from rahbar.retrieval.evaluation import score, validate_gold


def test_cross_topic_requires_both_facts():
    assert score(['a', 'alias-a'], [['a', 'alias-a'], ['b']])['recall'] == .5
    assert score(['a', 'alias-a'], [['a', 'alias-a'], ['b']])['complete_evidence'] == 0
    assert score(['a', 'b'], [['a', 'alias-a'], ['b']])['complete_evidence'] == 1


def test_reject_unreviewed_and_leaking_gold():
    row = dict(id='1', family_id='x', split='development', review_status='draft',
               reviewers=[], expected_outcome='answered', evidence_groups=[['a']])
    with pytest.raises(ValueError, match='human review'):
        validate_gold([row])
    row.update(review_status='reviewed', reviewers=['reviewer'])
    with pytest.raises(ValueError, match='leaks'):
        validate_gold([row, dict(row, id='2', split='test')])


def test_binary_ndcg_and_missing_evidence():
    assert score(['irrelevant', 'a'], [['a']])['ndcg'] == pytest.approx(1 / 1.584962500721156)
    assert score([], [['a']]) == dict(recall=0., complete_evidence=0., mrr=0., ndcg=0.)
