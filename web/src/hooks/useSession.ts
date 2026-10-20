import { useCallback, useEffect, useRef, useState } from 'react';
import { browserAuth, type SignInCredential } from '../auth';
import { ApiError, describeError } from '../lib/api';
import type { SessionInfo } from '../lib/contracts';
import { setupMessage, type SessionState } from '../lib/session';

function settled(result: SessionInfo): SessionState {
  if (!result.configured)
    return { kind: 'unavailable', message: setupMessage(result.setup_required) };
  return { kind: result.authenticated ? 'ready' : 'locked' };
}

function failed(error: unknown): SessionState {
  if (error instanceof ApiError && [400, 401, 429].includes(error.status)) {
    return { kind: 'locked', error: describeError(error) };
  }
  return { kind: 'unavailable', message: describeError(error) };
}

/**
 * Browser session state machine. It knows nothing about how a session is
 * established; that belongs to the provider in `web/src/auth/`.
 */
export function useSession() {
  const [state, setState] = useState<SessionState>({ kind: 'loading' });
  const [info, setInfo] = useState<SessionInfo>();
  const pending = useRef<AbortController | undefined>(undefined);

  // One provider operation at a time; a newer one cancels the older.
  const run = useCallback(
    async (initial: SessionState, task: (signal: AbortSignal) => Promise<boolean>) => {
      pending.current?.abort();
      const controller = new AbortController();
      pending.current = controller;
      setState(initial);
      try {
        return await task(controller.signal);
      } catch (error) {
        if (!controller.signal.aborted) setState(failed(error));
        return false;
      }
    },
    [],
  );

  // Read the session after authentication; local UI state is not proof of access.
  const settle = useCallback(async (signal: AbortSignal) => {
    const result = await browserAuth.session(signal);
    signal.throwIfAborted();
    setInfo(result);
    setState(settled(result));
    return result.configured && result.authenticated;
  }, []);

  const check = useCallback(
    (initial: SessionState = { kind: 'checking' }) => run(initial, settle),
    [run, settle],
  );

  useEffect(() => {
    void check({ kind: 'loading' });
    return () => pending.current?.abort();
  }, [check]);

  return {
    state,
    info,
    refresh: () => check(),
    // Recheck before starting: an expired session must prompt for sign-in
    // before requesting microphone access or allocating a call.
    authorize: () => check(),
    signIn: (credential: SignInCredential) =>
      run({ kind: 'signing-in' }, async (signal) => {
        await browserAuth.signIn(credential, signal);
        return settle(signal);
      }),
    signOut: () =>
      run({ kind: 'checking' }, async (signal) => {
        await browserAuth.signOut(signal);
        // Re-read the session so the page shows the signed-out state.
        await settle(signal);
        return false;
      }),
  };
}
