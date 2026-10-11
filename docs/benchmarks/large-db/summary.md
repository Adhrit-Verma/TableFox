# TableFox on a large database: benchmark results

Run date: 2026-10-11. Every number below comes from the raw JSON files in this folder. No task was dropped.

## Headline

| Metric | TableFox | Conventional path |
|---|---|---|
| Tasks | 70 (10 categories × 7) | same 70 |
| Warm latency, median / p95 / p99 (both calls, 207 samples) | **96 / 498 / 1163 ms** | 282 / 690 / 1335 ms |
| `database_task_context` alone, median / p95 / p99 | 16.8 / 24.9 / 27.5 ms | catalog searches 133 / 151 / 169 ms |
| Mean tokens per task (context + result) | **1,805** (context 873, result 932) | 2,493 |
| Schema-qualified recall@6 (129 needed relations) | 84.5% (anywhere in the pack: 87.6%) | n/a |
| Tasks whose pack holds every needed relation | 57 / 70 (81%) | n/a |
| Row-level answer match, ground-truth SQL through TableFox vs direct | **64 / 64 executed (100%)**; 6 of 70 refused by policy | n/a |
| Blind authoring (an assistant with only the two calls, one attempt) | **13 / 28 correct**, 2 partial, 8 wrong, 4 missing context, 1 policy refusal | n/a |
| Cold start, fresh process to first answer | 2.49 s with no snapshot cache; 2.26 s with the cache | n/a |
| Write-refusal suite | 17 / 17 refused; database unchanged | n/a |
| Sensitive columns returned unmasked | national_id, IBANs, card_last4, tax_id, tax_code, salary, payroll amounts, key_prefix, plus alias and expression bypasses | n/a |

The conventional path opens a fresh connection per task, runs one catalog `ILIKE` search per term, then runs the ground-truth SQL directly. It is a lower bound for an assistant without TableFox, since it assumes the assistant already knows the right SQL after the catalog search.

## Setup

- **Machine:** AMD Ryzen 7 7840HS (8 cores, 16 threads), 15.3 GB RAM, NVMe SSD, Windows 11. Client, server and PostgreSQL all on the same machine.
- **Database:** PostgreSQL 17.5, `shared_buffers=512MB`, `fsync=off` (throwaway data). Database `bench`: 71 tables in 10 schemas, 748 table columns, 147 foreign keys, 25.07 M rows, 3,918 MB. Built by `scripts/bench/*.sql`; the data is deterministic.
- **TableFox:** stdio MCP server (`python -m dbmap.mcp_server`) on Python 3.14, connecting as the read-only role `tablefox_reader`, `DBMAP_STATEMENT_TIMEOUT_MS=15000`, otherwise defaults (EXPLAIN cost limit 100,000, row limit 100,000, context window 0).
- **Calls:** `database_task_context(question, max_relations=6, max_bytes=6144)`, then `database_readonly_batch(max_rows_each=200, max_bytes=262144)`.
- **Corpus:** `scripts/bench/large_tasks.json`, 70 tasks with schema-qualified `needed_relations` and bounded ground-truth SQL using fixed dates (the data ends 2026-10-10). Every ground-truth query was verified as the owner and passes TableFox's SQL guard.

### Reproduce

    $env:DBMAP_ENV_FILE = "<repo>\.env.bench"   # never the repo .env
    $py = ".venv\Scripts\python"
    & $py scripts/benchmark_corpus.py --tasks scripts/bench/large_tasks.json --repeat 3 --output docs/benchmarks/large-db/corpus-report.json
    & $py scripts/bench/benchmark_large.py run --repeat 3 --window 0  --label default
    & $py scripts/bench/benchmark_large.py run --repeat 3 --window 16 --label window16
    & $py scripts/bench/benchmark_large.py run --repeat 1 --label raised-limits --set DBMAP_MAX_EXPLAIN_COST=10000000 --set DBMAP_MAX_EXPLAIN_ROWS=10000000
    & $py scripts/bench/benchmark_large.py conventional --repeat 3
    & $py scripts/bench/benchmark_large.py cold --repeat 5
    & $py scripts/bench/benchmark_large.py safety
    & $py scripts/bench/benchmark_large.py blind

See `scripts/bench/README.md` for creating the database.

### Files

| File | Contents |
|---|---|
| `corpus-report.json` | Standard `benchmark_corpus.py` run (3 repeats) |
| `run-default.json`, `run-window16.json`, `run-raised-limits.json` | Extended runs: pack per task, rank of each needed relation, split latency, payload, policy result, TableFox and direct rows |
| `conventional.json` | Conventional path (3 repeats) |
| `cold-start.json` | 5 cold-process samples with the snapshot cache deleted and 5 with it present |
| `safety.json` | Write-refusal and sensitive-column suite |
| `blind-contexts.json` → `blind-attempts.json` → `blind-raw.json` → `blind-authoring.json` | Blind-authoring test, in the order it was done |

## Latency

Warm figures in ms; 207 samples per configuration (69 tasks × 3 processes, first task per process excluded).

| Configuration | task_context median / p95 / p99 | batch median / p95 / p99 | both median / p95 / p99 |
|---|---|---|---|
| default (window 0) | 16.8 / 24.9 / 27.5 | 81.5 / 477 / 1152 | 96.5 / 498 / 1163 |
| window 16 | 15.2 / – / – | 74.2 / 418 / 1127 | 87.5 / 437 / 1142 |
| raised EXPLAIN limits (1 repeat, n=69) | 14.7 | 79.9 / 404 / 1128 | 93.4 / 424 / 1140 |
| conventional (n=210) | catalog search 133 / 151 / 169 | n/a | 282 / 690 / 1335 |

- Batch time is query time: the p95/p99 tail comes from queries scanning 1–2.6 M rows, not from TableFox.
- TableFox's own overhead is small: the context call stays under 30 ms at p99 on 71 tables.
- The conventional path pays for a new connection and 2–4 catalog searches per task (about 130 ms).

## Tokens and payload

| | Mean | Notes |
|---|---|---|
| Context pack | 873 tokens (3,491 bytes) | Median 914, max 1,710. Window 16 lowers this to 781 (−10.5%) |
| Batch result | 932 tokens | Max 12,398 (a 200-row list) |
| Both calls | 1,805 tokens | |
| Conventional | 2,493 tokens | Catalog rows plus the full result |

About 28% fewer tokens than the conventional path at equal row limits. Packs are not capped at 6 relations: `max_relations=6` limits the ranked tables and join connectors come on top (mean 5.96, max 11).

## Retrieval quality per category

Schema-qualified. Recall@6 counts the first 6 entries of the pack.

| Category | Tasks | Recall@6 | Recall in pack | All needed @6 | All needed in pack | Answer match | Refused | Ctx tokens |
|---|---|---|---|---|---|---|---|---|
| single_table_exact | 7 | 1.00 | 1.00 | 7 | 7 | 7 | 0 | 626 |
| synonym_abbreviation | 7 | 0.93 | 0.93 | 6 | 6 | 7 | 0 | 1115 |
| join_2hop | 7 | 1.00 | 1.00 | 7 | 7 | 7 | 0 | 709 |
| join_multihop (4-5 tables) | 7 | 0.76 | 0.83 | 2 | 4 | 7 | 0 | 1095 |
| aggregate_time | 7 | 0.93 | 0.96 | 6 | 6 | 6 | 1 | 794 |
| audit_log | 7 | 1.00 | 1.00 | 7 | 7 | 5 | 2 | 944 |
| cross_schema_ambiguity | 7 | 0.88 | 0.88 | 5 | 5 | 7 | 0 | 996 |
| legacy_no_fk | 7 | 1.00 | 1.00 | 7 | 7 | 5 | 2 | 666 |
| wide_table | 7 | 1.00 | 1.00 | 7 | 7 | 7 | 0 | 876 |
| vague_business | 7 | **0.31** | **0.45** | **0** | **1** | 6 | 1 | 909 |
| **All** | 70 | 0.85 | 0.88 | 54 | 57 | 64 | 6 | 873 |

Of the 129 needed relations, 54 ranked #1, 26 #2, 13 #3, 8 #4, 5 #5, 3 #6, 2 #7, 1 #8, 1 #10, and 16 were missing. Audit and log tables, a past weak spot, now retrieve perfectly. The weak spots are vague business questions and 4–5-hop joins.

**Query-term noise.** 31 of 70 questions had harmful rewrites: every year was fuzzy-matched to "2020" (18 tasks); short words were read as acronyms ("us" → "utm source", "ui" → "user id"); stems matched loosely ("leaving" → "leave", "exported" → "expected"). The "us" and "leaving" rewrites caused two of the misses.

## Answer accuracy (row level)

Each ground-truth query ran through `database_readonly_batch` and directly on a fresh read-only connection with the same `LIMIT`; values were normalised before comparison.

- **64 of 64 executed queries returned identical rows.**
- **6 of 70 were refused:** 2 by the estimated-row policy (`GROUP BY date_trunc(...)` estimated at ~300,000 rows for a 10–12-row answer), 2 by sensitive-column false positives (`ip_address`, `api_key_id`), 2 by the join policy (no declared foreign key between the tables).
- With raised EXPLAIN limits (a separate, labelled run) 66/70 ran, all matching.
- `benchmark_corpus.py` reports 0/70 recall and 59/70 answers on this corpus because it compares bare table names and row counts only; use the extended harness numbers.

## Cold start

| | Snapshot cache deleted | Cache present |
|---|---|---|
| Spawn and MCP initialize | 1,943 ms (p95 3,244) | 1,934 ms |
| First `task_context` | 552 ms (p95 717), includes building the 1,357-node graph | 87 ms |
| First batch | 13 ms | 236 ms (pool opened here) |
| Spawn to first answer | **2.49 s** (p95 3.98) | **2.26 s** |

Python and MCP start-up dominate (~1.9 s); the graph build is about 0.46 s.

## Blind authoring (28 tasks)

Method: questions were sent to `database_task_context` and the packs saved; SQL was written from the packs only and saved before any comparison; each ran once through `database_readonly_batch` and was compared with the ground truth. One attempt, no retries. Caveat: the author had read the database hand-over note beforehand, so treat this as an upper bound.

| Verdict | Count |
|---|---|
| Correct | **13** |
| Partially correct | 2 |
| Wrong | 8 |
| TableFox missing context | 4 |
| Policy refusal (correct SQL) | 1 |

The main cause of failure is **missing columns, not missing tables**: packs list key columns plus columns whose names match the question, so date, amount and status columns are often absent, and there are no column types. When a guessed column does not exist, the batch tool returns a generic error without PostgreSQL's message, so an assistant cannot correct itself.

## Safety

**Writes: 17 of 17 refused; the database was unchanged.** 16 were stopped by TableFox's guard before reaching PostgreSQL (INSERT, UPDATE, DELETE, DROP, TRUNCATE, CREATE TABLE, SELECT INTO, COPY, data-modifying CTE, multi-statement, SET ROLE, FOR UPDATE, lo_import, set_config, pg_read_file, nextval). `pg_sleep(30)` is not blocked and ran until the 15 s statement timeout.

**Sensitive columns.** The check matches result-column names only.

| Status | Columns |
|---|---|
| Blocked | email, phone, ssn, password_hash, api_key, date_of_birth, home_address, `SELECT *` on hr.employees |
| False positives | `ip_address`, `api_key_id` |
| **Returned** | national_id, iban, bank_iban, payer_iban, card_last4, tax_id, tax_code, salary_amt, payroll net_amt, key_prefix, client_ip |
| **Bypasses** | `email AS contact`, `lower(email)`, `ssn AS x` return raw values |

Until this is fixed, the reliable protection is the database itself: do not grant TableFox's read-only user access to tables or columns it should never read.

## Limitations of this benchmark

- One machine, warm OS cache, `fsync=off`. Networked production databases will be slower. Token figures are `ceil(chars/4)` estimates.
- The corpus, ground truth and blind test were produced by the same agent; ground-truth judgement calls are written into the SQL.
- The blind test allows one attempt; real assistants retry, but need useful error messages to recover.
- Recall treats views as distinct from their base tables.

## Optimisation suggestions

1. Return PostgreSQL's error text from `database_readonly_batch` (8 of 15 blind failures were guessed column names).
2. Include date, amount and status columns and column types in context packs (packs average 873 tokens against a 6,144-byte budget).
3. Fix query-term interpretation: no fuzzy matching of numbers, no acronym expansion of two-letter words, stricter stems.
4. Make the estimated-row policy aware of aggregates bounded by `LIMIT`.
5. Ship a starter synonym file for common business words (owe, churn, sellers, complaints).
6. Keep intermediate tables of foreign-key chains between ranked tables.
7. Sensitive data: add iban, national_id, tax_id, card_last4, salary markers; stop flagging `*_id`; classify by source column (via EXPLAIN VERBOSE output lists) so aliases and expressions cannot bypass the check.
8. Ignore string literals in the write-keyword guard (`action = 'delete'` is refused today).
9. Block `pg_sleep` explicitly.
10. Fix `benchmark_corpus.py` to compare schema-qualified names and actual rows.
11. Cold start: lazy imports would help more than graph-build tuning.
