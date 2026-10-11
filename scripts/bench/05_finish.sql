-- 05_finish.sql: verify foreign keys, compact, index, views, read-only role, ANALYZE.
\set ON_ERROR_STOP on
SET client_min_messages = warning;
SET maintenance_work_mem = '512MB';
SET timezone = 'UTC';

-- every single-column FK must resolve (loads ran with FK triggers off)
DO $$
DECLARE r record; bad bigint; total int := 0;
BEGIN
  FOR r IN
    SELECT c.conname, c.conrelid::regclass child, c.confrelid::regclass parent,
           a.attname child_col, af.attname parent_col
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1
  LOOP
    EXECUTE format('SELECT count(*) FROM %s c WHERE c.%I IS NOT NULL AND NOT EXISTS (SELECT 1 FROM %s p WHERE p.%I = c.%I)',
                   r.child, r.child_col, r.parent, r.parent_col, r.child_col) INTO bad;
    IF bad > 0 THEN RAISE WARNING 'FK % on %: % orphan rows', r.conname, r.child, bad; total := total + 1; END IF;
  END LOOP;
  IF total > 0 THEN RAISE EXCEPTION '% foreign keys have orphan rows', total; END IF;
END $$;

DROP SCHEMA IF EXISTS bench_gen CASCADE;

-- loads updated many rows in place; rewrite so on-disk size reflects live data
VACUUM FULL;

-- ---------------------------------------------------------------- indexes
-- index every single-column FK, except a few deliberately left unindexed on big tables
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT DISTINCT c.conrelid::regclass tbl, a.attname col, n.nspname sch, cl.relname rel
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    JOIN pg_class cl ON cl.oid = c.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
    WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1
      AND format('%s.%s.%s', n.nspname, cl.relname, a.attname) NOT IN (
        'sales.order_items.product_id', 'marketing.email_events.contact_id', 'finance.gl_entries.cost_center_id',
        'inventory.stock_movements.product_id', 'inventory.stock_movements.employee_id', 'public.audit_log.actor_user_id')
      -- skip when the column already leads the primary key
      AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])
  LOOP
    EXECUTE format('CREATE INDEX %I ON %s (%I)', left('ix_' || r.rel || '_' || r.col, 63), r.tbl, r.col);
  END LOOP;
END $$;

CREATE INDEX ix_orders_order_dt ON sales.orders (order_dt);
CREATE INDEX ix_orders_status ON sales.orders (status);
CREATE INDEX ix_tickets_opened_at ON support.tickets (opened_at);
CREATE INDEX ix_invoices_issued_dt ON finance.invoices (issued_dt);
CREATE INDEX ix_subscriptions_status ON finance.subscriptions (status);
CREATE INDEX ix_accounts_name_lower ON crm.accounts (lower(account_name));
CREATE INDEX ix_contacts_email ON crm.contacts (email);
CREATE INDEX ix_change_history_table_pk ON public.change_history (table_name, record_pk);
-- deliberately NOT indexed (seq scans on big tables): ops.api_request_log(requested_at, status_code),
-- analytics.page_views(viewed_at), public.audit_log(occurred_at, action), marketing.email_events(event_ts, event_type),
-- ops.login_events(event_ts, success), finance.gl_entries(entry_dt), inventory.stock_movements(moved_at),
-- sales.order_items(product_id), finance.payments(paid_dt)

-- ---------------------------------------------------------------- views
CREATE VIEW sales.v_order_summary AS
SELECT o.order_id, o.order_no, o.order_dt, o.status, o.channel, o.cust_id, c.customer_name, a.account_id, a.account_name,
       a.tier, r.region_code, o.currency_code, o.total_amt, o.total_usd
FROM sales.orders o
JOIN sales.customers c ON c.cust_id = o.cust_id
JOIN crm.accounts a ON a.account_id = c.account_id
JOIN public.regions r ON r.region_id = a.region_id;
COMMENT ON VIEW sales.v_order_summary IS 'Orders with customer, account and region; amounts in order currency and USD.';

CREATE VIEW support.v_open_tickets AS
SELECT t.ticket_id, t.ticket_ref, t.account_id, t.priority, t.status, t.opened_at, t.sla_due_at, tr.tier_name,
       t.assigned_agent_id, t.sla_due_at < timestamptz '2026-10-10 23:59:59+00' AS sla_breached
FROM support.tickets t JOIN support.tiers tr ON tr.tier_id = t.tier_id
WHERE t.resolved_at IS NULL;

CREATE VIEW hr.v_employee_directory AS
SELECT e.employee_id, e.first_name, e.last_name, e.job_title, d.dept_name, e.manager_id,
       m.first_name || ' ' || m.last_name AS manager_name, e.location_country, e.is_active
FROM hr.employees e JOIN hr.departments d ON d.department_id = e.department_id
LEFT JOIN hr.employees m ON m.employee_id = e.manager_id;

CREATE VIEW finance.v_mrr_by_plan AS
SELECT p.plan_code, p.plan_name, count(*) AS active_subscriptions, sum(s.mrr_amt) AS mrr_usd
FROM finance.subscriptions s JOIN finance.plans p ON p.plan_id = s.plan_id
WHERE s.status IN ('active', 'past_due')
GROUP BY p.plan_code, p.plan_name;

CREATE MATERIALIZED VIEW analytics.mv_monthly_revenue AS
SELECT date_trunc('month', o.order_dt)::date AS month, r.region_code, a.tier,
       count(*) AS orders, sum(o.total_usd) AS revenue_usd
FROM sales.orders o
JOIN sales.customers c ON c.cust_id = o.cust_id
JOIN crm.accounts a ON a.account_id = c.account_id
JOIN public.regions r ON r.region_id = a.region_id
WHERE o.status NOT IN ('cancelled', 'draft')
GROUP BY 1, 2, 3;
COMMENT ON MATERIALIZED VIEW analytics.mv_monthly_revenue IS 'Monthly order revenue (USD) by region and account tier; excludes cancelled and draft orders.';

-- ---------------------------------------------------------------- database settings and read-only role
DO $$ BEGIN
  EXECUTE format('ALTER DATABASE %I SET timezone = %L', current_database(), 'UTC');
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tablefox_reader') THEN
    CREATE ROLE tablefox_reader LOGIN PASSWORD 'bench_reader_pw';
  END IF;
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO tablefox_reader', current_database());
END $$;
ALTER ROLE tablefox_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD 'bench_reader_pw';
ALTER ROLE tablefox_reader SET default_transaction_read_only = on;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public, sales, crm, inventory, finance, hr, support, marketing, analytics, ops TO tablefox_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public, sales, crm, inventory, finance, hr, support, marketing, analytics, ops TO tablefox_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public, sales, crm, inventory, finance, hr, support, marketing, analytics, ops
  GRANT SELECT ON TABLES TO tablefox_reader;

VACUUM ANALYZE;
