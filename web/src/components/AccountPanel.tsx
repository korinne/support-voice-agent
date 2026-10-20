import clsx from 'clsx';
import { LoaderCircle, RefreshCw, UserRound } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { describeError } from '../lib/api';
import type { Account } from '../lib/contracts';
import { capitalize, money, percent, urgency, when } from '../lib/format';
import { Button } from './Button';

const orderStatuses = {
  processing: 'Processing',
  shipped: 'Shipped',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
  returned: 'Returned',
} satisfies Record<Account['orders'][number]['status'], string>;
const approvalStatuses = {
  pending: { label: 'Waiting for staff', className: 'text-amber-700' },
  approved: { label: 'Approved', className: 'text-emerald-700' },
  declined: { label: 'Declined', className: 'text-red-700' },
} satisfies Record<Account['approvals'][number]['status'], { label: string; className: string }>;

/**
 * What the agent can see and change for the signed-in customer. It reloads
 * whenever `version` changes, such as after a tool call or a staff decision.
 */
export function AccountPanel({
  load,
  enabled,
  version,
}: {
  load: (signal: AbortSignal) => Promise<Account>;
  enabled: boolean;
  version: number;
}) {
  const [account, setAccount] = useState<Account>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const pending = useRef<AbortController | undefined>(undefined);

  const refresh = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const result = await load(controller.signal);
      setAccount(result);
      setError(undefined);
    } catch (caught) {
      if (!controller.signal.aborted) setError(describeError(caught));
    } finally {
      if (pending.current === controller) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    if (!enabled) {
      setAccount(undefined);
      return;
    }
    void refresh();
    return () => pending.current?.abort();
  }, [enabled, version, refresh]);

  if (!enabled) {
    return (
      <Empty title="Not signed in">Sign in to see your orders, tickets, and store credit.</Empty>
    );
  }
  if (!account) {
    return error ? (
      <Empty title="Couldn't load the account">{error}</Empty>
    ) : (
      <div className="flex h-full items-center justify-center py-16 text-stone-500">
        <LoaderCircle className="size-5 motion-safe:animate-spin" aria-label="Loading account" />
      </div>
    );
  }
  return (
    <div className="space-y-6 px-5 py-5 sm:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {account.customer.name || account.customer.email}
          </p>
          {account.customer.name && (
            <p className="truncate text-xs text-stone-500">{account.customer.email}</p>
          )}
        </div>
        <Button
          tone="quiet"
          size="sm"
          onClick={() => void refresh()}
          disabled={loading}
          aria-label="Refresh account"
        >
          <RefreshCw
            className={clsx('size-3.5', loading && 'motion-safe:animate-spin')}
            aria-hidden="true"
          />
          Refresh
        </Button>
      </div>
      {error && <p className="text-xs text-red-700">{error}</p>}
      <dl className="flex items-baseline justify-between rounded-lg bg-stone-50 px-3 py-2.5 text-sm">
        <dt className="text-stone-600">Store credit</dt>
        <dd className="font-medium tabular-nums">{money(account.customer.credit_cents)}</dd>
      </dl>
      <Section title="Orders">
        {account.orders.map((order) => (
          <li key={order.id} className="space-y-1">
            <p className="flex justify-between gap-3 text-sm">
              <span className="font-medium">{order.id}</span>
              <span className="text-stone-600">{orderStatuses[order.status]}</span>
            </p>
            <p className="text-xs leading-5 text-stone-600">
              {order.items.map((item) => item.name).join(', ')} · {money(order.total_cents)}
            </p>
            <p className="text-xs leading-5 text-stone-500">
              Placed {when(order.placed_on)}.{' '}
              {order.shipping.replace(/\b\d{4}-\d{2}-\d{2}\b/g, (date) => when(date))}
            </p>
          </li>
        ))}
      </Section>
      <Section title="Credit requests" empty="No credit requests yet.">
        {account.approvals.map((approval) => {
          const status = approvalStatuses[approval.status];
          return (
            <li key={approval.id} className="space-y-1">
              <p className="flex justify-between gap-3 text-sm">
                <span className="font-medium">
                  {approval.id} · {money(approval.amount_cents)}
                </span>
                <span className={status.className}>{status.label}</span>
              </p>
              <p className="text-xs leading-5 text-stone-600">
                Order {approval.order_id}. {approval.reason}
              </p>
            </li>
          );
        })}
      </Section>
      <Section title="Tickets" empty="No tickets yet.">
        {account.tickets.map((ticket) => (
          <li key={ticket.id} className="space-y-1">
            <p className="flex justify-between gap-3 text-sm">
              <span className="min-w-0 truncate font-medium">
                {ticket.id} · {ticket.summary}
              </span>
              <span className="shrink-0 text-xs text-stone-500">{when(ticket.created_at)}</span>
            </p>
            <p className="text-xs leading-5 text-stone-600">
              {ticket.team && ticket.team_confidence !== null && ticket.urgency !== null
                ? `${capitalize(ticket.team)} team (${percent(ticket.team_confidence)} sure). ` +
                  `Urgency: ${urgency(ticket.urgency)}.`
                : 'Routing...'}
            </p>
          </li>
        ))}
      </Section>
    </div>
  );
}

function Section({
  title,
  empty,
  children,
}: {
  title: string;
  empty?: string;
  children: ReactNode[];
}) {
  return (
    <section>
      <h2 className="text-xs font-medium tracking-wide text-stone-500 uppercase">{title}</h2>
      {children.length ? (
        <ul className="mt-3 space-y-4">{children}</ul>
      ) : (
        <p className="mt-3 text-sm text-stone-600">{empty}</p>
      )}
    </section>
  );
}

function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-4 py-16 text-center">
      <UserRound className="mb-4 size-6 text-stone-300" strokeWidth={1.5} aria-hidden="true" />
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 max-w-64 text-sm leading-6 text-stone-600">{children}</p>
    </div>
  );
}
