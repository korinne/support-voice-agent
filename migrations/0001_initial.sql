-- Acme's support records. A real deployment reads customers and orders from
-- its own systems; these tables keep the example self-contained.
CREATE TABLE customers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  credit_cents INTEGER NOT NULL DEFAULT 0,
  -- Shown on the demo sign-in screen so visitors know what to ask about.
  demo_hint TEXT NOT NULL DEFAULT ''
);

CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers (id),
  placed_on TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('processing', 'shipped', 'delivered', 'cancelled', 'returned')
  ),
  -- JSON array of {"name": ..., "price_cents": ...}
  items TEXT NOT NULL,
  total_cents INTEGER NOT NULL,
  shipping TEXT NOT NULL DEFAULT ''
);
CREATE INDEX orders_by_customer ON orders (customer_id, placed_on DESC);

CREATE TABLE tickets (
  id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers (id),
  order_id TEXT REFERENCES orders (id),
  call_id TEXT,
  summary TEXT NOT NULL,
  details TEXT NOT NULL,
  created_at TEXT NOT NULL,
  -- Clef fills these in shortly after the ticket is created.
  team TEXT,
  team_confidence REAL,
  urgency REAL,
  wants_person REAL,
  triaged_at TEXT
);
CREATE INDEX tickets_by_customer ON tickets (customer_id, created_at DESC);
CREATE INDEX tickets_by_time ON tickets (created_at DESC);

-- Actions the agent can propose but only staff can approve.
CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK (action IN ('store_credit')),
  customer_id TEXT NOT NULL REFERENCES customers (id),
  order_id TEXT NOT NULL REFERENCES orders (id),
  call_id TEXT,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
  decided_by TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX approvals_by_status ON approvals (status, created_at);
CREATE INDEX approvals_by_customer ON approvals (customer_id, created_at DESC);
