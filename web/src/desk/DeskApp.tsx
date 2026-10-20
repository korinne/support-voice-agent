import clsx from 'clsx';
import {
  BookOpen,
  Check,
  HandCoins,
  LoaderCircle,
  LogOut,
  RotateCcw,
  ShieldCheck,
  Ticket,
  Upload,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppShell } from '../components/AppShell';
import { Button } from '../components/Button';
import { ApiError, describeError } from '../lib/api';
import type { Desk, DeskApproval, DeskTicket, HelpCenterStatus } from '../lib/contracts';
import { WANTS_PERSON, capitalize, money, percent, urgency, when } from '../lib/format';
import { liveDesk, type Decision, type DeskClient } from './client';

// Often enough for a credit request to appear while the customer is still talking.
const refreshMs = 4_000;

function loadError(error: unknown) {
  if (error instanceof ApiError) return error.message;
  // An expired Access session redirects to its login page, which fetch can't follow.
  return "Couldn't reach the desk. Your staff sign-in may have expired. Reload to sign in again.";
}

export default function DeskApp({ client = liveDesk }: { client?: DeskClient }) {
  const [desk, setDesk] = useState<Desk>();
  const [error, setError] = useState<string>();
  const [working, setWorking] = useState<string>();
  const [status, setStatus] = useState<string>();
  const pending = useRef<AbortController | undefined>(undefined);

  const refresh = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    try {
      setDesk(await client.overview(controller.signal));
      setError(undefined);
    } catch (caught) {
      if (!controller.signal.aborted) setError(loadError(caught));
    }
  }, [client]);

  useEffect(() => {
    document.title = 'Staff desk · Acme Corp';
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, refreshMs);
    return () => {
      clearInterval(timer);
      pending.current?.abort();
    };
  }, [refresh]);

  const act = async (key: string, task: () => Promise<string>) => {
    setWorking(key);
    try {
      setStatus(await task());
    } catch (caught) {
      setStatus(describeError(caught));
    } finally {
      setWorking(undefined);
      await refresh();
    }
  };

  const decide = (approval: DeskApproval, decision: Decision) =>
    act(approval.id, async () => {
      const result = await client.decide(approval.id, decision);
      const verb = decision === 'approve' ? 'Approved' : 'Declined';
      return result.caller_told
        ? `${verb} ${approval.id}. ${approval.customer_email} heard it on their call.`
        : `${verb} ${approval.id}. It shows on ${approval.customer_email}'s account.`;
    });

  return (
    <AppShell
      icon={ShieldCheck}
      label="Staff desk"
      skipLabel="Skip to staff desk"
      actions={
        desk && (
          <div className="flex min-w-0 items-center gap-2">
            <span className="hidden truncate text-xs text-stone-500 sm:inline">{desk.staff}</span>
            <a
              href="/cdn-cgi/access/logout"
              className="inline-flex min-h-9 items-center gap-2 rounded-lg px-2.5 text-xs font-medium text-stone-600 hover:bg-stone-100 hover:text-stone-900"
            >
              <LogOut className="size-3.5" aria-hidden="true" />
              Sign out
            </a>
          </div>
        )
      }
    >
      {!desk ? (
        <div className="flex min-h-[24rem] flex-col items-center justify-center gap-4 px-6 py-16 text-center">
          {error ? (
            <>
              <p className="max-w-md text-sm leading-6 text-stone-700">{error}</p>
              <Button onClick={() => location.reload()}>Reload</Button>
            </>
          ) : (
            <LoaderCircle
              className="size-5 text-stone-500 motion-safe:animate-spin"
              aria-label="Loading the staff desk"
            />
          )}
        </div>
      ) : (
        <>
          <div className="grid lg:grid-cols-2">
            <Section icon={HandCoins} title="Credit requests" empty="No credit requests yet.">
              {desk.approvals.map((approval) => (
                <ApprovalRow
                  key={approval.id}
                  approval={approval}
                  busy={working === approval.id}
                  disabled={working !== undefined}
                  onDecide={(decision) => void decide(approval, decision)}
                />
              ))}
            </Section>
            <Section
              icon={Ticket}
              title="Tickets"
              empty="No tickets yet."
              className="border-t border-stone-200 lg:border-t-0 lg:border-l"
            >
              {desk.tickets.map((ticket) => (
                <TicketRow key={ticket.id} ticket={ticket} />
              ))}
            </Section>
          </div>
          <HelpCenter
            status={desk.help_center}
            working={working}
            onPublish={() =>
              void act('publish', async () => {
                await client.publishHelpCenter();
                return 'Uploaded the help articles. Indexing takes about a minute.';
              })
            }
            onReset={() => {
              if (
                !confirm(
                  'Reset the demo data? This deletes every ticket and credit request and restores the demo orders.',
                )
              )
                return;
              void act('reset', async () => {
                await client.resetDemo();
                return 'Demo data restored.';
              });
            }}
          />
          <p
            role="status"
            aria-live="polite"
            className={clsx(
              'min-h-12 border-t border-stone-200 bg-stone-50 px-5 py-3 text-sm sm:px-6',
              error ? 'text-red-700' : 'text-stone-600',
            )}
          >
            {error ?? status ?? 'New requests and tickets appear here as customers call.'}
          </p>
        </>
      )}
    </AppShell>
  );
}

function Section({
  icon: Icon,
  title,
  empty,
  className,
  children,
}: {
  icon: LucideIcon;
  title: string;
  empty: string;
  className?: string;
  children: ReactNode[];
}) {
  return (
    <section className={clsx('min-w-0 px-5 py-5 sm:px-6', className)} aria-label={title}>
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="size-4 text-stone-500" aria-hidden="true" />
        {title}
      </h2>
      {children.length ? (
        <ul className="mt-4 divide-y divide-stone-200">{children}</ul>
      ) : (
        <p className="mt-4 text-sm text-stone-600">{empty}</p>
      )}
    </section>
  );
}

const decided = {
  approved: { label: 'Approved', className: 'text-emerald-700' },
  declined: { label: 'Declined', className: 'text-red-700' },
} as const;

function ApprovalRow({
  approval,
  busy,
  disabled,
  onDecide,
}: {
  approval: DeskApproval;
  busy: boolean;
  disabled: boolean;
  onDecide: (decision: Decision) => void;
}) {
  return (
    <li className="space-y-2 py-4 first:pt-0 last:pb-0">
      <p className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
        <span className="font-medium">
          {money(approval.amount_cents)} for {approval.customer_email}
        </span>
        <span className="text-xs text-stone-500">
          {approval.id} · Order {approval.order_id} · {when(approval.created_at)}
        </span>
      </p>
      <p className="text-sm leading-6 break-words text-stone-700">{approval.reason}</p>
      {approval.status === 'pending' ? (
        <div className="flex gap-2">
          <Button tone="primary" disabled={disabled} onClick={() => onDecide('approve')}>
            {busy ? (
              <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
            ) : (
              <Check className="size-3.5" aria-hidden="true" />
            )}
            Approve
          </Button>
          <Button disabled={disabled} onClick={() => onDecide('decline')}>
            <X className="size-3.5" aria-hidden="true" />
            Decline
          </Button>
        </div>
      ) : (
        <p className={clsx('text-xs', decided[approval.status].className)}>
          {decided[approval.status].label}
          {approval.decided_by && ` by ${approval.decided_by}`}
          {approval.decided_at && ` · ${when(approval.decided_at)}`}
        </p>
      )}
    </li>
  );
}

function TicketRow({ ticket }: { ticket: DeskTicket }) {
  const routed = ticket.team && ticket.team_confidence !== null && ticket.urgency !== null;
  return (
    <li className="space-y-2 py-4 first:pt-0 last:pb-0">
      <p className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
        <span className="min-w-0 font-medium break-words">{ticket.summary}</span>
        <span className="text-xs text-stone-500">
          {ticket.id} · {ticket.customer_email}
          {ticket.order_id && ` · Order ${ticket.order_id}`} · {when(ticket.created_at)}
        </span>
      </p>
      <p className="line-clamp-3 text-sm leading-6 break-words text-stone-700">{ticket.details}</p>
      {routed ? (
        <ul className="flex flex-wrap gap-1.5 text-xs" aria-label="Routing by Clef">
          <Chip strong={ticket.team === 'safety'}>
            {capitalize(ticket.team!)} team · {percent(ticket.team_confidence!)}
          </Chip>
          <Chip strong={ticket.urgency! >= 2.5}>Urgency: {urgency(ticket.urgency!)}</Chip>
          {ticket.wants_person !== null && ticket.wants_person >= WANTS_PERSON && (
            <Chip>Asked for a person</Chip>
          )}
        </ul>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-stone-500">
          <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
          Clef is routing this ticket...
        </p>
      )}
    </li>
  );
}

function Chip({ strong = false, children }: { strong?: boolean; children: ReactNode }) {
  return (
    <li
      className={clsx(
        'rounded-full px-2.5 py-1',
        strong ? 'bg-red-50 font-medium text-red-800' : 'bg-stone-100 text-stone-700',
      )}
    >
      {children}
    </li>
  );
}

function HelpCenter({
  status,
  working,
  onPublish,
  onReset,
}: {
  status: HelpCenterStatus;
  working?: string;
  onPublish: () => void;
  onReset: () => void;
}) {
  const summary = !status.published
    ? `${status.articles} articles, not published yet. The agent can't search them until you publish.`
    : status.indexing
      ? `${status.indexed} of ${status.articles} articles indexed, ${status.indexing} still indexing.`
      : `${status.indexed} of ${status.articles} articles indexed in AI Search.`;
  return (
    <section
      className="flex flex-wrap items-center gap-3 border-t border-stone-200 px-5 py-5 sm:px-6"
      aria-label="Help center and demo data"
    >
      <BookOpen className="size-4 shrink-0 text-stone-500" aria-hidden="true" />
      <p className="mr-auto min-w-0 text-sm text-stone-700">{summary}</p>
      <Button disabled={working !== undefined} onClick={onPublish}>
        {working === 'publish' ? (
          <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
        ) : (
          <Upload className="size-3.5" aria-hidden="true" />
        )}
        Publish help center
      </Button>
      <Button tone="quiet" size="sm" disabled={working !== undefined} onClick={onReset}>
        <RotateCcw className="size-3.5" aria-hidden="true" />
        Reset demo data
      </Button>
    </section>
  );
}
