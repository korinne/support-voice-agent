import { describe, expect, it } from 'vitest';
import { money, urgency, when } from '../../web/src/lib/format';
import { setupMessage } from '../../web/src/lib/session';

describe('display helpers', () => {
  it('formats cents, urgency scores, and plain dates', () => {
    expect(money(12900)).toBe('$129.00');
    expect(urgency(0.2)).toBe('Within two business days (0.2 of 3)');
    expect(urgency(2.88)).toBe('Right away (2.9 of 3)');
    expect(when('2026-10-13')).toBe('Oct 13');
  });

  it('tells the person deploying the app what is missing', () => {
    expect(setupMessage(['realtime', 'database'])).toBe(
      "This app isn't set up yet. It needs the Realtime SFU credentials and the D1 tables. See docs/deployment.md.",
    );
    expect(setupMessage([])).toContain('server credentials');
  });
});
