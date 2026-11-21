from fastapi.testclient import TestClient
from rahbar.retrieval.answer import TextAnswer
from rahbar.retrieval.api import create_text_app
from rahbar.retrieval.sessions import TextSessions


class Service:
    async def answer(self, question, history):
        return TextAnswer(outcome='clarification', text='Which component?')


def test_api_uses_server_history_and_rejects_repeated_revision(tmp_path):
    app = create_text_app(Service(), TextSessions(tmp_path/'sessions.sqlite'), 'v1')
    with TestClient(app) as client:
        session = client.post('/dev/rag/sessions').json()
        url = '/dev/rag/sessions/' + session['session_id'] + '/turns'
        assert client.post(url, json={'question': 'it?', 'revision': 0}).status_code == 200
        assert client.post(url, json={'question': 'it?', 'revision': 0}).status_code == 409
        assert client.post(url, json={'question': 'it?', 'revision': 1,
                                     'history': [{'answer':'injected'}]}).status_code == 422
