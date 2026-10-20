import { describe, expect, it } from 'vitest';
import { eventSchema } from '../../web/src/lib/contracts';
import { applyConversationEvent } from '../../web/src/voice/conversation';
import {
  describeCallEnd,
  describeStage,
  describeStatus,
  idleCall,
  nextActivity,
  type CallView,
} from '../../web/src/voice/model';

const call = (patch: Partial<CallView>): CallView => ({
  ...idleCall(),
  phase: 'connected',
  microphoneActive: true,
  connectedAt: 1,
  status: 'Connected.',
  ...patch,
});

describe('stage presentation', () => {
  it('invites the caller by the assistant name and reminds them of their problem', () => {
    const caller = { name: 'Maya Chen', hint: 'Her umbrella arrived with a bent rib.' };
    expect(describeStage(idleCall(), 'Matt', caller)).toMatchObject({
      heading: 'Talk to Matt',
      subheading: "You're calling as Maya Chen. Her umbrella arrived with a bent rib.",
      controls: 'start',
      microphone: 'hidden',
      showHeadphonesTip: true,
      showTimer: false,
    });
    expect(describeStage(idleCall(), '  ')).toMatchObject({
      heading: 'Talk to your assistant',
      subheading: expect.stringContaining('order'),
    });
    expect(describeStage(idleCall(), 'Matt', caller, 600).subheading).toContain('10 minutes');
  });

  it('spells out each phase of a call', () => {
    expect(describeStage(call({ phase: 'connecting' }), 'Matt')).toMatchObject({
      heading: 'Getting connected',
      activity: 'connecting',
      controls: 'cancel',
      microphone: 'live',
    });
    expect(describeStage(call({ phase: 'ending', microphoneActive: false }), 'Matt')).toMatchObject(
      {
        heading: 'Ending your call',
        controls: 'ending',
        microphone: 'off',
      },
    );
    expect(describeStage(call({ phase: 'ended', microphoneActive: false }), 'Matt')).toMatchObject({
      heading: 'Call ended',
      controls: 'start',
      microphone: 'hidden',
      showTimer: true,
    });
    const neverConnected = { ...idleCall(), phase: 'ended' as const, error: 'No connection.' };
    expect(describeStage(neverConnected, 'Matt').showTimer).toBe(false);
  });

  it('describes who is talking during a call', () => {
    expect(describeStage(call({ activity: 'listening' }), 'Matt')).toMatchObject({
      heading: 'Listening to you',
      activity: 'listening',
      controls: 'call',
    });
    expect(describeStage(call({ activity: 'speaking' }), 'Matt')).toMatchObject({
      heading: 'Matt is speaking',
      activity: 'speaking',
    });
    expect(describeStage(call({ activity: 'thinking' }), 'Matt')).toMatchObject({
      heading: 'Matt is thinking',
    });
    expect(describeStage(call({ activity: 'tool' }), 'Matt')).toMatchObject({
      heading: 'Looking that up',
      activity: 'looking it up',
    });
  });

  it('lets a muted microphone take over the listening state but not the assistant turn', () => {
    expect(describeStage(call({ muted: true }), 'Matt')).toMatchObject({
      heading: 'Your microphone is muted',
      activity: 'muted',
      subheading: "Unmute whenever you're ready.",
      microphone: 'muted',
    });
    expect(describeStage(call({ muted: true, activity: 'speaking' }), 'Matt')).toMatchObject({
      heading: 'Matt is speaking',
      microphone: 'muted',
    });
  });

  it('surfaces blocked playback only during a call', () => {
    expect(describeStage(call({ playbackBlocked: true }), 'Matt').showEnableAudio).toBe(true);
    expect(
      describeStage(call({ phase: 'ended', playbackBlocked: true }), 'Matt').showEnableAudio,
    ).toBe(false);
  });
});

describe('status presentation', () => {
  it('reports routine status with a tone that follows the call phase', () => {
    expect(describeStatus(idleCall(), { kind: 'ready' })).toEqual({
      tone: 'neutral',
      busy: false,
      message: 'Ready when you are.',
    });
    expect(describeStatus(call({}), { kind: 'ready' })).toMatchObject({ tone: 'ok', busy: false });
  });

  it('marks in-flight work as busy', () => {
    expect(describeStatus(call({ phase: 'connecting' }), { kind: 'ready' }).busy).toBe(true);
    expect(describeStatus(idleCall(), { kind: 'checking' }).busy).toBe(true);
    expect(describeStatus(idleCall(), { kind: 'signing-in' })).toEqual({
      tone: 'neutral',
      busy: true,
      message: 'Signing in...',
    });
    expect(describeStatus(idleCall(), { kind: 'locked' }).busy).toBe(false);
  });

  it('puts session problems ahead of call status so a failed sign-in never moves the layout', () => {
    expect(describeStatus(idleCall(), { kind: 'locked' })).toMatchObject({
      tone: 'neutral',
      message: 'Sign in to start a call.',
    });
    expect(describeStatus(idleCall(), { kind: 'locked', error: 'Wrong code.' })).toEqual({
      tone: 'error',
      busy: false,
      message: 'Wrong code.',
    });
    const failedCall = call({ phase: 'ended', error: 'The audio connection failed.' });
    expect(describeStatus(failedCall, { kind: 'unavailable', message: 'Not configured.' })).toEqual(
      { tone: 'error', busy: false, message: 'Not configured.' },
    );
  });

  it('lets errors replace the routine message and keeps the cleanup note', () => {
    const view = call({
      phase: 'ended',
      error: 'The audio connection failed.',
      cleanupPending: true,
    });
    expect(describeStatus(view, { kind: 'ready' })).toEqual({
      tone: 'error',
      busy: false,
      message: 'The audio connection failed. Server cleanup will retry automatically.',
    });
    expect(describeStatus(call({ cleanupPending: true }), { kind: 'ready' })).toMatchObject({
      tone: 'warning',
      message: expect.stringContaining('retry automatically'),
    });
    const ended = call({ phase: 'ended', ...describeCallEnd('provider_disconnected') });
    expect(describeStatus(ended, { kind: 'ready' })).toMatchObject({
      tone: 'error',
      message: expect.stringContaining('Speech recognition disconnected'),
    });
    expect(describeCallEnd('session_timeout').error).toBeUndefined();
  });
});

describe('call activity', () => {
  const started = { type: 'tool_started', call_id: 'a', name: 'search_web' };
  const finished = { type: 'tool_finished', call_id: 'a' };
  const play = (events: Record<string, unknown>[]) => {
    let view: Pick<CallView, 'activity' | 'history'> = { activity: 'thinking', history: [] };
    return events.map((data) => {
      const event = eventSchema.parse({ sequence: 1, at_ms: 0, server_time_ms: 0, ...data });
      const history = applyConversationEvent(view.history, event);
      view = { activity: nextActivity(view, event, history), history };
      return view.activity;
    });
  };

  it('keeps speaking through a filler and then resumes the lookup state', () => {
    const audio = { type: 'audio_started', generation: 1 };
    const filler = { type: 'filler_finished', generation: 1 };
    expect(play([started, audio, finished, filler])).toEqual([
      'tool',
      'speaking',
      'speaking',
      'thinking',
    ]);
    expect(play([started, audio, filler, finished])).toEqual([
      'tool',
      'speaking',
      'tool',
      'thinking',
    ]);
  });

  it('follows lookups while silent and ignores tool events that change nothing', () => {
    const replied = [
      { type: 'audio_started', generation: 1 },
      { type: 'assistant_response_finished', generation: 1 },
    ];
    expect(play([started, finished, ...replied, finished])).toEqual([
      'tool',
      'thinking',
      'speaking',
      'listening',
      'listening',
    ]);
  });
});
