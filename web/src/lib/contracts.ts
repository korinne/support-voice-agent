import { z } from 'zod';

/** What the browser sends to start a call. The server owns everything else. */
export const callSettingsSchema = z.object({
  utc_offset_minutes: z.number().int().min(-840).max(840),
});
export type CallSettings = z.infer<typeof callSettingsSchema>;
export function currentCallSettings(): CallSettings {
  return { utc_offset_minutes: -new Date().getTimezoneOffset() };
}

const customerId = z.string().regex(/^cus_[a-z0-9_]{1,40}$/);

export const sessionSchema = z.object({
  authenticated: z.boolean(),
  configured: z.boolean(),
  setup_required: z.array(z.string()).default([]),
  // The name is empty when the identity provider has none for the user.
  customer: z
    .object({ id: customerId, name: z.string().max(80), email: z.string().max(254) })
    .nullable()
    .default(null),
  assistant: z.string().min(1).max(40).default('Matt'),
  max_call_seconds: z.number().int().positive().optional(),
});
export type SessionInfo = z.infer<typeof sessionSchema>;

export const timingSchema = z.object({
  operation: z.string(),
  label: z.string(),
  status: z.enum(['ok', 'failed']),
  elapsed_ms: z.number().nonnegative(),
});
export type Timing = z.infer<typeof timingSchema> & {
  source: 'browser' | 'server';
  cumulative_ms?: number;
};

export const callSchema = z.object({
  call_id: z.string().nullable(),
  phase: z.enum(['created', 'published', 'preparing', 'active', 'ending', 'ended']),
  active: z.boolean(),
  ready: z.boolean(),
  generation: z.number().int(),
  expires_at_ms: z.number(),
  finish_reason: z.string().nullable(),
  cleanup_complete: z.boolean(),
  timings: z.array(timingSchema),
});

export const toolSourceSchema = z.object({
  title: z.string().min(1).max(160),
  // Help-center articles have no public page; tools that cite the web add one.
  url: z
    .string()
    .max(2048)
    .url()
    .refine((value) => {
      if (!URL.canParse(value)) return false;
      const url = new URL(value);
      return url.protocol === 'https:' && !url.port && !url.username && !url.password;
    })
    .optional(),
});
export type ToolSource = z.infer<typeof toolSourceSchema>;

export const toolEventSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('tool_started'),
    call_id: z.string().min(1),
    name: z.string().min(1),
  }),
  z.object({
    type: z.literal('tool_finished'),
    call_id: z.string().min(1),
    summary: z.string().max(160).optional(),
    sources: z.array(toolSourceSchema).max(6).optional(),
  }),
  z.object({
    type: z.literal('tool_empty'),
    call_id: z.string().min(1),
    summary: z.string().max(160).optional(),
  }),
  z.object({ type: z.literal('tool_failed'), call_id: z.string().min(1) }),
  z.object({ type: z.literal('tool_cancelled'), call_id: z.string().min(1) }),
]);
export type ToolEvent = z.infer<typeof toolEventSchema>;

const generation = z.number().int().nonnegative();
// Flux allows 8192 Unicode code points, each occupying up to two UTF-16 units.
const text = z.string().max(16_384);
export const eventSchema = z.intersection(
  z.object({
    sequence: z.number().int().positive(),
    at_ms: z.number(),
    server_time_ms: z.number(),
  }),
  z.discriminatedUnion('type', [
    ...toolEventSchema.options,
    z.object({ type: z.literal('user_transcript'), text }),
    z.object({ type: z.literal('user_turn_started'), generation }),
    z.object({ type: z.literal('user_turn_stopped'), text }),
    z.object({ type: z.literal('assistant_turn_started'), generation }),
    z.object({ type: z.literal('assistant_response'), text: text.min(1), generation }),
    z.object({ type: z.literal('assistant_response_finished'), generation }),
    z.object({ type: z.literal('filler_finished'), generation }),
    z.object({ type: z.literal('speech_failed'), generation }),
    z.object({
      type: z.literal('assistant_turn_stopped'),
      text,
      interrupted: z.boolean(),
      generation,
    }),
    z.object({ type: z.literal('clear'), generation }),
    z.object({ type: z.literal('audio_started'), generation }),
    z.object({ type: z.literal('output_unavailable'), generation }),
    z.object({ type: z.literal('ready'), ready: z.boolean(), generation }),
    z.object({ type: z.literal('closing'), reason: z.string() }),
    z.object({ type: z.literal('error') }),
    z.object({ type: z.literal('pipeline_error') }),
    z.object({ type: z.literal('pipeline_started') }),
    z.object({ type: z.literal('pipeline_finished') }),
    z.object({ type: z.literal('context_updated') }),
    z.object({
      type: z.literal('approval_decided'),
      request_id: z.string(),
      order_id: z.string(),
      amount_cents: z.number().int().positive(),
      status: z.enum(['approved', 'declined']),
    }),
    z.object({
      type: z.literal('ticket_triaged'),
      ticket_id: z.string(),
      team: z.string(),
      team_confidence: z.number(),
      urgency: z.number(),
      wants_person: z.number(),
    }),
  ]),
);
export type VoiceEvent = z.infer<typeof eventSchema>;
export const eventsSchema = callSchema.extend({
  last_sequence: z.number().int().nonnegative(),
  earliest_sequence: z.number().int().nonnegative(),
  server_time_ms: z.number(),
  events: z.array(eventSchema).max(128),
});
export type EventBatch = z.infer<typeof eventsSchema>;

export const negotiationSchema = z.object({
  sessionDescription: z
    .object({ type: z.enum(['offer', 'answer']), sdp: z.string().min(1) })
    .optional(),
  requiresImmediateRenegotiation: z.boolean().optional(),
  timings: z.array(timingSchema),
});
export const warmedSchema = z.object({ warm: z.literal(true) });
export const attachedSchema = z.object({
  attached: z.literal(true),
  timings: z.array(timingSchema),
});

const orderStatus = z.enum(['processing', 'shipped', 'delivered', 'cancelled', 'returned']);
const approvalStatus = z.enum(['pending', 'approved', 'declined']);
const triage = {
  team: z.string().nullable(),
  team_confidence: z.number().nullable(),
  urgency: z.number().nullable(),
  wants_person: z.number().nullable(),
  triaged_at: z.string().nullable(),
};

export const accountSchema = z.object({
  customer: z.object({
    id: customerId,
    name: z.string(),
    email: z.string(),
    credit_cents: z.number().int(),
  }),
  orders: z.array(
    z.object({
      id: z.string(),
      placed_on: z.string(),
      status: orderStatus,
      items: z.array(z.object({ name: z.string(), price_cents: z.number().int() })),
      total_cents: z.number().int(),
      shipping: z.string(),
    }),
  ),
  tickets: z.array(
    z.object({
      id: z.string(),
      order_id: z.string().nullable(),
      summary: z.string(),
      created_at: z.string(),
      ...triage,
    }),
  ),
  approvals: z.array(
    z.object({
      id: z.string(),
      order_id: z.string(),
      amount_cents: z.number().int(),
      reason: z.string(),
      status: approvalStatus,
      decided_at: z.string().nullable(),
      created_at: z.string(),
    }),
  ),
});
export type Account = z.infer<typeof accountSchema>;

export const helpCenterSchema = z.object({
  articles: z.number().int(),
  indexed: z.number().int(),
  indexing: z.number().int(),
  published: z.boolean(),
});
export type HelpCenterStatus = z.infer<typeof helpCenterSchema>;

export const deskApprovalSchema = z.object({
  id: z.string(),
  customer_name: z.string(),
  customer_email: z.string(),
  order_id: z.string(),
  amount_cents: z.number().int(),
  reason: z.string(),
  status: approvalStatus,
  decided_by: z.string().nullable(),
  decided_at: z.string().nullable(),
  created_at: z.string(),
});
export type DeskApproval = z.infer<typeof deskApprovalSchema>;

export const deskSchema = z.object({
  staff: z.string(),
  approvals: z.array(deskApprovalSchema),
  tickets: z.array(
    z.object({
      id: z.string(),
      customer_name: z.string(),
      customer_email: z.string(),
      order_id: z.string().nullable(),
      summary: z.string(),
      details: z.string(),
      created_at: z.string(),
      ...triage,
    }),
  ),
  help_center: helpCenterSchema,
});
export type Desk = z.infer<typeof deskSchema>;
export type DeskTicket = Desk['tickets'][number];

export const decisionSchema = z.object({
  approval: z.object({ id: z.string(), status: approvalStatus }),
  // True when the customer was still on the call and heard the decision there.
  caller_told: z.boolean(),
});
