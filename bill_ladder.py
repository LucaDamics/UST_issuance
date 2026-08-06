"""Bill ladder + full auction calendar -> gross issuance, weekly and monthly.

Gross issuance = coupon settlements (engine) + gross bills. Gross bills are
mechanical once the ladder is explicit: every outstanding bill has a maturity
date, every forward week issues enough across tenors to cover that week's
maturities plus the TGA model's solved net-bill requirement, and what is
issued at tenor T comes back as a maturity T later (the recursion that makes
short-tenor supply so sticky).

Ladder start: MSPD live bills (Jun 30) + actual auctions settled Jul 1-Aug 6
+ rule-generated issuance Aug 7-Sep 30 at latest per-tenor sizes (Treasury's
Q3 is already largely announced; sizes flex proportionally thereafter).

Also writes the explicit forward auction calendar (auction_calendar.csv):
every coupon auction through Dec 2027 - official XML dates where published
(through Jan 2027), rule-generated after (mid-month group auctions the week
before the 15th and settles on the 15th; end-month group auctions in the last
week and settles at EOM; TIPS/FRN on their cycles) - and the weekly bill
pattern (4/6/8-week Tue auction Thu settle; 13/26 Mon->Thu; 17 Wed->Tue;
52-week every fourth Tue).

Outputs: auction_calendar.csv, weekly_issuance.csv, monthly_issuance.csv
"""

import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pandas as pd

from daily_model import us_federal_holidays
from issuance_engine import (COUPON_SIZES, FRN_SIZE, NEW_MONTHS_10_20_30,
                             TIPS_CYCLE, next_bd, month_end)

XML = ("/private/tmp/claude-501/-Users-lucadamico-UST-project/"
       "a46ad2c7-636e-43b3-b827-9f10503dee45/scratchpad/Tentative-Auction-Schedule.xml")
BILL_TENORS_W = {"4-Week": 4, "6-Week": 6, "8-Week": 8, "13-Week": 13,
                 "17-Week": 17, "26-Week": 26, "52-Week": 52}


def prev_bd(d: date) -> date:
    hol = us_federal_holidays(d.year) | us_federal_holidays(d.year + 1)
    while d.weekday() >= 5 or d in hol:
        d -= timedelta(days=1)
    return d


def official_calendar():
    root = ET.parse(XML).getroot()
    rows = []
    for e in root.iter("AuctionCalendarDate"):
        g = lambda t: (e.find(t).text if e.find(t) is not None else None)
        rows.append({
            "security": g("SecurityTermWeekYear"), "type": g("SecurityType"),
            "reopening": g("ReOpeningIndicator") or "N",
            "tips": g("TIPS"), "frn": g("FloatingRate"),
            "auction_date": g("AuctionDate"), "settle_date": g("SettlementDate"),
            "source": "official_xml",
        })
    return pd.DataFrame(rows)


def generated_coupon_calendar():
    """Rule-generated coupon auctions Feb - Dec 2027 (beyond the XML window)."""
    rows = []
    for m in pd.period_range("2027-02", "2027-12", freq="M"):
        y, mo = m.year, m.month
        new = mo in NEW_MONTHS_10_20_30
        mid_settle = next_bd(date(y, mo, 15))
        eom_settle = next_bd(month_end(y, mo))
        # mid-month group: auctions Tue/Wed/Thu of the week before settlement
        anchor = prev_bd(mid_settle - timedelta(days=3))
        for i, sec in enumerate(["3-Year", "10-Year", "30-Year"]):
            rows.append({"security": sec, "type": "BOND" if sec == "30-Year" else "NOTE",
                         "reopening": "N" if (new or sec == "3-Year") else "Y",
                         "tips": "N", "frn": "N",
                         "auction_date": str(prev_bd(anchor - timedelta(days=2 - i))),
                         "settle_date": str(mid_settle), "source": "rule"})
        # end-month group: auctions in the final week, settle EOM
        anchor = prev_bd(eom_settle - timedelta(days=4))
        for i, sec in enumerate(["2-Year", "5-Year", "7-Year"]):
            rows.append({"security": sec, "type": "NOTE", "reopening": "N",
                         "tips": "N", "frn": "N",
                         "auction_date": str(prev_bd(anchor - timedelta(days=2 - i))),
                         "settle_date": str(eom_settle), "source": "rule"})
        rows.append({"security": "20-Year", "type": "BOND",
                     "reopening": "N" if new else "Y", "tips": "N", "frn": "N",
                     "auction_date": str(prev_bd(eom_settle - timedelta(days=8))),
                     "settle_date": str(eom_settle), "source": "rule"})
        t_term, t_kind, _ = TIPS_CYCLE[mo]
        rows.append({"security": t_term, "type": "NOTE" if t_term != "30-Year" else "BOND",
                     "reopening": "N" if t_kind == "new" else "Y", "tips": "Y", "frn": "N",
                     "auction_date": str(prev_bd(eom_settle - timedelta(days=6))),
                     "settle_date": str(eom_settle), "source": "rule"})
        rows.append({"security": "2-Year", "type": "NOTE",
                     "reopening": "N" if mo in (1, 4, 7, 10) else "Y",
                     "tips": "N", "frn": "Y",
                     "auction_date": str(prev_bd(eom_settle - timedelta(days=5))),
                     "settle_date": str(eom_settle), "source": "rule"})
    return pd.DataFrame(rows)


def generated_bill_calendar(start: date, end: date):
    """Weekly bill pattern: 13/26wk Mon->Thu, 4/6/8wk Tue->Thu (52wk every 4th
    Tue), 17wk Wed->following Tue."""
    rows = []
    d = start
    week_i = 0
    while d <= end:
        if d.weekday() == 0:  # Monday
            mon, tue, wed = d, d + timedelta(days=1), d + timedelta(days=2)
            thu = next_bd(d + timedelta(days=3))
            for sec, adate, sdate in [
                ("13-Week", mon, thu), ("26-Week", mon, thu),
                ("4-Week", tue, thu), ("6-Week", tue, thu), ("8-Week", tue, thu),
                ("17-Week", wed, next_bd(wed + timedelta(days=6))),
            ]:
                rows.append({"security": sec, "type": "BILL", "reopening": "N",
                             "tips": "N", "frn": "N",
                             "auction_date": str(next_bd(adate)), "settle_date": str(sdate),
                             "source": "rule"})
            if week_i % 4 == 0:
                rows.append({"security": "52-Week", "type": "BILL", "reopening": "N",
                             "tips": "N", "frn": "N",
                             "auction_date": str(next_bd(tue)), "settle_date": str(thu),
                             "source": "rule"})
            week_i += 1
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def initial_ladder():
    """Live bills by maturity date at model start (Oct 1, 2026), USD mn."""
    m = pd.read_csv("data/mspd_2026_06.csv")
    b = m[m.security_class1_desc.eq("Bills Maturity Value")].copy()
    for c in ["issued_amt", "redeemed_amt"]:
        b[c] = pd.to_numeric(b[c], errors="coerce").fillna(0.0)
    b["mat"] = pd.to_datetime(b.maturity_date, errors="coerce")
    b = b[b.mat > "2026-06-30"]
    ladder = b.groupby("mat").apply(lambda g: (g.issued_amt - g.redeemed_amt).sum(),
                                    include_groups=False)
    # public ladder: SOMA-held bills roll at auction outside the public sizes
    import json
    soma = json.load(open("data/soma_latest.json"))["soma"]["holdings"]
    soma_bills = sum(float(h["parValue"]) for h in soma
                     if h.get("securityType") == "Bills" and h.get("parValue")) / 1e6
    ladder *= max(0.0, 1 - soma_bills / ladder.sum())

    a = pd.read_csv("data/auctions_2025on.csv")
    ab = a[a.security_type.eq("Bill") & a.issue_date.ge("2026-07-01")].copy()
    ab["mat"] = pd.to_datetime(ab.maturity_date)
    ab["par"] = pd.to_numeric(ab.offering_amt, errors="coerce").fillna(0.0) / 1e6
    ladder = ladder.add(ab.groupby("mat")["par"].sum(), fill_value=0.0)

    # rule-generated Aug 7 - Sep 30 at latest per-tenor sizes
    sizes = {}
    for t in BILL_TENORS_W:
        s = a[a.security_term.eq(t) & a.offering_amt.notna()].sort_values("auction_date")
        if len(s):
            sizes[t] = pd.to_numeric(s.offering_amt, errors="coerce").iloc[-1] / 1e6
    cal = generated_bill_calendar(date(2026, 8, 7), date(2026, 9, 30))
    for _, r in cal.iterrows():
        t = r.security
        sd = pd.Timestamp(r.settle_date)
        mat = sd + pd.Timedelta(weeks=BILL_TENORS_W[t])
        ladder = ladder.add(pd.Series({mat: sizes.get(t, 0.0)}), fill_value=0.0)
    return ladder, sizes


def weekly_issuance():
    eng = pd.read_csv("engine_flows_daily.csv", parse_dates=["date"]).set_index("date")
    tga = pd.read_csv("tga_daily.csv")
    tga["date"] = pd.to_datetime(tga[tga.columns[0]])
    net_bills_w = tga.set_index("date")["net_bills"].resample("W-FRI").sum()

    ladder, sizes = initial_ladder()
    share = pd.Series(sizes) / sum(sizes.values())

    rows = []
    for wk_end, nb in net_bills_w.items():
        wk_start = wk_end - pd.Timedelta(days=6)
        matured = ladder[(ladder.index >= wk_start) & (ladder.index <= wk_end)].sum()
        gross = max(0.0, matured + nb)
        for t, sh in share.items():
            mat = wk_end - pd.Timedelta(days=2) + pd.Timedelta(weeks=BILL_TENORS_W[t])
            ladder = ladder.add(pd.Series({mat: gross * sh}), fill_value=0.0)
        cpn = eng.loc[wk_start:wk_end, "settlements"].sum()
        rows.append({"week_ending": wk_end.date(), "gross_bills": gross,
                     "bill_redemptions": matured, "net_bills": nb,
                     "coupon_settlements": cpn, "total_gross": gross + cpn})
    w = pd.DataFrame(rows).set_index("week_ending")
    (w / 1e3).round(1).to_csv("weekly_issuance.csv")
    # monthly by actual settle date: bills at each week's Thursday, coupons daily
    thu = pd.to_datetime(pd.Index(w.index)) - pd.Timedelta(days=1)
    m_bills = w[["gross_bills", "bill_redemptions", "net_bills"]].copy()
    m_bills.index = thu.to_period("M")
    m_bills = m_bills.groupby(level=0).sum()
    m_cpn = eng["settlements"].loc["2026-10":"2027-12"].resample("ME").sum()
    m_cpn.index = m_cpn.index.to_period("M")
    m = m_bills.join(m_cpn.rename("coupon_settlements"), how="left").fillna(0.0)
    m["total_gross"] = m.gross_bills + m.coupon_settlements
    m = m[m.index >= pd.Period("2026-10", "M")]
    (m / 1e3).round(1).to_csv("monthly_issuance.csv")
    return w, m


def main():
    cal = pd.concat([official_calendar(),
                     generated_coupon_calendar(),
                     generated_bill_calendar(date(2027, 2, 1), date(2027, 12, 31))],
                    ignore_index=True).sort_values("auction_date")
    cal.to_csv("auction_calendar.csv", index=False)
    n_off = (cal.source == "official_xml").sum()
    print(f"auction_calendar.csv: {len(cal)} auctions ({n_off} official XML, rest rule-generated)")

    w, m = weekly_issuance()
    pd.set_option("display.float_format", lambda x: f"{x/1e3:,.0f}")
    print("\nMonthly gross issuance (USD bn):")
    print(m[["gross_bills", "coupon_settlements", "total_gross", "net_bills"]]
          .iloc[[0, 3, 6, 9, 12, 14]].to_string())
    print(f"\nAvg weekly gross bills: {w.gross_bills.mean()/1e3:,.0f}bn"
          f"  |  avg weekly total gross: {w.total_gross.mean()/1e3:,.0f}bn")


if __name__ == "__main__":
    main()
