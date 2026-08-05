"""Generate mapping_dashboard.html - visual diagnostics for the DTS->MTS->CBO mapping.

Reads the reconciliation outputs (recon_*.csv, cbo_comparison.csv, data/ caches)
and writes a self-contained HTML dashboard with inline-JS SVG charts.
Rerun after any reconciliation refresh: python build_charts.py
"""

import json

import pandas as pd


def fy(p): return p.year + (1 if p.month >= 10 else 0)


def deficit_series():
    d = pd.read_csv("recon_deficit.csv")
    return {
        "months": d["month"].tolist(),
        "dts": (d["dts_implied_deficit"] / 1e3).round(1).tolist(),
        "mts": (d["mts_deficit"] / 1e3).round(1).tolist(),
        "wedge": (d["wedge"] / 1e3).round(1).tolist(),
    }


def attribution_series():
    d = pd.read_csv("recon_deficit.csv").set_index("month")
    months = d.index.tolist()

    dts = pd.read_csv("data/dts_table2_raw.csv")
    dts = dts[dts.account_type == "Treasury General Account (TGA)"].copy()
    dts["month"] = pd.to_datetime(dts["record_date"]).dt.to_period("M").astype(str)
    dts["amt"] = pd.to_numeric(dts["transaction_today_amt"], errors="coerce").fillna(0) / 1e3
    cash_int = dts[dts.transaction_catg == "Interest on Treasury Securities"].groupby("month")["amt"].sum()

    t5 = pd.read_csv("data/mts_table5_raw.csv")
    t5["month"] = pd.to_datetime(t5["record_date"]).dt.to_period("M").astype(str)
    t5["amt"] = pd.to_numeric(t5["current_month_net_outly_amt"], errors="coerce") / 1e9
    def row(name): return t5[t5.classification_desc.eq(name)].groupby("month")["amt"].sum()
    net_accrual = row("Total--Interest on the Public Debt") \
        + row("Total--Interest Received by Trust Funds") + row("Other Interest")
    int_wedge = (cash_int - net_accrual).reindex(months).fillna(0)

    out = pd.read_csv("recon_outlays.csv")
    ed_wedge = (out[out.bucket.eq("ed")].set_index("month")["wedge"] / 1e3).reindex(months).fillna(0)

    total = d["wedge"] / 1e3
    resid = total - int_wedge.values - ed_wedge.values
    return {
        "months": months,
        "total": total.cumsum().round(1).tolist(),
        "interest": int_wedge.cumsum().round(1).tolist(),
        "ed": ed_wedge.cumsum().round(1).tolist(),
        "residual": resid.cumsum().round(1).tolist(),
    }


def receipts_facets():
    r = pd.read_csv("recon_receipts.csv")
    labels = {
        "income_payroll": "Individual income + payroll",
        "corporate": "Corporate income",
        "excise": "Excise",
        "customs": "Customs duties",
        "estate_gift": "Estate & gift",
        "misc_receipts": "Miscellaneous receipts",
    }
    facets = []
    for b, label in labels.items():
        g = r[r.bucket.eq(b)]
        facets.append({
            "key": b, "label": label,
            "months": g["month"].tolist(),
            "dts": (g["dts"] / 1e3).round(1).tolist(),
            "mts": (g["mts"] / 1e3).round(1).tolist(),
        })
    return facets


def outlay_wedges():
    o = pd.read_csv("recon_outlays.csv")
    notes = {
        "treasury": "accrued interest sits in MTS, cash coupons in DTS",
        "residual_all_other": "DTS salary + unclassified pools vs MTS undistributed offsets",
        "dod": "DoD civilian payroll and classified flows outside DoD lines",
        "ed": "credit-reform reestimates are non-cash MTS entries",
        "hhs": "MTS nets premiums withheld from Social Security checks",
        "ssa": "DTS benefits are net of Medicare premium withholding",
        "va": "VA payroll flows through the pooled salary line",
        "dhs": "payroll pool + 2026 tariff refunds in CBP withdrawals",
        "opm": "employer-share retirement never touches the TGA",
        "iap": "payroll pool; AID wind-down",
        "doj": "payroll pool (FBI/BOP salaries)",
        "odcp": "military retirement timing",
        "usda": "clean", "hud": "FHA credit flows", "corps": "trust-fund timing",
        "dos": "payroll pool", "dot": "clean", "doc": "payroll pool",
        "doi": "royalties booked as undistributed offsets", "dol": "clean", "doe": "clean",
    }
    names = {
        "treasury": "Treasury", "residual_all_other": "All other (pooled)", "dod": "Defense",
        "ed": "Education", "hhs": "HHS", "ssa": "Social Security Admin", "va": "Veterans Affairs",
        "dhs": "Homeland Security", "opm": "OPM", "iap": "International", "doj": "Justice",
        "odcp": "Military retirement", "usda": "Agriculture", "hud": "HUD",
        "corps": "Corps of Engineers", "dos": "State", "dot": "Transportation",
        "doc": "Commerce", "doi": "Interior", "dol": "Labor", "doe": "Energy",
    }
    g = (o.groupby("bucket")
           .agg(dts=("dts", "sum"), mts=("mts", "sum"), wedge=("wedge", "sum"))
           .sort_values("wedge"))
    return [
        {"bucket": names.get(b, b), "wedge": round(r.wedge / 1e3, 1), "dts": round(r.dts / 1e3),
         "mts": round(r.mts / 1e3), "note": notes.get(b, "")}
        for b, r in g.iterrows()
    ]


def mirror_series():
    o = pd.read_csv("recon_outlays.csv")
    ssa = o[o.bucket.eq("ssa")].set_index("month")["wedge"] / 1e3
    hhs = o[o.bucket.eq("hhs")].set_index("month")["wedge"] / 1e3
    months = ssa.index.tolist()
    return {"months": months, "ssa": ssa.round(1).tolist(),
            "hhs": hhs.reindex(months).round(1).tolist()}


def cbo_rows():
    c = pd.read_csv("cbo_comparison.csv")
    labels = {
        "income_payroll": "Income + payroll taxes", "corporate": "Corporate income",
        "excise": "Excise", "estate_gift": "Estate & gift", "customs": "Customs duties",
        "misc_receipts": "Misc. receipts", "total_receipts": "Total receipts",
        "total_deficit": "Total deficit", "social_security": "Social Security",
        "medicare_net": "Medicare (net)", "medicaid": "Medicaid", "net_interest": "Net interest",
    }
    rows = []
    for _, r in c[c.fy.eq(2025)].iterrows():
        rows.append({
            "category": labels.get(r.category, r.category),
            "dts_pct": round(r.dts_vs_cbo_pct, 1),
            "mts_pct": None if pd.isna(r.mts_vs_cbo_pct) else round(r.mts_vs_cbo_pct, 1),
            "dts": round(r.dts_built / 1e3), "cbo": round(r.cbo / 1e3),
        })
    return rows


CBO_LABELS = {
    "income_payroll": "Income + payroll taxes", "corporate": "Corporate income",
    "excise": "Excise", "estate_gift": "Estate & gift", "customs": "Customs duties",
    "misc_receipts": "Misc. receipts", "total_receipts": "Total receipts",
    "total_deficit": "Total deficit", "social_security": "Social Security",
    "medicare_net": "Medicare (net)", "medicaid": "Medicaid", "net_interest": "Net interest",
}


def cbo_pairs():
    """DTS-built vs CBO historical actuals, FY2024 + FY2025, majors/minors panels."""
    c = pd.read_csv("cbo_comparison.csv")
    out = {}
    for fy in [2024, 2025]:
        majors, minors = [], []
        for _, r in c[c.fy.eq(fy)].iterrows():
            row = {"category": CBO_LABELS.get(r.category, r.category),
                   "dts": round(r.dts_built / 1e3), "cbo": round(r.cbo / 1e3),
                   "pct": round(r.dts_vs_cbo_pct, 1)}
            (majors if abs(r.cbo) >= 300000 else minors).append(row)
        out[str(fy)] = {"majors": majors, "minors": minors}
    return out


def fy26_series():
    s = pd.read_csv("fy2026_monthly_split.csv")
    return {
        "months": s["month"].tolist(),
        "proj": (s["proj_deficit"] / 1e3).round(1).tolist(),
        "actual": [None if pd.isna(v) else round(v / 1e3, 1) for v in s["actual_mts_deficit"]],
    }


DATA = {
    "deficit": deficit_series(),
    "attrib": attribution_series(),
    "receipts": receipts_facets(),
    "outlays": outlay_wedges(),
    "mirror": mirror_series(),
    "cbo": cbo_rows(),
    "cbopairs": cbo_pairs(),
    "fy26": fy26_series(),
}

HTML = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DTS &rarr; MTS &rarr; CBO mapping diagnostics</title>
<style>
:root{
  color-scheme:light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --div-neg:#2a78d6; --div-pos:#e34948; --div-mid:#f0efec;
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70;
    --div-neg:#3987e5; --div-pos:#e66767; --div-mid:#383835;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --div-neg:#3987e5; --div-pos:#e66767; --div-mid:#383835;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;}
.wrap{max-width:1060px;margin:0 auto;padding:40px 24px 72px}
header h1{font-size:26px;font-weight:650;margin:0 0 6px;text-wrap:balance}
header p{color:var(--ink-2);margin:0;max-width:68ch}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:28px 0 8px}
.kpi{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 16px}
.kpi .l{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.kpi .v{font-size:30px;font-weight:600;margin-top:2px}
.kpi .d{font-size:13px;color:var(--ink-2);margin-top:2px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
  padding:20px 22px 14px;margin-top:28px}
.card h2{font-size:17px;font-weight:650;margin:0 0 2px}
.card .sub{font-size:13.5px;color:var(--ink-2);margin:0 0 12px;max-width:80ch}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink-2);margin:2px 0 8px}
.legend .key{display:inline-flex;align-items:center;gap:7px}
.key .sw{width:14px;height:3px;border-radius:2px;display:inline-block}
.key .sq{width:10px;height:10px;border-radius:3px;display:inline-block}
.chart{position:relative}
.chart svg{display:block;width:100%;height:auto}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--ring);
  border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.14);padding:8px 11px;font-size:12.5px;
  display:none;z-index:5;min-width:150px}
.tip .t{color:var(--muted);font-size:11.5px;margin-bottom:3px}
.tip .r{display:flex;justify-content:space-between;gap:14px}
.tip .r b{font-variant-numeric:tabular-nums;font-weight:600}
details{margin:6px 0 4px}
summary{font-size:12.5px;color:var(--muted);cursor:pointer}
table{border-collapse:collapse;font-size:12.5px;margin-top:8px;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:4px 10px;text-align:right;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:500}
.tblwrap{overflow-x:auto}
.facets{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.facets.two{grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
.facet h3{font-size:13.5px;font-weight:600;margin:0 0 4px;color:var(--ink-2)}
.note{font-size:12.5px;color:var(--muted);margin-top:10px}
footer{margin-top:36px;font-size:13px;color:var(--muted)}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
</style>
<div class="wrap">
<header>
  <h1>DTS &rarr; MTS &rarr; CBO mapping diagnostics</h1>
  <p>How the daily Treasury cash mapping performs against the published Monthly Treasury
  Statement and CBO annual actuals &mdash; window Mar&nbsp;2023 to Jun&nbsp;2026, all figures
  in billions of dollars. Positive deficit = deficit.</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="l">Unmapped DTS flows</div><div class="v">0.000%</div>
    <div class="d">196 of 196 live lines classified</div></div>
  <div class="kpi"><div class="l">MTS vs CBO, worst category</div><div class="v">0.4%</div>
    <div class="d">the MTS&rarr;CBO hop is essentially free</div></div>
  <div class="kpi"><div class="l">Cumulative DTS&minus;MTS wedge</div><div class="v">&minus;$867bn</div>
    <div class="d">on a $5,942bn cumulative deficit</div></div>
  <div class="kpi"><div class="l">Unattributed residual</div><div class="v">+$214bn</div>
    <div class="d">3.6% of the deficit; no trend</div></div>
</div>

<div class="card">
  <h2>Monthly deficit: DTS-built vs published MTS</h2>
  <p class="sub">The first-order check. The two series move together through refund seasons,
  quarterly tax dates and shutdown months; the gaps that remain are accounting, not mapping.</p>
  <div class="legend" id="lg-def"></div>
  <div class="chart" id="ch-def"></div>
  <p class="sub" style="margin-top:14px">Monthly wedge (DTS &minus; MTS). Blue = DTS deficit smaller
  (interest accrual months), red = larger (Aug&nbsp;2023 and Sep&nbsp;2025 are Education
  credit-reform reversals in the MTS).</p>
  <div class="chart" id="ch-wedge"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-def"></div></details>
</div>

<div class="card">
  <h2>Where the cumulative wedge comes from</h2>
  <p class="sub">Cumulative DTS&minus;MTS deficit wedge, split into its two known mechanisms.
  Interest is cash in the DTS but accrued in the MTS &mdash; bill discount never appears as DTS
  interest at all &mdash; and Education's credit-reform reestimates are non-cash MTS entries.
  What's left is small and trendless.</p>
  <div class="legend" id="lg-att"></div>
  <div class="chart" id="ch-att"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-att"></div></details>
</div>

<div class="card">
  <h2>Receipts, bucket by bucket</h2>
  <p class="sub">DTS-built receipts vs MTS net receipts per category. Income + payroll runs
  &minus;2% (refundable-credit classification), corporate is clean. Customs breaks in
  May&ndash;Jun&nbsp;2026 when IEEPA tariff refunds push MTS negative while the refund cash sits
  in CBP withdrawals. Miscellaneous is a composition choice: most of it is netted against
  agency outlays instead.</p>
  <div class="legend" id="lg-rc"></div>
  <div class="facets" id="ch-rc"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-rc"></div></details>
</div>

<div class="card">
  <h2>Outlay wedges by bucket, with their mechanisms</h2>
  <p class="sub">Total DTS&minus;MTS gap per outlay bucket over the window. Every large bar has
  a named accounting mechanism; none of them is a mis-keyed line. Treasury and the residual
  block are mirror images &mdash; accrued interest sits in one, the offsetting entries and the
  DTS salary/unclassified pools in the other.</p>
  <div class="chart" id="ch-out"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-out"></div></details>
</div>

<div class="card">
  <h2>The premium mirror: SSA vs HHS monthly wedges</h2>
  <p class="sub">Medicare Part&nbsp;B/D premiums are withheld from Social Security checks: the DTS
  pays benefits net, the MTS records them gross and nets the premiums off Medicare instead. The
  two wedges are near mirror images and mostly cancel in the total &mdash; evidence the mapping is
  capturing real structure, not noise.</p>
  <div class="legend" id="lg-mir"></div>
  <div class="chart" id="ch-mir"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-mir"></div></details>
</div>

<div class="card">
  <h2>DTS-built fiscal years against CBO historical actuals</h2>
  <p class="sub">Levels, not deviations: each pair is the DTS-built fiscal-year total (blue) against
  the CBO historical actual (aqua) from the Feb&nbsp;2026 historical-budget workbook. Where the dots
  touch, daily cash aggregates straight to the CBO number; the visible gaps are the accrual items
  (net interest, Social Security premium withholding) and the netted misc. receipts.</p>
  <div class="legend" id="lg-pairs"></div>
  <div class="facets two" id="ch-pairs"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-pairs"></div></details>
</div>

<div class="card">
  <h2>Fiscal year 2025 against CBO actuals</h2>
  <p class="sub">Deviation from CBO's historical actuals (Feb&nbsp;2026 vintage). MTS aggregates
  (orange dots) sit on zero everywhere &mdash; CBO's actuals <em>are</em> Treasury's numbers
  &mdash; so the DTS deviations (bars) are exactly the DTS&rarr;MTS wedges: cash-basis interest,
  premium withholding in Social Security, tariff-refund timing in customs. Medicaid lands within
  0.3% and Medicare net within 2%.</p>
  <div class="legend" id="lg-cbo"></div>
  <div class="chart" id="ch-cbo"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-cbo"></div></details>
</div>

<div class="card">
  <h2>The payoff: CBO&rsquo;s FY2026 deficit, split into months</h2>
  <p class="sub">CBO&rsquo;s Feb&nbsp;2026 annual baseline distributed onto DTS-derived monthly
  seasonality per bucket, against the MTS months published so far. Nine months validated:
  mean absolute error $98bn on a $200bn mean actual, 8 of 9 surplus/deficit signs right.
  The two big misses are known mechanisms, not noise: Oct/Nov&nbsp;2025 is the
  Nov-1-on-a-Saturday benefit shift (the pair nets to &minus;$44bn), and May/Jun&nbsp;2026 is
  IEEPA tariff refunds that a smooth annual customs number cannot see. A payment-calendar rule
  is the v2 fix for the first; refund scenarios for the second.</p>
  <div class="legend" id="lg-f26"></div>
  <div class="chart" id="ch-f26"></div>
  <details><summary>Data table</summary><div class="tblwrap" id="tb-f26"></div></details>
</div>

<footer>Generated by build_charts.py from recon_deficit.csv, recon_receipts.csv,
recon_outlays.csv, cbo_comparison.csv and fy2026_monthly_split.csv &middot; sources:
fiscaldata.treasury.gov (DTS Table II, MTS Tables 4/5), CBO historical budget data and
Feb 2026 baseline projections &middot; run 2026-08-05.</footer>
</div>

<script>
const DATA = __DATA__;

const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = v => (v<0?"\\u2212":"") + Math.abs(v).toLocaleString("en-US",{maximumFractionDigits:0});
const fmtB = v => "$" + fmt(v) + "bn";
const mLab = m => { const [y,mo]=m.split("-");
  return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" \\u2019"+y.slice(2); };

function svgEl(tag, attrs){ const e=document.createElementNS("http://www.w3.org/2000/svg",tag);
  for(const k in attrs) e.setAttribute(k,attrs[k]); return e; }

function tooltip(host){
  let tip=host.querySelector(".tip");
  if(!tip){ tip=document.createElement("div"); tip.className="tip"; host.appendChild(tip); }
  return {
    show(html,x,y){ tip.innerHTML=html; tip.style.display="block";
      const hw=host.clientWidth, tw=tip.offsetWidth;
      tip.style.left=Math.min(Math.max(4,x+14), hw-tw-4)+"px";
      tip.style.top=Math.max(4,y-tip.offsetHeight-12)+"px"; },
    hide(){ tip.style.display="none"; }
  };
}

function niceTicks(lo,hi,n=5){
  const span=hi-lo, step0=span/n, mag=Math.pow(10,Math.floor(Math.log10(step0)));
  const step=[1,2,2.5,5,10].map(m=>m*mag).find(s=>span/s<=n)|| 10*mag;
  const t0=Math.ceil(lo/step)*step, out=[];
  for(let t=t0;t<=hi+1e-9;t+=step) out.push(Math.abs(t)<1e-9?0:t);
  return out;
}

// ---- line chart with month crosshair ----
function lineChart(hostId,{months,series,height=280,yfmt=fmtB,endLabels=true}){
  const host=document.getElementById(hostId); host.innerHTML="";
  const W=host.clientWidth||960, H=height, m={t:14,r:endLabels?70:16,b:26,l:52};
  const iw=W-m.l-m.r, ih=H-m.t-m.b;
  const all=series.flatMap(s=>s.values).filter(v=>v!=null && !Number.isNaN(v));
  let lo=Math.min(0,...all), hi=Math.max(0,...all); const pad=(hi-lo)*.06; lo-=pad; hi+=pad;
  const x=i=>m.l+iw*(months.length<2?0.5:i/(months.length-1));
  const y=v=>m.t+ih*(1-(v-lo)/(hi-lo));
  const svg=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  niceTicks(lo,hi).forEach(t=>{
    svg.appendChild(svgEl("line",{x1:m.l,x2:W-m.r,y1:y(t),y2:y(t),stroke:css(t===0?"--axis":"--grid"),"stroke-width":1}));
    const lb=svgEl("text",{x:m.l-8,y:y(t)+4,"text-anchor":"end",fill:css("--muted"),
      "font-size":11,style:"font-variant-numeric:tabular-nums"}); lb.textContent=fmt(t); svg.appendChild(lb);
  });
  const step=Math.ceil(months.length/Math.max(3,Math.floor(iw/76)));
  months.forEach((mm,i)=>{ if(i%step===0 && x(i)<W-m.r-30){
    const lb=svgEl("text",{x:x(i),y:H-8,"text-anchor":"middle",fill:css("--muted"),"font-size":11});
    lb.textContent=mLab(mm); svg.appendChild(lb); }});
  const lastIdx=s=>{ for(let i=s.values.length-1;i>=0;i--) if(s.values[i]!=null) return i; return -1; };
  series.forEach(s=>{
    let d="", pen=false;
    s.values.forEach((v,i)=>{ if(v==null){pen=false;return;}
      d+=(pen?"L":"M")+x(i).toFixed(1)+" "+y(v).toFixed(1)+" "; pen=true; });
    svg.appendChild(svgEl("path",{d:d.trim(),fill:"none",stroke:css(s.color),"stroke-width":2,
      "stroke-linejoin":"round","stroke-linecap":"round","stroke-dasharray":s.dashed?"5 5":"none"}));
    const li=lastIdx(s);
    if(li>=0) svg.appendChild(svgEl("circle",{cx:x(li),cy:y(s.values[li]),r:4,fill:css(s.color),
      stroke:css("--surface"),"stroke-width":2}));
  });
  // direct end labels only when they don't collide; otherwise the legend carries identity
  if(endLabels){
    const ends=series.map(s=>{const li=lastIdx(s); return li<0?null:y(s.values[li]);})
      .filter(v=>v!=null).sort((a,b)=>a-b);
    const collide=ends.some((v,i)=>i&&v-ends[i-1]<13);
    if(!collide) series.forEach(s=>{
      const li=lastIdx(s); if(li<0) return;
      const lb=svgEl("text",{x:x(li)+8,y:y(s.values[li])+4,fill:css("--ink-2"),"font-size":11.5});
      lb.textContent=s.name; svg.appendChild(lb);
    });
  }
  const cross=svgEl("line",{y1:m.t,y2:H-m.b,stroke:css("--axis"),"stroke-width":1,visibility:"hidden"});
  svg.appendChild(cross);
  const dots=series.map(s=>{ const c=svgEl("circle",{r:4.5,fill:css(s.color),
    stroke:css("--surface"),"stroke-width":2,visibility:"hidden"}); svg.appendChild(c); return c; });
  const tip=tooltip(host);
  svg.addEventListener("mousemove",ev=>{
    const r=svg.getBoundingClientRect(), px=(ev.clientX-r.left)*(W/r.width);
    const i=Math.max(0,Math.min(months.length-1,Math.round((px-m.l)/(iw/(months.length-1)))));
    cross.setAttribute("x1",x(i)); cross.setAttribute("x2",x(i)); cross.setAttribute("visibility","visible");
    series.forEach((s,k)=>{ const v=s.values[i];
      if(v==null){ dots[k].setAttribute("visibility","hidden"); return; }
      dots[k].setAttribute("cx",x(i)); dots[k].setAttribute("cy",y(v));
      dots[k].setAttribute("visibility","visible"); });
    tip.show(`<div class="t">${mLab(months[i])}</div>`+series.map(s=>
      `<div class="r"><span>${s.name}</span><b>${s.values[i]==null?"\\u2013":yfmt(s.values[i])}</b></div>`).join(""),
      (ev.clientX-r.left),(ev.clientY-r.top));
  });
  svg.addEventListener("mouseleave",()=>{ cross.setAttribute("visibility","hidden");
    dots.forEach(d=>d.setAttribute("visibility","hidden")); tip.hide(); });
  host.appendChild(svg);
}

// ---- monthly diverging column chart ----
function wedgeChart(hostId,{months,values,height=190}){
  const host=document.getElementById(hostId); host.innerHTML="";
  const W=host.clientWidth||960, H=height, m={t:10,r:16,b:26,l:52};
  const iw=W-m.l-m.r, ih=H-m.t-m.b;
  let lo=Math.min(0,...values), hi=Math.max(0,...values); const pad=(hi-lo)*.08; lo-=pad; hi+=pad;
  const y=v=>m.t+ih*(1-(v-lo)/(hi-lo));
  const bw=Math.min(24,(iw/months.length)-2);
  const x=i=>m.l+iw*(i+0.5)/months.length;
  const svg=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  niceTicks(lo,hi,4).forEach(t=>{
    svg.appendChild(svgEl("line",{x1:m.l,x2:W-m.r,y1:y(t),y2:y(t),stroke:css(t===0?"--axis":"--grid"),"stroke-width":1}));
    const lb=svgEl("text",{x:m.l-8,y:y(t)+4,"text-anchor":"end",fill:css("--muted"),
      "font-size":11,style:"font-variant-numeric:tabular-nums"}); lb.textContent=fmt(t); svg.appendChild(lb);
  });
  const step=Math.ceil(months.length/Math.max(3,Math.floor(iw/76)));
  months.forEach((mm,i)=>{ if(i%step===0 && x(i)<W-m.r-30){
    const lb=svgEl("text",{x:x(i),y:H-8,"text-anchor":"middle",fill:css("--muted"),"font-size":11});
    lb.textContent=mLab(mm); svg.appendChild(lb); }});
  const tip=tooltip(host);
  values.forEach((v,i)=>{
    const top=Math.min(y(v),y(0)), h=Math.max(1.5,Math.abs(y(v)-y(0)));
    const rx=Math.min(4,bw/2);
    const b=svgEl("rect",{x:x(i)-bw/2,y:top,width:bw,height:h,rx,
      fill:css(v>=0?"--div-pos":"--div-neg")});
    b.addEventListener("mousemove",ev=>{ const r=host.getBoundingClientRect();
      tip.show(`<div class="t">${mLab(months[i])}</div><div class="r"><span>wedge</span><b>${fmtB(v)}</b></div>`,
        ev.clientX-r.left,ev.clientY-r.top); });
    b.addEventListener("mouseleave",()=>tip.hide());
    svg.appendChild(b);
  });
  host.appendChild(svg);
}

// ---- horizontal diverging bars with notes ----
function hbarChart(hostId,{rows,valueKey,noteKey,labelKey,dotKey=null,height=null,vfmt=fmtB}){
  const host=document.getElementById(hostId); host.innerHTML="";
  const W=host.clientWidth||960, rh=30, m={t:8,r:20,b:28,l:150};
  const H=height||m.t+m.b+rows.length*rh;
  const iw=W-m.l-m.r;
  const vals=rows.map(r=>r[valueKey]).concat(dotKey?rows.map(r=>r[dotKey]??0):[]);
  let lo=Math.min(0,...vals), hi=Math.max(0,...vals); const pad=(hi-lo)*.06; lo-=pad; hi+=pad;
  const x=v=>m.l+iw*(v-lo)/(hi-lo);
  const svg=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  niceTicks(lo,hi,6).forEach(t=>{
    svg.appendChild(svgEl("line",{x1:x(t),x2:x(t),y1:m.t,y2:H-m.b,stroke:css(t===0?"--axis":"--grid"),"stroke-width":1}));
    const lb=svgEl("text",{x:x(t),y:H-10,"text-anchor":"middle",fill:css("--muted"),
      "font-size":11,style:"font-variant-numeric:tabular-nums"}); lb.textContent=fmt(t); svg.appendChild(lb);
  });
  const tip=tooltip(host);
  rows.forEach((r,i)=>{
    const cy=m.t+i*rh+rh/2, v=r[valueKey];
    const lb=svgEl("text",{x:m.l-10,y:cy+4,"text-anchor":"end",fill:css("--ink-2"),"font-size":12.5});
    lb.textContent=r[labelKey]; svg.appendChild(lb);
    const x0=Math.min(x(0),x(v)), w=Math.max(2,Math.abs(x(v)-x(0)));
    const bar=svgEl("rect",{x:x0,y:cy-8,width:w,height:16,rx:4,
      fill:css(v>=0?"--div-pos":"--div-neg")});
    svg.appendChild(bar);
    if(dotKey!=null && r[dotKey]!=null){
      svg.appendChild(svgEl("circle",{cx:x(r[dotKey]),cy,r:4.5,fill:css("--s2"),
        stroke:css("--surface"),"stroke-width":2}));
    }
    const hit=svgEl("rect",{x:m.l,y:cy-rh/2,width:iw,height:rh,fill:"transparent"});
    hit.addEventListener("mousemove",ev=>{ const rc=host.getBoundingClientRect();
      let html=`<div class="t">${r[labelKey]}</div><div class="r"><span>DTS&minus;MTS</span><b>${vfmt(v)}</b></div>`;
      if(dotKey!=null&&r[dotKey]!=null) html=`<div class="t">${r[labelKey]}</div>`+
        `<div class="r"><span>DTS vs CBO</span><b>${vfmt(v)}</b></div><div class="r"><span>MTS vs CBO</span><b>${vfmt(r[dotKey])}</b></div>`;
      if(noteKey&&r[noteKey]) html+=`<div class="t" style="margin-top:4px">${r[noteKey]}</div>`;
      tip.show(html,ev.clientX-rc.left,ev.clientY-rc.top); });
    hit.addEventListener("mouseleave",()=>tip.hide());
    svg.appendChild(hit);
  });
  host.appendChild(svg);
}

// ---- dumbbell: DTS-built vs CBO actual levels per category ----
function dumbbell(hostId,{rows}){
  const host=document.getElementById(hostId); host.innerHTML="";
  const W=host.clientWidth||460, rh=30, m={t:8,r:24,b:28,l:150};
  const H=m.t+m.b+rows.length*rh, iw=W-m.l-m.r;
  const vals=rows.flatMap(r=>[r.dts,r.cbo]);
  let lo=Math.min(0,...vals), hi=Math.max(...vals); const pad=(hi-lo)*.08; hi+=pad; lo=Math.min(0,lo-pad);
  const x=v=>m.l+iw*(v-lo)/(hi-lo);
  const svg=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  niceTicks(lo,hi,4).forEach(t=>{
    svg.appendChild(svgEl("line",{x1:x(t),x2:x(t),y1:m.t,y2:H-m.b,stroke:css(t===0?"--axis":"--grid"),"stroke-width":1}));
    const lb=svgEl("text",{x:x(t),y:H-10,"text-anchor":"middle",fill:css("--muted"),
      "font-size":11,style:"font-variant-numeric:tabular-nums"}); lb.textContent=fmt(t); svg.appendChild(lb);
  });
  const tip=tooltip(host);
  rows.forEach((r,i)=>{
    const cy=m.t+i*rh+rh/2;
    const lb=svgEl("text",{x:m.l-10,y:cy+4,"text-anchor":"end",fill:css("--ink-2"),"font-size":12.5});
    lb.textContent=r.category; svg.appendChild(lb);
    svg.appendChild(svgEl("line",{x1:x(r.cbo),x2:x(r.dts),y1:cy,y2:cy,stroke:css("--axis"),"stroke-width":2}));
    svg.appendChild(svgEl("circle",{cx:x(r.cbo),cy,r:5,fill:css("--s3"),stroke:css("--surface"),"stroke-width":2}));
    svg.appendChild(svgEl("circle",{cx:x(r.dts),cy,r:5,fill:css("--s1"),stroke:css("--surface"),"stroke-width":2}));
    const hit=svgEl("rect",{x:m.l,y:cy-rh/2,width:iw,height:rh,fill:"transparent"});
    hit.addEventListener("mousemove",ev=>{ const rc=host.getBoundingClientRect();
      tip.show(`<div class="t">${r.category}</div>`+
        `<div class="r"><span>DTS-built</span><b>${fmtB(r.dts)}</b></div>`+
        `<div class="r"><span>CBO actual</span><b>${fmtB(r.cbo)}</b></div>`+
        `<div class="r"><span>gap</span><b>${(r.pct<0?"\\u2212":"+")+Math.abs(r.pct).toFixed(1)}%</b></div>`,
        ev.clientX-rc.left,ev.clientY-rc.top); });
    hit.addEventListener("mouseleave",()=>tip.hide());
    svg.appendChild(hit);
  });
  host.appendChild(svg);
}

function legend(id,keys){
  document.getElementById(id).innerHTML=keys.map(k=>
    `<span class="key"><span class="${k.shape||'sw'}" style="background:var(${k.color})"></span>${k.name}</span>`).join("");
}
function table(id,head,rows){
  document.getElementById(id).innerHTML="<table><tr>"+head.map(h=>`<th>${h}</th>`).join("")+"</tr>"+
    rows.map(r=>"<tr>"+r.map(c=>`<td>${c}</td>`).join("")+"</tr>").join("")+"</table>";
}

function drawAll(){
  const D=DATA;
  legend("lg-def",[{name:"DTS-built deficit",color:"--s1"},{name:"MTS published deficit",color:"--s2"}]);
  lineChart("ch-def",{months:D.deficit.months,series:[
    {name:"DTS",color:"--s1",values:D.deficit.dts},
    {name:"MTS",color:"--s2",values:D.deficit.mts}]});
  wedgeChart("ch-wedge",{months:D.deficit.months,values:D.deficit.wedge});
  table("tb-def",["Month","DTS deficit $bn","MTS deficit $bn","Wedge $bn"],
    D.deficit.months.map((mm,i)=>[mLab(mm),fmt(D.deficit.dts[i]),fmt(D.deficit.mts[i]),fmt(D.deficit.wedge[i])]));

  legend("lg-att",[{name:"Total wedge",color:"--s1"},{name:"Interest cash vs accrual",color:"--s2"},
    {name:"Education credit reform",color:"--s3"},{name:"Residual",color:"--muted"}]);
  lineChart("ch-att",{months:D.attrib.months,series:[
    {name:"Total",color:"--s1",values:D.attrib.total},
    {name:"Interest",color:"--s2",values:D.attrib.interest},
    {name:"Education",color:"--s3",values:D.attrib.ed},
    {name:"Residual",color:"--muted",values:D.attrib.residual}],height:300});
  table("tb-att",["Month","Total $bn","Interest $bn","Education $bn","Residual $bn"],
    D.attrib.months.map((mm,i)=>[mLab(mm),fmt(D.attrib.total[i]),fmt(D.attrib.interest[i]),
      fmt(D.attrib.ed[i]),fmt(D.attrib.residual[i])]));

  legend("lg-rc",[{name:"DTS-built",color:"--s1"},{name:"MTS net",color:"--s2"}]);
  const rc=document.getElementById("ch-rc"); rc.innerHTML="";
  D.receipts.forEach(f=>{
    const d=document.createElement("div"); d.className="facet";
    d.innerHTML=`<h3>${f.label}</h3><div class="chart" id="fc-${f.key}"></div>`;
    rc.appendChild(d);
  });
  D.receipts.forEach(f=>lineChart("fc-"+f.key,{months:f.months,series:[
    {name:"DTS",color:"--s1",values:f.dts},{name:"MTS",color:"--s2",values:f.mts}],
    height:150,endLabels:false}));
  table("tb-rc",["Month"].concat(D.receipts.flatMap(f=>[f.label+" DTS",f.label+" MTS"])),
    D.receipts[0].months.map((mm,i)=>[mLab(mm)].concat(D.receipts.flatMap(f=>[fmt(f.dts[i]),fmt(f.mts[i])]))));

  hbarChart("ch-out",{rows:DATA.outlays,valueKey:"wedge",noteKey:"note",labelKey:"bucket"});
  table("tb-out",["Bucket","DTS $bn","MTS $bn","Wedge $bn","Mechanism"],
    DATA.outlays.map(r=>[r.bucket,fmt(r.dts),fmt(r.mts),fmt(r.wedge),r.note]));

  legend("lg-mir",[{name:"SSA wedge (benefits net of withholding)",color:"--s1"},
    {name:"HHS wedge (Medicare net of premiums)",color:"--s2"}]);
  lineChart("ch-mir",{months:D.mirror.months,series:[
    {name:"SSA",color:"--s1",values:D.mirror.ssa},
    {name:"HHS",color:"--s2",values:D.mirror.hhs}],height:230});
  table("tb-mir",["Month","SSA wedge $bn","HHS wedge $bn"],
    D.mirror.months.map((mm,i)=>[mLab(mm),fmt(D.mirror.ssa[i]),fmt(D.mirror.hhs[i])]));

  legend("lg-pairs",[{name:"DTS-built",color:"--s1",shape:"sq"},
    {name:"CBO actual",color:"--s3",shape:"sq"}]);
  const pr=document.getElementById("ch-pairs"); pr.innerHTML="";
  [["2024","majors","FY2024 \\u2014 large categories"],["2024","minors","FY2024 \\u2014 smaller categories"],
   ["2025","majors","FY2025 \\u2014 large categories"],["2025","minors","FY2025 \\u2014 smaller categories"]]
    .forEach(([fy,part,title])=>{
      const d=document.createElement("div"); d.className="facet";
      d.innerHTML=`<h3>${title}</h3><div class="chart" id="pb-${fy}-${part}"></div>`;
      pr.appendChild(d);
    });
  ["2024","2025"].forEach(fy=>["majors","minors"].forEach(part=>
    dumbbell(`pb-${fy}-${part}`,{rows:DATA.cbopairs[fy][part]})));
  table("tb-pairs",["Category","FY","DTS-built $bn","CBO actual $bn","Gap"],
    ["2024","2025"].flatMap(fy=>DATA.cbopairs[fy].majors.concat(DATA.cbopairs[fy].minors)
      .map(r=>[r.category,fy,fmt(r.dts),fmt(r.cbo),(r.pct<0?"\\u2212":"+")+Math.abs(r.pct).toFixed(1)+"%"])));

  legend("lg-f26",[{name:"Projected: CBO annual \\u00d7 DTS seasonality",color:"--s3"},
    {name:"Actual (MTS)",color:"--s2"}]);
  lineChart("ch-f26",{months:D.fy26.months,series:[
    {name:"Projected",color:"--s3",values:D.fy26.proj,dashed:true},
    {name:"Actual",color:"--s2",values:D.fy26.actual}],height:280});
  table("tb-f26",["Month","Projected deficit $bn","Actual (MTS) $bn"],
    D.fy26.months.map((mm,i)=>[mLab(mm),fmt(D.fy26.proj[i]),
      D.fy26.actual[i]==null?"\\u2013":fmt(D.fy26.actual[i])]));

  legend("lg-cbo",[{name:"DTS-built vs CBO",color:"--div-neg",shape:"sq"},
    {name:"MTS vs CBO",color:"--s2",shape:"sq"}]);
  hbarChart("ch-cbo",{rows:DATA.cbo,valueKey:"dts_pct",dotKey:"mts_pct",labelKey:"category",
    vfmt:v=>(v<0?"\\u2212":"+")+Math.abs(v).toFixed(1)+"%"});
  table("tb-cbo",["Category (FY2025)","DTS-built $bn","CBO actual $bn","DTS vs CBO","MTS vs CBO"],
    DATA.cbo.map(r=>[r.category,fmt(r.dts),fmt(r.cbo),
      (r.dts_pct<0?"\\u2212":"+")+Math.abs(r.dts_pct).toFixed(1)+"%",
      r.mts_pct==null?"\\u2013":(r.mts_pct<0?"\\u2212":"+")+Math.abs(r.mts_pct).toFixed(1)+"%"]));
}

drawAll();
let rt; addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(drawAll,150);});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",drawAll);
new MutationObserver(drawAll).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
</script>
"""


def main():
    html = HTML.replace("__DATA__", json.dumps(DATA))
    with open("mapping_dashboard.html", "w") as f:
        f.write(html)
    print(f"Wrote mapping_dashboard.html ({len(html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
