"""Emit a compact JSON status of the lock paper bot (run on the VPS, read-only).

Reads the lock ledger + service health and prints one JSON object on stdout.
Consumed by the home-PC refresh job that renders index.html for GitHub Pages.
"""
import json, os, collections, subprocess

DATA = "/opt/clawbot-collector/data"
LEDGER = os.path.join(DATA, "lock_ledger.jsonl")

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=6).stdout.strip()
    except Exception:
        return ""

entries = []
if os.path.exists(LEDGER):
    for line in open(LEDGER, encoding="utf-8", errors="ignore"):
        try:
            entries.append(json.loads(line))
        except Exception:
            pass

settled = [e for e in entries if e.get("status") in ("won", "lost")]
pend = [e for e in entries if e.get("status") == "pending"]

def agg(es):
    n = len(es)
    tot = sum((e.get("pnl") or 0) for e in es)
    win = (100 * sum(1 for e in es if e.get("status") == "won") / n) if n else 0
    return {"n": n, "pnl": round(tot, 2), "win": round(win), "per": round(tot / n, 3) if n else 0}

by = collections.defaultdict(lambda: [0, 0.0])
for e in settled:
    by[e.get("asset")][0] += 1
    by[e.get("asset")][1] += (e.get("pnl") or 0)

bw = [e for e in settled if e.get("fill_mode") == "bookwalk"]
bw_stat = agg(bw)
if bw:
    bw_stat["fillable"] = round(100 * sum(1 for e in bw if e.get("fill_full")) / len(bw))
    deps = sorted((e.get("depth_usd") or 0) for e in bw)
    slips = sorted(((e.get("fill_avg") or 0) - (e.get("entry_ask") or 0)) for e in bw)
    bw_stat["med_depth"] = round(deps[len(deps) // 2])
    bw_stat["med_slip"] = round(slips[len(slips) // 2], 3)

try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import portfolio
    PF = portfolio.compute(DATA)
except Exception as e:
    PF = {"error": str(e)[:90]}

def _loadj(p):
    out = []
    if os.path.exists(p):
        for l in open(p, encoding="utf-8", errors="ignore"):
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out
_nobs = _loadj(os.path.join(DATA, "negrisk_ledger.jsonl"))
_latest = {}
for _o in _nobs:
    _latest[_o.get("slug") or _o.get("title")] = _o          # latest obs per distinct arb
_arbs = sorted(_latest.values(), key=lambda x: -((x.get("net") if x.get("net") is not None else x.get("gap")) or 0))
NEG = {"n_obs": len(_nobs), "n_distinct": len(_latest),
       "recent": [{"type": o.get("type"), "title": (o.get("title") or "")[:40], "gap": o.get("gap"),
                   "net": o.get("net"), "units": o.get("units"), "fill_usd": o.get("fill_usd")}
                  for o in _arbs[:12]]}

_red = _loadj(os.path.join(DATA, "redemption_ledger.jsonl"))
_rdone = [e for e in _red if e.get("status") in ("won", "lost")]
_rpend = [e for e in _red if e.get("status") == "pending"]
RED = {"open": len(_rpend), "settled": len(_rdone),
       "pnl": round(sum(e.get("pnl") or 0 for e in _rdone), 2),
       "win": round(100 * sum(1 for e in _rdone if e.get("status") == "won") / len(_rdone)) if _rdone else 0,
       "positions": [{"side": e.get("side"), "ask": e.get("entry_ask"), "end": e.get("end"),
                      "title": (e.get("title") or "")[:42]}
                     for e in sorted(_rpend, key=lambda x: x.get("end") or "")[:12]]}

_wx = _loadj(os.path.join(DATA, "weather_ledger.jsonl"))
_wdone = [e for e in _wx if e.get("status") in ("won", "lost")]
_wpend = [e for e in _wx if e.get("status") == "pending"]
def _wxp(e):
    ask = e.get("entry_ask") or 0; st = e.get("stake") or 5.0; fc = e.get("fc_prob") or 0
    fee = st * 0.05 * (1 - ask)
    pwin = round(st / ask - st - fee, 2) if ask > 0 else 0.0      # paper profit if this bet hits
    ev = round(fc * pwin + (1 - fc) * (-st - fee), 3)            # forecast-expected value of the bet
    return {"city": e.get("city"), "date": e.get("date"), "bucket": e.get("bucket"),
            "forecast": e.get("forecast"), "ask": ask, "edge": e.get("edge"),
            "stake": round(st, 2), "pwin": pwin, "ev": ev}
_wall = [_wxp(e) for e in _wpend]
WX = {"open": len(_wpend), "settled": len(_wdone),
      "pnl": round(sum(e.get("pnl") or 0 for e in _wdone), 2),
      "win": round(100 * sum(1 for e in _wdone if e.get("status") == "won") / len(_wdone)) if _wdone else 0,
      "staked": round(sum(p["stake"] for p in _wall), 2),
      "pot_win": round(sum(p["pwin"] for p in _wall), 2),
      "exp_val": round(sum(p["ev"] for p in _wall), 2),
      "positions": _wall[:12]}

status = {
    "ts": sh("date -u +%Y-%m-%dT%H:%M:%SZ"),
    "portfolio": PF,
    "negrisk": NEG,
    "redemption": RED,
    "weather": WX,
    "load": sh("cut -d' ' -f1 /proc/loadavg"),
    "svc": {s: (sh("systemctl is-active " + s) or "unknown")
            for s in ["clawbot-collector", "clawbot-lock-paper"]},
    "depth_today": sh("grep -c '\"asks\":' " + os.path.join(DATA, "forward_collect",
                      "poly_5m_$(date -u +%Y%m%d).jsonl") + " 2>/dev/null"),
    "all": agg(settled),
    "pending": len(pend),
    "by_asset": {a: {"n": c, "pnl": round(p, 2)} for a, (c, p) in sorted(by.items())},
    "bookwalk": bw_stat,
    "recent": [{"asset": e.get("asset"), "side": e.get("side"), "ask": e.get("entry_ask"),
                "ttc": e.get("entry_ttc_s"), "move": e.get("move_pct"),
                "status": e.get("status"), "pnl": e.get("pnl"),
                "fill": e.get("fill_mode"), "slug": e.get("slug")}
               for e in entries[-15:]][::-1],
}
print(json.dumps(status))
