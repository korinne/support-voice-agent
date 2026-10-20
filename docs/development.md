# Development

Run commands from the repository root. If Node is managed by nvm, run `nvm use` first.

## Interface preview

`npm run dev` opens the customer page at `http://127.0.0.1:5173` and the staff desk at `http://127.0.0.1:5173/desk`, both with sample data. The preview needs no Python, credentials, microphone, or backend. Its sign-in button skips the trip to Cloudflare.

Use the scenario selector to work on sign-in, connection, conversation, and failure states. A URL such as `/?scenario=<id>` opens a state directly. The IDs are in [`web/src/preview/scenarios.ts`](../web/src/preview/scenarios.ts). Add a preview case for each new thing a person can see, such as a tool row or a notice, so it can be reviewed without making a call. Keep preview code out of production builds.

## Real calls

First [deploy the backend](deployment.md). Create `.env.local` from `.env.example` and set `VOICE_BACKEND_URL` to the deployed Worker's HTTPS origin, without a path or query string.

```sh
npm run dev:live
```

Open `http://127.0.0.1:5173`. Realtime connects to the deployed Worker directly, because it can't reach a media endpoint on your laptop.

Sign-in doesn't work through the local proxy yet. Cloudflare returns the browser to the redirect URL registered on your OAuth client, which is the deployed hostname, so the session cookie lands there instead of on `127.0.0.1`. To test calls end to end, use the deployed app or a [Preview](deployment.md#previews).

Use the staff desk on the deployed hostname. The local proxy forwards only `/api/`, and Access signs staff in on the deployed hostname, so a local desk can't load its data.

The local API proxy is meant for loopback use only. Keep its host and origin checks, and keep credentials out of `VITE_*` variables. The cookie's `Secure` attribute is removed only for local HTTP responses. Deployed cookies must keep it.

`npm run build && npm run preview` serves the production frontend locally on port 4173 against the same backend. `npm run worker:dev` is useful for Worker and API work, but a local Worker alone doesn't give Realtime a public adapter endpoint.

## Checks

```sh
uv sync --locked
npm run check
npm run lint:python
npm run test:python
```

Use `npm run format` for Prettier and `npm run format:python` for Ruff. Frontend checks include formatting, tests, and a production build. Python tests use test doubles at the Workers boundary. The D1 double in [`tests/server/conftest.py`](../tests/server/conftest.py) runs the real schema in SQLite, with fixed test customers from [`seed.sql`](../tests/server/seed.sql). It runs every file in `migrations/` in name order, so a new migration is tested without changes to the double.

Python tests can't prove that code runs on Workers. When changing dependencies, providers, or the audio path, also test a deployed call. See the [Python compatibility guide](../vendor/README.md) before adding packages.

For a voice check on a deployed app:

1. Select **Reset demo data** on the desk, then sign in with Cloudflare.
2. Ask about the umbrella and request store credit. Approve it on the desk while the call is live, and confirm that Matt announces it and that the **Your account** tab shows the new balance after **Refresh**.
3. Ask about the late sneakers. Confirm that Matt opens a ticket and that the desk shows Clef's routing.
4. Report the pogo stick's fuel smell, then cancel the tunnel kit.
5. Ask a policy question and check that the help article titles appear as sources.
6. Interrupt a reply, end the call, and confirm that the microphone stops and cleanup finishes.

Use a headset, so speaker echo doesn't look like an intentional interruption.

## Timing and connection problems

Browser startup timings are in the console and in **Connection details**. Server timings are nested inside browser operations, so adding every row together overcounts setup time. The total measures application startup, including microphone access and network setup. It isn't a Worker cold-start measurement. Measure synchronous Python startup with Workers CPU telemetry, because Worker clocks advance with I/O.

Each call has its own Durable Object, and a new object's first request includes its cold start. The page warms the next call's object while it's open and when the pointer or focus reaches Start, so creating the call usually skips that delay. A warm request needs a sign-in, doesn't count toward the call rate limit, and stores nothing.

For a failed connection, use the negotiation phase and ICE state in the browser console to find the failing step. ICE (Interactive Connectivity Establishment) is how WebRTC finds a network path. Don't log raw SDP (the session descriptions WebRTC exchanges), adapter callback URLs, cookies, or full provider responses: these can carry credentials or conversation data.

## Worker logs

Use the call ID from the exported transcript to filter `call_id` in the Worker's **Logs** view. For a failed HTTP request, the browser's `[Voice API]` console entry also shows a `CF-Ray` and, when available, an application `requestId`. Search for that value as `request_id` to find the matching server failure.

Events worth knowing:

| Event                      | Meaning                                                |
| -------------------------- | ------------------------------------------------------ |
| `call_created`             | A call's Durable Object saved its owner and customer.  |
| `tool_failed`              | A tool returned an error or raised an exception.       |
| `ticket_triage_failed`     | Clef couldn't route a ticket. The ticket still exists. |
| `approval_decided`         | A staff member approved or declined a credit request.  |
| `decision_delivery_failed` | The decision was saved, but the call couldn't be told. |
| `database_unavailable`     | D1 failed, usually because the migrations haven't run. |

Look for the first failure before cleanup retries: a failing Stop request can be a consequence of an earlier error. A runtime failure can prevent application logging, so also check invocation errors. Logs exist only for traffic after observability was turned on.

Use [the structured logger](../src/server/observability.py) when adding events, and keep payloads out of its allowed fields. Keep URL query redaction on, because adapter requests carry credentials in their query strings.
