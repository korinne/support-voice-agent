import { describe, expect, it } from 'vitest';
import { eventSchema, toolEventSchema } from '../../web/src/lib/contracts';
import {
  applyConversationEvent,
  applyToolEvent,
  describeTool,
  interruptResponses,
  type ConversationEntry,
} from '../../web/src/voice/conversation';

describe('tool conversation model', () => {
  it('keeps a tool summary and help articles that have no link', () => {
    const pending = applyToolEvent([], {
      type: 'tool_started',
      call_id: 'help',
      name: 'search_help_center',
    });
    const finished = eventSchema.parse({
      sequence: 2,
      at_ms: 100,
      server_time_ms: 100,
      type: 'tool_finished',
      call_id: 'help',
      code: null,
      summary: 'Returns and refunds',
      sources: [{ title: 'Returns and refunds' }],
    });
    expect(applyConversationEvent(pending, finished)[0]).toMatchObject({
      status: 'complete',
      summary: 'Returns and refunds',
      sources: [{ title: 'Returns and refunds' }],
    });
  });

  it('resolves tool presentation from the registry with an unknown-tool fallback', () => {
    expect(describeTool('get_account')).toMatchObject({
      icon: 'account',
      label: 'Look up your account',
    });
    expect(describeTool('request_store_credit')).toMatchObject({ icon: 'credit' });
    expect(describeTool('search_help_center')).toMatchObject({
      icon: 'help',
      label: 'Search the help center',
    });
    expect(describeTool('new_tool')).toMatchObject({
      name: 'new_tool',
      icon: 'tool',
      label: 'Run a tool',
    });
    expect(describeTool('toString').icon).toBe('tool');
  });
  it('updates only the matching tool and leaves text messages untouched', () => {
    const message: ConversationEntry = { kind: 'user', id: '1', text: 'Where is my order?' };
    let history = applyToolEvent([message], {
      type: 'tool_started',
      call_id: 'account',
      name: 'get_account',
    });
    history = applyToolEvent(history, { type: 'tool_finished', call_id: 'account' });
    expect(history[0]).toBe(message);
    expect(history[1]).toMatchObject({
      kind: 'tool',
      status: 'complete',
      tool: { name: 'get_account' },
    });
    expect(history[1]).not.toHaveProperty('text');
  });
  it('does not resurrect a cancelled call with duplicate or delayed events', () => {
    const started = {
      type: 'tool_started',
      call_id: 'account',
      name: 'get_account',
    } as const;
    let history = applyToolEvent([], started);
    history = applyToolEvent(history, { type: 'tool_cancelled', call_id: 'account' });
    const cancelled = history;
    history = applyToolEvent(history, started);
    history = applyToolEvent(history, { type: 'tool_finished', call_id: 'account' });
    expect(history).toHaveLength(1);
    expect(history).toBe(cancelled);
    expect(history[0]).toMatchObject({ status: 'cancelled' });
  });
  it('requires the fields appropriate to each event variant', () => {
    expect(toolEventSchema.safeParse({ type: 'tool_started', call_id: '1' }).success).toBe(false);
    expect(toolEventSchema.safeParse({ type: 'tool_finished', call_id: '1' }).success).toBe(true);
    expect(toolEventSchema.safeParse({ type: 'tool_failed' }).success).toBe(false);
  });

  it('keeps an empty search distinct from a failed lookup', () => {
    const pending = applyToolEvent([], {
      type: 'tool_started',
      call_id: 'help',
      name: 'search_help_center',
    });
    const empty = eventSchema.parse({
      sequence: 2,
      at_ms: 100,
      server_time_ms: 100,
      type: 'tool_empty',
      call_id: 'help',
    });
    expect(applyConversationEvent(pending, empty)[0]).toMatchObject({
      kind: 'tool',
      status: 'empty',
    });
  });

  it('attaches source links to the completed lookup and preserves cancellation', () => {
    const started = {
      type: 'tool_started',
      call_id: 'help',
      name: 'search_help_center',
    } as const;
    const sources = [
      { title: 'Realtime SFU', url: 'https://developers.cloudflare.com/realtime/sfu/' },
    ];
    const finished = eventSchema.parse({
      sequence: 2,
      at_ms: 100,
      server_time_ms: 100,
      type: 'tool_finished',
      call_id: 'help',
      sources,
    });
    const pending = applyToolEvent([], started);
    const complete = applyConversationEvent(pending, finished);
    expect(complete[0]).toMatchObject({ kind: 'tool', status: 'complete', sources });
    const cancelled = applyToolEvent(pending, { type: 'tool_cancelled', call_id: 'help' });
    expect(applyConversationEvent(cancelled, finished)).toBe(cancelled);
    expect(cancelled[0]).not.toHaveProperty('sources');
  });

  it.each([
    'https://www.cloudflarestatus.com/incidents/example',
    'https://open-meteo.com/',
    'https://www.nasa.gov/',
  ])('accepts an HTTPS tool source %s', (url) => {
    expect(
      toolEventSchema.safeParse({
        type: 'tool_finished',
        call_id: 'lookup',
        sources: [{ title: 'Source', url }],
      }).success,
    ).toBe(true);
  });

  it.each([
    'not a URL',
    'javascript:alert(1)',
    'http://example.com/',
    'data:text/html,<script>alert(1)</script>',
    'https://developers.cloudflare.com@evil.example/',
    'https://user@developers.cloudflare.com/',
    'https://developers.cloudflare.com:8443/',
    'https://user@www.cloudflarestatus.com/',
  ])('rejects unsafe source URL %s', (url) => {
    expect(
      toolEventSchema.safeParse({
        type: 'tool_finished',
        call_id: 'help',
        sources: [{ title: 'Source', url }],
      }).success,
    ).toBe(false);
  });
});

describe('conversation events', () => {
  const metadata = { sequence: 1, at_ms: 100, server_time_ms: 100, generation: 1 };
  const prepared = eventSchema.parse({
    ...metadata,
    type: 'assistant_response',
    text: 'The complete prepared answer.',
  });

  it('keeps a long transcript in the conversation', () => {
    const text = '文'.repeat(5000);
    const event = eventSchema.parse({ ...metadata, type: 'user_transcript', text });
    expect(applyConversationEvent([], event)).toEqual([{ kind: 'user', id: '1', text }]);
  });

  it('shows a prepared answer immediately and updates the same bubble through playback', () => {
    let history = applyConversationEvent([], prepared);
    expect(history).toEqual([
      {
        kind: 'assistant',
        id: 'assistant-1',
        generation: 1,
        text: 'The complete prepared answer.',
        status: 'preparing',
      },
    ]);
    history = applyConversationEvent(
      history,
      eventSchema.parse({ ...metadata, type: 'audio_started' }),
    );
    expect(history[0]).toMatchObject({ id: 'assistant-1', status: 'speaking' });
    history = applyConversationEvent(
      history,
      eventSchema.parse({ ...metadata, type: 'assistant_response_finished' }),
    );
    expect(history).toHaveLength(1);
    expect(history[0]).toMatchObject({ id: 'assistant-1', status: 'complete' });
    const stopped = eventSchema.parse({
      ...metadata,
      type: 'assistant_turn_stopped',
      text: 'The complete prepared answer.',
      interrupted: false,
    });
    expect(applyConversationEvent(history, stopped)).toBe(history);
    expect(applyConversationEvent(history, prepared)).toBe(history);
  });

  it('retains interrupted preview text and ignores stale completions and previews', () => {
    let history = applyConversationEvent([], prepared);
    history = applyConversationEvent(
      history,
      eventSchema.parse({ ...metadata, type: 'clear', generation: 2 }),
      2,
    );
    expect(history[0]).toMatchObject({
      text: 'The complete prepared answer.',
      status: 'interrupted',
    });
    history = applyConversationEvent(
      history,
      eventSchema.parse({
        ...metadata,
        type: 'assistant_response',
        generation: 2,
        text: 'The new reply.',
      }),
      2,
    );
    const stale = eventSchema.parse({ ...metadata, type: 'assistant_response_finished' });
    expect(applyConversationEvent(history, stale, 2)).toBe(history);
    expect(applyConversationEvent(history, prepared, 2)).toBe(history);
    // The aggregator may report the interrupted stop after the generation advanced.
    const stopped = eventSchema.parse({
      ...metadata,
      type: 'assistant_turn_stopped',
      generation: 2,
      text: '',
      interrupted: true,
    });
    expect(applyConversationEvent(history, stopped)).toBe(history);
    expect(history[1]).toMatchObject({ text: 'The new reply.', status: 'preparing' });
  });

  it('keeps a speech failure distinct from interruption through late playback events', () => {
    let history = applyConversationEvent([], prepared);
    history = applyConversationEvent(
      history,
      eventSchema.parse({ ...metadata, type: 'speech_failed' }),
    );
    history = applyConversationEvent(
      history,
      eventSchema.parse({ ...metadata, type: 'clear', generation: 2 }),
      2,
    );
    expect(history[0]).toMatchObject({
      text: 'The complete prepared answer.',
      status: 'audio_failed',
    });
    expect(
      applyConversationEvent(
        history,
        eventSchema.parse({ ...metadata, type: 'assistant_response_finished' }),
      ),
    ).toEqual(history);
  });

  it('ends pending previews on call termination', () => {
    const history = applyConversationEvent([], prepared);
    expect(interruptResponses(history)[0]).toMatchObject({ status: 'interrupted' });
    for (const type of ['closing', 'error', 'pipeline_error', 'output_unavailable']) {
      const ending = eventSchema.parse({ ...metadata, type, reason: 'finished' });
      const ended = applyConversationEvent(history, ending);
      expect(ended[0]).toMatchObject({
        text: 'The complete prepared answer.',
        status: 'interrupted',
      });
      expect(
        applyConversationEvent(
          ended,
          eventSchema.parse({ ...metadata, type: 'assistant_response_finished' }),
        )[0],
      ).toMatchObject({ status: 'interrupted' });
    }
  });

  it('rejects malformed media and transcript events before changing playback or history', () => {
    const metadata = { sequence: 1, at_ms: 0, server_time_ms: 0 };
    expect(eventSchema.safeParse({ ...metadata, type: 'clear' }).success).toBe(false);
    expect(eventSchema.safeParse({ ...metadata, type: 'user_transcript' }).success).toBe(false);
    expect(
      eventSchema.safeParse({ ...metadata, type: 'assistant_response', generation: 1 }).success,
    ).toBe(false);
    expect(eventSchema.safeParse({ ...metadata, type: 'tool_started', call_id: 'a' }).success).toBe(
      false,
    );
    expect(eventSchema.safeParse({ ...metadata, type: 'pipeline_started' }).success).toBe(true);
  });
});

describe('updates from outside the conversation', () => {
  const at = { at_ms: 100, server_time_ms: 100 };
  it('adds one notice per staff decision', () => {
    const decided = eventSchema.parse({
      ...at,
      sequence: 7,
      type: 'approval_decided',
      request_id: 'R-4417',
      order_id: 'A-1042',
      amount_cents: 1000,
      status: 'approved',
    });
    const history = applyConversationEvent([], decided);
    expect(history).toEqual([
      {
        kind: 'notice',
        id: 'approval-R-4417',
        icon: 'approved',
        text: 'Acme support approved $10.00 credit on order A-1042.',
      },
    ]);
    expect(applyConversationEvent(history, { ...decided, sequence: 8 })).toBe(history);
  });

  it("describes Clef's routing in words", () => {
    const routed = eventSchema.parse({
      ...at,
      sequence: 9,
      type: 'ticket_triaged',
      ticket_id: 'T-4821',
      team: 'safety',
      team_confidence: 0.931,
      urgency: 2.88,
      wants_person: 0.82,
    });
    expect(applyConversationEvent([], routed)[0]).toMatchObject({
      kind: 'notice',
      icon: 'routed',
      text:
        'Clef routed ticket T-4821 to Safety (93% sure). Urgency: Right away (2.9 of 3). ' +
        'The customer asked for a person.',
    });
  });
});
