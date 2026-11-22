"""Compare the rule score with the model's score on jobs that have both. Read-only: no model call, nothing is saved."""
import argparse
from statistics import mean

from . import db, resume, target
from .rules import load_facts, rule_score
from .target import title_check


def pearson(xs, ys):
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else 0.0


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1          # tied values share the average rank
        i = j + 1
    return out


def compare(pairs, cfg, res, facts, top=15, strong=70):
    """pairs: [(Job, model_score)]. Returns the rows (best rule score first) and the summary numbers."""
    rows = []
    for job, fit in pairs:
        r = rule_score(job, cfg, res, facts)
        rows.append({"job": job, "model": fit, "rule": r["score"], "deal_breakers": r["deal_breakers"], "parts": r["parts"]})
    rows.sort(key=lambda x: (-x["rule"], x["job"].company, x["job"].title))
    for i, x in enumerate(rows, 1):
        x["rank"] = i
    xs, ys = [x["rule"] for x in rows], [x["model"] for x in rows]
    strong_rows = [x for x in rows if x["model"] >= strong]
    return rows, {"n": len(rows), "pearson": pearson(xs, ys), "spearman": pearson(ranks(xs), ranks(ys)),
                  "strong": [(x["job"].company, x["model"], x["rule"], x["rank"]) for x in strong_rows],
                  "strong_in_top": all(x["rank"] <= top for x in strong_rows), "top": top}


def main(argv, backend=None):
    import yaml
    ap = argparse.ArgumentParser(prog="python -m jobfinder calibrate", description=__doc__)
    ap.add_argument("--db", default="jobs.db")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    res = resume.load_resume()
    cfg = target.with_target(resume.with_resume_keywords(cfg, res), res)
    con = db.connect(args.db)
    pairs = db.model_scored(con)
    tgt = cfg.get("target")
    current = [p for p in pairs if not tgt or title_check(p[0].title, tgt)[0]]      # what today's pre-filter still lets through
    for label, ps in (("all", pairs), ("title check passes today", current)):
        rows, s = compare(ps, cfg, res, load_facts(), args.top)
        print(f"\n=== {label}:{s['n']} jobs with both scores. Pearson {s['pearson']:.2f}, Spearman {s['spearman']:.2f}")
        if label == "all":
            print(f"{'rank':>4} {'rule':>5} {'model':>6}  company / title")
            for x in rows[:max(args.top, 20)]:
                print(f"{x['rank']:>4} {x['rule']:>5} {x['model']:>6}  {x['job'].company[:22]} / {x['job'].title[:46]}"
                      + ("  [deal-breaker]" if x["deal_breakers"] else ""))
        for x in rows:
            if x["model"] >= 50:
                print(f"model {x['model']}: {x['job'].company}: rule {x['rule']}, rank {x['rank']} of {s['n']}"
                      + ("" if x["model"] < 70 else f" ({'in' if x['rank'] <= args.top else 'NOT in'} the top {args.top})"))
    return 0
