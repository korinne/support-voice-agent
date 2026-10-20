import type { ComponentType } from 'react';
import type { SessionInfo } from '../lib/contracts';

/**
 * Browser-side counterpart of the server's `BrowserAuth` interface
 * (`src/server/auth.py`). The session hook and the room components use only
 * this contract; everything specific to a sign-in method lives in a provider
 * module next to this file, selected in `index.ts`.
 *
 * The default provider signs customers in with Cloudflare. The server maps
 * each verified Cloudflare user to an Acme customer account, so the browser
 * never chooses whose account the agent uses. A real store would use its own
 * identity provider the same way.
 *
 * - `session` reads `/api/session`. A provider that redirects to a login page
 *   should treat a non-JSON response as "sign in again" rather than an error.
 * - `signIn` exchanges what `SignIn` collected for a session cookie, or
 *   navigates to the identity provider and ignores the credential.
 * - Keep the SFU adapter routes (`/media/<call-id>/...`) reachable without a
 *   browser login; their per-call token checks stay in `src/transport/auth.py`.
 */
export type BrowserAuth = {
  /** Read the current session, including whether the server is configured. */
  session(signal: AbortSignal): Promise<SessionInfo>;
  /** Establish a session from whatever `SignIn` collected. */
  signIn(credential: SignInCredential, signal: AbortSignal): Promise<void>;
  /** End the session. Edge-authenticated providers may navigate away. */
  signOut(signal: AbortSignal): Promise<void>;
  /** Rendered on the call stage while the session is locked. */
  SignIn: ComponentType<SignInProps>;
};

/** What a sign-in form collected. Redirect providers collect nothing. */
export type SignInCredential = Record<string, string>;

export type SignInProps = {
  /** True while a submitted credential is being checked. */
  busy: boolean;
  /**
   * Why the previous attempt failed, in words meant for the user. The app
   * already shows it in the status bar; use it for field state such as
   * `aria-invalid` rather than adding a line that moves the layout.
   */
  error?: string;
  /** The assistant's name, for copy such as "Call Matt". */
  assistant: string;
  onSubmit: (credential: SignInCredential) => void;
};
