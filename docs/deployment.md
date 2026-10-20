# Deployment

This guide takes you from an empty Cloudflare account to a working demo:

1. [Configure](#configure) the app secrets.
2. [Deploy](#deploy) the Worker.
3. [Create the tables](#create-the-tables) in D1.
4. [Set up Sign in with Cloudflare](#set-up-sign-in-with-cloudflare) for customers.
5. [Protect the staff desk with Cloudflare Access](#protect-the-staff-desk-with-cloudflare-access).
6. [Publish the help center](#publish-the-help-center).
7. [Set per-customer spending limits](#set-per-customer-spending-limits) with AI Gateway.

Steps 4, 5, and 7 happen partly in the Cloudflare dashboard. Dashboard labels change over time. If one doesn't match, the Cloudflare doc linked from that step has the current path.

## What you need

- Node.js 22.12 or later, Python 3.14, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Cloudflare account on Workers Paid. The default [DeepSeek model requires paid access](https://developers.cloudflare.com/workers-ai/models/deepseek-v4-flash-0731/).
- A [Realtime SFU app](https://developers.cloudflare.com/realtime/sfu/). Its app ID and secret go in the Worker's secrets.
- Zero Trust, for the staff desk. The Free plan is enough for a demo. Each staff member who signs in through Access uses one seat.

Realtime connects to the Worker's `/media/` endpoints, and it can't get past a login page. Access protects only `/desk` in this guide. Read [Keep Access off the rest of the app](authentication.md#keep-access-off-the-rest-of-the-app) before you change that.

## Configure

From the repository root:

```sh
npm ci
npm run setup
cp .dev.vars.example .dev.vars
chmod 600 .dev.vars
```

`npm run setup` installs the Python packages that ship with the Worker. Fill in the app secrets in `.dev.vars`, which Git ignores:

| Secret                | Value                                                                    |
| --------------------- | ------------------------------------------------------------------------ |
| `REALTIME_APP_ID`     | Your SFU app ID                                                          |
| `REALTIME_AUTH_TOKEN` | That SFU app's secret. A general Cloudflare API token doesn't work here. |
| `SESSION_SECRET`      | A random signing key of at least 32 characters                           |
| `OAUTH_CLIENT_ID`     | Your OAuth client's ID, from step 4                                      |
| `OAUTH_CLIENT_SECRET` | Your OAuth client's secret, from step 4                                  |

Generate a signing key with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep the file and its values out of Git and out of `VITE_*` variables, which end up in the browser.

Leave the `OAUTH_*` values as placeholders and the two `ACCESS_*` lines commented out for now. You fill them in during steps 4 and 5.

## Deploy

Sign in to Wrangler for the target account, then run:

```sh
export CLOUDFLARE_ACCOUNT_ID=your-account-id
npm run deploy
python3 scripts/worker.py secret bulk .dev.vars
```

Both commands must use the same account. The first deploy creates the `acme-support` D1 database and the `acme-support` AI Search namespace if they don't exist yet. A local `.dev.vars` file doesn't set deployed secrets: the page reports that setup is required until the upload finishes.

For later deployments, run `npm run deploy`. Upload secrets again only when their values change.

## Create the tables

```sh
npx wrangler d1 migrations apply acme-support --remote
```

This creates the tables from [`migrations/`](../migrations). There's no data to load. Each person's first sign-in creates their account with four starter orders: an umbrella delivered two days ago, sneakers stuck in transit, a pogo stick, and a tunnel kit that hasn't shipped. **Reset demo data** on the desk deletes every account, and each person gets fresh orders on their next visit.

## Set up Sign in with Cloudflare

Customers sign in with their Cloudflare account through an [OAuth client](https://developers.cloudflare.com/fundamentals/oauth/create-an-oauth-client/) that you create. [Authentication](authentication.md#customers-sign-in-with-cloudflare) explains what happens during sign-in.

1. In the Cloudflare dashboard, go to **Manage Account** > **OAuth clients** and select **Create client**.
2. Fill in the form:
   - **Redirect URL:** `https://<your-worker-hostname>/auth/callback`, using the hostname that `npm run deploy` printed.
   - **Response type:** code. **Grant type:** authorization code. **Token authentication method:** client secret, basic.
   - **Scope:** User Details Read only. It lets the app read who someone is, and nothing on their account.
3. Copy the client ID and secret. The secret is shown only once.
4. Put them in `.dev.vars` as `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`, then upload the secrets again with `python3 scripts/worker.py secret bulk .dev.vars`.

New clients are private, so only members of your Cloudflare account can sign in. That keeps strangers from running up your model costs. To let someone else try the demo, invite them under **Manage Account** > **Members** with the most limited role.

Open the Worker URL, select **Sign in with Cloudflare**, and approve the permission. Then select **Start conversation** and allow microphone access. Matt can already look up orders. Policy questions need the help center, which you publish from the desk in step 6.

## Protect the staff desk with Cloudflare Access

Cloudflare Access puts a login page in front of a URL. In this guide, staff sign in with a one-time PIN: Access emails them a code, and only addresses on your list get in. Access runs on Cloudflare's network before the request reaches the Worker, and the Worker checks Access's signed token again on every desk request ([why](authentication.md#the-worker-checks-the-token-too)).

Protect `/desk` only. Don't put Access in front of the whole hostname, and don't use the Worker's **Access** tab (**Protect this Worker behind Access**). Realtime's audio adapters connect to `/media/` and can't sign in, and [Worker-level Access](https://developers.cloudflare.com/workers/configuration/cloudflare-access/) rejects WebSocket connections with a `403` error.

### Turn on Zero Trust

Skip this if your account already uses Zero Trust.

1. In the [Cloudflare dashboard](https://dash.cloudflare.com/), select **Zero Trust**.
2. Choose a team name. Your login page becomes `<your-team>.cloudflareaccess.com`, and this is the team domain you need later. You can find it again in **Zero Trust** > **Settings**.
3. Choose a plan. The Free plan asks for payment details but doesn't charge.

See [Get started with Zero Trust](https://developers.cloudflare.com/cloudflare-one/setup/) for details.

### Add one-time PIN sign-in

New Zero Trust organizations don't get one-time PIN automatically.

1. Go to **Zero Trust** > **Integrations** > **Identity providers**.
2. Select **Add new identity provider**, then **One-time PIN**.

See [One-time PIN login](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/).

### Create the Access application

1. Go to **Zero Trust** > **Access controls** > **Applications**.
2. Select **Create new application** > **Self-hosted and private** > **Add public hostname**.
3. Enter the Worker's hostname and the desk path:
   - **Subdomain:** `acme-support`
   - **Domain:** `<your-subdomain>.workers.dev`
   - **Path:** `desk`

   The path covers `/desk` and everything under it, including the desk's API at `/desk/api/`. If the **Domain** list shows only zones you own, add a [Custom Domain](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/) to the Worker and use that hostname with the path `desk`.

4. Under **Access policies**, create a policy:
   - **Action:** Allow
   - **Include:** Emails, with each staff member's address

   Access denies everyone who doesn't match an Allow policy.

5. Select **One-time PIN** as the identity provider.
6. Set **Session Duration**, for example 24 hours. Staff sign in again after it runs out.
7. Select **Create**.

See [Publish a self-hosted application](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) and [Application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/).

### Give the Worker the team domain and audience tag

The Worker needs two values to check Access's tokens:

- **Team domain:** `<your-team>.cloudflareaccess.com`.
- **Application Audience (AUD) tag:** a long ID that names this Access application. In **Zero Trust** > **Access controls** > **Applications**, select **Configure** for the application, then copy **Application Audience (AUD) Tag** from **Additional settings**. It changes only if you delete and recreate the application. See [Validate JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

Uncomment the two lines in `.dev.vars` and replace the placeholder values:

```sh
ACCESS_TEAM_DOMAIN="<your-team>.cloudflareaccess.com"
ACCESS_AUD="your-access-application-audience-tag"
```

Upload the secrets again:

```sh
python3 scripts/worker.py secret bulk .dev.vars
```

### Check the desk

Open `https://acme-support.<your-subdomain>.workers.dev/desk`. Access asks for your email address and sends a PIN. After you sign in, the desk lists credit requests and tickets.

| What you see                                                                | What it means                                                                                        |
| --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| "The staff desk needs Cloudflare Access"                                    | `ACCESS_TEAM_DOMAIN` or `ACCESS_AUD` is missing.                                                     |
| You signed in, but the desk says your sign-in expired                       | The Worker rejected the token. Check that the team domain and AUD tag match this Access application. |
| The desk says your sign-in expired, and you never saw the Access login page | Access doesn't cover this hostname or path. Check the application's hostname and its `desk` path.    |
| Calls stop connecting                                                       | Access covers more than `/desk`. Narrow the application to the `desk` path.                          |

## Publish the help center

On the desk, find **Help center** and select **Publish help center**. The Worker creates the `help-center` AI Search instance through its binding, so you don't need an AI Search API token, and uploads the eight articles in [`src/help/`](../src/help). Indexing takes about a minute. The desk shows how many articles are indexed, and until indexing finishes, Matt can't answer policy questions.

The articles ship inside the Worker. After you edit one, run `npm run deploy`, then publish again. Publishing uploads every article in `src/help/` but doesn't delete articles you removed from that folder.

## Set per-customer spending limits

AI Gateway sits between the app and Workers AI. It logs each model request and can cap spending. Every language model, speech, and Clef request from this app carries `customer_id` and `call_id` metadata ([`AgentResources.gateway`](../src/agent/resources.py)), so one spend-limit rule can give every customer their own budget. Speech recognition (Flux) connects to Workers AI directly and isn't counted.

Out of the box, the app uses your account's `default` gateway, which Cloudflare creates on first use. This guide creates a gateway for the app, so its limits and logs cover only this app.

### Create the gateway

1. In the dashboard, go to **AI** > **AI Gateway** and select **Create Gateway**.
2. Name it `acme-support`.
3. Choose **Workers AI Billing**:
   - **Standard billing** charges your Cloudflare account at the end of each billing cycle.
   - **Unified billing** deducts from prepaid AI Gateway credits as requests happen.

   [Spend limits](https://developers.cloudflare.com/ai-gateway/features/spend-limits/) apply to Unified Billing requests and to requests made with your own provider keys. Choose **Unified billing**, and add credits, if you want the limit to count this app's Workers AI usage.

4. Select **Create**.

See [Create a gateway](https://developers.cloudflare.com/ai-gateway/get-started/).

### Add the spend-limit rule

1. Open the gateway and go to **Settings** > **Spend limits**. Add a rule.
2. Under **Limit by metadata**, select **Add metadata dimension** and enter `customer_id`.
3. Set the dimension to **Split by value**. Each customer gets a separate budget.
4. Set the budget amount and time window, for example $1 per day, and save.

### Point the app at the gateway

Create the gateway before this step. A request to a named gateway that doesn't exist fails with error 2001, and Matt can't reply.

In [`wrangler.jsonc`](../wrangler.jsonc), set `AI_GATEWAY_ID` to `acme-support` in both `vars` blocks, the top-level one and the one inside `previews`. Then deploy:

```sh
npm run deploy
```

### Check it

Make a call, then open the gateway's logs. Each request should show its `customer_id` and `call_id` metadata and an estimated cost. To test the limit, set a tiny budget, make a call, and confirm that Matt stops replying. Then raise the budget.

When a customer goes over their budget, the gateway rejects their model requests with a `429` error until the time window resets, so Matt can't answer that customer. Other customers keep their own budgets. Enforcement catches up shortly after each request, so a burst of requests can go slightly over the limit, and costs are estimates based on token counts.

Gateway logs are on by default and can include prompts and responses. In this app, that means call transcripts and account details. To stop storing them, turn logs off in the gateway's **Settings**.

## Previews

[Workers Previews](https://developers.cloudflare.com/workers/previews/) (open beta) run a branch at its own URL without replacing production. From the branch, using the same account, run `npm run deploy:preview`.

The script deploys a Preview named after the Git branch, served at `<branch>-acme-support.<your-subdomain>.workers.dev`, then uploads `.dev.vars` as its secrets. A new Preview deployment doesn't keep the previous deployment's secrets, and `wrangler preview --secrets-file` currently fails with API error 10013, so the upload is a separate step. The Preview reports that setup is required until it finishes. If it fails, run `npx wrangler preview secret bulk .dev.vars` again. To sign in on a Preview, add `https://<branch>-acme-support.<your-subdomain>.workers.dev/auth/callback` to your OAuth client's redirect URLs.

A Preview inherits none of the top-level bindings. The `previews` block in [`wrangler.jsonc`](../wrangler.jsonc) declares them again, so keep it in step when you add a binding. Without `CALLS`, every request fails with error 1101.

What a Preview shares with production:

- **Shared:** the D1 database, the help center, and the SFU app. Tickets and credit requests from a Preview appear on the production desk, and **Reset demo data** in either place resets both.
- **Separate:** the Durable Object namespace and the rate-limit counters. A decision made on the production desk can't reach a call running on a Preview. The customer still sees it in their **Your account** tab.
- **Not covered by Access:** the production Access application covers only the hostnames you added. On a Preview, the desk keeps saying the sign-in expired. To use the desk there, add the Preview's hostname, with the path `desk`, to the same Access application. It then shares the same AUD tag.

Delete a Preview when the branch is done with `npx wrangler preview delete`.

## Operating the example

Calls don't resume after a Worker restart or browser disconnect. Short network drops are tolerated: the browser waits 8 seconds for a disconnected audio connection to recover, and the agent waits 16 seconds for Realtime to reconnect its input adapter. An output adapter disconnect ends the call, and so does a browser that stops checking in for a minute. Calls end after 15 minutes (`MAX_CALL_SECONDS` in [the agent configuration](../src/agent/config.py)). For longer calls, read [models, speech, and memory](how-it-works.md#models-speech-and-memory) first.

New calls are limited to 5 per minute per browser session. The limits are in [`wrangler.jsonc`](../wrangler.jsonc).

Ending a call closes its SFU adapters and tracks. If a close request fails, a Durable Object alarm retries it with backoff, so cleanup can stay pending after the page closes. A failed close request doesn't prove that an SFU resource is gone. Check the call's logs before assuming it was released. The call record is deleted an hour after cleanup succeeds.
