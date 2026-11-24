import asyncio
from types import SimpleNamespace

from rahbar.retrieval.answer import AnswerService


class Router:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def generate(self, *args):
        self.calls += 1
        return SimpleNamespace(data=next(self.responses), model='fixture')


def passage(reviewed=True):
    return dict(passage_id='p', canonical_text='A relay is a switch.',
                review_status='reviewed' if reviewed else 'draft', publication_eligible=reviewed,
                reviewed_by='human', review_reference='review-1', safety='ordinary',
                provenance=[dict(location='fixture', source_hash='fixture-hash')])


def test_unreviewed_evidence_never_generates_answer():
    router = Router([{'standalone': 'What is a relay?'}])
    answer = asyncio.run(AnswerService(router, lambda q, k: [passage(False)], content_policy='reviewed').answer('relay?', []))
    assert answer.outcome == 'refused'
    assert router.calls == 1


def test_valid_citation_but_unsupported_claim_is_rejected():
    router = Router([{'standalone': 'What is a relay?'}, {'outcome': 'answered', 'sentences': [
        {'text': 'A relay increases voltage.', 'citations': [
            {'passage_id': 'p', 'quote': 'A relay is a switch.'}]}]},
        {'supported': [False], 'covers_question': True, 'unsafe': False}])
    answer = asyncio.run(AnswerService(router, lambda q, k: [passage()]).answer('relay?', []))
    assert answer.outcome == 'refused'
    assert not answer.sentences


def test_supported_answer_and_ambiguous_followup():
    router = Router([{'standalone': 'What is a relay?'}, {'outcome': 'answered', 'sentences': [
        {'text': 'A relay is a switch.', 'citations': [
            {'passage_id': 'p', 'quote': 'A relay is a switch.'}]}]},
        {'supported': [True], 'covers_question': True, 'unsafe': False}])
    answer = asyncio.run(AnswerService(router, lambda q, k: [passage()]).answer('relay?', []))
    assert answer.outcome == 'answered'
    router = Router([{'standalone': 'it?', 'clarification': 'Which component?'}])
    answer = asyncio.run(AnswerService(router, lambda q, k: []).answer('it?', []))
    assert answer.outcome == 'clarification'


def test_shared_planner_receives_recent_history_and_searches_each_facet():
    searched = []
    class PlanningRouter:
        async def generate(self, instruction, payload, schema):
            assert payload['history'] == history[-4:]
            return SimpleNamespace(model='planner-fixture', data={
                'uses_history': True,
                'standalone': 'How does a relay differ from a fuse?',
                'subquestions': ['relay function', 'fuse function']})
    history = [{'question': str(i), 'answer': 'A relay is a switch.'} for i in range(6)]
    def search(query, k):
        searched.append(query)
        return []
    result = asyncio.run(AnswerService(PlanningRouter(), search).answer('How is it different?', history))
    assert set(searched) == {'How does a relay differ from a fuse?',
                             'relay function', 'fuse function'}
    assert result.models == ['planner-fixture']
    assert result.outcome == 'refused'


def test_prepared_concept_uses_citation_and_support_checks_without_manual_approval():
    candidate = dict(passage_id='p', canonical_text='What is a relay? A relay is an electrically operated switch.',
                     unit_ids=['u'], provenance=[{'source_url': 'fixture'}], review_flags=[],
                     review_status='draft', publication_eligible=False)
    router = Router([{'standalone': 'What is a relay?'}, {'outcome': 'answered', 'sentences': [
        {'text': 'A relay is an electrically operated switch.', 'citations': [
            {'passage_id': 'p', 'quote': 'A relay is an electrically operated switch.'}]}]},
        {'supported': [True], 'covers_question': True, 'unsafe': False}])
    answer = asyncio.run(AnswerService(router, lambda q, k: [candidate], allow_prepared=True).answer('What is a relay?', []))
    assert answer.outcome == 'answered'
    assert router.calls == 3


def test_resolved_followup_replaces_vague_query_and_facets_keep_priority():
    from rahbar.retrieval.answer import QueryPlan, retrieval_queries
    plan = QueryPlan(uses_history=True, standalone='Compare relay and fuse functions',
                     subquestions=['relay function', 'fuse function', 'relay function'])
    assert retrieval_queries('How do they differ?', plan, has_history=True) == [
        'relay function', 'fuse function', 'Compare relay and fuse functions']
    assert retrieval_queries('original', plan, has_history=False)[-1] == 'original'
    plan.clarification = 'Which component?'
    assert retrieval_queries('it?', plan, has_history=True) == []


def test_devanagari_planner_output_never_reaches_retrieval():
    from unittest.mock import Mock

    import pytest
    search = Mock()
    router = Router([{'standalone': 'Relay के function'}])
    with pytest.raises(ValueError, match='Devanagari'):
        asyncio.run(AnswerService(router, search).answer('ریلے کیا کرتا ہے؟', []))
    search.assert_not_called()


def test_multiple_referents_force_clarification_even_if_model_chooses_one():
    from unittest.mock import Mock
    search = Mock()
    router = Router([dict(reference_candidates=['relay', 'fuse'], standalone='relay function',
                          subquestions=['relay function'], clarification=None)])
    result = asyncio.run(AnswerService(router, search).answer('What does it do?',
        [{'question': 'Tell me about a relay and a fuse.', 'answer': 'Both are components.'}]))
    assert result.outcome == 'clarification' and router.calls == 1
    search.assert_not_called()


def test_unrelated_history_does_not_remove_a_self_contained_question():
    from rahbar.retrieval.answer import QueryPlan, retrieval_queries
    plan = QueryPlan(standalone='brake pad function')
    assert 'How does a brake pad work?' in retrieval_queries(
        'How does a brake pad work?', plan, has_history=True)


def test_supplied_source_needs_no_expert_review_but_still_checks_support():
    candidate = passage(False)
    candidate.pop('reviewed_by')
    candidate.pop('review_reference')
    router = Router([{'standalone': 'relay function'}, {'outcome': 'answered', 'sentences': [
        {'text': 'A relay is a switch.', 'citations': [
            {'passage_id': 'p', 'quote': 'A relay is a switch.'}]}]},
        {'supported': [True], 'covers_question': True, 'unsafe': False}])
    result = asyncio.run(AnswerService(router, lambda q,k:[candidate]).answer('relay?', []))
    assert result.outcome == 'answered' and router.calls == 3
    assert candidate['review_status'] == 'draft'


def test_supplied_policy_rejects_excluded_or_untraceable_sources():
    for changes in ({'provenance': []}, {'review_status': 'withheld'}, {'exclusion_reason': 'malformed'}):
        candidate = dict(passage(False), **changes)
        router = Router([{'standalone': 'relay function'}])
        result = asyncio.run(AnswerService(router, lambda q,k:[candidate]).answer('relay?', []))
        assert result.outcome == 'refused' and router.calls == 1
