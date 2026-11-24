"""Serve the experimental text API on loopback; requires a verified free-tier project."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from rahbar.config import load_retrieval_settings, load_supabase_settings
from rahbar.retrieval.answer import AnswerService
from rahbar.retrieval.api import create_text_app
from rahbar.retrieval.gemini import GeminiRouter
from rahbar.retrieval.hybrid import HybridIndex
from rahbar.retrieval.postgres import PostgresTextSessions
from rahbar.retrieval.sessions import TextSessions

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--project-root', type=Path, required=True)
parser.add_argument('--revision', required=True)
parser.add_argument('--free-tier-confirmed', action='store_true', required=True)
parser.add_argument('--port', type=int, default=8010)
parser.add_argument('--url')
parser.add_argument('--storage', choices=['sqlite','postgres'], default='sqlite')
args = parser.parse_args()
settings = load_retrieval_settings(args.project_root)
index = HybridIndex(args.directory, args.revision, args.url)
client = httpx.AsyncClient()
service = AnswerService(GeminiRouter(client, settings.gemini_api_key, list(settings.models)),
                        lambda q, k: index.search(q, k, reviewed_only=False))
store = (PostgresTextSessions(load_supabase_settings(args.project_root).database_url)
         if args.storage == 'postgres' else TextSessions(args.directory/'text-sessions.sqlite'))
app = create_text_app(service, store, index.collection)

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await client.aclose()
        index.client.close()

app.router.lifespan_context = lifespan
uvicorn.run(app, host='127.0.0.1', port=args.port)
