# TableFox on a large database: benchmark results

Run date: 2026-10-11. Every number below comes from the raw JSON files in this folder. No task was dropped.

## After the fixes

The gaps measured below were then fixed in the backend. Each change has unit tests (pytest and ruff pass):

| Gap (first run) | Change |
|---|---|
| Sensitive-column check read result names only; aliases and expressions bypassed it | Every output value is traced through the `EXPLAIN (VERBOSE)` output lists to its source table column (`query_policy.plan_source_columns`) and refused **before execution**. Plans with inputs EXPLAIN does not print (`VALUES`, recursive CTEs) are checked conservatively. New markers: national/tax/passport IDs, IBAN, card number and last 4, PAN, salary, wage, payroll amounts, key prefix and hash. Names that describe a secret rather than hold it (`api_key_id`, `password_changed_at`) are no longer flagged. IP addresses are opt-in (`DBMAP_BLOCK_IP_ADDRESSES`); extra markers via `DBMAP_SENSITIVE_MARKERS` |
| Batch errors were a generic "Error executing tool" | Each statement runs in a savepoint; a failing one returns PostgreSQL's message and hint (`column "x" does not exist ... Hint: Perhaps you meant ...`) and the rest still run. Data errors (class 22, which can quote row values) return only their condition name; connection errors never echo connection details |
| Packs lacked date, amount and status columns and types | Every ranked relation lists its status, date/timestamp and amount columns; tables of 10 columns or fewer are listed in full; each column carries a short type and, for enums and `CHECK (col IN (...))` lists, its allowed values (`status text: draft|open|paid|overdue|void`). Materialized-view columns are now introspected (`information_schema` omits them) |
| Estimated-row policy refused `GROUP BY date_trunc(...)` | The row estimate is a warning, not a refusal, when TableFox's own `LIMIT` (at most `DBMAP_MAX_QUERY_ROWS`) bounds the result; the cost ceiling is unchanged |
| Write guard refused `action = 'delete'`; `pg_sleep` ran until the timeout | Keywords inside plain string literals and comments are ignored; ambiguous literals (backslashes, `E''`, dollar quotes, unterminated) keep the strict check. `pg_sleep`, `pg_sleep_for` and `pg_sleep_until` are blocked, and quoted function names (`"pg_read_file"(...)`, which bypassed the function list) are caught |
| 4–5-table joins at 80% recall@6 | Relations the question names in full (`invoices ... by region`) are joined through the shortest foreign-key chain that does not pass through a shared lookup table (`invoices -> currencies <- regions`), and those connecting tables are listed first. "issued" no longer expands to support issues |
| Optional | `DBMAP_TENANT_KEY` accepts `new,old` (MultiFernet); saved connections are re-encrypted with the new key at start-up. `benchmark_corpus.py` compares schema-qualified names and actual rows |

Security invariants are unchanged: read-only transactions, SELECT/WITH-only validation, the join policy and the read-only-role check.

### Before and after, same machine

Re-measured in a Linux container (4 vCPU, 15 GB RAM, PostgreSQL 16.15, Python 3.13) on the same database rebuilt from `scripts/bench/*.sql`. "Before" is commit `5b092e6` (after the retrieval update), "after" is this change. Latency here is not comparable with the Windows figures further down.

| Measure (70 tasks, 3 repeats) | Before | After |
|---|---|---|
| Recall@6, all 129 needed relations | 93.0% | **96.9%** |
| 4–5-table joins, recall@6 / tasks complete | 80.0% / 2 of 7 | **96.4% / 6 of 7** |
| Tasks with every needed table in the top 6 | 62 / 70 | **66 / 70** |
| Row-level answer match | 63 / 70 (7 refused by policy) | **67 / 70** (3 refused) |
| Policy refusals | 3 row-estimate, 2 sensitive false positives, 2 join | 1 cost, 2 join (no declared FK) |
| Warm median / p95 / p99, both calls | 48.9 / 248 / 603 ms | 51.5 / 237 / 533 ms |
| `task_context` median / p95 | 11.1 / 16.2 ms | 10.8 / 15.7 ms |
| Mean context tokens | 960 | **1,447** (median 1,560; largest pack 3.5 KB of the 6 KB budget, none trimmed) |
| Mean tokens, both calls | 1,887 | 2,395 (conventional path: 2,493) |
| Vague holdout 1 / holdout 2, recall@6 | 89.1% / 81.0% | 89.1% / 81.0% (no change) |

The packs are the trade-off: about 490 more tokens per question buy the columns the blind test showed were missing. `benchmark_corpus.py` on this corpus now reports 67/70 recall (anywhere in the pack) and 67/70 matching answers, instead of 0/70 and 59/70.

**Safety suite** (`safety.json`), extended with 8 more bypass shapes and 5 reads that must run:

| | Before | After |
|---|---|---|
| Writes refused, database unchanged | 17 / 17 (`pg_sleep` only by the 15 s timeout) | 17 / 17 (`pg_sleep` refused by the guard) |
| Sensitive reads refused (29 cases) | 10 (one of them only by the join policy) | **29** |
| Alias, expression, concat, union, CTE, subquery, lateral `VALUES`, scalar subquery, `to_jsonb(row)` | 1 of 12 | **12 of 12** |
| Reads that must run (`action = 'delete'`, `api_key_id`, counts and filters on email, a directory view) | 3 of 5 | **5 of 5** |
| Returned by design | `ip_address` was blocked, `client_ip` returned | both returned; blocked with `DBMAP_BLOCK_IP_ADDRESSES=true` |

**Blind authoring, re-authored** (`blind2-*.json`). Packs for the same 28 questions were generated with the new code. A separate agent that could read only the questions and packs (no repository, schema SQL, ground truth, database or earlier attempts) wrote one statement per task. Each ran once, and results were judged against the ground truth by mapping columns by value, then reviewing every non-identical result by hand. Extra columns, different labels and more rows than an arbitrary ground-truth `LIMIT` count as correct.

| Verdict | First run | After |
|---|---|---|
| Correct | 13 | **21** |
| Partially correct | 2 | 3 |
| Wrong | 8 | 2 |
| TableFox missing context | 4 | 0 (one wrong answer followed a table missing from the pack) |
| Policy refusal | 1 | 2 (both over the unchanged cost limit) |
| Query errors | 8 guessed column names | 0 |

The first run's author had read the database hand-over note; this one had not. With no errors on the first try, the "retry after a useful error message" case could not be measured here; it is covered by unit and integration tests. Remaining misses: an interpretation of "active" subscriptions, a materialized view whose filter the pack does not show, a pack without employee names, and the territory-versus-account reading of "sales region".

**Still open:**
- Sensitive columns are still recognised **by name**. Values in generically named columns are not detected: the blind test returned salary changes from `public.change_history.old_value`. Database grants remain the guarantee.
- Packs cost about 490 more tokens per question.
- Party tables used as ranked relations do not list name columns (`first_name`, `last_name`).
- When two tables are linked by several foreign keys, the pack shows only the one on the chosen path (`departments.head_employee_id`, not `employees.department_id`).
- Aggregates over a sensitive column (`count(distinct email)`) are refused, conservatively.

Files: `run-before-fixes.json` / `run-after-fixes.json`, `safety-before-fixes.json` / `safety.json`, `blind2-contexts.json` → `blind2-attempts.json` → `blind2-raw.json` → `blind2-authoring.json`. Reproduce with `eval_retrieval.py` as below, and `benchmark_large.py run --label after-fixes`, `safety` and `blind --prefix blind2`.

## Update: better retrieval for vague questions

The first run below found vague business questions to be the weakest category (31% recall@6). The retrieval layer (`services/dbmap/src/dbmap/retrieval.py`) was then improved with:
- a general business vocabulary ("owes" → invoices and balances, "complaining" → tickets, "leaving" → cancellations);
- a boost for transaction tables on ranking and trend questions, and for the party table on "who" questions;
- removal of word-matching noise (no fuzzy years, no lower-case two-letter acronyms, stricter typo matching);
- foreign-key-first join paths, with connecting tables listed before speculative matches.

| Measure (70-task corpus, `run-default.json` → `run-after-retrieval.json`) | Before | After |
|---|---|---|
| Vague business questions, mean recall@6 | 31% | **88%** |
| Vague business questions with every needed table in the top 6 | 0 / 7 | **5 / 7** |
| 4–5-table joins, mean recall@6 | 76% | 80% |
| All tasks, recall@6 (129 needed relations) | 84.5% | **93.0%** |
| Tasks with every needed table in the top 6 | 54 / 70 | **62 / 70** |
| Row-level answer match | 64 / 64 executed | 64 / 64 executed (same 6 policy refusals) |
| Warm median, both calls | 96 ms | 80 ms |
| `task_context` median | 16.8 ms | 14.8 ms |
| Mean context tokens | 873 | 960 (more of the right tables are listed) |

**Held-out checks.** To avoid tuning to the 7 vague questions above, two further sets of 40 vague questions were written by separate agents that could not see the retrieval code (`scripts/bench/vague_holdout.json`, `vague_holdout2.json`). Recall@6 on 40 questions each:

| Set | Before | After | Note |
|---|---|---|---|
| Holdout 1 | 60.9% (22/40 complete) | 75.0% (28/40) at its first check | Its misses were then studied for general patterns, so its later score (89.1%) is not independent |
| **Holdout 2** | **70.7% (24/40)** | **81.0% (30/40)** | Scored once, after tuning stopped. This is the honest generalisation figure |
| Original 38-table HR corpus (different schema, 14 tasks) | 70.8% (8/14) | 95.8% (13/14) | Regression check on another database |

Reproduce retrieval scores with `scripts/bench/eval_retrieval.py` (no queries are run). The sections below describe the first run; latency, safety and the blind test there were measured before this change. See [After the fixes](#after-the-fixes) for the re-run.

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
