"""Lock-logic PAPER decider — the validated causal edge, live.

Reuses the collector's live feed (poly_5m_<date>.jsonl: per-window book asks + on-chain
chainlink + ttc, all assets, ~12s cadence). For each open ALT 5-min window (bnb/doge/eth/
xrp), near close (ttc<90s), if on-chain Chainlink moved >0.1% from window start, buy the
implied-winner side IF its ask is in [0.82,0.95]; hold to settlement. Logs paper entries to
lock_ledger.jsonl and scores them when the collector records the settlement.

CAUSAL by construction (decision uses only data <= the decision snapshot; winner used only
for scoring). NO simulate, NO look-ahead. Run once (single pass) or loop with --watch.
Usage: python lock_paper.py [--watch] [data_dir]
"""
from __future__ import annotations
import glob, json, os, sys, time, collections

ALT = {"bnb", "doge", "eth", "xrp"}
LOCKSET = ALT | {"btc"}      # btc added 2026-06-24 to test a tight-band lock (its book is more efficient)
LO, HI = 0.82, 0.95          # alt ask band where the book underprices the locked winner
BTC_LO, BTC_HI = 0.90, 0.95  # btc: winner only reliably underpriced at >=0.90 (0.82-0.90 reverses too often)
MOVE = 0.001                 # 0.1% chainlink move from start = decisive
TTC_MAX = 90.0               # only act in the final 90s
STAKE = 5.0
SLIP = 0.01                  # assumed entry slippage for paper P&L
FEE_RATE = 0.07             # Polymarket CROSS taker fee rate (crypto category); fee = shares*rate*p*(1-p)

HERE = os.path.dirname(os.path.abspath(__file__))
def _data_dir():
    for c in ["/opt/clawbot-collector/data/forward_collect",
              os.path.join(HERE, "data", "forward_collect")]:
        if os.path.isdir(c):
            return c
    return os.path.join(HERE, "data", "forward_collect")

def f(x):
    try:
        return float(x) if x not in (None, "") else None
    except Exception:
        return None
def cl(d):
    v = d.get("chainlink")
    return f(v) if v is not None else f(d.get("chainlink_btcusd"))

def walk_fill(levels, stake):
    """Sweep ask levels [[price, size_shares], ...] (best-first) to BUY `stake`
    USD of shares. Returns (avg_price, usd_filled, fully_filled) or None.
    Cost at a level = price*size; shares bought = usd/price. This is the realistic
    marketable-buy fill — captures depth (thin books partial-fill) and spread."""
    if not levels:
        return None
    spent = shares = 0.0
    for lvl in levels:
        p = f(lvl[0]); s = f(lvl[1]) if len(lvl) > 1 else None
        if not p or not s:
            continue
        take = min(p * s, stake - spent)      # USD we consume at this level
        if take <= 0:
            break
        shares += take / p
        spent += take
        if spent >= stake - 1e-9:
            break
    if shares <= 0:
        return None
    return (spent / shares, spent, spent >= stake - 1e-9)

def evaluate(data_dir):
    W = collections.defaultdict(lambda: {"snaps": [], "winner": None, "asset": None, "ws": None})
    # lightest viable set on the t3.micro: today's snapshots + the settlements file
    # (settle_backfill writes winners to the alphabetically-last poly*5m file)
    allf = sorted(glob.glob(os.path.join(data_dir, "poly*5m_*.jsonl")))
    today = sorted(glob.glob(os.path.join(data_dir, "poly_5m_*.jsonl")))
    files = list(dict.fromkeys(([today[-1]] if today else []) + (allf[-1:] if allf else [])))
    for fn in files:
        for line in open(fn, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            s = d.get("slug")
            if not s or "-updown-15m-" in s:
                continue
            asset = d.get("asset") or s.split("-")[0]
            if asset not in LOCKSET:
                continue
            if d.get("type") == "snapshot":
                W[s]["snaps"].append(d)
                if W[s]["asset"] is None:
                    W[s]["asset"] = asset
                if W[s]["ws"] is None:
                    W[s]["ws"] = d.get("window_start")
            elif d.get("type") == "settlement" and d.get("winner") in ("UP", "DOWN"):
                W[s]["winner"] = d.get("winner")

    entries = []
    for slug, w in W.items():
        snaps = [s for s in w["snaps"] if f(s.get("ttc_s")) is not None and cl(s) is not None]
        if len(snaps) < 2:
            continue
        snaps.sort(key=lambda s: -f(s.get("ttc_s")))   # ttc descending = time order
        start_cl = cl(snaps[0])
        if not start_cl:
            continue
        for s in snaps:
            ttc = f(s.get("ttc_s"))
            if ttc is None or ttc > TTC_MAX or ttc < 2:
                continue
            move = cl(s) / start_cl - 1.0
            if abs(move) < MOVE:
                continue
            side = "UP" if move > 0 else "DOWN"
            ask = f((s.get(side.lower()) or {}).get("ask"))
            lo, hi = (BTC_LO, BTC_HI) if w["asset"] == "btc" else (LO, HI)
            if ask is None or not (lo <= ask <= hi):
                continue
            levels = (s.get(side.lower()) or {}).get("asks")   # banked depth (final-90s alts)
            depth_usd = round(sum(f(l[0]) * f(l[1]) for l in levels
                                  if len(l) > 1 and f(l[0]) and f(l[1])), 2) if levels else None
            e = {"slug": slug, "asset": w["asset"], "side": side, "entry_ask": ask,
                 "entry_ttc_s": round(ttc, 1), "move_pct": round(move * 100, 4),
                 "window_start": w["ws"], "depth_usd": depth_usd}
            win = w["winner"]
            if win in ("UP", "DOWN"):
                won = side == win
                walk = walk_fill(levels, STAKE)
                if walk:                                        # honest book-walk fill
                    avg, usd, full = walk
                    fee = usd * FEE_RATE * (1.0 - avg)           # shares*rate*p*(1-p) = usd*rate*(1-p)
                    e["fill_mode"] = "bookwalk"; e["fill_avg"] = round(avg, 4)
                    e["fill_usd"] = round(usd, 2); e["fill_full"] = full; e["fee"] = round(fee, 4)
                    e["pnl"] = round((usd / avg if won else 0.0) - usd - fee, 4)
                else:                                           # no depth banked -> legacy estimate
                    fill = ask + SLIP
                    fee = STAKE * FEE_RATE * (1.0 - fill)
                    e["fill_mode"] = "topask"; e["fill_avg"] = round(fill, 4); e["fee"] = round(fee, 4)
                    e["pnl"] = round((STAKE / fill if won else 0.0) - STAKE - fee, 4)
                e["status"] = "won" if won else "lost"
                e["winner"] = win
            else:
                e["status"] = "pending"
                e["pnl"] = None
            entries.append(e)
            break
    return entries

def report(entries, ledger_path):
    with open(ledger_path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    settled = [e for e in entries if e["status"] in ("won", "lost")]
    pend = [e for e in entries if e["status"] == "pending"]
    print(f"LOCK PAPER ledger -> {ledger_path}")
    print(f"  entries: {len(entries)}  (settled {len(settled)}, pending {len(pend)})")
    if settled:
        n = len(settled); tot = sum(e["pnl"] for e in settled)
        wr = 100 * sum(1 for e in settled if e["status"] == "won") / n
        print(f"  SETTLED: win={wr:.0f}%  TOTAL=${tot:+.2f}  per-trade=${tot/n:+.3f}")
        by = collections.defaultdict(lambda: [0, 0.0])
        for e in settled:
            by[e["asset"]][0] += 1; by[e["asset"]][1] += e["pnl"]
        print("  by asset: " + "  ".join(f"{a}:{c}tr ${p:+.2f}" for a, (c, p) in sorted(by.items())))
        bw = [e for e in settled if e.get("fill_mode") == "bookwalk"]
        ta = [e for e in settled if e.get("fill_mode") == "topask"]
        if bw:
            med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0.0
            full = 100 * sum(1 for e in bw if e.get("fill_full")) / len(bw)
            slips = [e["fill_avg"] - e["entry_ask"] for e in bw]
            deps = [e["depth_usd"] for e in bw if e.get("depth_usd") is not None]
            d50 = 100 * sum(1 for d in deps if d >= 50) / len(deps) if deps else 0.0
            bt = sum(e["pnl"] for e in bw); bwr = 100 * sum(1 for e in bw if e["status"] == "won") / len(bw)
            print(f"  FILL(bookwalk n={len(bw)}): win={bwr:.0f}%  ${bt:+.2f}  per=${bt/len(bw):+.3f}"
                  f"  | fillable@${STAKE:.0f}={full:.0f}%  med_slip={med(slips):+.3f}"
                  f"  med_depth=${med(deps):.0f}  depth>=$50={d50:.0f}%")
        if ta:
            print(f"  (legacy top-ask estimate, no depth banked: {len(ta)} entries)")
    if pend:
        print("  pending: " + "  ".join(f"{e['asset']}@{e['entry_ask']:.2f}({e['side']})" for e in pend[:8]))

def main():
    watch = "--watch" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    data_dir = args[0] if args else _data_dir()
    ledger = os.path.join(os.path.dirname(data_dir.rstrip("/")), "lock_ledger.jsonl") \
        if os.path.isdir(data_dir) else os.path.join(HERE, "lock_ledger.jsonl")
    if not watch:
        report(evaluate(data_dir), ledger)
        return
    while True:
        try:
            report(evaluate(data_dir), ledger)
        except Exception as e:
            print(f"eval error: {e}")
        sys.stdout.flush()
        time.sleep(300)            # windows settle every 5 min; no need to recompute faster

if __name__ == "__main__":
    main()
