"""Annual three-way reconciliation: DTS-built vs MTS vs CBO historical actuals.

Aggregates the monthly reconciliation to fiscal years and compares against
CBO's historical budget data workbook (data/cbo_historical_budget_2026_02.xlsx,
the Feb 2026 vintage, actuals through FY2025). Only FY2024 and FY2025 are
fully inside the DTS window (2023-03 onward), so those are the test years.

The point of the three-way: MTS fiscal-year totals should match CBO actuals
almost exactly (CBO's actuals ARE Treasury's numbers), so any DTS-vs-CBO gap
should equal the DTS-vs-MTS gap. If MTS and CBO disagree somewhere, the
MTS->CBO category hop needs attention; if they agree, all reconciliation risk
sits in the DTS->MTS leg, which is where the mapping operates.

Writes cbo_comparison.csv and prints a summary.
"""

import openpyxl
import pandas as pd

FYS = [2024, 2025]


def fy(period: pd.Period) -> int:
    return period.year + (1 if period.month >= 10 else 0)


def cbo_rows(ws, cols: dict) -> pd.DataFrame:
    """Extract {fiscal_year: {name: value}} from a CBO sheet; cols = name -> 0-based col."""
    out = {}
    for row in ws.iter_rows(values_only=True):
        y = row[0]
        if isinstance(y, (int, float)) and int(y) in FYS:
            out[int(y)] = {name: float(row[i]) * 1000 for name, i in cols.items()}  # $bn -> $mn
    return pd.DataFrame(out).T


def main():
    wb = openpyxl.load_workbook("data/cbo_historical_budget_2026_02.xlsx")
    cbo_rev = cbo_rows(
        wb["2. Revenues"],
        {"individual": 1, "payroll": 2, "corporate": 3, "excise": 4,
         "estate_gift": 5, "customs": 6, "misc_receipts": 7, "total_receipts": 8},
    )
    cbo_rev["income_payroll"] = cbo_rev["individual"] + cbo_rev["payroll"]
    cbo_tot = cbo_rows(
        wb["1. Rev, Outlays, Surplus, Debt"],
        {"revenues": 1, "outlays": 2, "total_deficit": 6},
    )
    cbo_tot["total_deficit"] = -cbo_tot["total_deficit"]  # positive = deficit
    cbo_out = cbo_rows(
        wb["3. Outlays"],
        {"discretionary": 1, "mandatory_prog": 2, "offsetting_rcpts": 3, "net_interest": 4},
    )
    cbo_mand = cbo_rows(
        wb["5. Mandatory Outlays"],
        {"social_security": 1, "medicare": 2, "medicaid": 3},
    )

    # ---- our monthly outputs -> fiscal years ----
    rcpt = pd.read_csv("recon_receipts.csv")
    rcpt["fy"] = rcpt["month"].map(lambda m: fy(pd.Period(m)))
    dfc = pd.read_csv("recon_deficit.csv")
    dfc["fy"] = dfc["month"].map(lambda m: fy(pd.Period(m)))

    rows = []
    for bucket, cbo_name in [
        ("income_payroll", "income_payroll"), ("corporate", "corporate"),
        ("excise", "excise"), ("estate_gift", "estate_gift"),
        ("customs", "customs"), ("misc_receipts", "misc_receipts"),
    ]:
        g = rcpt[rcpt.bucket == bucket].groupby("fy")[["dts", "mts"]].sum()
        for y in FYS:
            rows.append({"category": bucket, "fy": y, "dts_built": g.loc[y, "dts"],
                         "mts": g.loc[y, "mts"], "cbo": cbo_rev.loc[y, cbo_name]})
    g = dfc.groupby("fy")[["dts_receipts", "mts_receipts", "dts_implied_deficit", "mts_deficit"]].sum()
    for y in FYS:
        rows.append({"category": "total_receipts", "fy": y, "dts_built": g.loc[y, "dts_receipts"],
                     "mts": g.loc[y, "mts_receipts"], "cbo": cbo_rev.loc[y, "total_receipts"]})
        rows.append({"category": "total_deficit", "fy": y, "dts_built": g.loc[y, "dts_implied_deficit"],
                     "mts": g.loc[y, "mts_deficit"], "cbo": cbo_tot.loc[y, "total_deficit"]})

    # ---- outlay categories: rebuild DTS flows at cbo_category level ----
    from reconcile_dts_mts import load_dts, apply_mapping

    mapping = pd.read_csv("dts_mts_cbo_mapping.csv")
    dts = load_dts("2023-03-01", "2026-06-30", refresh=False)
    mapped, _, _ = apply_mapping(dts, mapping)
    mapped["fy"] = mapped["month"].map(fy)
    live = mapped[~mapped.treatment.isin(["strip", "exclude", "total_row"])]

    def dts_fy(mask, sign=1):
        s = live[mask].groupby("fy")["amount"].sum() * sign
        return {y: float(s.get(y, 0.0)) for y in FYS}

    # Social Security: benefit lines vs CBO Social Security (program outlays)
    ss = dts_fy(live.cbo_category.eq("social_security"))
    # Medicare: gross CMS medicare lines minus direct premium receipts
    med_gross = dts_fy(live.cbo_category.eq("medicare"))
    med_prem = dts_fy(live.line.eq("HHS - Medicare Premiums"))
    # Medicaid grants
    mcd = dts_fy(live.cbo_category.eq("medicaid"))
    # Net interest: cash interest vs CBO net interest (accrual)
    ni = dts_fy(live.line.eq("Interest on Treasury Securities"))

    mts_int = pd.read_csv("data/mts_table5_raw.csv")
    mts_int["month"] = pd.to_datetime(mts_int["record_date"]).dt.to_period("M")
    mts_int["fy"] = mts_int["month"].map(fy)
    mts_int["amt"] = pd.to_numeric(mts_int["current_month_net_outly_amt"], errors="coerce") / 1e6
    def t5row(name):
        s = mts_int[mts_int.classification_desc.eq(name)].groupby("fy")["amt"].sum()
        return {y: float(s.get(y, 0.0)) for y in FYS}
    mts_net_int = {
        y: t5row("Total--Interest on the Public Debt")[y]
        + t5row("Total--Interest Received by Trust Funds")[y] + t5row("Other Interest")[y]
        for y in FYS
    }

    for y in FYS:
        rows.append({"category": "social_security", "fy": y, "dts_built": ss[y],
                     "mts": None, "cbo": cbo_mand.loc[y, "social_security"]})
        rows.append({"category": "medicare_net", "fy": y, "dts_built": med_gross[y] - med_prem[y],
                     "mts": None, "cbo": cbo_mand.loc[y, "medicare"]})
        rows.append({"category": "medicaid", "fy": y, "dts_built": mcd[y],
                     "mts": None, "cbo": cbo_mand.loc[y, "medicaid"]})
        rows.append({"category": "net_interest", "fy": y, "dts_built": ni[y],
                     "mts": mts_net_int[y], "cbo": cbo_out.loc[y, "net_interest"]})

    out = pd.DataFrame(rows)
    out["dts_vs_cbo_pct"] = (out.dts_built - out.cbo) / out.cbo.abs() * 100
    out["mts_vs_cbo_pct"] = (out.mts - out.cbo) / out.cbo.abs() * 100
    out.to_csv("cbo_comparison.csv", index=False)

    pd.set_option("display.float_format", lambda x: f"{x:,.0f}")
    with pd.option_context("display.width", 200):
        print(out.to_string(index=False))


if __name__ == "__main__":
    main()
