"""CONVERGENCE module (paper) — Lucas's original edge, rebuilt clean and causal.

Buy the CHEAP side (ask 0.25-0.50) the on-chain move favors, ONLY when it's confirmed rising
(filters the 'instant reversal' entries), then CASH OUT into the book's convergence: sell at the
bid when it rises +0.12 (take profit), else flatten near close. NO tight stop (it cuts recoverable
dips — backtested harmful). The edge is the book lagging Chainlink, captured by selling early —
NOT the settlement outcome (which is why HOLD_TO_SETTLEMENT killed the original bot).

Validated 2026-06-24 on 5 days collected BTC: confirm-only 81% win, +$1.30/trade, +$89 (69 trades).
Causal (real bid path, no look-ahead). Read-only paper. Logs convergence_ledger.jsonl.
"""
from __future__ import annotations
import glob, json, os, sys, time, collections

ASSETS = {"btc"}              # validated on BTC; book-lag-Chainlink convergence
LO, HI = 0.25, 0.50          # buy cheap
MOVE_THR = 0.0003            # Chainlink must have moved this much = a direction signal
TP = 0.12                    # cash out when the bid has risen this much
RISE = 0.005                 # confirmation: the side's mid must be rising (momentum)
FEE_RATE = 0.07              # Polymarket crypto taker fee, both legs
STAKE = 5.0
HERE = os.path.dirname(os.path.abspath(__file__))

def _data_dir():
    for c in ["/opt/clawbot-collector/data/forward_collect", os.path.join(HERE, "data", "forward_collect")]:
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
def mid(sd):
    b, a = f(sd.get("bid")), f(sd.get("ask"))
    return (b + a) / 2 if b is not None and a is not None else None

def evaluate(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, "poly_5m_*.jsonl")))[-2:]
    W = collections.defaultdict(list)
    for fn in files:
        for line in open(fn, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "snapshot":
                continue
            a = d.get("asset") or (d.get("slug") or "").split("-")[0]
            if a in ASSETS and d.get("slug"):
                W[d["slug"]].append(d)
    entries = []
    for slug, snaps in W.items():
        snaps = [x for x in snaps if f(x.get("ttc_s")) is not None]
        snaps.sort(key=lambda x: -f(x.get("ttc_s")))
        start = cl(snaps[0]) if snaps else None
        if not start:
            continue
        prevmid = {}; pos = None
        for d in snaps:
            ttc = f(d.get("ttc_s")); c = cl(d)
            up, dn = d.get("up") or {}, d.get("down") or {}
            if pos is None and c is not None and 15 < ttc < 280:
                mv = c / start - 1.0
                if abs(mv) >= MOVE_THR:
                    side = "up" if mv > 0 else "down"
                    sd = up if side == "up" else dn
                    ask = f(sd.get("ask")); m = mid(sd)
                    rising = prevmid.get(side) is not None and m is not None and m > prevmid[side] + RISE
                    if ask is not None and LO <= ask <= HI and rising:
                        pos = {"side": side, "ask": ask, "ttc": round(ttc, 1), "move": round(mv * 100, 4)}
            elif pos is not None:
                sd = up if pos["side"] == "up" else dn
                bid = f(sd.get("bid"))
                if bid is not None:
                    reason = "tp" if bid >= pos["ask"] + TP else ("flat" if ttc <= 15 else None)
                    if reason:
                        sh = STAKE / pos["ask"]
                        fee = STAKE * FEE_RATE * (1 - pos["ask"]) + (sh * bid) * FEE_RATE * (1 - bid)
                        entries.append({"slug": slug, "asset": "btc", "side": pos["side"].upper(),
                                        "entry_ask": pos["ask"], "exit_bid": round(bid, 4), "reason": reason,
                                        "entry_ttc_s": pos["ttc"], "move_pct": pos["move"],
                                        "status": "won" if (sh * bid - STAKE - fee) > 0 else "lost",
                                        "pnl": round(sh * bid - STAKE - fee, 4)})
                        pos = None
            for nm, sd in (("up", up), ("down", dn)):
                m = mid(sd)
                if m is not None:
                    prevmid[nm] = m
        if pos is not None:                                  # window ended holding -> flatten at last bid
            sd = (snaps[-1].get(pos["side"]) or {})
            bid = f(sd.get("bid"))
            if bid is not None:
                sh = STAKE / pos["ask"]
                fee = STAKE * FEE_RATE * (1 - pos["ask"]) + (sh * bid) * FEE_RATE * (1 - bid)
                entries.append({"slug": slug, "asset": "btc", "side": pos["side"].upper(),
                                "entry_ask": pos["ask"], "exit_bid": round(bid, 4), "reason": "flat",
                                "entry_ttc_s": pos["ttc"], "move_pct": pos["move"],
                                "status": "won" if (sh * bid - STAKE - fee) > 0 else "lost",
                                "pnl": round(sh * bid - STAKE - fee, 4)})
    return entries

def report(entries, ledger):
    with open(ledger, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    n = len(entries)
    print(f"CONVERGENCE ledger -> {ledger}")
    if n:
        tot = sum(e["pnl"] for e in entries); wr = 100 * sum(1 for e in entries if e["status"] == "won") / n
        tp = sum(1 for e in entries if e["reason"] == "tp")
        print(f"  trades: {n}  win={wr:.0f}%  TOTAL=${tot:+.2f}  per-trade=${tot/n:+.3f}  (tp={tp} flat={n-tp})")
    else:
        print("  no trades yet")

def main():
    data_dir = _data_dir()
    ledger = os.path.join(os.path.dirname(data_dir.rstrip("/")), "convergence_ledger.jsonl") \
        if os.path.isdir(data_dir) else os.path.join(HERE, "convergence_ledger.jsonl")
    if "--watch" in sys.argv:
        while True:
            try:
                report(evaluate(data_dir), ledger)
            except Exception as e:
                print(f"eval error: {e}")
            sys.stdout.flush(); time.sleep(300)
    else:
        report(evaluate(data_dir), ledger)

if __name__ == "__main__":
    main()
