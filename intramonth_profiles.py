"""Intra-month deficit profiles from the daily DTS: the layer below monthly.

Below monthly frequency there is no external benchmark - the MTS is monthly and
the DTS is itself the primary source. The honesty devices are therefore:
  (a) dispersion bands (interquartile range across the ~40 observed months),
  (b) the accounting constraint that these paths sum to the monthly totals
      already reconciled against the MTS, and
  (c) separating estimated/corporate tax months (Jan, Apr, Jun, Sep, Dec) from
      regular months - two genuinely different intra-month regimes.

Daily flows are budget-classified through the same mapping as everything else
(refunds negative receipts, offsetting deposits negative outlays, financing
stripped). Deficit is positive when outlays exceed receipts.

Outputs:
  daily_profile.csv   cumulative deficit by business day (1..19 + EOM),
                      mean and IQR, tax months vs regular months
  weekly_profile.csv  mean receipts/outlays/deficit per week-of-month
"""

import pandas as pd

from reconcile_dts_mts import apply_mapping, load_dts

TAX_MONTHS = {1, 4, 6, 9, 12}  # estimated + corporate payment dates on the 15th


def daily_budget_flows():
    mapping = pd.read_csv("dts_mts_cbo_mapping.csv")
    dts = load_dts("2023-03-01", "2026-06-30", refresh=False)
    mapped, _, _ = apply_mapping(dts, mapping)
    live = mapped[~mapped.treatment.isin(["strip", "exclude", "total_row"])].copy()
    refunds = live.treatment.eq("negative_receipt")
    offsetting = live.treatment.eq("offsetting")
    live.loc[refunds | offsetting, "amount"] *= -1
    daily = (live.groupby(["record_date", "mts_side"])["amount"].sum()
             .unstack().fillna(0.0) / 1e3)  # -> USD bn
    daily["deficit"] = daily["outlay"] - daily["receipt"]
    daily["month"] = daily.index.to_period("M")
    daily["bd"] = daily.groupby("month").cumcount() + 1  # business-day index
    return daily


def build_daily_profile(daily):
    """Cumulative deficit at business days 1..18, plus the month-end total.

    Every month in the sample has at least 18 business days; days 19-23 are
    collapsed into the EOM point so month-length differences never misalign
    the grid, and EOM equals the MTS-reconciled month total by construction.
    """
    rows = []
    for month, g in daily.groupby("month"):
        cum = g.sort_values("bd")["deficit"].cumsum()
        rec = {"month": str(month), "regime": "tax" if month.month in TAX_MONTHS else "regular"}
        for k in range(1, 19):
            rec[f"bd{k}"] = cum.iloc[k - 1]
        rec["eom"] = cum.iloc[-1]
        rows.append(rec)
    paths = pd.DataFrame(rows)

    out = []
    cols = [f"bd{k}" for k in range(1, 19)] + ["eom"]
    for regime, g in paths.groupby("regime"):
        for i, c in enumerate(cols):
            out.append({
                "x": c, "order": i, "regime": regime, "n_months": len(g),
                "mean": g[c].mean(), "p25": g[c].quantile(.25), "p75": g[c].quantile(.75),
            })
    prof = pd.DataFrame(out).sort_values(["regime", "order"])
    prof.to_csv("daily_profile.csv", index=False)
    return prof


def build_weekly_profile(daily):
    """Mean receipts / outlays / deficit per week-of-month (business-day weeks)."""
    d = daily.copy()
    d["week"] = ((d["bd"] - 1) // 5 + 1).clip(upper=5)
    per_month = d.groupby(["month", "week"])[["receipt", "outlay", "deficit"]].sum()
    prof = per_month.groupby("week").mean()
    prof["n_days_avg"] = d.groupby(["month", "week"])["bd"].count().groupby("week").mean()
    prof.to_csv("weekly_profile.csv")
    return prof


def main():
    daily = daily_budget_flows()
    prof = build_daily_profile(daily)
    weekly = build_weekly_profile(daily)

    pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
    print(f"Sample: {daily['month'].nunique()} months "
          f"({(daily['month'].dt.month.isin(TAX_MONTHS)).sum() and ''}"
          f"tax months = Jan/Apr/Jun/Sep/Dec)")
    print("\nWeekly profile (USD bn, mean per week-of-month):")
    print(weekly.to_string())
    eom = prof[prof.x.eq("eom")][["regime", "mean", "p25", "p75"]]
    print("\nMonth-end cumulative deficit by regime (USD bn):")
    print(eom.to_string(index=False))
    mid = prof[prof.x.eq("bd10")][["regime", "mean"]]
    print("\nMid-month (bd10) cumulative deficit by regime (USD bn):")
    print(mid.to_string(index=False))


if __name__ == "__main__":
    main()
