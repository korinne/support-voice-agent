import { useState } from 'react';
import type { Desk } from '../lib/contracts';
import DeskApp from '../desk/DeskApp';
import type { DeskClient } from '../desk/client';

const sample: Desk = {
  staff: 'ana@example.com',
  approvals: [
    {
      id: 'R-4417',
      customer_name: 'Maya',
      customer_email: 'maya@example.com',
      order_id: 'A-1042',
      amount_cents: 1000,
      reason: 'The umbrella arrived with a bent rib.',
      status: 'pending',
      decided_by: null,
      decided_at: null,
      created_at: '2026-10-15T18:04:00+00:00',
    },
    {
      id: 'R-2290',
      customer_name: 'Sam',
      customer_email: 'sam@example.com',
      order_id: 'A-1057',
      amount_cents: 900,
      reason: 'The map glows only on one side.',
      status: 'declined',
      decided_by: 'ana@example.com',
      decided_at: '2026-10-15T17:41:00+00:00',
      created_at: '2026-10-15T17:30:00+00:00',
    },
  ],
  tickets: [
    {
      id: 'T-4821',
      customer_name: 'Rosa',
      customer_email: 'rosa@example.com',
      order_id: 'A-1033',
      summary: 'Pogo stick smells like fuel',
      details:
        'The PS-2 pogo stick smells of fuel after a ride. The customer stopped using it and wants someone from safety to call her.',
      created_at: '2026-10-15T18:10:00+00:00',
      team: 'safety',
      team_confidence: 0.931,
      urgency: 2.88,
      wants_person: 0.82,
      triaged_at: '2026-10-15T18:10:03+00:00',
    },
    {
      id: 'T-3175',
      customer_name: 'Sam',
      customer_email: 'sam@example.com',
      order_id: 'A-1057',
      summary: 'Sneakers stuck in transit',
      details: 'No carrier scan for five days since Albuquerque.',
      created_at: '2026-10-15T18:12:00+00:00',
      team: null,
      team_confidence: null,
      urgency: null,
      wants_person: null,
      triaged_at: null,
    },
  ],
  help_center: { articles: 8, indexed: 8, indexing: 0, published: true },
};

/** The staff desk with sample data, for the UI dev server. Decisions change only this page. */
export default function DeskPreview() {
  const [client] = useState<DeskClient>(() => {
    let desk = structuredClone(sample);
    return {
      overview: async () => desk,
      async decide(id, decision) {
        const status = decision === 'approve' ? 'approved' : 'declined';
        desk = {
          ...desk,
          approvals: desk.approvals.map((approval) =>
            approval.id === id
              ? {
                  ...approval,
                  status,
                  decided_by: desk.staff,
                  decided_at: new Date().toISOString(),
                }
              : approval,
          ),
        };
        return { approval: { id, status }, caller_told: true };
      },
      async publishHelpCenter() {},
      async resetDemo() {
        desk = { ...desk, approvals: [], tickets: [] };
      },
    };
  });
  return <DeskApp client={client} />;
}
