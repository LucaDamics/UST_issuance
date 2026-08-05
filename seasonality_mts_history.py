"""Seasonality v2: monthly share profiles from long MTS history (Table 9).

Why: the DTS-based v1 profiles can only use FY2024-FY2025 - the only complete
fiscal years on the post-Feb-2023 DTS format. MTS Table 9 (receipts by source,
outlays by function) is format-stable back to Mar 2015, giving ten complete
fiscal years of monthly category history. Monthly shares belong on that longer
history; the DTS keeps its role for intra-month (weekly/daily) shape later.

Profile: per-bucket median month-of-FY share across FY2016-2019 + FY2023-2025
(COVID years FY2020-2022 excluded; median damps one-off credit-reform months
like Aug 2023). Interest here is MTS-accrual - the correct shape when the
target is the published MTS deficit (v1 used lumpy cash-coupon shares).

Buckets are the projection buckets with one merge: medicaid + health_other ->
health_550 (function 550 is not split into Medicaid vs other in Table 9).

Validates FY2026 exactly like seasonality.py and prints v1 vs v2 side by side.
Writes: data/mts_table9_raw.csv, seasonality_shares_v2.csv, fy2026_monthly_split_v2.csv
"""

import pandas as pd

from reconcile_dts_mts import cached_fetch

T9 = "/v1/accounting/mts/mts_table_9"

# line_code_nbr -> (side, bucket); functions merge into projection bucket space
CODES = {
    "20": ("receipt", "income_payroll"),   # individual income taxes
    "50": ("receipt", "income_payroll"),   # employment & general retirement
    "60": ("receipt", "income_payroll"),   # unemployment insurance
    "70": ("receipt", "income_payroll"),   # other retirement
    "30": ("receipt", "corporate"),
    "80": ("receipt", "excise"),
    "90": ("receipt", "estate_gift"),
    "100": ("receipt", "customs"),
    "110": ("receipt", "misc_receipts"),
    "270": ("outlay", "social_security"),
    "250": ("outlay", "medicare_net"),
    "240": ("outlay", "health_550"),
    "140": ("outlay", "defence"),
    "260": ("outlay", "income_security_retirement"),
    "280": ("outlay", "veterans"),
    "230": ("outlay", "education_training"),
    "320": ("outlay", "net_interest"),
    "340": ("outlay", "_total_outlays"),
}
PROFILE_FYS = [2016, 2017, 2018, 2019, 2023, 2024, 2025]
RECEIPT_BUCKETS = ["income_payroll", "corporate", "excise", "estate_gift", "customs", "misc_receipts"]
OUTLAY_BUCKETS = ["social_security", "medicare_net", "health_550", "defence",
                  "income_security_retirement", "veterans", "education_training",
                  "net_interest", "nondefense_other"]


def fy_of(p): return p.year + (1 if p.month >= 10 else 0)


def load_t9(refresh=False):
    df = cached_fetch(
        "data/mts_table9_raw.csv", T9,
        {"filter": "record_date:gte:2015-03-01,record_date:lte:2026-06-30",
         "fields": "record_date,classification_desc,line_code_nbr,current_month_rcpt_outly_amt"},
        refresh,
    )
    df["month"] = pd.to_datetime(df["record_date"]).dt.to_period("M")
    df["amt"] = pd.to_numeric(df["current_month_rcpt_outly_amt"], errors="coerce") / 1e6
    df["code"] = df["line_code_nbr"].astype(str)
    return df


def bucket_monthly(t9):
    rows = t9[t9.code.isin(CODES)].copy()
    rows["side"] = rows.code.map(lambda c: CODES[c][0])
    rows["bucket"] = rows.code.map(lambda c: CODES[c][1])
    m = rows.groupby(["month", "side", "bucket"])["amt"].sum().unstack([1, 2]).fillna(0.0)
    named = [b for b in OUTLAY_BUCKETS if b != "nondefense_other"]
    m[("outlay", "nondefense_other")] = m[("outlay", "_total_outlays")] - m[
        [("outlay", b) for b in named]].sum(axis=1)
    return m.drop(columns=[("outlay", "_total_outlays")])


def share_profiles(monthly):
    fys = [fy_of(m) for m in monthly.index]
    sub = monthly[[y in PROFILE_FYS for y in fys]]
    by_fy = [fy_of(m) for m in sub.index]
    by_month = [m.month for m in sub.index]
    shares = {}
    for col in sub.columns:
        g = sub[col].groupby([by_fy, by_month]).sum().unstack(0)  # month x fy
        prof = (g / g.sum()).median(axis=1)
        shares[col] = prof / prof.sum()
    order = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    return pd.DataFrame(shares).reindex(order)


def project_fy2026(prof):
    cbo = pd.read_csv("cbo_projection_buckets.csv")
    c = cbo[cbo.fy.eq(2026)].set_index(["side", "bucket"])["usd_mn"]
    annual = {("receipt", b): c.get(("receipt", b)) for b in RECEIPT_BUCKETS}
    for b in OUTLAY_BUCKETS:
        if b == "health_550":
            annual[("outlay", b)] = c.get(("outlay", "medicaid"), 0) + c.get(("outlay", "health_other"), 0)
        else:
            annual[("outlay", b)] = c.get(("outlay", b))
    months = pd.period_range("2025-10", "2026-09", freq="M")
    proj = pd.DataFrame(index=months)
    for key, a in annual.items():
        if a is None:
            continue
        proj[key] = [a * prof.loc[m.month, key] for m in months]
    rec = proj[[c_ for c_ in proj.columns if c_[0] == "receipt"]].sum(axis=1)
    out = proj[[c_ for c_ in proj.columns if c_[0] == "outlay"]].sum(axis=1)
    return pd.DataFrame({"proj_receipts": rec, "proj_outlays": out, "proj_deficit": out - rec})


def main():
    t9 = load_t9()
    # guard: line codes must mean the same thing across the whole history
    for code, (_, _b) in CODES.items():
        names = t9[t9.code.eq(code)]["classification_desc"].unique()
        assert len(names) == 1, f"line code {code} changed meaning: {names}"

    monthly = bucket_monthly(t9)
    monthly.columns = [f"{s}:{b}" for s, b in monthly.columns]
    monthly.to_csv("data/mts9_bucket_monthly.csv")
    monthly.columns = pd.MultiIndex.from_tuples([tuple(c.split(":")) for c in monthly.columns])

    prof = share_profiles(monthly)
    flat = prof.copy()
    flat.columns = [f"{s}:{b}" for s, b in flat.columns]
    flat.index.name = "calendar_month"
    flat.to_csv("seasonality_shares_v2.csv")
    print(f"Profiles from {len(PROFILE_FYS)} fiscal years ({PROFILE_FYS}), median shares.")

    v2 = project_fy2026(prof).round(0)
    act = pd.read_csv("recon_deficit.csv")
    act["month"] = pd.PeriodIndex(act["month"], freq="M")
    v2["actual_mts_deficit"] = act.set_index("month")["mts_deficit"]
    v2.index.name = "month"
    v2.to_csv("fy2026_monthly_split_v2.csv")

    v1 = pd.read_csv("fy2026_monthly_split.csv")
    v1["month"] = pd.PeriodIndex(v1["month"], freq="M")
    v1 = v1.set_index("month")

    have = v2.dropna(subset=["actual_mts_deficit"]).copy()
    have["proj_v1"] = v1["proj_deficit"]
    pd.set_option("display.float_format", lambda x: f"{x:,.0f}")
    print("\nFY2026 monthly deficit (USD mn): v1 (DTS FY24-25) vs v2 (MTS 7-yr median) vs actual")
    print(have[["proj_v1", "proj_deficit", "actual_mts_deficit"]]
          .rename(columns={"proj_deficit": "proj_v2", "actual_mts_deficit": "actual"}).to_string())
    for name, col in [("v1", "proj_v1"), ("v2", "proj_deficit")]:
        err = have[col] - have["actual_mts_deficit"]
        sign = (have[col].gt(0) == have["actual_mts_deficit"].gt(0)).mean()
        print(f"{name}: mean |error| {err.abs().mean():,.0f}  mean error {err.mean():,.0f}  signs {sign:.0%}")


if __name__ == "__main__":
    main()
