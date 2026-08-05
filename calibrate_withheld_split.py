"""Calibrate the withheld income-tax / FICA split from MTS Table 4 history.

The DTS line "Taxes - Withheld Individual/FICA" mixes individual income tax
withholding with FICA payroll tax; the IRS allocates to the trust funds later.
MTS Table 4 publishes the allocated pieces monthly (gross basis): "Withheld"
under Individual Income Taxes, and FICA under the three trust funds. Same for
non-withheld: "Other" individual income vs SECA.

This script pulls the full Table 4 history (Mar 2015 ->) and produces the split
parameters the CBO-composition stage needs:

  withheld_income_share  = Withheld / (Withheld + FICA)
  nonwithheld_income_share = Other / (Other + SECA)

Writes withheld_split_calibration.csv (monthly + trailing-12m) and prints the
current calibration. The split is only needed when the individual-vs-payroll
composition matters; the combined income_payroll bucket never uses it.
"""

import pandas as pd

from reconcile_dts_mts import cached_fetch

T4 = "/v1/accounting/mts/mts_table_4"

ROWS = {
    "Withheld": "withheld_income",
    "Federal Insurance Contributions Act Taxes": "fica",
    "Other": "other_income",
    "Self-Employment Contributions Act Taxes": "seca",
}


def main():
    df = cached_fetch(
        "data/mts_table4_gross_raw.csv", T4,
        {"filter": "record_date:gte:2015-03-01,record_date:lte:2026-06-30,record_type_cd:eq:SRS",
         "fields": "record_date,classification_desc,current_month_gross_rcpt_amt,record_type_cd"},
        refresh=False,
    )
    df["month"] = pd.to_datetime(df["record_date"]).dt.to_period("M")
    df["amt"] = pd.to_numeric(df["current_month_gross_rcpt_amt"], errors="coerce") / 1e6
    df = df[df.classification_desc.isin(ROWS)]
    df["key"] = df.classification_desc.map(ROWS)
    # FICA appears once per trust fund (OASI, DI, HI); "Other" also under SECA parents -
    # summing per (month, key) is the correct aggregation for FICA, and "Other"/"Withheld"
    # appear once under Individual Income Taxes plus adjustment context rows with nulls.
    m = df.groupby(["month", "key"])["amt"].sum().unstack()

    out = pd.DataFrame(index=m.index)
    out["withheld_income_share"] = m["withheld_income"] / (m["withheld_income"] + m["fica"])
    out["nonwithheld_income_share"] = m["other_income"] / (m["other_income"] + m["seca"])
    for c in list(out.columns):
        out[c + "_ttm"] = (
            m[["withheld_income", "fica"]].rolling(12).sum().pipe(
                lambda g: g["withheld_income"] / (g["withheld_income"] + g["fica"]))
            if c == "withheld_income_share" else
            m[["other_income", "seca"]].rolling(12).sum().pipe(
                lambda g: g["other_income"] / (g["other_income"] + g["seca"]))
        )
    out.index.name = "month"
    out.round(4).to_csv("withheld_split_calibration.csv")

    cur = out.dropna().iloc[-1]
    print(f"History: {out.index[0]} .. {out.index[-1]} ({len(out)} months)")
    print(f"Current trailing-12m withheld income share:    {cur['withheld_income_share_ttm']:.3f}"
          f"  (income {cur['withheld_income_share_ttm']:.1%} / FICA {1-cur['withheld_income_share_ttm']:.1%})")
    print(f"Current trailing-12m non-withheld income share: {cur['nonwithheld_income_share_ttm']:.3f}")
    ttm = out["withheld_income_share_ttm"].dropna()
    print(f"Withheld share stability: min {ttm.min():.3f}, max {ttm.max():.3f} over {len(ttm)} months")
    monthly = out["withheld_income_share"].dropna()
    print(f"Monthly seasonality of the share (Jan-Dec std): {monthly.groupby(monthly.index.month).mean().std():.4f}"
          " - use month-specific shares if this matters at CBO stage")


if __name__ == "__main__":
    main()
