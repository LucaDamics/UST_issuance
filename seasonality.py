"""Seasonal split of CBO annual projections using DTS-derived monthly shares.

Builds monthly cash series per seasonality bucket from the mapped DTS data,
extracts month-of-fiscal-year share profiles from the complete fiscal years
(FY2024, FY2025), applies them to CBO's Feb 2026 baseline (FY2026), and
validates the projected monthly deficit path against the actual months
published so far (Oct 2025 - Jun 2026).

Outputs:
  seasonality_monthly_shares.csv  bucket x month-of-FY share profile
  dts_bucket_monthly.csv          the underlying DTS bucket series
  fy2026_monthly_split.csv        projected vs actual FY2026 monthly path

Caveats (v1, documented in the summary):
- net_interest seasonality is CASH coupon timing; CBO's level is accrual.
  The accrual-cash gap (mostly bill discount) belongs to the financing side.
- Payment-date shifts (1st/3rd on a weekend) are inside the FY24/FY25 shares;
  a calendar rule should replace raw shares in v2.
"""

import pandas as pd

from reconcile_dts_mts import apply_mapping, load_dts

RECEIPT_BUCKETS = ["income_payroll", "corporate", "excise", "estate_gift", "customs", "misc_receipts"]
OUTLAY_BUCKETS = [
    "social_security", "medicare_net", "medicaid", "defence", "net_interest",
    "veterans", "income_security_retirement", "education_training",
    "health_other", "nondefense_other",
]
CBO_CAT_TO_BUCKET = {
    "social_security": "social_security",
    "medicare": "medicare_net",
    "medicaid": "medicaid",
    "defence_discretionary": "defence",
    "net_interest": "net_interest",
    "veterans_mandatory": "veterans",
    "veterans_mixed": "veterans",
    "income_security": "income_security_retirement",
    "federal_retirement": "income_security_retirement",
    "education_mixed": "education_training",
    "health_subsidies": "health_other",
}


def fy_of(p): return p.year + (1 if p.month >= 10 else 0)


def build_dts_bucket_monthly():
    mapping = pd.read_csv("dts_mts_cbo_mapping.csv")
    dts = load_dts("2023-03-01", "2026-06-30", refresh=False)
    mapped, _, _ = apply_mapping(dts, mapping)
    live = mapped[~mapped.treatment.isin(["strip", "exclude", "total_row"])].copy()

    refunds = live.treatment.eq("negative_receipt")
    offsetting = live.treatment.eq("offsetting")
    live.loc[refunds | offsetting, "amount"] *= -1
    live["side"] = live["mts_side"]

    rec = (live[live.side.eq("receipt")]
           .groupby(["month", "recon_bucket"])["amount"].sum().unstack().fillna(0.0))

    out_flows = live[live.side.eq("outlay")].copy()
    out_flows["bucket"] = out_flows["cbo_category"].map(CBO_CAT_TO_BUCKET)
    # Medicare premiums (offsetting deposit) net against Medicare, not the residual
    prem = out_flows.line.eq("HHS - Medicare Premiums")
    out_flows.loc[prem, "bucket"] = "medicare_net"
    out_flows["bucket"] = out_flows["bucket"].fillna("nondefense_other")
    out = out_flows.groupby(["month", "bucket"])["amount"].sum().unstack().fillna(0.0)

    monthly = pd.concat(
        {"receipt": rec[RECEIPT_BUCKETS], "outlay": out[OUTLAY_BUCKETS]}, axis=1
    )
    monthly.index.name = "month"
    return monthly


def share_profiles(monthly):
    fys = [fy_of(m) for m in monthly.index]
    sub = monthly[[y in (2024, 2025) for y in fys]]
    by_fy = [fy_of(m) for m in sub.index]
    by_month = [m.month for m in sub.index]
    shares = {}
    for col in monthly.columns:
        g = sub[col].groupby([by_fy, by_month]).sum().unstack(0)  # month x fy
        prof = (g / g.sum()).mean(axis=1)
        shares[col] = prof / prof.sum()
    prof = pd.DataFrame(shares)
    order = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    return prof.reindex(order)


def main():
    monthly = build_dts_bucket_monthly()
    monthly.to_csv("dts_bucket_monthly.csv")

    prof = share_profiles(monthly)
    flat = prof.copy()
    flat.columns = [f"{s}:{b}" for s, b in flat.columns]
    flat.index.name = "calendar_month"
    flat.to_csv("seasonality_monthly_shares.csv")

    cbo = pd.read_csv("cbo_projection_buckets.csv")
    cbo26 = cbo[cbo.fy.eq(2026)].set_index(["side", "bucket"])["usd_mn"]

    months = pd.period_range("2025-10", "2026-09", freq="M")
    proj = pd.DataFrame(index=months)
    for side, buckets in [("receipt", RECEIPT_BUCKETS), ("outlay", OUTLAY_BUCKETS)]:
        for b in buckets:
            annual = cbo26.get((side, b))
            if annual is None:
                continue
            proj[(side, b)] = [annual * prof.loc[m.month, (side, b)] for m in months]
    proj["proj_receipts"] = proj[[c for c in proj.columns if c[0] == "receipt"]].sum(axis=1)
    proj["proj_outlays"] = proj[[c for c in proj.columns if c[0] == "outlay"]].sum(axis=1)
    proj["proj_deficit"] = proj["proj_outlays"] - proj["proj_receipts"]

    act = pd.read_csv("recon_deficit.csv")
    act["month"] = pd.PeriodIndex(act["month"], freq="M")
    act = act.set_index("month")
    proj["actual_mts_deficit"] = act["mts_deficit"]
    proj["actual_dts_deficit"] = act["dts_implied_deficit"]

    keep = proj[["proj_receipts", "proj_outlays", "proj_deficit",
                 "actual_mts_deficit", "actual_dts_deficit"]].round(0)
    keep.index.name = "month"
    keep.to_csv("fy2026_monthly_split.csv")

    have = keep.dropna(subset=["actual_mts_deficit"])
    err = have["proj_deficit"] - have["actual_mts_deficit"]
    pd.set_option("display.float_format", lambda x: f"{x:,.0f}")
    print("FY2026 monthly deficit: CBO annual x DTS seasonality vs MTS actual (USD mn)")
    print(have[["proj_deficit", "actual_mts_deficit", "actual_dts_deficit"]].to_string())
    print(f"\nMonths validated: {len(have)}  mean error {err.mean():,.0f}  "
          f"mean |error| {err.abs().mean():,.0f}  "
          f"(vs mean |actual| {have['actual_mts_deficit'].abs().mean():,.0f})")
    print(f"Sign agreement (surplus/deficit months): "
          f"{(have.proj_deficit.gt(0) == have.actual_mts_deficit.gt(0)).mean():.0%}")


if __name__ == "__main__":
    main()
