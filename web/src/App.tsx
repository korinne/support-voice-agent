import { LogOut } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { AppShell } from './components/AppShell';
import { Button } from './components/Button';
import { VoiceRoom } from './components/VoiceRoom';
import { useSession } from './hooks/useSession';
import { useVoiceCall } from './hooks/useVoiceCall';
import { request } from './lib/api';
import { accountSchema, currentCallSettings } from './lib/contracts';
import { isCallBusy } from './voice/model';

const loadAccount = (signal: AbortSignal) => request('account', accountSchema, { signal });

export default function App() {
  const session = useSession();
  const { call, view } = useVoiceCall(session.state.kind === 'ready');
  const beginning = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const start = async () => {
    if (beginning.current || isCallBusy(call.snapshot())) return;
    beginning.current = true;
    try {
      // The session is re-read first; a stale one returns to the sign-in form.
      if (!(await session.authorize()) || !mounted.current) return;
      await call.recover();
      if (!mounted.current) return;
      await call.start(currentCallSettings());
    } finally {
      beginning.current = false;
    }
  };

  const customer = session.state.kind === 'ready' ? session.info?.customer : null;
  return (
    <AppShell
      label={customer ? customer.name || customer.email : 'Support'}
      actions={
        customer && (
          <Button
            tone="quiet"
            size="sm"
            disabled={isCallBusy(view)}
            onClick={() => {
              void session.signOut();
            }}
          >
            <LogOut className="size-3.5" aria-hidden="true" />
            Sign out
          </Button>
        )
      }
    >
      <VoiceRoom
        view={view}
        session={session.state}
        info={session.info}
        loadAccount={loadAccount}
        onStart={() => {
          void start();
        }}
        onWarm={() => call.warm()}
        onSignIn={(credential) => {
          void session.signIn(credential);
        }}
        onRetry={() => {
          void session.refresh();
        }}
        onStop={() => {
          void call.stop();
        }}
        onMute={() => call.toggleMute()}
        onResume={() => {
          void call.resumeAudio();
        }}
      />
    </AppShell>
  );
}
