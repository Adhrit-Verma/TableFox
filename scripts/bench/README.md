# Benchmark database (local, throwaway)

A large fictional company database ("Northwind Corp") for benchmarking TableFox locally. It has 71 tables in 10 schemas and about 25M rows, and takes about 3.9 GB on disk. The full load takes about 9 minutes.

## 1. Create an instance (Windows, PostgreSQL 17)

```powershell
$b = "C:\Program Files\PostgreSQL\17\bin"; $d = "$env:TEMP\bench-pg"
& "$b\initdb.exe" -D $d -U bench_owner -A trust -E UTF8 --locale=C
Start-Process -FilePath "$b\pg_ctl.exe" -WindowStyle Hidden -ArgumentList "-D `"$d`" -o `"-p 55450 -c listen_addresses=127.0.0.1 -c shared_buffers=512MB -c maintenance_work_mem=512MB -c max_wal_size=4GB -c fsync=off -c synchronous_commit=off -c full_page_writes=off`" -l `"$d.log`" start"
& "$b\psql.exe" -h 127.0.0.1 -p 55450 -U bench_owner -d postgres -c "create database bench"
```

`fsync=off` is fine here because the data is throwaway. Stop the instance with `pg_ctl -D $d stop -m fast`.

## 2. Load

Run all steps in one go: `psql -h 127.0.0.1 -p 55450 -U bench_owner -d bench -f scripts/bench/create_big_db.sql`

You can also run the steps one at a time, in order. Each step takes a few minutes:

| Step | What it does |
|---|---|
| `01_schema.sql` | Drops and recreates all schemas, types, tables and comments |
| `02_load_master.sql` | Loads reference and master data: org, accounts, products, users |
| `03_load_sales.sql` | Loads orders, items, invoices, payments, shipments, purchasing, GL and subscriptions |
| `04_load_events.sql` | Loads tickets, email and web events, logins, API log, audit log and change history |
| `05_finish.sql` | Verifies FKs, runs VACUUM FULL, builds indexes, views and the materialized view, creates the `tablefox_reader` role, runs ANALYZE |

The data is deterministic because it uses `setseed`. "Today" in the data is fixed at 2026-10-10 23:59 UTC.

## 3. Read-only user

`05_finish.sql` creates `tablefox_reader` / `bench_reader_pw`. The role has CONNECT, USAGE and SELECT only, plus `default_transaction_read_only = on`. To check it:

```
.venv\Scripts\python -c "import sys; sys.path.insert(0,'services/dbmap/src'); from dbmap.tenants import check_reader_role; check_reader_role('postgresql://tablefox_reader:bench_reader_pw@127.0.0.1:55450/bench?sslmode=disable'); print('reader ok')"
```

## 4. Point TableFox at it

Create `.env.bench` in the repo root. It is git-ignored by `.env.*`:

```
DATABASE_URL=postgresql://tablefox_reader:bench_reader_pw@127.0.0.1:55450/bench?sslmode=disable
DBMAP_CACHE_DIR=.dbmap-cache-bench
DBMAP_AUDIT_DIR=.logs/audit-bench
DBMAP_RUNTIME_FILE=.tablefox-runtime-bench.json
DBMAP_STATEMENT_TIMEOUT_MS=15000
```

Then set `$env:DBMAP_ENV_FILE` to that file before you start TableFox.
