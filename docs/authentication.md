# Authentication

This app has two kinds of users, and they sign in differently:

- **Customers** talk to Matt on the page at `/`. They sign in with their Cloudflare account.
- **Staff** approve or decline credit on the desk at `/desk`. Cloudflare Access signs them in, and the Worker checks Access's token again.

Realtime's audio adapters are a third kind of visitor. They use a per-call token and never sign in.

## Customers: Sign in with Cloudflare

Customers sign in through OAuth, the standard way for one site to let you sign in with your account on another. [`CloudflareAuth`](../src/server/auth_cloudflare.py) handles the trip there and back:

1. **Sign in with Cloudflare** opens `/auth/login`. The Worker sends the browser to Cloudflare's sign-in page with a random `state` value and a PKCE challenge, a hash of a secret the Worker keeps. It saves both in a signed `acme_sign_in` cookie that lasts 10 minutes.
2. The person approves one permission, User Details Read. It lets Acme read who they are, and nothing on their Cloudflare account.
3. Cloudflare sends the browser back to `/auth/callback` with a one-time code. The Worker checks that `state` matches the cookie, so a sign-in that another browser started can't finish in this one. Then it trades the code, its client secret, and the PKCE secret for an access token.
4. The Worker reads the person's Cloudflare user ID from the userinfo endpoint, which shares nothing else, and their email and first name from the [user API](https://developers.cloudflare.com/api/resources/user/methods/get/). Then it revokes the token. Acme never acts on the person's Cloudflare account, so it doesn't keep the token.
5. It sets a signed `acme_session` cookie with a new owner ID and the person's Acme customer ID, and sends the browser to `/`.

The customer ID comes from the Cloudflare user ID, not the name or email. One login always maps to the same account, and two logins are two customers, even when one person owns both. The first visit creates the account with four starter orders, in [`ensure_customer`](../src/support/store.py), so anyone can bring up any of the demo's problems. **Reset demo data** on the desk deletes every account, and each person gets a fresh one on their next visit.

- **Who can sign in.** A new OAuth client is private, so only members of the Cloudflare account that owns it can sign in. To let someone try the demo, invite them as a member with the most limited role. Making the client public lets any Cloudflare user sign in. That requires a verified domain, and it can't be undone.
- **Sessions** last 8 hours. Rotating `SESSION_SECRET` signs everyone out. Rotating the client secret affects only new sign-ins.
- **A sign-in that doesn't finish** returns to `/?sign_in=cancelled`, `expired`, or `failed`, and the page says what happened. `failed` usually means the client ID or secret is wrong, or the OAuth client's redirect URL isn't exactly `https://<your-host>/auth/callback`.

### Replace it with your own identity provider

A real store signs customers in with its own login, such as an existing customer account system or any OpenID Connect provider. Nothing after sign-in changes, because the call, the tools, and the account page all read the customer ID from the server.

1. Implement the [`BrowserAuth`](../src/server/auth.py) contract. Its `identity` method verifies the request, for example by checking a session cookie or a JSON Web Token's (JWT's) signature, issuer, audience, and expiry, and returns `Caller(owner=..., customer_id=...)`. `login` and `callback` handle a redirect sign-in. The contract is async so a provider can fetch and cache signing keys.
2. Look up the customer ID on the server, for example in a D1 table keyed by the provider's stable user ID (its `sub` claim). Add that table in a new file in [`migrations/`](../migrations). The demo derives the ID from the Cloudflare user ID instead, so a reset can recreate the account.
3. Replace the starter orders in [`ensure_customer`](../src/support/store.py) with a lookup of the customer's real records, or remove the call from [`/api/session`](../src/server/routes.py).
4. Return your provider from `browser_auth()` in [`auth.py`](../src/server/auth.py), and pair it with a [browser provider](../web/src/auth/provider.ts), selected in [the frontend factory](../web/src/auth/index.ts). Sign-in screens, redirects, and sign-out belong there.

Never take the customer ID from the request body, a URL, or the language model. `test_an_async_identity_provider_preserves_call_ownership` in [`tests/server/test_auth_boundary.py`](../tests/server/test_auth_boundary.py) shows a provider whose user ID isn't a cookie.

## Authorization

What each kind of visitor can reach once signed in:

- **Calls belong to their owner.** A call belongs to the browser session that created it. Requests for that call from any other session get `404`, so knowing a call ID grants nothing.
- **A call keeps its customer.** The customer is saved when the call is created and doesn't change if the browser signs in as someone else.
- **Tools act only for that customer.** Every tool reads the customer from the call's server-side resources, and every D1 query filters on it. See [how it works](how-it-works.md#5-tools-act-only-for-the-signed-in-customer).
- **Changes come from the app's own pages.** Requests that change something must carry the app's own origin, and call creation is limited to 5 per minute per browser session.
- **Realtime uses a per-call token.** The adapter URLs carry a [token](../src/transport/auth.py) issued when the agent starts. It stops working when the call ends or expires.
- **Staff see every customer.** The desk lists all tickets and credit requests, so every desk endpoint requires a verified Access token.

## Staff: Cloudflare Access

Access runs on Cloudflare's network before a request reaches the Worker. A staff member who opens `/desk` gets Access's login page, enters their email address, and types the one-time PIN that Access sends. Access then sets a `CF_Authorization` cookie and adds a signed token, a JWT, to each request in the `Cf-Access-Jwt-Assertion` header. [Deployment](deployment.md#protect-the-staff-desk-with-cloudflare-access) covers the dashboard setup.

### The Worker checks the token too

On every desk request, [`StaffAuth`](../src/server/desk.py) checks:

- **Signature:** the token is signed with RS256, a public-key signature, by one of the team's keys published at `https://<your-team>.cloudflareaccess.com/cdn-cgi/access/certs`. The Worker caches the keys for an hour. Access rotates its keys, so a token signed with an unknown key triggers a fresh download, at most once a minute so forged tokens can't make every request fetch them.
- **Issuer (`iss`):** equals the team domain in `ACCESS_TEAM_DOMAIN`.
- **Audience (`aud`):** includes `ACCESS_AUD`, so a token from another Access application in the same team doesn't work.
- **Expiry (`exp`) and not-before (`nbf`):** the token is current, allowing 60 seconds of clock difference.
- **Email:** the token has an `email` claim. The desk records it as the person who decided.

Requests that change something also need the desk's own origin, checked before the token.

The Worker checks again because the edge setting can't be the only lock:

- An Access application can be deleted, or its path edited, without anyone touching the Worker.
- Preview URLs, and any other hostname that reaches the Worker, aren't covered unless someone adds them to the application.
- Workers that serve static assets, like this one, don't receive `ctx.access`, the runtime's built-in Access identity. The Worker reads the token itself.
- An email header on its own proves nothing, because any client can send one, so the Worker trusts only the signed token.

The desk fails closed:

| Situation                                      | Response                      |
| ---------------------------------------------- | ----------------------------- |
| `ACCESS_TEAM_DOMAIN` or `ACCESS_AUD` isn't set | `503 desk_not_configured`     |
| No token, or a token that fails any check      | `401 staff_login_required`    |
| Access's public keys can't be downloaded       | `503 staff_login_unavailable` |

When a staff member's Access session expires, Access answers desk requests with a redirect to its login page, which the desk's background requests can't follow. The desk shows a message with a **Reload** button, and reloading the page signs in again. **Sign out** goes to `/cdn-cgi/access/logout`.

### Keep Access off the rest of the app

- Realtime's adapters connect to `/media/<call-id>/input` and `/media/<call-id>/output`. They can't sign in, follow a redirect, or send custom headers. If Access covers those paths, calls fail to connect.
- [Worker-level Access](https://developers.cloudflare.com/workers/configuration/cloudflare-access/), set on the Worker's **Access** tab, rejects WebSocket connections with a `403` error.
- So protect `/desk` with a self-hosted Access application for that path, and leave `/`, `/api/`, and `/media/` to the app's own checks.

Some organizations require Access on every endpoint. This app can't run under that rule, because Realtime's adapters can't authenticate to Access. See the [adapter endpoint requirements](https://developers.cloudflare.com/realtime/sfu/features/media-transport-adapters/websocket-adapter/).
