import { z } from 'zod';
import { request } from '../lib/api';
import { decisionSchema, deskSchema, helpCenterSchema, type Desk } from '../lib/contracts';

export type Decision = 'approve' | 'decline';

/** The staff desk's server calls. Cloudflare Access signs staff in before these reach the Worker. */
export type DeskClient = {
  overview(signal?: AbortSignal): Promise<Desk>;
  decide(id: string, decision: Decision): Promise<z.infer<typeof decisionSchema>>;
  publishHelpCenter(): Promise<void>;
  resetDemo(): Promise<void>;
};

export const liveDesk: DeskClient = {
  overview: (signal) => request('/desk/api/overview', deskSchema, { signal, timeout: 15_000 }),
  decide: (id, decision) =>
    request(`/desk/api/approvals/${id}`, decisionSchema, { body: { decision }, timeout: 15_000 }),
  async publishHelpCenter() {
    await request('/desk/api/help-center', z.object({ help_center: helpCenterSchema }), {
      body: {},
      timeout: 60_000,
    });
  },
  async resetDemo() {
    await request('/desk/api/demo-data', z.object({ reset: z.literal(true) }), {
      body: {},
      timeout: 15_000,
    });
  },
};
