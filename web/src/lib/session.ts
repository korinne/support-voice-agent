/**
 * Browser session state as the UI sees it. Provider-specific details (OAuth
 * redirects, Cloudflare Access) stay inside `web/src/auth/`.
 */
export type SessionState =
  | { kind: 'loading' }
  | { kind: 'locked'; error?: string }
  | { kind: 'signing-in' }
  | { kind: 'checking' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; message: string };

// What each setup gap means to the person deploying the app.
const setupNeeds: Record<string, string> = {
  sign_in: 'the OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, and SESSION_SECRET secrets',
  realtime: 'the Realtime SFU credentials',
  database: 'the D1 tables',
};

export function setupMessage(needs: string[]) {
  const missing = needs.map((need) => setupNeeds[need] ?? need);
  if (!missing.length) return 'This app needs its server credentials before it can take calls.';
  const list = new Intl.ListFormat('en', { type: 'conjunction' }).format(missing);
  return `This app isn't set up yet. It needs ${list}. See docs/deployment.md.`;
}
