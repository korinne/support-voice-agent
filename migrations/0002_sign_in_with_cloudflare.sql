-- Customers now sign in with Cloudflare and bring up any problem, so the
-- sign-in screen no longer shows a hint for each demo customer.
ALTER TABLE customers DROP COLUMN demo_hint;
