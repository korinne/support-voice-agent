import { useCallback, useEffect, useRef, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { Button } from '../components/Button';
import { Select } from '../components/Field';
import { VoiceRoom } from '../components/VoiceRoom';
import { interruptResponses } from '../voice/conversation';
import {
  previewAccountFor,
  previewFor,
  previewInfoFor,
  scenarioFrom,
  scenarios,
  type Scenario,
} from './scenarios';

export default function PreviewApp() {
  const [preview, setPreview] = useState(() =>
    previewFor(scenarioFrom(new URLSearchParams(location.search).get('scenario'))),
  );
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const select = (scenario: Scenario) => {
    clearTimeout(timer.current);
    setPreview(previewFor(scenario));
    const url = new URL(location.href);
    url.searchParams.set('scenario', scenario);
    history.replaceState(null, '', url);
  };

  // The account panel reloads when this changes, so keep it stable within a scenario.
  const loadAccount = useCallback(
    async () => previewAccountFor(preview.scenario),
    [preview.scenario],
  );
  const info = previewInfoFor(preview.scenario);
  const signedIn = preview.session.kind === 'ready';
  return (
    <>
      <div className="mx-auto max-w-6xl px-4 pt-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm sm:px-6">
          <p className="mr-auto text-stone-600">
            <strong className="font-medium text-stone-900">Frontend preview</strong>
            <span className="ml-2">Sample data · no microphone or backend</span>
          </p>
          <label htmlFor="preview-scenario" className="font-medium">
            Preview scenario
          </label>
          <Select
            id="preview-scenario"
            className="w-56"
            value={preview.scenario}
            onChange={(event) => select(scenarioFrom(event.target.value))}
          >
            {scenarios.map(({ id, label }) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </Select>
          <Button tone="quiet" onClick={() => select(preview.scenario)}>
            Reset scenario
          </Button>
        </div>
      </div>
      <AppShell
        label={signedIn ? info.customer?.name || info.customer?.email || 'Support' : 'Support'}
        actions={<span className="font-mono text-xs text-stone-500">Local preview</span>}
      >
        <VoiceRoom
          view={preview.view}
          session={preview.session}
          info={signedIn ? info : { ...info, authenticated: false, customer: null }}
          loadAccount={loadAccount}
          onStart={() => {
            select('connecting');
            timer.current = setTimeout(() => select('listening'), 650);
          }}
          onWarm={() => {}}
          onSignIn={() => {
            // The preview skips the trip to Cloudflare: show the busy state, then unlock.
            select('signing-in');
            timer.current = setTimeout(() => select('idle'), 650);
          }}
          onRetry={() => select('idle')}
          onStop={() => {
            clearTimeout(timer.current);
            setPreview((current) => ({
              ...current,
              scenario: 'ended',
              view: {
                ...current.view,
                phase: 'ended',
                status: 'Call ended. Your microphone is off.',
                microphoneActive: false,
                userLevel: 0,
                agentLevel: 0,
                history: interruptResponses(current.view.history),
              },
            }));
          }}
          onMute={() =>
            setPreview((current) => ({
              ...current,
              view: {
                ...current.view,
                muted: !current.view.muted,
                userLevel: current.view.muted ? 0.5 : 0,
              },
            }))
          }
          onResume={() =>
            setPreview((current) => ({
              ...current,
              view: { ...current.view, playbackBlocked: false },
            }))
          }
        />
      </AppShell>
    </>
  );
}
