# How it works

This guide follows one call from sign-in to hang-up and points to the code for each step. It ends with how to change the agent.

| Folder                               | What's in it                                                                             |
| ------------------------------------ | ---------------------------------------------------------------------------------------- |
| [`src/server/`](../src/server)       | The Worker's routes, the sign-in, the call's Durable Object, and the staff desk API      |
| [`src/agent/`](../src/agent)         | Matt's prompt and limits, the Pipecat pipeline, the tools, and the spoken fillers        |
| [`src/services/`](../src/services)   | Workers AI clients: Flux for speech recognition, the language model, and Aura for speech |
| [`src/transport/`](../src/transport) | Audio in and out of Realtime's WebSocket adapters                                        |
| [`src/support/`](../src/support)     | Acme's records in D1, the starter orders, the help center, and Clef triage               |
| [`src/help/`](../src/help)           | The eight help center articles                                                           |
| [`migrations/`](../migrations)       | The D1 tables                                                                            |
| [`web/`](../web)                     | The customer page at `/` and the staff desk at `/desk`                                   |

## 1. Signing in

Signing in works like checking into a hotel. You show your ID once at the front desk, and the desk hands you a key card that says which room is yours. Every later request shows the card. Here Cloudflare checks your ID, the Worker is the front desk, the card is a signed cookie, and the room is your Acme account.

- **Sign in with Cloudflare** goes to `/auth/login`, and the Worker sends the browser to Cloudflare's sign-in page. After the person approves, Cloudflare sends the browser back with a one-time code ([`CloudflareAuth`](../src/server/auth_cloudflare.py)).
- The Worker trades the code for a token, reads the person's Cloudflare user ID, email, and first name, and revokes the token. It sets an `acme_session` cookie holding `owner`, a random ID for this sign-in, and `customer`, an Acme customer ID derived from the Cloudflare user ID. The cookie is signed with `SESSION_SECRET`, so the browser can't change either value. It's `HttpOnly` (page scripts can't read it) and lasts 8 hours.
- The page then asks [`/api/session`](../src/server/routes.py) who's signed in. The first time, that creates the account with four starter orders ([`ensure_customer`](../src/support/store.py)). The stage shows the email the server signed in, so you can see whose account Matt will use.
- **Sign out** clears the cookie. Signing in with another Cloudflare login gives a different customer, even for the same person.

The sign-in sits behind a small contract in [`src/server/auth.py`](../src/server/auth.py). [Authentication](authentication.md) covers the security checks and how to swap in your own identity provider.

## 2. Starting a call

Each call gets its own Durable Object. A Durable Object is a small server with its own memory and storage, found by name: `env.CALLS.getByName(call_id)` always reaches the same object. Every request about one call, whether from the browser, Realtime, or the staff desk, lands in that one place.

- The page picks a random call ID and warms that call's object ahead of time (`GET /api/calls/<id>/warm`). A new object takes a moment to start the first time it's used, and warming moves that wait to before you press Start.
- `POST /api/calls/<id>/create` reaches the Worker, which checks the cookie, checks that the request came from the app's own page, and applies the call limit of 5 per minute per browser session ([`routes.py`](../src/server/routes.py)). It then forwards the request to the call's object.
- [`VoiceCall.create`](../src/server/call.py) looks up the customer in D1 and saves the owner and customer in the object's storage. The call keeps this customer until it ends, even if the browser signs in as someone else.

## 3. The audio path

- The browser asks for the microphone and prepares its WebRTC offer at the same time ([`web/src/voice/call.ts`](../web/src/voice/call.ts)). The offer describes the audio the browser wants to send and receive.
- On `publish`, the call's object sends the offer to the Realtime SFU and starts the agent in parallel ([`VoiceCall.mutate`](../src/server/call.py)). Starting the agent ([`VoiceEngine.prepare`](../src/agent/engine.py)) builds the Pipecat pipeline and opens a WebSocket to Flux.
- On `attach`, the object asks Realtime to open two WebSocket adapters back to the Worker. `/media/<call>/input` carries the caller's audio to the agent, and `/media/<call>/output` carries Matt's audio back. Their URLs carry a per-call token ([`AdapterAuth`](../src/transport/auth.py)), which the engine checks. Realtime isn't a browser, so it never has the customer's cookie.
- On `subscribe`, the browser receives Matt's audio track. It plays the track through a quarter-second jitter buffer, a short waiting area that smooths out packets arriving in bursts (`playoutBufferMs` in `call.ts`).
- Once the browser answers that track (`renegotiate`) and both adapters are connected, Matt says hello ([`VoiceEngine.greet`](../src/agent/engine.py)). The greeting is a fixed line with the caller's first name, so it needs no model request. It joins the conversation history like any reply. If the caller starts talking first, Matt skips it.

## 4. The conversation loop

[`start_pipeline`](../src/agent/pipeline.py) builds the Pipecat pipeline:

```text
caller audio -> Flux -> conversation -> language model -> Aura -> Matt's audio
```

- **Hearing.** Flux turns speech into text and decides when the caller has finished a turn ([`services/flux.py`](../src/services/flux.py)). It sends `StartOfTurn` when the caller starts talking and `EndOfTurn` with the transcript when they finish. `STT_KEYTERMS` in [`config.py`](../src/agent/config.py) nudges recognition toward "Acme", "PS-2", and "pogo stick".
- **Thinking.** At the end of a turn, the transcript joins the conversation and the language model runs ([`services/llm.py`](../src/services/llm.py)). The system prompt in [`config.py`](../src/agent/config.py) names the customer, says when to use each tool, and sets the safety rules. It also holds the customer's account as the call started, loaded when the agent starts, so Matt can answer the first question about an order without a lookup. Reasoning is turned off, and the reply arrives whole because the request doesn't stream (`"stream": False` in [`services/models.py`](../src/services/models.py)).
- **Speaking.** Aura turns the reply into speech one sentence at a time and streams the audio ([`services/tts.py`](../src/services/tts.py)).
- **Interruptions.** When Flux reports `StartOfTurn` while Matt is talking, the pipeline stops his audio and cancels the work in progress. The conversation keeps only the part of his reply the caller probably heard ([`speech_history.py`](../src/agent/speech_history.py)).
- **The browser's view.** The browser long-polls `GET /api/calls/<id>/events`: the object holds each request open for up to 20 seconds until something happens, then the browser asks again. Transcripts, tool rows, and notices all arrive this way. If the browser stops asking for a minute, the call ends.

## 5. Tools act only for the signed-in customer

| Tool                   | What it does                                                      | Changes data             |
| ---------------------- | ----------------------------------------------------------------- | ------------------------ |
| `get_account`          | Rereads orders, store credit, recent tickets, and credit requests | No                       |
| `search_help_center`   | Returns up to 3 help article excerpts from AI Search              | No                       |
| `cancel_order`         | Reads the order back, then cancels it after the caller says yes   | Yes                      |
| `open_ticket`          | Saves a ticket, then starts Clef triage in the background         | Yes                      |
| `request_store_credit` | Saves a credit request for staff to decide                        | Only after staff approve |

The language model fills in arguments, such as an order number. It never supplies the customer. Each handler in [`tools.py`](../src/agent/tools.py) reads `customer_id` from `params.app_resources`, an [`AgentResources`](../src/agent/resources.py) object the call builds from its saved customer. Every query in [`store.py`](../src/support/store.py) filters on it, for example `WHERE id = ? AND customer_id = ?`. If the model asks for Sam's order while Maya is signed in, the lookup finds nothing, and the tool tells the model to check the number.

Other details that matter on a voice call:

- **Order numbers.** Speech recognition might hear "a 1042" or "A1042". [`store.order_id`](../src/support/store.py) keeps the four digits and rebuilds `A-1042`.
- **Checked arguments.** Each tool's arguments are a Pydantic model that rejects unknown fields. Errors go back to the model as results, and the prompt says an error means the action didn't happen.
- **Confirming before a cancel.** A cancel can't be undone, so the code, not just the prompt, makes Matt ask first ([`run_tool`](../src/agent/tools.py)). The first `cancel_order` call changes nothing and returns the order for Matt to read back. The cancel happens only when the model calls it again with the same order in the caller's next turn. The model still judges whether the caller said yes; the code makes sure the caller heard the order and answered first.
- **Fillers and typing.** While a tool runs, Matt says a short phrase such as "Let me pull up your account" ([`fillers.py`](../src/agent/fillers.py)). A lookup that takes longer than half a second also gets quiet keyboard typing ([`thinking.py`](../src/transport/thinking.py)).
- **A slow model.** If the language model hasn't answered after 4 seconds, Matt says "Sorry, give me a second." After 12 seconds he gives up on that reply and asks the caller to say it again, rather than leaving a long silence ([`llm.py`](../src/services/llm.py)).
- **Limits.** Each tool has a 10-second deadline. One caller turn can use up to five tool calls (`MAX_TOOL_CALLS`). After that, the model has to answer with what it has.
- **The conversation panel.** Each tool reports a one-line summary, such as "Opened ticket T-4821", and help searches report the article titles as sources (`Outcome` in [`tools.py`](../src/agent/tools.py)).

## 6. The help center

- The articles live in [`src/help/`](../src/help) and ship inside the Worker through the `Data` rule in [`wrangler.jsonc`](../wrangler.jsonc).
- **Publish help center** on the desk calls [`help_center.publish`](../src/support/help_center.py). It creates the `help-center` instance in the `acme-support` AI Search namespace through the `AI_SEARCH` binding and uploads each article. A binding gives the Worker direct access, so the app needs no AI Search API token.
- `search_help_center` asks for at most 3 results and trims each excerpt to 900 characters, which keeps the conversation short. The prompt tells Matt to answer policy questions only from these results and to treat them as reference material, which makes it harder for text inside an article to act as new instructions.

## 7. Tickets and Clef triage

`open_ticket` saves the ticket in D1 and returns its number right away, so Matt can read it to the caller. Then, in the background, [`triage`](../src/support/triage.py) asks Clef three questions about the ticket:

- **Which team?** Shipping, billing, product, or safety. Clef returns its choice and how sure it is. The question asks Clef to route on what the ticket asks for, because Matt's tickets often retell earlier problems: a request for something free after a fuel smell used to land in safety. Matt names the earlier ticket's number, and the question tells Clef that a problem with its own ticket doesn't decide the team.
- **How urgent?** A score from 0 (within two business days) to 3 (right away, because someone could get hurt).
- **Did the customer ask for a person?** Clef returns a probability.

Triage runs after the reply because its answer doesn't change what Matt says next. Keeping it off the speaking path means the caller never waits for it. If it fails, the ticket still exists without routing, and the log shows `ticket_triage_failed`.

The result is saved on the ticket and sent to the call as a `ticket_triaged` event, which the conversation shows as a notice. The desk shows the team and confidence, the urgency, and whether the customer asked for a person.

## 8. Store credit waits for a person

A cashier can start a refund, but a manager's key finishes it. Matt can write up a credit request, and only a person on the desk can approve it.

- `request_store_credit` checks that the order belongs to the customer and was delivered, and that the amount is no more than the order total. It saves an approval row with the status `pending` and the call's ID, and tells the model that the credit isn't approved yet.
- A person decides because speech recognition can hear "fifteen" as "fifty", and a caller can talk a language model into things, so no credit is added until someone has looked.
- [`store.decide_approval`](../src/support/store.py) runs two statements in one D1 batch, which D1 applies as one transaction. The first adds the credit only if the request is still pending and the decision is approve. The second marks the request decided. A second click finds nothing pending, so the credit is added only once.

## 9. How a staff decision reaches a live call

1. A staff member selects **Approve** on the desk, and the browser sends `POST /desk/api/approvals/R-1234`.
2. The Worker checks that the request came from the desk's own page, verifies the Access token ([`StaffAuth`](../src/server/desk.py)), and records the decision in D1.
3. The approval row holds the call's ID, so the Worker calls the call's object directly: `env.CALLS.getByName(call_id).approval_decided(...)`. This is a remote procedure call (RPC), calling a method on another object as if it were local code. Only this Worker can reach the object through its binding, so the method needs no extra credential.
4. If the call is still live and belongs to the same customer, [`approval_decided`](../src/server/call.py) adds a notice to the conversation and asks the engine to [`announce`](../src/agent/engine.py) the decision. `announce` waits up to 20 seconds for a pause: the caller isn't talking, and no reply is being written or spoken. Then it adds a `<runtime_context>` message to the conversation and runs the language model, which tells the caller in a sentence or two.
5. The desk's response includes `caller_told`, which says whether the call was live to hear it. If the call has already ended, the decision still appears in the customer's **Your account** tab.

## 10. AI Gateway labels model requests by customer

Every language model, Aura, and Clef request passes these options:

```python
{
    "id": env.AI_GATEWAY_ID or "default",
    "metadata": {"customer_id": customer_id, "call_id": call_id},
}
```

They come from `AgentResources.gateway` in [`resources.py`](../src/agent/resources.py). With this metadata, the gateway's logs can be filtered by customer or by call, and a spend limit split by `customer_id` gives each customer a separate budget. Flux connects to Workers AI over a WebSocket and doesn't pass through the gateway. [Deployment](deployment.md#set-per-customer-spending-limits) sets up the limit.

## 11. Ending a call

A call ends when the caller selects **End call**, after 15 minutes, when the browser stops checking in for a minute, or when a part of the pipeline fails. [`VoiceCall.finish`](../src/server/call.py) stops Flux and the pipeline, revokes the adapter token, and closes the SFU adapters and tracks. If a close request fails, an alarm retries it with backoff.

The conversation text is never saved in the call's storage. The call record is deleted an hour after cleanup succeeds.

## Change the agent

Matt's prompt, voice, models, and limits are in [`src/agent/config.py`](../src/agent/config.py).

### Add a tool

1. In [`tools.py`](../src/agent/tools.py), write an argument model and a handler. Read the customer from `resources.customer_id`, return an `Outcome` with a one-line `summary`, and raise `ToolError` for expected failures such as a missing order.
2. Add a `Tool` entry to `TOOLS`. Its description tells the model when to use it.
3. Put new queries in [`store.py`](../src/support/store.py). Have each one take the customer's ID and filter on it.
4. Give the tool its own [filler phrases](../src/agent/fillers.py). A test checks that every tool has some and that each phrase is eight words or fewer: Pipecat waits for a playing filler to finish before sending a tool result to the model, so a long phrase delays the answer. If the tool answers almost instantly, also add it to `SILENT` so Matt skips the filler.
5. If the model needs guidance about when to use the tool, add a line to the prompt's Tools section.
6. Add the tool's label and icon to [the conversation registry](../web/src/voice/conversation.ts) and a [preview scenario](../web/src/preview/scenarios.ts).

If a tool moves money, have it create a pending request for staff, as `request_store_credit` does. If the caller can't undo what it does, give its `Tool` a `confirm` function that returns what Matt should read back, as `cancel_order` does.

Tool handlers also need to account for:

- **Cancellation:** let `asyncio.CancelledError` propagate and close network resources. Cancelling a request doesn't undo a write that already happened, so decide how a repeated request behaves before adding a tool that changes data.
- **Deadlines:** leave time between a service's timeout and the tool's deadline, so the handler can still return an ordinary error. Set `timeout_seconds` on the `Tool` when a tool needs a different deadline.
- **Context growth:** return only the fields the model needs. Tool results stay in the conversation and are sent with every later model request.
- **Required sequences:** when steps must happen in order, run them inside one handler. The model may not finish every step before its tool budget or the caller's turn ends.

### Models, speech, and memory

Use [the model adapter contract](../src/services/models.py) when changing language models. Response formats, reasoning controls, and tool behavior vary by model, so a plain-text reply that works doesn't prove that tool calls and conversation history work. Reasoning can also use up a reply's token budget before any spoken answer appears.

Check [Python package compatibility](../vendor/README.md) before adding a speech service, local VAD (voice activity detection), or a turn model. A package that imports doesn't prove it works on Workers.

The visible conversation can include words the caller never heard. An interrupted reply keeps an estimated spoken prefix, followed by a short note about the interruption. The estimate counts the audio sent to the caller, which can run ahead of what actually played, so the cutoff can drift around pauses, numbers, or unusual words. See [speech history](../src/agent/speech_history.py) for the tuning constants.

When adding retries to a provider, keep cancellation and the request's existing deadline. Replaying audio that was already sent, or a turn whose tools already ran, can repeat speech or actions.

Flux's key terms bias recognition. They don't rewrite finished transcripts. Add individual names, product codes, or acronyms to `STT_KEYTERMS`.

For [prompt caching](https://developers.cloudflare.com/workers-ai/features/prompt-caching/), keep the instructions and tool definitions that don't change at the start of the request, and add changing context after them. Use the usage counters the model returns to see whether caching happened.

Longer calls need a plan for summarizing old turns, remembering things between calls, and how long to keep data. Raising `MAX_CALL_SECONDS` alone doesn't provide any of these, and a larger context window doesn't remove the cost and delay of a growing conversation.
