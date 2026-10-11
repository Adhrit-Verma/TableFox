r"""Score TableFox retrieval only (recall@6 and recall anywhere in the pack) for corpus files.

Uses the real service path (`task_context`, max_relations=6) against the database named by
DBMAP_ENV_FILE, without running any queries. Fast enough to compare retrieval changes.

    $env:DBMAP_ENV_FILE = "<repo>/.env.bench"
    python scripts/bench/eval_retrieval.py . scripts/bench/large_tasks.json scripts/bench/vague_holdout2.json [--verbose CATEGORY|all]

`vague_holdout.json` and `vague_holdout2.json` were written by separate agents that could not
see the retrieval code; holdout 2 was scored once, after tuning stopped.
"""
import json
import sys
from collections import defaultdict

repo = sys.argv[1]
sys.path.insert(0, f"{repo}/services/dbmap/src")
from dbmap.service import build_service

args = sys.argv[2:]
verbose = None
if "--verbose" in args:
    i = args.index("--verbose")
    verbose = args[i + 1]
    args = args[:i] + args[i + 2:]

service = build_service()
for path in args:
    with open(path, encoding="utf-8") as handle:
        tasks = json.load(handle)["tasks"]
    by_cat = defaultdict(lambda: [0, 0, 0, 0, 0])  # tasks, needed, hit@6, hit_pack, all@6
    per_task = []
    for task in tasks:
        ctx = service.task_context(task["question"], max_relations=6, max_bytes=6144, actor="eval")
        pack = [r["id"].replace("table:", "").replace("view:", "").replace("materialized_view:", "") for r in ctx["relations"]]
        needed = task["needed_relations"]
        top6 = pack[:6]
        hit6 = sum(n in top6 for n in needed)
        hitp = sum(n in pack for n in needed)
        per_task.append(hit6 / len(needed))
        c = by_cat[task["category"]]
        c[0] += 1; c[1] += len(needed); c[2] += hit6; c[3] += hitp; c[4] += hit6 == len(needed)
        if verbose and (verbose == "all" or verbose == task["category"]):
            mark = "OK " if hit6 == len(needed) else "MISS"
            print(f"{mark} {task['name']}: {task['question']}\n     need {needed}\n     pack {pack[:8]}\n     interp {ctx.get('interpreted')}")
    total = [sum(v[i] for v in by_cat.values()) for i in range(5)]
    print(f"\n== {path.split('/')[-1].split(chr(92))[-1]}")
    for cat, (n, need, h6, hp, a6) in sorted(by_cat.items()):
        print(f"  {cat:24s} tasks {n:2d}  recall@6 {h6/need:5.1%}  pack {hp/need:5.1%}  all@6 {a6}/{n}")
    n, need, h6, hp, a6 = total
    print(f"  {'ALL':24s} tasks {n:2d}  recall@6 {h6/need:5.1%}  pack {hp/need:5.1%}  all@6 {a6}/{n}")
    print(f"  mean per-task recall@6 {sum(per_task)/len(per_task):.1%}")
