import { ArrowRight, LoaderCircle } from 'lucide-react';
import { z } from 'zod';
import { Button } from '../components/Button';
import { ApiError, request } from '../lib/api';
import { sessionSchema } from '../lib/contracts';
import type { BrowserAuth, SignInProps } from './provider';

// Why `CloudflareAuth.callback` sent the browser back without a session.
const outcomes = new Set(['cancelled', 'expired', 'failed']);

/**
 * Sign in with Cloudflare, paired with `CloudflareAuth` in
 * `src/server/auth_cloudflare.py`. The browser goes to Cloudflare and back,
 * and the server sets an HttpOnly session cookie. Nothing is kept in browser JS.
 */
export const cloudflareLogin: BrowserAuth = {
  async session(signal) {
    const url = new URL(window.location.href);
    const outcome = url.searchParams.get('sign_in');
    if (outcome !== null) {
      // Report the outcome once, and keep it out of the address bar.
      url.searchParams.delete('sign_in');
      window.history.replaceState(window.history.state, '', url);
    }
    const info = await request('session', sessionSchema, { signal, timeout: 15_000 });
    if (outcome && outcomes.has(outcome) && info.configured && !info.authenticated) {
      throw new ApiError(`sign_in_${outcome}`, 401);
    }
    return info;
  },
  signIn(_credential, signal) {
    window.location.assign('/auth/login');
    // The page unloads on its way to Cloudflare, so this settles only if cancelled.
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
  },
  async signOut(signal) {
    await request('logout', z.object({ authenticated: z.literal(false) }), {
      body: {},
      signal,
      timeout: 15_000,
    });
  },
  SignIn: CloudflareSignIn,
};

function CloudflareSignIn({ busy, assistant, onSubmit }: SignInProps) {
  return (
    <div className="w-full max-w-md text-center">
      <h1 className="text-2xl leading-tight font-semibold tracking-tight text-balance text-stone-50 sm:text-3xl">
        Call Acme support
      </h1>
      <p className="mx-auto mt-3 max-w-sm text-sm leading-6 text-pretty text-stone-400">
        Sign in with your Cloudflare account. Acme learns only your email and name, and {assistant}{' '}
        can see only your account.
      </p>
      <Button tone="primary" className="mt-6" disabled={busy} onClick={() => onSubmit({})}>
        {busy ? (
          <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />
        ) : null}
        {busy ? 'Opening Cloudflare...' : 'Sign in with Cloudflare'}
        {!busy && <ArrowRight className="size-4" aria-hidden="true" />}
      </Button>
    </div>
  );
}
