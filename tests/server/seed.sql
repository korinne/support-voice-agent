-- Fixed customers and orders for the Python tests. The app creates real
-- accounts with random order numbers when people sign in.
INSERT INTO customers (id, name, email, credit_cents) VALUES
  ('cus_maya', 'Maya Chen', 'maya@example.com', 0),
  ('cus_sam', 'Sam Rivera', 'sam@example.com', 0),
  ('cus_rosa', 'Rosa Alvarez', 'rosa@example.com', 500);

INSERT INTO orders (id, customer_id, placed_on, status, items, total_cents, shipping) VALUES
  ('A-1042', 'cus_maya', date('now', '-6 days'), 'delivered',
   '[{"name": "Self-Folding Umbrella", "price_cents": 3400}]', 3400,
   'Delivered ' || date('now', '-2 days')),
  ('A-1019', 'cus_maya', date('now', '-41 days'), 'delivered',
   '[{"name": "Pocket Trampoline", "price_cents": 5900}]', 5900,
   'Delivered ' || date('now', '-36 days')),
  ('A-1057', 'cus_sam', date('now', '-9 days'), 'shipped',
   '[{"name": "Spring-Loaded Sneakers", "price_cents": 7200}, {"name": "Glow-in-the-Dark Map", "price_cents": 900}]',
   8100, 'In transit. Last carrier scan ' || date('now', '-5 days') || ' in Albuquerque, NM'),
  ('A-1033', 'cus_rosa', date('now', '-20 days'), 'delivered',
   '[{"name": "Jet-Powered Pogo Stick, model PS-2", "price_cents": 12900}]', 12900,
   'Delivered ' || date('now', '-15 days')),
  ('A-1061', 'cus_rosa', date('now'), 'processing',
   '[{"name": "Instant Tunnel Kit", "price_cents": 4500}]', 4500, 'Not shipped yet');
