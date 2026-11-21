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
                reviewed_by='human', review_reference='review-1', safety='ordinary')


def test_unreviewed_evidence_never_generates_answer():
    router = Router([{'standalone': 'What is a relay?'}])
    answer = asyncio.run(AnswerService(router, lambda q, k: [passage(False)]).answer('relay?', []))
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
                'standalone': 'How does a relay differ from a fuse?',
                'subquestions': ['relay function', 'fuse function']})
    history = [{'question': str(i), 'answer': 'A relay is a switch.'} for i in range(6)]
    def search(query, k):
        searched.append(query)
        return []
    result = asyncio.run(AnswerService(PlanningRouter(), search).answer('How is it different?', history))
    assert set(searched) == {'How is it different?', 'How does a relay differ from a fuse?',
                             'relay function', 'fuse function'}
    assert result.models == ['planner-fixture']
    assert result.outcome == 'refused'
