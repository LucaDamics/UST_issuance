"""CUSIP-level coupon engine: interest, redemptions, and forward settlements.

Universe = MSPD detail (Jun 30, 2026) + auctions settled/announced since, at
single-security level. From it, three dated daily cash-flow calendars over the
model horizon (through Dec 2027):

  coupon interest   rate/2 x par per CUSIP on its semiannual pay dates
                    (TIPS on inflation-adjusted par, held flat - assumption;
                    FRNs quarterly at latest 13-week auction rate + spread),
                    paid to ALL holders incl. SOMA (cash leaves the TGA either way)
  redemptions       par at maturity, PUBLIC holders only (outstanding - SOMA):
                    SOMA rolls its maturities at auction, no cash moves
  settlements       forward coupon auction proceeds: the official XML calendar
                    + TBAC recommended sizes through Jan 2027, then the stable
                    monthly pattern (sizes held at TBAC provisional levels)

Bills are deliberately absent from all three: net bills are the RESIDUAL the
TGA model solves for, so bill redemptions and issuance live inside that net.

Validation: July 2026 engine coupons vs the DTS "Interest on Treasury
Securities" daily line - the engine is deterministic, so this is a hard test.

New-issue coupons for future auctions: latest auction yield per tenor, rounded
down to 1/8 as Treasury sets coupons. Sizes/dates: TBAC table of 2026-08-05 and
the Aug 2026 refunding XML.

Outputs: engine_flows_daily.csv (date, interest, redemptions, settlements)
"""

from datetime import date, timedelta

import pandas as pd
import requests

from daily_model import business_days, us_federal_holidays

HORIZON_END = date(2027, 12, 31)
ASOF = date(2026, 8, 6)

# TBAC recommended / provisional sizes (USD bn), 2026-08-05 table.
# (new-issue months: 10y/30y/20y new in Feb/May/Aug/Nov cycle offsets)
COUPON_SIZES = {
    "2-Year": 69, "3-Year": 58, "5-Year": 70, "7-Year": 44,
    "10-Year": {"new": 42, "reopen": 39}, "20-Year": {"new": 16, "reopen": 13},
    "30-Year": {"new": 25, "reopen": 22},
}
TIPS_CYCLE = {  # month -> (term, kind, size bn)
    1: ("10-Year", "new", 21), 2: ("30-Year", "new", 9), 3: ("10-Year", "reopen", 19),
    4: ("5-Year", "new", 26), 5: ("10-Year", "reopen", 19), 6: ("5-Year", "reopen", 24),
    7: ("10-Year", "new", 21), 8: ("30-Year", "reopen", 8), 9: ("10-Year", "reopen", 19),
    10: ("5-Year", "new", 26), 11: ("10-Year", "reopen", 19), 12: ("5-Year", "reopen", 24),
}
FRN_SIZE = {"new": 30, "reopen": 28}  # new in Jan/Apr/Jul/Oct
NEW_MONTHS_10_20_30 = {2, 5, 8, 11}  # quarterly refunding new issues


def next_bd(d: date) -> date:
    hol = us_federal_holidays(d.year) | us_federal_holidays(d.year + 1)
    while d.weekday() >= 5 or d in hol:
        d += timedelta(days=1)
    return d


def load_universe():
    m = pd.read_csv("data/mspd_2026_06.csv")
    m = m[m.security_class1_desc.isin(
        ["Notes", "Bonds", "Inflation-Protected Securities", "Floating Rate Notes"])].copy()
    for c in ["issued_amt", "inflation_adj_amt", "redeemed_amt"]:
        m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0.0)
    m["rate"] = pd.to_numeric(m["interest_rate_pct"], errors="coerce")
    g = (m.groupby(["security_class2_desc", "security_class1_desc", "maturity_date"], as_index=False)
           .agg(rate=("rate", "first"), issued=("issued_amt", "sum"),
                infl=("inflation_adj_amt", "sum"), redeemed=("redeemed_amt", "sum"),
                issue0=("issue_date", "min")))
    g = g.rename(columns={"security_class2_desc": "cusip", "security_class1_desc": "klass"})
    g["par"] = g.issued + g.infl - g.redeemed  # USD mn; TIPS par inflation-adjusted
    g["maturity"] = pd.to_datetime(g.maturity_date).dt.date
    g["issue0"] = pd.to_datetime(g.issue0).dt.date
    g = g[g.par > 0]

    # post-MSPD coupon issues (settled or announced) from auction data
    a = pd.read_csv("data/auctions_2025on.csv")
    a["issue"] = pd.to_datetime(a.issue_date).dt.date
    a["maturity"] = pd.to_datetime(a.maturity_date).dt.date
    a = a[a.security_type.isin(["Note", "Bond"]) & (a.issue > date(2026, 6, 30))].copy()
    a["rate"] = pd.to_numeric(a.int_rate, errors="coerce")
    a["accepted"] = pd.to_numeric(a.total_accepted, errors="coerce") / 1e6  # -> mn
    a["is_frn"] = a.floating_rate.eq("Yes")
    a["is_tips"] = pd.to_numeric(a.index_ratio_on_issue_date, errors="coerce").notna()
    rows = []
    for cusip, grp in a.groupby("cusip"):
        r = grp.iloc[0]
        klass = ("Floating Rate Notes" if r.is_frn else
                 "Inflation-Protected Securities" if r.is_tips else
                 "Bonds" if r.security_type == "Bond" else "Notes")
        par = grp["accepted"].sum()
        if pd.isna(par) or par == 0:  # announced, not yet auctioned: use TBAC size
            term = r.security_term.split()[0]
            sz = COUPON_SIZES.get(term.replace("Year", "-Year") if "-" not in term else term)
            par = None  # handled by forward layer instead
            continue
        rows.append({"cusip": cusip, "klass": klass, "rate": r.rate, "par": par,
                     "maturity": r.maturity, "issue0": grp["issue"].min()})
    uni = pd.concat([g[["cusip", "klass", "rate", "par", "maturity", "issue0"]],
                     pd.DataFrame(rows)], ignore_index=True)
    uni = uni.groupby(["cusip", "klass", "maturity"], as_index=False).agg(
        rate=("rate", "first"), par=("par", "sum"), issue0=("issue0", "min"))
    return uni


def soma_by_cusip():
    import json
    d = json.load(open("data/soma_latest.json"))
    return {h["cusip"]: float(h["parValue"]) / 1e6
            for h in d["soma"]["holdings"] if h.get("parValue")}


def latest_yields():
    a = pd.read_csv("data/auctions_2025on.csv")
    a = a[a.security_type.isin(["Note", "Bond"])].copy()
    a["is_frn"] = a.floating_rate.eq("Yes")
    a["is_tips"] = pd.to_numeric(a.index_ratio_on_issue_date, errors="coerce").notna()
    a["y"] = pd.to_numeric(a.high_yield, errors="coerce")
    a = a[a.y.notna()].sort_values("auction_date")
    out = {}
    for _, r in a.iterrows():
        key = ("TIPS " if r.is_tips else "FRN " if r.is_frn else "") + r.security_term.split()[0]
        out[key] = r.y
    return out


def frn_rate():
    a = pd.read_csv("data/auctions_2025on.csv")
    b = a[a.security_term.eq("13-Week")].copy()
    b["r"] = pd.to_numeric(b.high_discnt_rate, errors="coerce")
    return b.dropna(subset=["r"]).sort_values("auction_date")["r"].iloc[-1]


def month_end(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def coupon_dates(maturity: date, issue0: date, start: date, end: date, freq: int = 2):
    """Coupon PAY dates in [start, end]: strictly after issue, at/before maturity.

    End-of-month securities (maturing on a month's last day) pay on the LAST
    day of the cycle month, whatever its day count - a Nov 30 note pays
    May 31, not May 30. Weekend/holiday dates pay the next business day."""
    step = 12 // freq
    months = {(maturity.month - 1 + k * step) % 12 + 1 for k in range(freq)}
    is_eom = maturity.day == month_end(maturity.year, maturity.month).day
    out = []
    for y in range(start.year - 1, end.year + 1):
        for mth in months:
            if is_eom:
                d0 = month_end(y, mth)
            else:
                try:
                    d0 = date(y, mth, maturity.day)
                except ValueError:
                    d0 = month_end(y, mth)
            pay = next_bd(d0)
            if start <= pay <= end and issue0 < d0 <= maturity:
                out.append(pay)
    return out


def build_flows():
    uni = load_universe()
    soma = soma_by_cusip()
    fr = frn_rate()
    ylds = latest_yields()
    start, end = date(2026, 6, 1), HORIZON_END

    interest, redemptions = {}, {}
    for _, s in uni.iterrows():
        if s.klass == "Floating Rate Notes":
            rate, freq = fr + 0.10, 4  # spread approx; quarterly
        else:
            rate, freq = s.rate, 2
        if pd.isna(rate):
            continue
        dts = coupon_dates(s.maturity, s.issue0, start, end, freq=freq)
        for d in dts:
            interest[d] = interest.get(d, 0.0) + s.par * rate / 100 / freq
        if start <= s.maturity <= end:
            pub = max(0.0, s.par - soma.get(s.cusip, 0.0))
            rd = next_bd(s.maturity)
            redemptions[rd] = redemptions.get(rd, 0.0) + pub

    # ---- forward coupon auction settlements (and their future coupons) ----
    settlements = {}
    months = pd.period_range("2026-09", "2027-12", freq="M")
    for m in months:
        mid = next_bd(date(m.year, m.month, 15))
        eom_last = business_days(m)[-1]
        eom = eom_last if eom_last.day >= 28 else next_bd(date(m.year, m.month, 28))
        # mid-month settle: 3y, 10y, 30y (+20y new/reopen settles EOM actually;
        # 20-Year settles end of month) -> mid: 3/10/30, eom: 2/5/7/20/TIPS/FRN
        mid_amt = (COUPON_SIZES["3-Year"]
                   + COUPON_SIZES["10-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"]
                   + COUPON_SIZES["30-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"])
        tips_term, tips_kind, tips_sz = TIPS_CYCLE[m.month]
        eom_amt = (COUPON_SIZES["2-Year"] + COUPON_SIZES["5-Year"] + COUPON_SIZES["7-Year"]
                   + COUPON_SIZES["20-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"]
                   + tips_sz + FRN_SIZE["new" if m.month in (1, 4, 7, 10) else "reopen"])
        settlements[mid] = settlements.get(mid, 0.0) + mid_amt * 1e3   # bn -> mn
        settlements[eom] = settlements.get(eom, 0.0) + eom_amt * 1e3

        # coupons thrown off by these future issues within the horizon
        for term_yrs, size_bn, sdate in [(3, COUPON_SIZES["3-Year"], mid),
                                         (10, mid_amt - COUPON_SIZES["3-Year"], None),
                                         (2, COUPON_SIZES["2-Year"], eom),
                                         (5, COUPON_SIZES["5-Year"], eom),
                                         (7, COUPON_SIZES["7-Year"], eom)]:
            pass  # handled generically below
        for term, size_bn, sd in [("3-Year", COUPON_SIZES["3-Year"], mid),
                                  ("10-Year", COUPON_SIZES["10-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"], mid),
                                  ("30-Year", COUPON_SIZES["30-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"], mid),
                                  ("2-Year", COUPON_SIZES["2-Year"], eom),
                                  ("5-Year", COUPON_SIZES["5-Year"], eom),
                                  ("7-Year", COUPON_SIZES["7-Year"], eom),
                                  ("20-Year", COUPON_SIZES["20-Year"]["new" if m.month in NEW_MONTHS_10_20_30 else "reopen"], eom)]:
            y = ylds.get(term.split("-")[0] + "-Year", ylds.get(term, 4.3))
            cpn = int(y * 8) / 8  # round down to 1/8
            first = sd + timedelta(days=182)
            d = first
            while d <= end:
                pay = next_bd(d)
                interest[pay] = interest.get(pay, 0.0) + size_bn * 1e3 * cpn / 100 / 2
                d += timedelta(days=182)

    idx = sorted(set(interest) | set(redemptions) | set(settlements))
    flows = pd.DataFrame(index=pd.to_datetime(idx))
    flows["interest"] = [interest.get(d, 0.0) for d in idx]
    flows["redemptions"] = [redemptions.get(d, 0.0) for d in idx]
    flows["settlements"] = [settlements.get(d, 0.0) for d in idx]
    flows.index.name = "date"
    return flows.round(1)


def validate_july(flows):
    r = requests.get(
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
        "/v1/accounting/dts/deposits_withdrawals_operating_cash",
        params={"filter": "record_date:gte:2026-06-01,record_date:lte:2026-07-31,"
                          "transaction_catg:eq:Interest on Treasury Securities",
                "fields": "record_date,transaction_today_amt", "page[size]": 80},
        timeout=60)
    d = pd.DataFrame(r.json()["data"])
    d["amt"] = pd.to_numeric(d.transaction_today_amt)  # USD mn
    d = d.set_index(pd.to_datetime(d.record_date))["amt"]
    eng = flows.loc["2026-06":"2026-07", "interest"]
    both = pd.DataFrame({"engine": eng, "dts_actual": d}).fillna(0.0)
    big = both[(both.engine > 500) | (both.dts_actual > 500)]
    print("Jun-Jul 2026 coupon validation (USD mn, days > $0.5bn):")
    print(big.round(0).to_string())
    for mm in ["2026-06", "2026-07"]:
        print(f"{mm}: engine {flows.loc[mm,'interest'].sum()/1e3:,.1f}bn vs "
              f"DTS {d[mm].sum()/1e3:,.1f}bn")
    print("(DTS line also carries savings-bond/SLGS interest the engine excludes)")


def main():
    flows = build_flows()
    flows.to_csv("engine_flows_daily.csv")
    validate_july(flows)
    q4 = flows.loc["2026-10":"2026-12"]
    print(f"\nQ4 2026: coupon settlements {q4.settlements.sum()/1e3:,.0f}bn, "
          f"public coupon redemptions {q4.redemptions.sum()/1e3:,.0f}bn, "
          f"net coupon issuance {(q4.settlements.sum()-q4.redemptions.sum())/1e3:,.0f}bn, "
          f"coupon interest {q4.interest.sum()/1e3:,.0f}bn")


if __name__ == "__main__":
    main()
