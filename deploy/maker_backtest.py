"""Can MAKER execution revive the original BTC scalp? Taker is fee-killed; maker pays $0 fee
and captures the spread instead of paying it. But you only fill when price trades to your post.

Same signal as the original (buy the confirmed leader early). Two models:
  OPTIMISTIC : assume both legs fill at maker prices (buy at bid, sell at ask, 0 fee) -> upper bound.
  REALISTIC  : post buy at the leader's bid; it fills only if a later ask <= your bid (price dipped
               to you). Then post sell at the entry-ask; fills only if a later bid >= that. If the
               sell never fills by ttc<=10, force-flatten at the bid as a TAKER (fee). Models misses
               + adverse selection honestly.

Run on the VPS: python3 maker_backtest.py
"""
import glob, json, os, collections

DATA = "/opt/clawbot-collector/data/forward_collect" if os.path.isdir("/opt/clawbot-collector/data/forward_collect") \
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "forward_collect")
FEE = 0.07
REQ_STREAK = 3
ENTRY_MIN_ASK = 0.55
STAKE = 5.0

def f(x):
    try:
        return float(x) if x not in (None, "") else None
    except Exception:
        return None
def cl(d):
    v = d.get("chainlink")
    return f(v) if v is not None else f(d.get("chainlink_btcusd"))

def run(files, asset_filter, label):
    W = collections.defaultdict(list)
    for fn in files:
        for line in open(fn, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "snapshot":
                continue
            s = d.get("slug")
            if not s:
                continue
            a = d.get("asset") or s.split("-")[0]
            if asset_filter and a not in asset_filter:
                continue
            W[s].append(d)
    opt, real = [], []
    entries = fills_entry = fills_exit = 0
    for s, snaps in W.items():
        snaps = [x for x in snaps if f(x.get("ttc_s")) is not None]
        snaps.sort(key=lambda x: -f(x.get("ttc_s")))
        start = cl(snaps[0]) if snaps else None
        streak = 0; prev = None
        # find the entry snapshot index
        ei = None; side = None; e_bid = e_ask = None
        for i, d in enumerate(snaps):
            ttc = f(d.get("ttc_s")); up, dn = d.get("up") or {}, d.get("down") or {}
            ua, da = f(up.get("ask")), f(dn.get("ask"))
            if ua is None or da is None:
                continue
            leader = "UP" if ua > da else "DOWN"
            streak = streak + 1 if leader == prev else 1; prev = leader
            if not (60 < ttc < 280) or streak < REQ_STREAK:
                continue
            lask = ua if leader == "UP" else da
            if lask < ENTRY_MIN_ASK or not start:
                continue
            c = cl(d)
            if not c or not ((leader == "UP" and c > start) or (leader == "DOWN" and c < start)):
                continue
            sd = (d.get(leader.lower()) or {})
            e_bid, e_ask = f(sd.get("bid")), f(sd.get("ask"))
            if e_bid is None or e_ask is None:
                continue
            ei = i; side = leader; break
        if ei is None:
            continue
        entries += 1
        rest = snaps[ei + 1:]
        # OPTIMISTIC: bought at bid, sell at ask near exit (ttc<=10 or last)
        ex = next((d for d in rest if f(d.get("ttc_s")) <= 10), rest[-1] if rest else None)
        if ex:
            xa = f((ex.get(side.lower()) or {}).get("ask"))
            if xa:
                opt.append((STAKE / e_bid) * xa - STAKE)        # 0 fee, maker both sides
        # REALISTIC: entry fills if a later ask <= e_bid (dip to our bid)
        fi = None
        for j, d in enumerate(rest):
            a2 = f((d.get(side.lower()) or {}).get("ask"))
            if a2 is not None and a2 <= e_bid:
                fi = j; break
        if fi is None:
            continue                                            # entry never filled (price ran away) = miss
        fills_entry += 1
        sh = STAKE / e_bid
        sell_target = e_ask                                     # post sell at the entry ask (capture spread)
        after = rest[fi + 1:]
        sold = None
        for d in after:
            ttc = f(d.get("ttc_s")); b2 = f((d.get(side.lower()) or {}).get("bid"))
            if b2 is not None and b2 >= sell_target:
                sold = sell_target; fills_exit += 1; break
            if ttc is not None and ttc <= 10:
                sold = ("FLAT", b2); break
        if sold is None:
            d = after[-1] if after else None
            b2 = f((d.get(side.lower()) or {}).get("bid")) if d else None
            sold = ("FLAT", b2)
        if isinstance(sold, tuple):
            _, b2 = sold
            if b2 is None:
                continue
            fee = (sh * b2) * FEE * (1 - b2)                    # taker fee on forced exit only
            real.append(sh * b2 - STAKE - fee)
        else:
            real.append(sh * sold - STAKE)                     # maker exit, 0 fee
    def stat(p):
        if not p:
            return "n=0"
        wr = 100 * sum(1 for x in p if x > 0) / len(p)
        return f"n={len(p):>4} win={wr:>3.0f}% per={sum(p)/len(p):+.4f} total={sum(p):+.2f}"
    print(f"\n===== {label} =====")
    print(f"  signals: {entries}  entry-fill: {fills_entry} ({100*fills_entry/max(entries,1):.0f}%)  exit-maker-fill: {fills_exit}")
    print(f"  OPTIMISTIC maker (assume fills): {stat(opt)}")
    print(f"  REALISTIC  maker (modeled fills): {stat(real)}")

f5 = sorted(glob.glob(os.path.join(DATA, "poly_5m_*.jsonl")))[-3:]
run(f5, {"btc"}, "BTC 5-min — maker revival of the original scalp")
run(f5, {"bnb", "doge", "eth", "xrp"}, "ALT 5-min — maker")
