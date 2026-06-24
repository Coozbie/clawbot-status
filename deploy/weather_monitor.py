"""Weather module (paper): forecast-vs-price edge on Polymarket temperature markets.

Temperature markets ("Highest temperature in <City> on <date>?") are negRisk buckets of
integer degrees. Free forecasts (Open-Meteo) give a better probability than the crowd,
and the price lags the forecast by HOURS (not a latency race). Each cycle: (1) score any
pending entry whose market resolved, (2) for each live temp market, model the forecast as
Normal(mu, sigma) over the buckets and paper-buy any bucket the forecast says is worth more
than its ask + margin. EV (not win rate) is the ruler; the portfolio backbone judges it.

Public Gamma + Open-Meteo APIs. Read-only, no orders. Logs to weather_ledger.jsonl.
"""
import urllib.request, json, os, re, time, math

UA = {"User-Agent": "clawbot-research/1.0"}
GAMMA = "https://gamma-api.polymarket.com"
GEO = "https://geocoding-api.open-meteo.com/v1/search"
FC = "https://api.open-meteo.com/v1/forecast"
STAKE = 5.0
FEE_RATE = 0.05                 # weather category taker rate
MARGIN = 0.06                   # forecast_prob must beat ask by this (fee + buffer) to enter
DATA = "/opt/clawbot-collector/data" if os.path.isdir("/opt/clawbot-collector/data") \
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEDGER = os.path.join(DATA, "weather_ledger.jsonl")
GEOCACHE = os.path.join(DATA, "weather_geo.json")

def get(url, tries=3):
    for _ in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read().decode())
        except Exception:
            time.sleep(0.4)
    return None

def fnum(x):
    try:
        return float(x)
    except Exception:
        return None

def parse(x, d):
    try:
        return json.loads(x) if isinstance(x, str) else (x or d)
    except Exception:
        return d

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bucket_prob(kind, val, mu, sigma):
    if kind == "below":
        return norm_cdf((val + 0.5 - mu) / sigma)
    if kind == "above":
        return 1 - norm_cdf((val - 0.5 - mu) / sigma)
    return norm_cdf((val + 0.5 - mu) / sigma) - norm_cdf((val - 0.5 - mu) / sigma)

def parse_bucket(title):
    """'23' -> ('eq',23); '22 or below' -> ('below',22); '30 or above' -> ('above',30)."""
    m = re.search(r"(-?\d+)", title or "")
    if not m:
        return None, None
    v = int(m.group(1)); t = (title or "").lower()
    if any(w in t for w in ["below", "lower", "under", "or less", "<"]):
        return "below", v
    if any(w in t for w in ["above", "higher", "over", "or more", ">", "+"]):
        return "above", v
    return "eq", v

_geo = {}
if os.path.exists(GEOCACHE):
    try:
        _geo = json.load(open(GEOCACHE))
    except Exception:
        _geo = {}
def geocode(city):
    if city in _geo:
        return _geo[city]
    g = get(f"{GEO}?name={urllib.parse.quote(city)}&count=1")
    r = (g.get("results") or [None])[0] if isinstance(g, dict) else None
    _geo[city] = ([r["latitude"], r["longitude"]] if r else None)
    try:
        json.dump(_geo, open(GEOCACHE, "w"))
    except Exception:
        pass
    return _geo[city]
import urllib.parse

def forecast_temp(lat, lon, date, unit, low=False):
    var = "temperature_2m_min" if low else "temperature_2m_max"
    f = get(f"{FC}?latitude={lat}&longitude={lon}&daily={var}&timezone=auto"
            f"&start_date={date}&end_date={date}&temperature_unit={unit}")
    d = (f or {}).get("daily", {})
    vals = d.get(var) or []
    return vals[0] if vals else None

def load_ledger():
    out = []
    if os.path.exists(LEDGER):
        for l in open(LEDGER, encoding="utf-8", errors="ignore"):
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out

def save_ledger(rows):
    with open(LEDGER, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

def score(entries):
    n = 0
    for e in [x for x in entries if x.get("status") == "pending"]:
        m = get(f"{GAMMA}/markets?slug={e['slug']}")
        m = (m or [None])[0] if isinstance(m, list) else None
        if not m or not m.get("closed"):
            continue
        prices = parse(m.get("outcomePrices"), [])
        if "1" not in prices and "1.0" not in prices:
            continue
        won = prices[0] in ("1", "1.0")          # we bought YES (outcome 0) of the bucket market
        p = e["entry_ask"]; fee = STAKE * FEE_RATE * (1 - p)
        e["status"] = "won" if won else "lost"
        e["pnl"] = round(((STAKE / p - STAKE) if won else -STAKE) - fee, 4)
        n += 1
    return n

def scan(entries):
    have = {e["slug"] for e in entries}
    events = []
    for pg in range(6):                              # paginate: temp markets have low 24h volume
        b = get(f"{GAMMA}/events?closed=false&active=true&limit=500&offset={pg*500}&order=volume24hr&ascending=false")
        if not isinstance(b, list) or not b:
            break
        events += b
    added = 0
    for e in events:
        title = e.get("title") or ""
        if "temperature" not in title.lower() or not e.get("negRisk"):
            continue
        cm = re.search(r"\bin (.+?) on ", title)
        if not cm:
            continue
        city = cm.group(1).strip()
        low = "lowest" in title.lower()
        legs = [m for m in (e.get("markets") or []) if not m.get("closed") and m.get("clobTokenIds")]
        if not legs:
            continue
        labels = " ".join((m.get("groupItemTitle") or "") for m in legs).lower()
        um = re.search(r"\d\s*°?\s*([cf])", labels)          # unit comes from the bucket labels, not the title
        unit = "fahrenheit" if (um and um.group(1) == "f") else "celsius"
        end = legs[0].get("endDate") or e.get("endDate")
        if not end:
            continue
        date = end[:10]
        ll = geocode(city)
        if not ll:
            continue
        mu = forecast_temp(ll[0], ll[1], date, unit, low)
        if mu is None:
            continue
        bvals = [int(m.group(1)) for m in (re.search(r"(-?\d+)", x.get("groupItemTitle") or "") for x in legs) if m]
        if bvals and not (min(bvals) - 25 <= mu <= max(bvals) + 25):   # unit/forecast sanity guard
            continue
        try:
            tt = time.mktime(time.strptime(date, "%Y-%m-%d"))
            lead = max(0.0, (tt - time.time()) / 86400)
        except Exception:
            lead = 1.0
        sigma = 0.8 + 0.6 * lead                 # forecast error grows with lead time
        for m in legs:
            slug = m.get("slug")
            if not slug or slug in have:
                continue
            kind, val = parse_bucket(m.get("groupItemTitle"))
            if kind is None:
                continue
            ask = fnum(m.get("bestAsk"))
            if ask is None or ask < 0.02 or ask > 0.95:
                continue
            prob = bucket_prob(kind, val, mu, sigma)
            if prob - ask < MARGIN:              # only enter when forecast clearly beats the price
                continue
            entries.append({"slug": slug, "city": city, "date": date, "bucket": m.get("groupItemTitle"),
                            "forecast": round(mu, 1), "fc_prob": round(prob, 3), "entry_ask": round(ask, 4),
                            "edge": round(prob - ask, 3), "stake": STAKE,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "status": "pending", "pnl": None})
            have.add(slug); added += 1
    return added

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    ent = load_ledger()
    s = score(ent)
    a = scan(ent)
    save_ledger(ent)
    pend = [e for e in ent if e["status"] == "pending"]
    done = [e for e in ent if e["status"] in ("won", "lost")]
    pnl = sum(e.get("pnl") or 0 for e in done)
    wr = (100 * sum(1 for e in done if e["status"] == "won") / len(done)) if done else 0
    print(f"{time.strftime('%H:%M:%S')} weather: +{a} new, {s} settled | open={len(pend)} settled={len(done)} "
          f"win={wr:.0f}% P&L=${pnl:+.2f}")
    for e in pend[-6:]:
        print(f"   {e['city']} {e['date']} '{e['bucket']}' fc={e['forecast']} P={e['fc_prob']} ask={e['entry_ask']} edge=+{e['edge']}")
