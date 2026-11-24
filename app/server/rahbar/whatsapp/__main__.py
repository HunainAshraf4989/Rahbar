"""Opt-in WhatsApp server: python -m rahbar.whatsapp."""

import argparse

import uvicorn

from .app import create_whatsapp_app
from .public import WebhookOnly

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--webhook-only", action="store_true", help="Expose only the webhook on port 8001"
    )
    args = parser.parse_args()
    application = create_whatsapp_app()
    # Meta's verification request contains the verify token in its query string.
    uvicorn.run(
        WebhookOnly(application) if args.webhook_only else application,
        host="127.0.0.1",
        port=8001 if args.webhook_only else 8000,
        access_log=False,
    )
