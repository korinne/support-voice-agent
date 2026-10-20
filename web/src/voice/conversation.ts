import { WANTS_PERSON, capitalize, money, percent, urgency } from '../lib/format';
import type { ToolEvent, ToolSource, VoiceEvent } from '../lib/contracts';

export type ToolDefinition = {
  name: string;
  label: string;
  icon: 'account' | 'help' | 'cancel' | 'ticket' | 'credit' | 'tool';
};

const tools = new Map<string, ToolDefinition>([
  ['get_account', { name: 'get_account', label: 'Look up your account', icon: 'account' }],
  [
    'search_help_center',
    { name: 'search_help_center', label: 'Search the help center', icon: 'help' },
  ],
  ['cancel_order', { name: 'cancel_order', label: 'Cancel an order', icon: 'cancel' }],
  ['open_ticket', { name: 'open_ticket', label: 'Open a support ticket', icon: 'ticket' }],
  [
    'request_store_credit',
    { name: 'request_store_credit', label: 'Request store credit', icon: 'credit' },
  ],
]);

export function describeTool(name: string): ToolDefinition {
  return tools.get(name) ?? { name, label: 'Run a tool', icon: 'tool' };
}

export type ToolStatus = 'running' | 'complete' | 'empty' | 'failed' | 'cancelled';
export type AssistantStatus =
  'preparing' | 'speaking' | 'complete' | 'interrupted' | 'audio_failed';
export type ConversationEntry =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'assistant'; id: string; generation: number; text: string; status: AssistantStatus }
  | ({ kind: 'tool'; id: string; tool: ToolDefinition } & (
      | { status: 'complete'; summary?: string; sources?: ToolSource[] }
      | { status: 'empty'; summary?: string }
      | { status: Exclude<ToolStatus, 'complete' | 'empty'> }
    ))
  // Updates from outside the conversation: a staff decision or a ticket's routing.
  | { kind: 'notice'; id: string; icon: 'approved' | 'declined' | 'routed'; text: string };

const completion = {
  tool_finished: 'complete',
  tool_empty: 'empty',
  tool_failed: 'failed',
  tool_cancelled: 'cancelled',
} as const satisfies Record<Exclude<ToolEvent['type'], 'tool_started'>, ToolStatus>;

export function applyToolEvent(
  history: ConversationEntry[],
  event: ToolEvent,
): ConversationEntry[] {
  const id = `tool-${event.call_id}`;
  if (event.type === 'tool_started') {
    if (history.some((entry) => entry.kind === 'tool' && entry.id === id)) return history;
    const entry: ConversationEntry = {
      kind: 'tool',
      id,
      tool: describeTool(event.name),
      status: 'running',
    };
    return [...history, entry];
  }
  const current = history.find((entry) => entry.kind === 'tool' && entry.id === id);
  if (!current || current.kind !== 'tool' || current.status !== 'running') return history;
  let updated: ConversationEntry;
  if (event.type === 'tool_finished') {
    updated = { ...current, status: 'complete', summary: event.summary, sources: event.sources };
  } else if (event.type === 'tool_empty') {
    updated = { ...current, status: 'empty', summary: event.summary };
  } else {
    updated = { ...current, status: completion[event.type] };
  }
  return history.map((entry) => (entry === current ? updated : entry));
}

export function interruptResponses(
  history: ConversationEntry[],
  beforeGeneration = Infinity,
): ConversationEntry[] {
  return history.map((entry) =>
    entry.kind === 'assistant' &&
    entry.generation < beforeGeneration &&
    (entry.status === 'preparing' || entry.status === 'speaking')
      ? { ...entry, status: 'interrupted' }
      : entry,
  );
}

export function applyConversationEvent(
  history: ConversationEntry[],
  event: VoiceEvent,
  minimumGeneration = 0,
): ConversationEntry[] {
  if ('generation' in event && event.generation < minimumGeneration) return history;
  switch (event.type) {
    case 'tool_started':
    case 'tool_finished':
    case 'tool_empty':
    case 'tool_failed':
    case 'tool_cancelled':
      return applyToolEvent(history, event);
    case 'user_transcript': {
      if (!event.text.trim()) return history;
      const entry: ConversationEntry = {
        kind: 'user',
        id: String(event.sequence),
        text: event.text,
      };
      return [...history, entry];
    }
    case 'assistant_response': {
      const id = `assistant-${event.generation}`;
      if (!event.text.trim() || history.some((entry) => entry.id === id)) return history;
      const entry: ConversationEntry = {
        kind: 'assistant',
        id,
        generation: event.generation,
        text: event.text,
        status: 'preparing',
      };
      return [...history, entry];
    }
    case 'audio_started':
    case 'assistant_response_finished':
    case 'speech_failed':
      return history.map((entry) =>
        entry.kind === 'assistant' &&
        entry.generation === event.generation &&
        (entry.status === 'preparing' || entry.status === 'speaking')
          ? { ...entry, status: speechStatuses[event.type] }
          : entry,
      );
    case 'approval_decided': {
      const id = `approval-${event.request_id}`;
      if (history.some((entry) => entry.id === id)) return history;
      const entry: ConversationEntry = {
        kind: 'notice',
        id,
        icon: event.status,
        text: `Acme support ${event.status} ${money(event.amount_cents)} credit on order ${event.order_id}.`,
      };
      return [...history, entry];
    }
    case 'ticket_triaged': {
      const id = `triage-${event.ticket_id}`;
      if (history.some((entry) => entry.id === id)) return history;
      const person = event.wants_person >= WANTS_PERSON ? ' The customer asked for a person.' : '';
      const entry: ConversationEntry = {
        kind: 'notice',
        id,
        icon: 'routed',
        text:
          `Clef routed ticket ${event.ticket_id} to ${capitalize(event.team)} ` +
          `(${percent(event.team_confidence)} sure). Urgency: ${urgency(event.urgency)}.${person}`,
      };
      return [...history, entry];
    }
    case 'clear':
      return interruptResponses(history, event.generation);
    case 'closing':
    case 'error':
    case 'pipeline_error':
    case 'output_unavailable':
      return interruptResponses(history);
    default:
      return history;
  }
}

const speechStatuses = {
  audio_started: 'speaking',
  assistant_response_finished: 'complete',
  speech_failed: 'audio_failed',
} as const satisfies Record<string, AssistantStatus>;
