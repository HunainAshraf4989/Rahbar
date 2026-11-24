# WhatsApp tutor

This is an opt-in channel for the existing relay tutor. It does not implement
Hunain's future retrieval engine or broaden the available lesson content.

## Configuration

Use the ignored repository-root `.env.whatsapp.local`. Never commit credentials.

| Variable | Source |
| --- | --- |
| `RAHBAR_WHATSAPP_PHONE_NUMBER_ID` | Meta API setup: Phone Number ID, not the displayed test phone number |
| `RAHBAR_WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta API setup: WhatsApp business/messaging account ID |
| `RAHBAR_WHATSAPP_ACCESS_TOKEN` | Meta access token authorized for this business phone |
| `RAHBAR_WHATSAPP_APP_SECRET` | Meta app dashboard: App settings > Basic > App secret > Show |
| `RAHBAR_WHATSAPP_VERIFY_TOKEN` | Local random verification token; enter the same value in Meta webhook setup |
| `RAHBAR_WHATSAPP_API_VERSION` | Graph API version supported by the Meta app |

Keep the shared database, Gemini, speech recognition and ElevenLabs configuration
in the existing ignored root environment files. The ElevenLabs setting is
`RAHBAR_ELEVENLABS_API_KEY` in `.env`.
Set `RAHBAR_ANSWER_MODE=gemini` in that file; the WhatsApp channel deliberately
rejects dummy answer mode.

Install the project with its `whatsapp` extra, then run from the repository root:

```console
python tools/migrate_whatsapp.py
python -m rahbar.whatsapp
```

Migrations add account profile columns and separate WhatsApp runtime tables.
Existing accounts can still sign in; WhatsApp access requires a matching signup
phone number in international format, for example `+923001234567`.

## Meta webhook setup

Meta needs a public HTTPS callback ending in `/webhooks/whatsapp`. A localhost
address is not reachable by Meta. Configure a reverse proxy or development tunnel
to expose **only** this route; do not expose all development platform endpoints.
Set the matching verify token and subscribe to the `messages` webhook field.
For a development tunnel, run `python -m rahbar.whatsapp --webhook-only` and
forward the tunnel to `http://127.0.0.1:8001`. This mode rejects all other routes,
including platform APIs, development routes and API documentation.
Complete Meta's test-recipient verification before testing with a personal phone.
Temporary access tokens must be refreshed when they expire.

A business-specific `override_callback_uri` takes precedence over the app's
callback. Inspect `/{business-account-id}/subscribed_apps` when the app callback
works but incoming messages do not arrive. An override must point to the current
webhook address, especially after restarting a temporary tunnel.

The entry point disables HTTP access logs because verification query strings
contain the verify token. Apply equivalent redaction to reverse proxy logs.

## Processing and retention

Signed requests are verified against the raw body before parsing. Only active
accounts whose signup phone matches the incoming sender can enqueue work. Unknown
numbers receive a fixed text registration reminder and cause no speech or tutor
calls. Reminders are queued separately, contain no saved question text, and are
limited to one per number per minute. They only reply to a received message.
Duplicate webhook deliveries do not create duplicate reminders. Metadata-only
reminder receipts expire after eight days and never enter the five-pair history.
Accounts disabled by the system receive no registration reminder.

Text and voice questions run asynchronously through a durable PostgreSQL queue.
Workers serialize processing per student, pass the last five completed Q&A pairs
to a standalone-question resolver, and use the existing grounded relay pipeline
for answers. Successful responses send text and Opus audio.

`wa_history` retains at most five completed Q&A rows per student, enforced by
database triggers. Quoted message IDs are resolved only within that student's
retained history. Missing references receive a request to resend the older
question. Queue payloads are transient and cleared at completion; text-free
receipt metadata is retained for eight days to suppress webhook redelivery.

Meta does not provide an exactly-once send guarantee. A timeout after a send may
mean the message was accepted. Such jobs become `unknown` for operational review
instead of automatically sending a duplicate. This needs monitoring in production.
Meta API acceptance is not proof of delivery to the student's device.

## Verification

Unit tests cover webhook signatures and limits, registration validation, history
context, media restrictions, audio conversion and ambiguous sends. Set
`RAHBAR_TEST_WHATSAPP_DSN` to a dedicated PostgreSQL test database with the schema
and migrations applied to enable database integration tests. Tests create and
remove only their own synthetic accounts.

Live Meta delivery still requires valid credentials, an HTTPS callback, a webhook
subscription and an authorized test recipient. Mock transport tests do not verify
these external settings.

## Tutor answer-quality limitation for Hunain

The current `GeminiGeneration` selects one whole prepared lesson passage rather
than composing an answer to the particular question. `RelayChecker` verifies an
exact match to that passage and its provenance; it does not establish that the
passage fully answers the question. `RelayNormalizer` only permits prepared text.
Consequently distinct questions can receive the same generic paragraph.

During live testing, a coil-to-switch question received a general relay operation
summary. A reproduction preserved the question through the WhatsApp context
resolver; direct calls to the existing generator also selected a generic prepared
passage. Passage selection varied between calls. This is an answer-quality issue,
separate from successful WhatsApp text/audio delivery. No changes were made to the
existing retrieval, generation, checker or normalization implementation.

The future grounded answering pipeline needs question-specific evidence retrieval,
evidence-backed answer composition, and a relevance/completeness check that
refuses or asks for clarification when the lesson cannot answer the question.

Official references: [App settings](https://developers.facebook.com/documentation/development/create-an-app/app-dashboard/basic-settings),
[WhatsApp messages webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages).
