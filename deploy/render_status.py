"""Render the portfolio status JSON (emit_status.py) into a tabbed mobile dashboard:
overview/resume on top, one tab per strategy module below.
Usage: python render_status.py status.json index.html
"""
import json, sys, html

src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))

def esc(x):
    return html.escape(str(x))

# ---------- RESUME (portfolio overview) ----------
pf = d.get("portfolio") or {}
def _mod_row(m):
    s = m.get("stat", {})
    ci = f"[{s.get('ci_lo',0):+.3f}, {s.get('ci_hi',0):+.3f}]"
    proven = '<span class="tag bw">PROVEN +EV</span>' if s.get("proven") else '<span class="tag est">building</span>'
    pcl = "pos" if s.get("pnl", 0) >= 0 else "neg"
    return (f"<tr><td><b>{esc(m.get('name'))}</b></td><td class='muted'>{esc(m.get('kind'))}</td>"
            f"<td>{s.get('n',0)}</td><td class='{pcl}'>${s.get('pnl',0):+.2f}</td>"
            f"<td>${s.get('ev',0):+.3f}</td><td class='muted'>{ci}</td><td>{proven}</td></tr>")
pf_rows = "".join(_mod_row(m) for m in pf.get("modules", [])) or '<tr><td colspan="7" class="muted">—</td></tr>'
real = pf.get("realized_pnl", 0); opp = pf.get("opportunity_pnl", 0)
real_cls = "pos" if real >= 0 else "neg"
proven = pf.get("proven_modules") or []
proven_txt = ", ".join(proven) if proven else "none proven yet — need more volume"

# ---------- LOCK panel ----------
alls = d.get("all", {}); bw = d.get("bookwalk", {})
pnl = alls.get("pnl", 0); pnl_cls = "pos" if pnl >= 0 else "neg"
asset_rows = "".join(
    f"<tr><td>{esc(a)}</td><td>{v.get('n')}</td><td class=\"{'pos' if v.get('pnl',0)>=0 else 'neg'}\">${v.get('pnl'):+.2f}</td></tr>"
    for a, v in d.get("by_asset", {}).items()) or '<tr><td colspan="3" class="muted">no settled entries yet today</td></tr>'
def rrow(e):
    st = e.get("status"); pc = "pos" if (e.get("pnl") or 0) > 0 else ("neg" if st == "lost" else "muted")
    pnl_txt = "—" if e.get("pnl") is None else f"${e.get('pnl'):+.3f}"
    fb = '<span class="tag bw">book-walk</span>' if e.get("fill") == "bookwalk" else (
        '<span class="tag est">est</span>' if e.get("fill") == "topask" else '')
    return (f"<tr><td>{esc(e.get('asset'))}</td><td>{esc(e.get('side'))}</td><td>{esc(e.get('ask'))}</td>"
            f"<td>{esc(e.get('ttc'))}s</td><td>{esc(e.get('move'))}%</td><td>{esc(st)} {fb}</td>"
            f"<td class=\"{pc}\">{pnl_txt}</td></tr>")
recent_rows = "".join(rrow(e) for e in d.get("recent", [])) or '<tr><td colspan="7" class="muted">waiting…</td></tr>'
bw_line = (f"{bw.get('n',0)} fills · win {bw.get('win',0)}% · ${bw.get('pnl',0):+.2f} · "
           f"fillable@$5 {bw.get('fillable','—')}% · slip {bw.get('med_slip','—')} · depth ${bw.get('med_depth','—')}") \
          if bw.get("n") else "no book-walk fills yet"

# ---------- NEGRISK panel ----------
neg = d.get("negrisk", {})
def nrow(a):
    net = "—" if a.get("net") is None else f"${a.get('net'):+.3f}"
    ncl = "pos" if (a.get("net") or a.get("gap") or 0) > 0 else "muted"
    return (f"<tr><td>{esc(a.get('type'))}</td><td>{esc(a.get('title'))}</td>"
            f"<td class='pos'>+{a.get('gap',0):.3f}</td><td class='{ncl}'>{net}</td>"
            f"<td>{esc(a.get('units'))}</td><td>{('$'+format(a.get('fill_usd'),',.0f')) if a.get('fill_usd') else '—'}</td></tr>")
neg_rows = "".join(nrow(a) for a in neg.get("recent", [])) or '<tr><td colspan="6" class="muted">no live arbs this cycle</td></tr>'

# ---------- REDEMPTION panel ----------
red = d.get("redemption", {})
def redrow(p):
    return (f"<tr><td>{esc(p.get('side'))}</td><td>{esc(p.get('title'))}</td>"
            f"<td>{esc(p.get('ask'))}</td><td class='muted'>{esc(p.get('end'))}</td></tr>")
red_rows = "".join(redrow(p) for p in red.get("positions", [])) or '<tr><td colspan="4" class="muted">no open positions</td></tr>'
red_pnl = red.get("pnl", 0); red_cls = "pos" if red_pnl >= 0 else "neg"

# ---------- WEATHER panel ----------
wx = d.get("weather", {})
def wxrow(p):
    return (f"<tr><td>{esc(p.get('city'))}</td><td>{esc(p.get('bucket'))}</td>"
            f"<td>{esc(p.get('forecast'))}°</td><td>{esc(p.get('ask'))}</td>"
            f"<td>${esc(p.get('stake'))}</td><td class='pos'>+${esc(p.get('pwin'))}</td>"
            f"<td class='muted'>{p.get('edge',0):+.2f}</td></tr>")
wx_rows = "".join(wxrow(p) for p in wx.get("positions", [])) or '<tr><td colspan="7" class="muted">no live forecast edges</td></tr>'
wx_pnl = wx.get("pnl", 0); wx_cls = "pos" if wx_pnl >= 0 else "neg"

svc = d.get("svc", {})
def badge(s):
    return f'<span class="dot {"g" if s=="active" else "r"}"></span>{esc(s)}'

page = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>CLAWBOT · portfolio</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;--g:#3fb950;--r:#f85149;--b:#58a6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:16px;max-width:780px;margin:auto}}
h1{{font-size:18px;margin:0 0 2px}}.sub{{color:var(--mut);font-size:13px;margin-bottom:16px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-bottom:14px}}
.big{{font-size:32px;font-weight:700;letter-spacing:-.5px}}.pos{{color:var(--g)}}.neg{{color:var(--r)}}.muted{{color:var(--mut)}}
.row{{display:flex;gap:18px;flex-wrap:wrap;align-items:baseline}}
.kv{{font-size:13px;color:var(--mut)}}.kv b{{color:var(--fg);font-size:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd)}}
th{{color:var(--mut);font-weight:600}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}}.dot.g{{background:var(--g)}}.dot.r{{background:var(--r)}}
.tag{{font-size:10px;padding:1px 6px;border-radius:6px;margin-left:4px}}.tag.bw{{background:#1f6feb33;color:var(--b)}}.tag.est{{background:#6e768133;color:var(--mut)}}
.foot{{color:var(--mut);font-size:12px;margin-top:6px}}
.tabs{{display:flex;gap:8px;margin:4px 0 14px}}
.tabs a{{padding:7px 15px;border:1px solid var(--bd);border-radius:8px;color:var(--mut);text-decoration:none;font-size:13px}}
.tabs a:hover{{color:var(--fg);border-color:var(--b)}}
.panel{{display:none}}.panel:target{{display:block}}
.wrap:not(:has(.panel:target)) #lock{{display:block}}
</style></head><body>
<h1>CLAWBOT · portfolio <span class="muted">(paper)</span></h1>
<div class="sub">stack of EV-positive strategies · no real money · updates ~every 5 min</div>

<div class="card">
  <div class="kv" style="margin-bottom:4px">RESUME — EV is the ruler (win more than you lose)</div>
  <div class="row"><div class="big {real_cls}">${real:+.2f}</div>
    <div class="kv">realized paper P&amp;L · {pf.get('realized_n',0)} trades</div>
    <div class="kv">+ NegRisk risk-free (if executed) <b>${opp:+.2f}</b></div></div>
  <table style="margin-top:10px"><tr><th>module</th><th>kind</th><th>n</th><th>P&amp;L</th><th>EV</th><th>95% CI</th><th></th></tr>{pf_rows}</table>
  <div class="foot">proven +EV (CI &gt; 0): {esc(proven_txt)}</div>
</div>

<div class="wrap">
<div class="tabs"><a href="#lock">▣ Lock</a><a href="#negrisk">▣ NegRisk</a><a href="#redemption">▣ Redemption</a><a href="#weather">▣ Weather</a></div>

<section class="panel" id="lock">
  <div class="card">
    <div class="kv" style="margin-bottom:6px">LOCK — settlement edge on alts (5-min)</div>
    <div class="big {pnl_cls}">${pnl:+.2f}</div>
    <div class="row" style="margin-top:8px">
      <div class="kv">win <b>{alls.get('win',0)}%</b></div>
      <div class="kv">settled <b>{alls.get('n',0)}</b></div>
      <div class="kv">per-trade <b>${alls.get('per',0):+.3f}</b></div>
      <div class="kv">pending <b>{d.get('pending',0)}</b></div>
    </div>
    <div class="foot">FILL QUALITY (book-walk): {esc(bw_line)}</div>
  </div>
  <div class="card"><table><tr><th>asset</th><th>n</th><th>P&amp;L</th></tr>{asset_rows}</table></div>
  <div class="card"><div class="kv" style="margin-bottom:6px">RECENT ENTRIES</div>
    <table><tr><th>asset</th><th>side</th><th>ask</th><th>ttc</th><th>move</th><th>status</th><th>P&amp;L</th></tr>{recent_rows}</table></div>
</section>

<section class="panel" id="negrisk">
  <div class="card">
    <div class="kv" style="margin-bottom:6px">NEGRISK — risk-free multi-outcome arbs (monitor, not executed)</div>
    <div class="row"><div class="kv">distinct arbs seen <b>{neg.get('n_distinct',0)}</b></div>
      <div class="kv">observations <b>{neg.get('n_obs',0)}</b></div></div>
  </div>
  <div class="card"><div class="kv" style="margin-bottom:6px">LIVE / RECENT ARBS</div>
    <table><tr><th>type</th><th>market</th><th>gap</th><th>net</th><th>baskets</th><th>fill$</th></tr>{neg_rows}</table>
    <div class="foot">gap = raw edge · net = after fees (buy-side) · baskets = fillable at top-of-book</div>
  </div>
</section>

<section class="panel" id="redemption">
  <div class="card">
    <div class="kv" style="margin-bottom:6px">REDEMPTION — buy near-certain favorites, hold to $1</div>
    <div class="row"><div class="big {red_cls}">${red_pnl:+.2f}</div>
      <div class="kv">settled <b>{red.get('settled',0)}</b></div>
      <div class="kv">win <b>{red.get('win',0)}%</b></div>
      <div class="kv">open <b>{red.get('open',0)}</b></div></div>
  </div>
  <div class="card"><div class="kv" style="margin-bottom:6px">OPEN POSITIONS (held to resolution)</div>
    <table><tr><th>side</th><th>market</th><th>entry</th><th>resolves</th></tr>{red_rows}</table></div>
</section>

<section class="panel" id="weather">
  <div class="card">
    <div class="kv" style="margin-bottom:6px">WEATHER — forecast-vs-price edge (Open-Meteo)</div>
    <div class="row"><div class="big {wx_cls}">${wx_pnl:+.2f}</div>
      <div class="kv">settled <b>{wx.get('settled',0)}</b></div>
      <div class="kv">win <b>{wx.get('win',0)}%</b></div></div>
    <div class="row" style="margin-top:8px">
      <div class="kv">open bets <b>{wx.get('open',0)}</b></div>
      <div class="kv">staked <b>${wx.get('staked',0):.0f}</b></div>
      <div class="kv">max win if all hit <b class="pos">+${wx.get('pot_win',0):.0f}</b></div>
      <div class="kv">expected value <b>${wx.get('exp_val',0):+.2f}</b></div>
    </div>
  </div>
  <div class="card"><div class="kv" style="margin-bottom:6px">OPEN FORECAST BETS · $5 paper each</div>
    <table><tr><th>city</th><th>bucket</th><th>fc</th><th>ask</th><th>stake</th><th>win if hit</th><th>edge</th></tr>{wx_rows}</table></div>
</section>
</div>

<div class="card">
  <div class="row">
    <div class="kv">collector {badge(svc.get('clawbot-collector','?'))}</div>
    <div class="kv">lock {badge(svc.get('clawbot-lock-paper','?'))}</div>
    <div class="kv">load <b>{esc(d.get('load','?'))}</b></div>
    <div class="kv">depth today <b>{esc(d.get('depth_today','?'))}</b></div>
  </div>
  <div class="foot">data as of {esc(d.get('ts','?'))}</div>
</div>
</body></html>"""

open(out, "w", encoding="utf-8").write(page)
print(f"wrote {out} ({len(page)} bytes)")
