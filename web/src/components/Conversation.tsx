import clsx from 'clsx';
import {
  ArrowUpRight,
  BadgeCheck,
  BadgeX,
  BookOpen,
  Check,
  HandCoins,
  LoaderCircle,
  MessageSquare,
  PackageX,
  Route,
  SearchX,
  Ticket,
  UserRound,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react';
import { memo, useLayoutEffect, useRef } from 'react';
import type {
  AssistantStatus,
  ConversationEntry,
  ToolDefinition,
  ToolStatus,
} from '../voice/conversation';

const assistantStatuses = {
  preparing: 'Preparing audio...',
  speaking: 'Speaking...',
  complete: '',
  interrupted: 'Interrupted (not fully spoken).',
  audio_failed: 'Audio unavailable. You can read the reply.',
} satisfies Record<AssistantStatus, string>;

const toolIcons = {
  account: UserRound,
  help: BookOpen,
  cancel: PackageX,
  ticket: Ticket,
  credit: HandCoins,
  tool: Wrench,
} satisfies Record<ToolDefinition['icon'], LucideIcon>;
const noticeIcons = {
  approved: { icon: BadgeCheck, className: 'text-emerald-700' },
  declined: { icon: BadgeX, className: 'text-red-700' },
  routed: { icon: Route, className: 'text-stone-500' },
} satisfies Record<
  Extract<ConversationEntry, { kind: 'notice' }>['icon'],
  { icon: LucideIcon; className: string }
>;
const toolStatuses = {
  running: {
    label: 'Running',
    icon: LoaderCircle,
    className: 'text-stone-500',
    iconClassName: 'motion-safe:animate-spin',
  },
  complete: { label: 'Done', icon: Check, className: 'text-emerald-700', iconClassName: '' },
  empty: { label: 'No results', icon: SearchX, className: 'text-stone-500', iconClassName: '' },
  failed: { label: 'Failed', icon: X, className: 'text-red-700', iconClassName: '' },
  cancelled: { label: 'Cancelled', icon: X, className: 'text-stone-500', iconClassName: '' },
} satisfies Record<
  ToolStatus,
  { label: string; icon: LucideIcon; className: string; iconClassName: string }
>;

function Entry({ item, name }: { item: ConversationEntry; name: string }) {
  switch (item.kind) {
    case 'tool': {
      const Icon = toolIcons[item.tool.icon];
      const presentation = toolStatuses[item.status];
      const StatusIcon = presentation.icon;
      return (
        <div className="space-y-2 text-sm text-stone-600">
          <div className="flex items-center gap-2">
            <Icon className="size-4 shrink-0 text-stone-500" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{item.tool.label}</span>
            <span
              className={clsx(
                'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium',
                presentation.className,
              )}
            >
              <StatusIcon
                className={clsx('size-3.5', presentation.iconClassName)}
                aria-hidden="true"
              />
              {presentation.label}
            </span>
          </div>
          {item.status === 'complete' && item.sources?.length ? (
            <ul className="space-y-1.5 pl-6" aria-label="Sources">
              {item.sources.map((source) => (
                <li key={source.url ?? source.title} className="text-xs leading-5">
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-start gap-1 underline decoration-stone-300 underline-offset-2 hover:text-stone-900"
                    >
                      <span className="break-words">{source.title}</span>
                      <ArrowUpRight className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                    </a>
                  ) : (
                    <span className="break-words">{source.title}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (item.status === 'complete' || item.status === 'empty') && item.summary ? (
            <p className="pl-6 text-xs leading-5 break-words">{item.summary}</p>
          ) : null}
        </div>
      );
    }
    case 'notice': {
      const { icon: Icon, className } = noticeIcons[item.icon];
      return (
        <p className="flex items-start gap-2 rounded-lg bg-stone-50 px-3 py-2 text-sm leading-6 text-stone-700">
          <Icon className={clsx('mt-1 size-4 shrink-0', className)} aria-hidden="true" />
          <span className="min-w-0 break-words">{item.text}</span>
        </p>
      );
    }
    case 'user':
      return (
        <div>
          <p className="text-xs font-medium text-stone-500">You</p>
          <p className="mt-1 text-sm leading-6 break-words whitespace-pre-wrap">{item.text}</p>
        </div>
      );
    case 'assistant':
      return (
        <div>
          <p className="text-xs font-medium text-orange-700">{name}</p>
          {item.text && (
            <p className="mt-1 text-sm leading-6 break-words whitespace-pre-wrap">{item.text}</p>
          )}
          {assistantStatuses[item.status] && (
            <p className="mt-1 text-xs text-stone-500">{assistantStatuses[item.status]}</p>
          )}
        </div>
      );
  }
}

export const Conversation = memo(function Conversation({
  history,
  name,
}: {
  history: ConversationEntry[];
  name: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const agent = name.trim() || 'the assistant';
  useLayoutEffect(() => {
    if (container.current && atBottom.current)
      container.current.scrollTop = container.current.scrollHeight;
  }, [history]);
  return (
    <div
      ref={container}
      className="max-h-[60dvh] overflow-y-auto overscroll-contain px-5 py-5 sm:px-6 lg:h-full lg:max-h-none"
      role="log"
      aria-label="Conversation transcript"
      aria-live="polite"
      aria-relevant="additions text"
      onScroll={(event) => {
        const el = event.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
    >
      {history.length ? (
        <ol className="space-y-5">
          {history.map((item) => (
            <li key={item.id}>
              <Entry item={item} name={agent} />
            </li>
          ))}
        </ol>
      ) : (
        <div className="flex h-full flex-col items-center justify-center px-4 py-16 text-center">
          <MessageSquare
            className="mb-4 size-6 text-stone-300"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          <p className="text-sm font-medium">Nothing here yet</p>
          <p className="mt-1 max-w-60 text-sm leading-6 text-stone-600">
            Your words and {agent}'s replies show up here as you talk.
          </p>
        </div>
      )}
    </div>
  );
});
