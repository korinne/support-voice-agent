import clsx from 'clsx';
import { useEffect, useState } from 'react';
import { browserAuth, type SignInCredential } from '../auth';
import type { Account, SessionInfo } from '../lib/contracts';
import type { SessionState } from '../lib/session';
import { describeStatus, isCallBusy, type CallView, type StatusView } from '../voice/model';
import { AccountPanel } from './AccountPanel';
import { CallStage } from './CallStage';
import { ConnectionDetails } from './ConnectionDetails';
import { Conversation } from './Conversation';
import { ExportChat } from './ExportChat';
import { StartCall } from './StartCall';

const { SignIn } = browserAuth;
const tabs = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'account', label: 'Your account' },
] as const;
type Tab = (typeof tabs)[number]['id'];

export function VoiceRoom({
  view,
  session,
  info,
  loadAccount,
  onStart,
  onWarm,
  onSignIn,
  onRetry,
  onStop,
  onMute,
  onResume,
}: {
  view: CallView;
  session: SessionState;
  info?: SessionInfo;
  loadAccount: (signal: AbortSignal) => Promise<Account>;
  onStart: () => void;
  onWarm: () => void;
  onSignIn: (credential: SignInCredential) => void;
  onRetry: () => void;
  onStop: () => void;
  onMute: () => void;
  onResume: () => void;
}) {
  // Before a call there is nothing to read, so open on the account the agent
  // will work with. The transcript takes over as soon as a call begins.
  const [tab, setTab] = useState<Tab>(view.history.length ? 'conversation' : 'account');
  const busy = isCallBusy(view);
  useEffect(() => {
    if (busy) setTab('conversation');
  }, [busy]);
  const spoken = view.history.filter(
    (item) => item.kind === 'user' || item.kind === 'assistant',
  ).length;
  // Finished tool calls and staff decisions can change the account.
  const accountVersion =
    view.history.filter(
      (item) => item.kind === 'notice' || (item.kind === 'tool' && item.status !== 'running'),
    ).length + (view.phase === 'ended' ? 1 : 0);
  const name = info?.assistant ?? 'Matt';
  const customer = session.kind === 'ready' ? info?.customer : undefined;
  const signingIn = session.kind === 'locked' || session.kind === 'signing-in';
  return (
    <>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_22rem]">
        <CallStage
          view={view}
          name={name}
          caller={
            customer
              ? {
                  name: customer.email || customer.name,
                  hint: `${name} can see the orders on your account.`,
                }
              : undefined
          }
          cover={
            signingIn ? (
              <SignIn
                busy={session.kind === 'signing-in'}
                error={session.kind === 'locked' ? session.error : undefined}
                assistant={name}
                onSubmit={onSignIn}
              />
            ) : undefined
          }
          maxCallSeconds={info?.max_call_seconds}
          onStop={onStop}
          onMute={onMute}
          onResume={onResume}
          startControl={
            <StartCall
              session={session}
              ended={view.phase === 'ended'}
              disabled={false}
              onStart={onStart}
              onWarm={onWarm}
              onRetry={onRetry}
            />
          }
        />
        <aside
          className="flex min-h-0 flex-col border-t border-stone-200 lg:border-t-0 lg:border-l"
          aria-label="Conversation and account"
        >
          <div className="flex items-center justify-between gap-2 px-5 sm:px-6">
            <div
              className="flex gap-4 sm:gap-6"
              role="tablist"
              aria-label="Conversation and account"
            >
              {tabs.map(({ id, label }, index) => (
                <button
                  type="button"
                  key={id}
                  id={`${id}-tab`}
                  role="tab"
                  aria-selected={tab === id}
                  aria-controls={`${id}-panel`}
                  tabIndex={tab === id ? 0 : -1}
                  onClick={() => setTab(id)}
                  onKeyDown={(event) => {
                    const positions: Record<string, number> = {
                      Home: 0,
                      End: tabs.length - 1,
                      ArrowLeft: index - 1,
                      ArrowRight: index + 1,
                    };
                    if (!(event.key in positions)) return;
                    event.preventDefault();
                    const next = tabs[(positions[event.key] + tabs.length) % tabs.length].id;
                    setTab(next);
                    document.getElementById(`${next}-tab`)?.focus();
                  }}
                  className={clsx(
                    'flex min-h-12 items-center gap-2 border-b-2 text-sm font-medium transition-colors motion-reduce:transition-none',
                    tab === id
                      ? 'border-orange-700 text-stone-900'
                      : 'border-transparent text-stone-600 hover:text-stone-900',
                  )}
                >
                  {label}
                  {id === 'conversation' && spoken > 0 && (
                    <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-600 tabular-nums">
                      {spoken}
                    </span>
                  )}
                </button>
              ))}
            </div>
            {tab === 'conversation' && <ExportChat view={view} name={name} />}
          </div>
          <div className="relative min-h-0 flex-1 border-t border-stone-200">
            <div
              role="tabpanel"
              id="conversation-panel"
              aria-labelledby="conversation-tab"
              hidden={tab !== 'conversation'}
              className="lg:absolute lg:inset-0"
            >
              <Conversation history={view.history} name={name} />
            </div>
            <div
              role="tabpanel"
              id="account-panel"
              aria-labelledby="account-tab"
              hidden={tab !== 'account'}
              className="lg:absolute lg:inset-0 lg:overflow-y-auto"
            >
              <AccountPanel
                load={loadAccount}
                enabled={session.kind === 'ready'}
                version={accountVersion}
              />
            </div>
          </div>
        </aside>
      </div>
      <StatusBar {...describeStatus(view, session)} />
      <ConnectionDetails timings={view.timings} />
    </>
  );
}

const statusStyles = {
  neutral: { marker: 'bg-stone-400', text: 'text-stone-600' },
  ok: { marker: 'bg-emerald-700', text: 'text-emerald-700' },
  warning: { marker: 'bg-amber-700', text: 'text-amber-700' },
  error: { marker: 'bg-red-700', text: 'font-medium text-red-700' },
} satisfies Record<StatusView['tone'], { marker: string; text: string }>;

function StatusBar({ tone, busy, message }: StatusView) {
  const style = statusStyles[tone];
  return (
    <div className="flex min-h-12 items-start gap-2.5 border-t border-stone-200 bg-stone-50 px-5 py-3 text-sm sm:px-6">
      <span
        aria-hidden="true"
        className={clsx(
          'mt-1.5 size-2 shrink-0 rounded-full',
          style.marker,
          busy && 'motion-safe:animate-pulse',
        )}
      />
      <span className="font-medium text-stone-700" aria-hidden="true">
        Status
      </span>
      <p
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className={clsx('min-w-0 wrap-anywhere', style.text)}
      >
        {message}
      </p>
    </div>
  );
}
