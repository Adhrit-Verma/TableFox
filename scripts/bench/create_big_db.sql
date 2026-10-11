-- create_big_db.sql: rebuild the TableFox benchmark database ("Northwind Corp", fictional) from nothing.
--
-- Run as a superuser against an EMPTY database named e.g. "bench" (PostgreSQL 17):
--   createdb -h 127.0.0.1 -p 55450 -U bench_owner bench
--   psql -h 127.0.0.1 -p 55450 -U bench_owner -d bench -f scripts/bench/create_big_db.sql
-- or run the five steps one by one (each finishes in a few minutes; see README.md).
--
-- What you get (deterministic: setseed + generate_series, fixed "today" = 2026-10-10 23:59 UTC):
--   * 71 tables in 10 schemas (public, hr, finance, crm, support, marketing, inventory, sales, ops, analytics),
--     748 columns, 147 declared foreign keys, 4 views + 1 materialized view, 3 enums, COMMENTs on 27 tables
--   * 25,070,898 table rows (+739 in the materialized view); biggest: sales.order_items 2.61M, ops.api_request_log 2.5M, finance.invoice_lines 2.49M,
--     analytics.page_views 1.94M, marketing.email_events 1.5M, finance.gl_entries 1.3M, public.audit_log 1.2M
--   * ~3.9 GB on disk with indexes; full load took 8m50s on a laptop with fsync=off (05_finish verifies all FKs)
--   * role tablefox_reader / bench_reader_pw: CONNECT + USAGE + SELECT only, default_transaction_read_only = on
-- Exact counts are in the hand-over note produced after loading (query pg_stat_user_tables).
\set ON_ERROR_STOP on
\ir 01_schema.sql
\ir 02_load_master.sql
\ir 03_load_sales.sql
\ir 04_load_events.sql
\ir 05_finish.sql
