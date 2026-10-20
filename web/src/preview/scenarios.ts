import type { Account, SessionInfo } from '../lib/contracts';
import type { SessionState } from '../lib/session';
import { describeTool, type ConversationEntry } from '../voice/conversation';
import { describeCallEnd, idleCall, type CallView } from '../voice/model';

export const scenarios = [
  { id: 'idle', label: 'Ready' },
  { id: 'sign-in', label: 'Sign in' },
  { id: 'sign-in-error', label: 'Sign-in cancelled' },
  { id: 'signing-in', label: 'Opening Cloudflare' },
  { id: 'setup', label: 'Setup required' },
  { id: 'connecting', label: 'Connecting' },
  { id: 'listening', label: 'Listening' },
  { id: 'preparing-audio', label: 'Answer ready, preparing audio' },
  { id: 'speaking', label: 'Speaking' },
  { id: 'tool-running', label: 'Looking up the account' },
  { id: 'help-sources', label: 'Help center sources' },
  { id: 'help-empty', label: 'No help center matches' },
  { id: 'credit-pending', label: 'Credit sent for review' },
  { id: 'credit-approved', label: 'Staff approved credit' },
  { id: 'ticket-routed', label: 'Ticket routed by Clef' },
  { id: 'tool-failed', label: 'Action not allowed' },
  { id: 'interrupted', label: 'Interrupted reply' },
  { id: 'audio-failed', label: 'Speech failed, call continues' },
  { id: 'playback-blocked', label: 'Audio permission needed' },
  { id: 'error', label: 'Connection error' },
  { id: 'call-error', label: 'Call failed with a transcript' },
  { id: 'cleanup', label: 'Cleanup pending' },
  { id: 'ended', label: 'Call ended' },
] as const;
export type Scenario = (typeof scenarios)[number]['id'];
export type Preview = { scenario: Scenario; view: CallView; session: SessionState };

export function scenarioFrom(value: string | null): Scenario {
  return scenarios.find((item) => item.id === value)?.id ?? 'idle';
}

export const previewSession: SessionInfo = {
  authenticated: true,
  configured: true,
  setup_required: [],
  customer: { id: 'cus_maya', name: 'Maya', email: 'maya@example.com' },
  assistant: 'Matt',
  max_call_seconds: 15 * 60,
};

const mayaAccount: Account = {
  customer: { id: 'cus_maya', name: 'Maya', email: 'maya@example.com', credit_cents: 0 },
  orders: [
    {
      id: 'A-1042',
      placed_on: '2026-10-09',
      status: 'delivered',
      items: [{ name: 'Self-Folding Umbrella', price_cents: 3400 }],
      total_cents: 3400,
      shipping: 'Delivered 2026-10-13',
    },
    {
      id: 'A-1019',
      placed_on: '2026-09-04',
      status: 'delivered',
      items: [{ name: 'Pocket Trampoline', price_cents: 5900 }],
      total_cents: 5900,
      shipping: 'Delivered 2026-09-09',
    },
  ],
  tickets: [],
  approvals: [
    {
      id: 'R-4417',
      order_id: 'A-1042',
      amount_cents: 1000,
      reason: 'The umbrella arrived with a bent rib.',
      status: 'pending',
      decided_at: null,
      created_at: '2026-10-15T18:04:00+00:00',
    },
  ],
};

const rosaAccount: Account = {
  customer: { id: 'cus_rosa', name: 'Rosa', email: 'rosa@example.com', credit_cents: 500 },
  orders: [
    {
      id: 'A-1061',
      placed_on: '2026-10-15',
      status: 'processing',
      items: [{ name: 'Instant Tunnel Kit', price_cents: 4500 }],
      total_cents: 4500,
      shipping: 'Not shipped yet',
    },
    {
      id: 'A-1033',
      placed_on: '2026-09-25',
      status: 'delivered',
      items: [{ name: 'Jet-Powered Pogo Stick, model PS-2', price_cents: 12900 }],
      total_cents: 12900,
      shipping: 'Delivered 2026-09-30',
    },
  ],
  tickets: [
    {
      id: 'T-4821',
      order_id: 'A-1033',
      summary: 'Pogo stick smells like fuel',
      created_at: '2026-10-15T18:10:00+00:00',
      team: 'safety',
      team_confidence: 0.931,
      urgency: 2.88,
      wants_person: 0.82,
      triaged_at: '2026-10-15T18:10:03+00:00',
    },
  ],
  approvals: [],
};

/** The signed-in customer whose problem the scenario's conversation is about. */
export function previewInfoFor(scenario: Scenario): SessionInfo {
  return scenario === 'ticket-routed'
    ? { ...previewSession, customer: { id: 'cus_rosa', name: 'Rosa', email: 'rosa@example.com' } }
    : previewSession;
}

/** Account records as they stand at the point the scenario shows. */
export function previewAccountFor(scenario: Scenario): Account {
  if (scenario === 'ticket-routed') return rosaAccount;
  if (scenario !== 'credit-approved') return mayaAccount;
  return {
    ...mayaAccount,
    customer: { ...mayaAccount.customer, credit_cents: 1000 },
    approvals: mayaAccount.approvals.map((approval) => ({
      ...approval,
      status: 'approved',
      decided_at: '2026-10-15T18:06:00+00:00',
    })),
  };
}

const question = {
  kind: 'user',
  id: '1',
  text: 'Hi, my umbrella showed up with a bent rib. Can I get store credit?',
} satisfies ConversationEntry;
const lookup = (status: 'running' | 'complete'): ConversationEntry => ({
  kind: 'tool',
  id: 'tool-account',
  tool: describeTool('get_account'),
  ...(status === 'complete' ? { status, summary: '2 orders, $0.00 store credit' } : { status }),
});
const answer = {
  kind: 'assistant',
  id: '3',
  generation: 1,
  text: "I'm sorry about that. I see your Self-Folding Umbrella on order A-1042. How much credit would you like?",
  status: 'complete',
} satisfies ConversationEntry;

export function previewFor(scenario: Scenario): Preview {
  const view = idleCall();
  const preview: Preview = { scenario, view, session: { kind: 'ready' } };
  switch (scenario) {
    case 'idle':
      return preview;
    case 'sign-in':
      return { ...preview, session: { kind: 'locked' } };
    case 'sign-in-error':
      return {
        ...preview,
        session: {
          kind: 'locked',
          error: 'Sign-in was cancelled. Sign in with Cloudflare to call Acme support.',
        },
      };
    case 'signing-in':
      return { ...preview, session: { kind: 'signing-in' } };
    case 'setup':
      return {
        ...preview,
        session: {
          kind: 'unavailable',
          message: "This app isn't set up yet. It needs the D1 tables. See docs/deployment.md.",
        },
      };
    case 'connecting':
      return {
        ...preview,
        view: {
          ...view,
          phase: 'connecting',
          status: 'Prepare voice agent',
          microphoneActive: true,
        },
      };
    case 'error':
      return {
        ...preview,
        view: {
          ...view,
          phase: 'ended',
          status: 'Call ended. Your microphone is off.',
          error: 'The audio connection could not be established. Please try again.',
        },
      };
    case 'cleanup':
    case 'ended':
    case 'call-error':
      return {
        ...preview,
        view: {
          ...view,
          phase: 'ended',
          startedAt: Date.now() - 42_000,
          ...describeCallEnd(scenario === 'call-error' ? 'output_disconnected' : 'user_left'),
          history:
            scenario === 'call-error'
              ? [question, lookup('complete'), { ...answer, status: 'interrupted' }]
              : [question, lookup('complete'), answer],
          cleanupPending: scenario === 'cleanup',
        },
      };
  }

  Object.assign(view, {
    phase: 'connected',
    microphoneActive: true,
    connectedAt: Date.now() - 24_000,
    status: 'Connected.',
    history: [question, lookup('complete'), answer],
  });
  switch (scenario) {
    case 'listening':
      view.userLevel = 0.5;
      break;
    case 'preparing-audio':
      view.activity = 'thinking';
      view.history = [question, lookup('complete'), { ...answer, status: 'preparing' }];
      break;
    case 'audio-failed':
      view.history = [question, lookup('complete'), { ...answer, status: 'audio_failed' }];
      break;
    case 'speaking':
      view.activity = 'speaking';
      view.agentLevel = 0.65;
      view.history = [question, lookup('complete'), { ...answer, status: 'speaking' }];
      break;
    case 'tool-running':
      view.activity = 'tool';
      view.history = [question, lookup('running')];
      break;
    case 'help-sources':
    case 'help-empty':
      view.history = [
        { kind: 'user', id: '1', text: 'How long do I have to return something?' },
        {
          kind: 'tool',
          id: 'tool-help',
          tool: describeTool('search_help_center'),
          ...(scenario === 'help-sources'
            ? {
                status: 'complete' as const,
                summary: 'Returns and refunds',
                sources: [{ title: 'Returns and refunds' }],
              }
            : { status: 'empty' as const, summary: 'No matching articles' }),
        },
        {
          ...answer,
          text:
            scenario === 'help-sources'
              ? 'You have 30 days from delivery. We take six dollars off the refund for the return label.'
              : "I couldn't find that in our help center. I can open a ticket so someone follows up.",
        },
      ];
      break;
    case 'credit-pending':
    case 'credit-approved':
      view.history = [
        question,
        lookup('complete'),
        answer,
        { kind: 'user', id: '4', text: 'Ten dollars would be great.' },
        {
          kind: 'tool',
          id: 'tool-credit',
          tool: describeTool('request_store_credit'),
          status: 'complete',
          summary: 'Sent $10.00 credit for staff review',
        },
        {
          kind: 'assistant',
          id: '6',
          generation: 2,
          text: "I've sent a ten dollar credit request to our team. If you stay on the line, I'll tell you when they decide.",
          status: 'complete',
        },
      ];
      if (scenario === 'credit-approved')
        view.history.push(
          {
            kind: 'notice',
            id: 'approval-R-4417',
            icon: 'approved',
            text: 'Acme support approved $10.00 credit on order A-1042.',
          },
          {
            kind: 'assistant',
            id: '8',
            generation: 3,
            text: 'Good news, our team approved your ten dollar credit. It is on your account now.',
            status: 'complete',
          },
        );
      break;
    case 'ticket-routed':
      view.history = [
        { kind: 'user', id: '1', text: 'My pogo stick smells like fuel.' },
        {
          kind: 'assistant',
          id: '2',
          generation: 1,
          text: 'Please stop using it and move it away from anything that can burn. I am opening a ticket for our safety team now.',
          status: 'complete',
        },
        {
          kind: 'tool',
          id: 'tool-ticket',
          tool: describeTool('open_ticket'),
          status: 'complete',
          summary: 'Opened ticket T-4821',
        },
        {
          kind: 'notice',
          id: 'triage-T-4821',
          icon: 'routed',
          text: 'Clef routed ticket T-4821 to Safety (93% sure). Urgency: Right away (2.9 of 3). The customer asked for a person.',
        },
      ];
      break;
    case 'tool-failed':
      view.history = [
        { kind: 'user', id: '1', text: 'Please cancel my umbrella order.' },
        { kind: 'tool', id: 'tool-cancel', tool: describeTool('cancel_order'), status: 'failed' },
        {
          ...answer,
          text: 'That order was already delivered, so I cannot cancel it. I can help you return it instead.',
        },
      ];
      break;
    case 'interrupted':
      view.history = [
        question,
        lookup('complete'),
        {
          ...answer,
          text: "I'm sorry about that. I see your Self-Folding...",
          status: 'interrupted',
        },
        { kind: 'user', id: '4', text: 'Actually, it was the trampoline order.' },
      ];
      break;
    case 'playback-blocked':
      view.playbackBlocked = true;
      break;
  }
  return preview;
}
