"""Portfolio metrics: bill share and WAM, historical and forecast.

History: quarterly MSPD detail snapshots (Mar 2015 - Jun 2026) give exact
per-CUSIP outstanding, from which bills share of marketable debt and the
weighted-average maturity are computed directly.

Forecast (monthly, Oct 2026 - Dec 2027): the outstanding universe is evolved
forward - existing coupons age and mature (full par; SOMA rollovers net out by
construction), new coupons arrive at TBAC recommended sizes on their tenors
(held at provisional levels beyond Jan 2027: Treasury's stated posture is
steady nominal coupon sizes, so bills absorb the residual financing need -
exactly how the TGA model solves them), and the bill stock moves with the
solved weekly net bills. Bills WAM is held at its historical average (~0.2y).

Outputs: issuance_metrics_hist.csv, issuance_metrics_fcst.csv
"""

from datetime import date

import pandas as pd
import requests

from issuance_engine import (COUPON_SIZES, FRN_SIZE, NEW_MONTHS_10_20_30,
                             TIPS_CYCLE, load_universe)

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
BILL_WAM_Y = 0.21  # historical average bills WAM, years (computed below too)


def fetch_mspd(record_date):
    frames, page = [], 1
    while True:
        r = requests.get(BASE + "/v1/debt/mspd/mspd_table_3_market", params={
            "filter": f"record_date:eq:{record_date}",
            "fields": "record_date,security_class1_desc,security_class2_desc,"
                      "issued_amt,inflation_adj_amt,redeemed_amt,maturity_date",
            "page[size]": 10000, "page[number]": page}, timeout=120)
        r.raise_for_status()
        p = r.json()
        rows = p.get("data", [])
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        if page >= p.get("meta", {}).get("total-pages", page):
            break
        page += 1
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def snapshot_metrics(df, asof):
    df = df[df.security_class1_desc.isin(
        ["Bills Maturity Value", "Notes", "Bonds",
         "Inflation-Protected Securities", "Floating Rate Notes"])].copy()
    for c in ["issued_amt", "inflation_adj_amt", "redeemed_amt"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["par"] = df.issued_amt + df.inflation_adj_amt - df.redeemed_amt
    df = df[df.par > 0]
    df["mat"] = pd.to_datetime(df.maturity_date, errors="coerce")
    df = df[df.mat.notna()]
    df["yrs"] = (df.mat - pd.Timestamp(asof)).dt.days / 365.25
    df = df[df.yrs > 0]
    bills = df[df.security_class1_desc.eq("Bills Maturity Value")]
    total = df.par.sum()
    return {
        "date": asof, "total_bn": total / 1e3,
        "bills_bn": bills.par.sum() / 1e3,
        "bill_share_pct": bills.par.sum() / total * 100,
        "wam_months": (df.par * df.yrs).sum() / total * 12,
        "bills_wam_years": (bills.par * bills.yrs).sum() / max(bills.par.sum(), 1),
    }


def history():
    rows = []
    for q in pd.period_range("2015Q1", "2026Q2", freq="Q"):
        asof = q.to_timestamp(how="end").date()
        asof = date(asof.year, asof.month, [31, 30, 30, 31, 31, 30, 31, 31, 30, 31, 30, 31][asof.month - 1]) \
            if False else asof  # quarter-end calendar date
        df = fetch_mspd(str(asof))
        if df.empty:  # some months publish on slightly different dates
            continue
        rows.append(snapshot_metrics(df, asof))
        print(f"  {asof}: share {rows[-1]['bill_share_pct']:.1f}%  wam {rows[-1]['wam_months']:.1f}m")
    h = pd.DataFrame(rows).set_index("date")
    h.round(2).to_csv("issuance_metrics_hist.csv")
    return h


TENORS = {"2-Year": 2, "3-Year": 3, "5-Year": 5, "7-Year": 7,
          "10-Year": 10, "20-Year": 20, "30-Year": 30}


def monthly_coupon_issuance(m: pd.Period):
    """(par mn, tenor years) list for one forecast month at TBAC sizes."""
    out = []
    for term, yrs in TENORS.items():
        sz = COUPON_SIZES[term]
        if isinstance(sz, dict):
            sz = sz["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"]
        out.append((sz * 1e3, yrs))
    t_term, _, t_sz = TIPS_CYCLE[m.month]
    out.append((t_sz * 1e3, {"5-Year": 5, "10-Year": 10, "30-Year": 30}[t_term]))
    out.append((FRN_SIZE["new" if m.month in (1, 4, 7, 10) else "reopen"] * 1e3, 2))
    return out


def forecast(hist):
    uni = load_universe()[["par", "maturity"]].copy()
    uni["mat"] = pd.to_datetime(uni.maturity)

    tga = pd.read_csv("tga_daily.csv")
    tga["date"] = pd.to_datetime(tga[tga.columns[0]])
    tga["month"] = tga.date.dt.to_period("M")
    bills_net_m = tga.groupby("month")["net_bills"].sum()

    m0 = pd.read_csv("data/mspd_2026_06.csv")
    b = m0[m0.security_class1_desc.eq("Bills Maturity Value")].copy()
    for c in ["issued_amt", "redeemed_amt"]:
        b[c] = pd.to_numeric(b[c], errors="coerce").fillna(0.0)
    b["mat"] = pd.to_datetime(b.maturity_date, errors="coerce")
    b = b[b.mat > "2026-06-30"]  # live bills only - the section also lists matured CUSIPs
    bills_stock = (b.issued_amt - b.redeemed_amt).sum()  # Jun 30 stock
    # bridge Jul-Sep 2026 (before the model's own path starts): Treasury's Aug
    # Sources & Uses plans $739bn Q3 borrowing; engine net coupons ~$310bn
    bills_stock += 430_000

    rows = []
    for m in pd.period_range("2026-10", "2027-12", freq="M"):
        eom = pd.Timestamp(m.to_timestamp(how="end").date())
        uni = uni[uni.mat > eom]  # matured coupons drop
        for par, yrs in monthly_coupon_issuance(m):
            uni = pd.concat([uni, pd.DataFrame(
                {"par": [par], "mat": [eom + pd.DateOffset(years=yrs)]})], ignore_index=True)
        bills_stock += bills_net_m.get(m, 0.0)
        yrs_left = (uni.mat - eom).dt.days / 365.25
        coup_par = uni.par.sum()
        total = coup_par + bills_stock
        wam = ((uni.par * yrs_left).sum() + bills_stock * BILL_WAM_Y) / total * 12
        rows.append({"month": str(m), "bills_bn": bills_stock / 1e3,
                     "total_bn": total / 1e3,
                     "bill_share_pct": bills_stock / total * 100,
                     "wam_months": wam,
                     "gross_coupons_bn": sum(p for p, _ in monthly_coupon_issuance(m)) / 1e3,
                     "net_bills_bn": bills_net_m.get(m, 0.0) / 1e3})
    f = pd.DataFrame(rows).set_index("month")
    f.round(2).to_csv("issuance_metrics_fcst.csv")
    return f


def main():
    import os
    if os.path.exists("issuance_metrics_hist.csv"):
        h = pd.read_csv("issuance_metrics_hist.csv", index_col=0)
        print(f"History loaded from cache ({len(h)} quarters).")
    else:
        print("Pulling MSPD quarterly snapshots (Mar 2015 - Jun 2026)...")
        h = history()
    print(f"\nHistorical bills WAM average: {h.bills_wam_years.mean():.2f}y (model uses {BILL_WAM_Y}y)")
    f = forecast(h)
    pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
    print("\nForecast (monthly):")
    print(f[["bills_bn", "bill_share_pct", "wam_months", "gross_coupons_bn", "net_bills_bn"]]
          .iloc[[0, 2, 5, 8, 11, 14]].to_string())
    print(f"\nJun 2026 actual: share {h.bill_share_pct.iloc[-1]:.1f}%, WAM {h.wam_months.iloc[-1]:.1f}m")
    print(f"Dec 2027 forecast: share {f.bill_share_pct.iloc[-1]:.1f}%, WAM {f.wam_months.iloc[-1]:.1f}m")


if __name__ == "__main__":
    main()
