from rahbar.retrieval.content_policy import prepared_fact_eligible


def test_prepared_concepts_require_mapping_provenance_and_exclude_procedures():
    row = dict(unit_ids=['u'], provenance=[{'source_url': 'source'}], review_flags=[],
               canonical_text='Source topic: Relays\nWhat is a relay? A relay is an electrically operated switch.')
    assert prepared_fact_eligible(row)
    for changed in [dict(unit_ids=[]), dict(provenance=[]), dict(review_flags=['conflict']),
                    dict(review_status='withheld'),
                    dict(canonical_text='What is the correct connection? Connect the wire to the terminal.'),
                    dict(canonical_text='What is the voltage? The voltage is 12V.')]:
        assert not prepared_fact_eligible(dict(row, **changed))
