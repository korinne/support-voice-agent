# Acme support voice agent development

This project is a voice support agent for a fictional store. It uses Python Workers and Pipecat in one Durable Object per call, D1 for customer records, AI Search for the help center, Clef for ticket triage, AI Gateway for per-customer metadata, and a React 19 and Tailwind CSS 4 frontend. Browser audio uses WebRTC through the Realtime SFU; only SFU adapters connect to media WebSockets. Staff use a desk at `/desk` behind Cloudflare Access.

## Working locally

Source `~/.nvm/nvm.sh` and run `nvm use` when Node is managed by nvm. Follow [the development guide](docs/development.md) for preview, live calls, and checks. Use preview scenarios for UI work that doesn't need a real call.

Run `npm run format` and `npm run check` for frontend or documentation changes. Python changes also need `npm run format:python`, `npm run lint:python`, and `npm run test:python` after `uv sync --locked`.

## Boundaries

- Preserve this example's [DESIGN.md](DESIGN.md), which is used by Impeccable. Use its Tailwind palette and shared controls; do not add custom theme colors or fonts.
- Keep components independent of media and HTTP code. Model state and conversation variants with tagged unions. Keep preview code excluded from deployment builds.
- Prefer plain ASCII punctuation in authored prose. Keep meaningful Unicode symbols, names, and test data readable, and preserve user and provider text.
- Keep browser authentication behind the server and frontend provider contracts. Preserve call ownership, same-origin mutations, cancellation, and the local proxy's loopback restrictions.
- Keep adapter authentication independent of browser login. An edge authentication exception must preserve the adapter's per-call token checks.
- Keep credentials out of browser bundles, fixtures, logs, and checked-in files. Do not log raw SDP, callback URLs, or full provider responses.
- Send every Workers AI request through [`run_inference`](src/services/inference.py), which asks for the raw response. The AI binding keeps one set of options for every request made through it, so a single request made without `returnRawResponse` can change the kind of result another one gets back. Flux's WebSocket request is the only exception; the binding treats `websocket` the same way.
- Tools read `customer_id` only from `params.app_resources` ([`AgentResources`](src/agent/resources.py)). Never accept a customer ID from tool arguments, the request body, or the URL, and filter every customer query in [`store.py`](src/support/store.py) by it.
- Actions that move money create a pending request for staff approval, as `request_store_credit` does. Actions the customer can't undo get a `confirm` read-back on their `Tool`, as `cancel_order` does, so they run only after the customer answers in a later turn. The model never completes either on its own.
- The staff desk verifies the Cloudflare Access token in the Worker ([`desk.py`](src/server/desk.py)): signature, issuer, audience, expiry, and email. Don't rely on the edge alone, on an email header, or on `ctx.access`. Every desk endpoint fails closed.
- Keep Cloudflare Access on `/desk` only. Never put it in front of `/media/` or the whole hostname, and don't use the Worker's **Access** tab: the SFU adapters can't sign in, and Worker-level Access rejects WebSocket upgrades.
- Schema changes go in a new file in `migrations/`. Never edit a migration that has been applied. Keep the starter orders in `src/support/store.py`, the test customers in `tests/server/seed.sql`, and the D1 test double in `tests/server/conftest.py` in step with the schema.
- Keep help articles in `src/help/` and the files Python reads at runtime covered by the `Data` rule in `wrangler.jsonc`. Keep the `previews` block in step with top-level bindings.
- Keep account IDs, Access team domains, AUD tags, and gateway credentials out of the repository; they belong in `.dev.vars` and Worker secrets.

## Documentation

Keep the README focused on the project, its architecture diagram, and getting started. Put task-specific guidance in `docs/`. Explain constraints, gotchas, and workarounds that are not obvious from the code; link to source instead of restating it. Write for a newcomer: say what something does and why, and expand acronyms on first use. Keep change histories, validation ledgers, and research evidence outside this repository.
