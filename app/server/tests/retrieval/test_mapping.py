import pytest
from rahbar.retrieval.mapping import apply_reviews, map_passages


def test_exact_join_never_confuses_making_and_marking():
    rows, report=map_passages([dict(passage_id='p', topics=['Making wires'], provenance=['source'])],
                              dict(version='v1', units=[dict(content_key='Marking wires',unit_id='u')]))
    assert rows[0]['unit_ids']==[]
    assert report['unresolved_topics']==['Making wires']
    review=dict(passage_id='p',status='reviewed',reviewed_by='human',review_reference='r',safety='ordinary')
    with pytest.raises(ValueError,match='Unmapped'):
        apply_reviews(rows,[review])
    rows[0]['unit_ids']=['u']
    assert apply_reviews(rows,[review])[0]['publication_eligible']
