"""Portfolio backbone: accumulate every module's settled trades into ONE permanent log,
and report each module's EV with a 95% CI. EV (win more than you lose) is the ruler.

Why a persistent log: module ledgers are light/rolling (e.g. lock_ledger = today only).
`accumulate()` appends each newly-settled trade to portfolio_trades.jsonl (deduped by a
stable key), so EV keeps tightening over days/weeks toward a verdict instead of resetting.

Module kinds:
  realized    = executed paper trades w/ settled P&L, read from the persistent log (lock, redemption).
  opportunity = risk-free arbs observed but NOT executed (negrisk), 'if executed' $, deduped per market.

Usage: python portfolio.py --accumulate   # append new settled trades (run each cycle)
       python portfolio.py                 # print report
       import portfolio; portfolio.compute(data_dir)   # dict for the dashboard
"""
import json, os, sys, math, time

def _load(path):
    out = []
    if os.path.exists(path):
        for l in open(path, encoding="utf-8", errors="ignore"):
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out

def _persist_path(data_dir):
    return os.path.join(data_dir, "portfolio_trades.jsonl")

# ---- harvest settled trades from each realized module's source ledger ----
def _lock_settled(data_dir):
    out = []
    for e in _load(os.path.join(data_dir, "lock_ledger.jsonl")):
        if e.get("status") in ("won", "lost") and e.get("pnl") is not None and e.get("slug"):
            out.append({"module": "lock", "key": "lock:" + e["slug"], "pnl": e["pnl"],
                        "won": e["status"] == "won"})
    return out

def _redemption_settled(data_dir):
    out = []
    for e in _load(os.path.join(data_dir, "redemption_ledger.jsonl")):
        if e.get("status") in ("won", "lost") and e.get("pnl") is not None and e.get("slug"):
            out.append({"module": "redemption", "key": "redemption:" + e["slug"], "pnl": e["pnl"],
                        "won": e["status"] == "won"})
    return out

def _weather_settled(data_dir):
    out = []
    for e in _load(os.path.join(data_dir, "weather_ledger.jsonl")):
        if e.get("status") in ("won", "lost") and e.get("pnl") is not None and e.get("slug"):
            out.append({"module": "weather", "key": "weather:" + e["slug"], "pnl": e["pnl"],
                        "won": e["status"] == "won"})
    return out

def _convergence_settled(data_dir):
    out = []
    for e in _load(os.path.join(data_dir, "convergence_ledger.jsonl")):
        if e.get("status") in ("won", "lost") and e.get("pnl") is not None and e.get("slug"):
            out.append({"module": "convergence", "key": "convergence:" + e["slug"], "pnl": e["pnl"],
                        "won": e["status"] == "won"})
    return out

def accumulate(data_dir):
    pp = _persist_path(data_dir)
    have = {r.get("key") for r in _load(pp)}
    fresh = [r for r in (_lock_settled(data_dir) + _redemption_settled(data_dir) + _weather_settled(data_dir)
                         + _convergence_settled(data_dir)) if r["key"] not in have]
    if fresh:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(pp, "a", encoding="utf-8") as fh:
            for r in fresh:
                r["ts"] = ts
                fh.write(json.dumps(r) + "\n")
    return len(fresh), len(have) + len(fresh)

# ---- stats ----
def _stats(pnls):
    n = len(pnls)
    if n == 0:
        return {"n": 0, "pnl": 0.0, "ev": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "win": 0, "proven": False}
    tot = sum(pnls); ev = tot / n
    sd = (sum((x - ev) ** 2 for x in pnls) / n) ** 0.5
    se = sd / math.sqrt(n)
    return {"n": n, "pnl": round(tot, 2), "ev": round(ev, 4),
            "ci_lo": round(ev - 1.96 * se, 4), "ci_hi": round(ev + 1.96 * se, 4),
            "win": round(100 * sum(1 for x in pnls if x > 0) / n), "proven": (ev - 1.96 * se) > 0}

def _negrisk_opp(data_dir):
    best = {}
    for o in _load(os.path.join(data_dir, "negrisk_ledger.jsonl")):
        if o.get("type") != "BUY":
            continue
        key = o.get("slug") or o.get("title")
        prof = (o.get("net") or 0) * (o.get("units") or 0)
        if prof <= 0 or not key:
            continue
        best[key] = max(best.get(key, 0.0), prof)
    return list(best.values())

def compute(data_dir):
    persist = _load(_persist_path(data_dir))
    by = {}
    for r in persist:
        by.setdefault(r.get("module"), []).append(r.get("pnl", 0))
    modules = [{"name": n, "kind": "realized", "stat": _stats(by.get(n, []))}      # always show the full stack
               for n in ("lock", "convergence", "redemption", "weather")]
    modules.append({"name": "negrisk", "kind": "opportunity", "stat": _stats(_negrisk_opp(data_dir))})
    realized = [m for m in modules if m["kind"] == "realized"]
    return {"modules": modules,
            "realized_pnl": round(sum(m["stat"]["pnl"] for m in realized), 2),
            "realized_n": sum(m["stat"]["n"] for m in realized),
            "opportunity_pnl": round(sum(m["stat"]["pnl"] for m in modules if m["kind"] == "opportunity"), 2),
            "proven_modules": [m["name"] for m in modules if m["stat"]["proven"]]}

def report(data_dir):
    P = compute(data_dir)
    print("PORTFOLIO (paper) — EV is the ruler, not win rate")
    print(f"{'module':11} {'kind':12} {'n':>5} {'P&L':>9} {'EV/trade':>9} {'EV 95% CI':>20} {'win%':>5}")
    for m in P["modules"]:
        s = m["stat"]; ci = f"[{s['ci_lo']:+.3f}, {s['ci_hi']:+.3f}]"
        print(f"{m['name']:11} {m['kind']:12} {s['n']:>5} {s['pnl']:>+9.2f} {s['ev']:>+9.3f} {ci:>20} {s['win']:>4}%"
              + (" PROVEN+EV" if s["proven"] else ""))
    print(f"\n  REALIZED paper P&L: ${P['realized_pnl']:+.2f} over {P['realized_n']} trades")
    print(f"  NEGRISK opportunity (if executed): ${P['opportunity_pnl']:+.2f}")
    print(f"  proven +EV: {P['proven_modules'] or 'none yet'}")

if __name__ == "__main__":
    d = "/opt/clawbot-collector/data" if os.path.isdir("/opt/clawbot-collector/data") \
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if "--accumulate" in sys.argv:
        n, tot = accumulate(d)
        print(f"accumulated {n} new settled trades (persistent total {tot})")
    else:
        report(d)
