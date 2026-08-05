"""DTS -> MTS reconciliation for the UST supply model fiscal pipeline.

Pulls DTS Table II (deposits/withdrawals of operating cash) and MTS Tables 4/5
from the Treasury Fiscal Data API, applies the line mapping, aggregates DTS to
monthly budget-classified flows, and compares them per reconciliation bucket
against the published MTS - including the headline deficit against MTS
"Total Surplus (+) or Deficit (-)".

    pip install pandas requests
    python reconcile_dts_mts.py --start 2023-03-01 --end 2026-06-30

Raw pulls are cached under data/; pass --refresh to re-download.
Start no earlier than 2023-03: the Feb 2023 DTS redesign moved federal tax
deposits into Table II and the mapping is keyed to the redesigned line names.

Sign conventions, all in USD millions:
  DTS receipts[bucket] = deposits (map/split/pooled) - refund withdrawals
  DTS outlays[bucket]  = withdrawals (map/pooled) - offsetting deposits
  deficit              = total outlays - total receipts (positive = deficit)
"""

import argparse
import os
import sys
import time

import pandas as pd
import requests

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
DTS_ENDPOINT = "/v1/accounting/dts/deposits_withdrawals_operating_cash"
MTS_RECEIPTS_ENDPOINT = "/v1/accounting/mts/mts_table_4"
MTS_OUTLAYS_ENDPOINT = "/v1/accounting/mts/mts_table_5"
PAGE_SIZE = 10000

RECEIPT_BUCKETS = ["income_payroll", "corporate", "excise", "estate_gift", "customs", "misc_receipts"]

# MTS Table 4: bucket -> classification_desc rows summed (values already net of refunds)
MTS_T4_MATCH = {
    "income_payroll": ["Total -- Individual Income Taxes", "Total -- Social Insurance and Retirement Receipts"],
    "corporate": ["Corporation Income Taxes"],
    "excise": ["Total -- Excise Taxes"],
    "estate_gift": ["Estate and Gift Taxes"],
    "customs": ["Customs Duties"],
    "misc_receipts": ["Total -- Miscellaneous Receipts"],
    "total_receipts": ["Total -- Receipts"],
}

# MTS Table 5: bucket -> agency total row
MTS_T5_MATCH = {
    "hhs": "Total--Department of Health and Human Services",
    "ssa": "Total--Social Security Administration",
    "dod": "Total--Department of Defense--Military Programs",
    "odcp": "Total--Other Defense Civil Programs",
    "va": "Total--Department of Veterans Affairs",
    "usda": "Total--Department of Agriculture",
    "ed": "Total--Department of Education",
    "dol": "Total--Department of Labor",
    "dot": "Total--Department of Transportation",
    "dhs": "Total--Department of Homeland Security",
    "doj": "Total--Department of Justice",
    "dos": "Total--Department of State",
    "doe": "Total--Department of Energy",
    "hud": "Total--Department of Housing and Urban Development",
    "doi": "Total--Department of the Interior",
    "doc": "Total--Department of Commerce",
    "treasury": "Total--Department of the Treasury",
    "opm": "Total--Office of Personnel Management",
    "corps": "Total--Corps of Engineers",
    "iap": "Total--International Assistance Programs",
}
MTS_TOTAL_OUTLAYS = "Total Outlays"
MTS_DEFICIT = "Total Surplus (+) or Deficit (-)"

# DTS buckets with no single MTS agency counterpart - compared as one residual
RESIDUAL_BUCKETS = ["salaries", "unclassified", "other_agencies"]


def fetch_all(endpoint: str, params: dict) -> pd.DataFrame:
    frames = []
    page = 1
    while True:
        q = dict(params)
        q["page[size]"] = PAGE_SIZE
        q["page[number]"] = page
        r = requests.get(BASE + endpoint, params=q, timeout=120)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data", [])
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        total_pages = payload.get("meta", {}).get("total-pages", page)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def cached_fetch(cache_path: str, endpoint: str, params: dict, refresh: bool) -> pd.DataFrame:
    if os.path.exists(cache_path) and not refresh:
        return pd.read_csv(cache_path)
    df = fetch_all(endpoint, params)
    if df.empty:
        sys.exit(f"No rows from {endpoint} - check endpoint path against the API docs.")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df


def load_dts(start: str, end: str, refresh: bool) -> pd.DataFrame:
    df = cached_fetch(
        "data/dts_table2_raw.csv",
        DTS_ENDPOINT,
        {
            "filter": f"record_date:gte:{start},record_date:lte:{end}",
            "fields": "record_date,account_type,transaction_type,transaction_catg,transaction_today_amt",
        },
        refresh,
    )
    # Line rows only: the two 'Total Deposits/Withdrawals' account types are daily totals
    df = df[df["account_type"] == "Treasury General Account (TGA)"].copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    df = df[(df["record_date"] >= start) & (df["record_date"] <= end)]
    df["amount"] = pd.to_numeric(df["transaction_today_amt"], errors="coerce").fillna(0.0)
    df["month"] = df["record_date"].dt.to_period("M")
    df["line"] = df["transaction_catg"].astype(str).str.strip()
    df["flow_type"] = df["transaction_type"].map({"Deposits": "deposit", "Withdrawals": "withdrawal"})
    return df


def apply_mapping(dts: pd.DataFrame, mapping: pd.DataFrame):
    m = mapping.copy()
    m["dts_line"] = m["dts_line"].str.strip()
    merged = dts.merge(
        m[["dts_line", "flow_type", "treatment", "recon_bucket", "mts_side", "cbo_category"]],
        left_on=["line", "flow_type"],
        right_on=["dts_line", "flow_type"],
        how="left",
    )
    gross = merged["amount"].abs().sum()
    unmapped = (
        merged[merged["treatment"].isna()]
        .groupby(["line", "flow_type"])["amount"]
        .agg(total_usd_mn="sum", days="size")
        .sort_values("total_usd_mn", ascending=False)
        .reset_index()
    )
    unmapped_share = merged.loc[merged["treatment"].isna(), "amount"].abs().sum() / gross if gross else 0.0
    mapped = merged[merged["treatment"].notna()].copy()
    return mapped, unmapped, unmapped_share


def build_monthly_buckets(mapped: pd.DataFrame) -> pd.DataFrame:
    """Monthly DTS flows per recon bucket under budget conventions."""
    df = mapped[~mapped["treatment"].isin(["strip", "exclude", "total_row"])].copy()

    df["side"] = df["mts_side"]
    refunds = df["treatment"] == "negative_receipt"
    df.loc[refunds, "amount"] = -df.loc[refunds, "amount"]  # refunds: negative receipts
    offsetting = df["treatment"] == "offsetting"
    df.loc[offsetting, "amount"] = -df.loc[offsetting, "amount"]  # nets against outlays

    return (
        df.groupby(["month", "side", "recon_bucket"])["amount"].sum().reset_index()
    )


def mts_value(df: pd.DataFrame, names, amount_col: str) -> pd.Series:
    """Monthly series (USD mn) summed over the given classification_desc rows."""
    sel = df[df["classification_desc"].isin(names if isinstance(names, list) else [names])]
    out = sel.groupby("month")["amt_mn"].sum()
    n_expected = len(names) if isinstance(names, list) else 1
    counts = sel.groupby("month").size()
    bad = counts[counts != n_expected]
    if not bad.empty:
        print(f"WARNING: unexpected row count for {names} in months {list(bad.index.astype(str))}", file=sys.stderr)
    return out


def load_mts(start: str, end: str, refresh: bool):
    t4 = cached_fetch(
        "data/mts_table4_raw.csv",
        MTS_RECEIPTS_ENDPOINT,
        {
            "filter": f"record_date:gte:{start},record_date:lte:{end}",
            "fields": "record_date,classification_desc,current_month_net_rcpt_amt,record_type_cd",
        },
        refresh,
    )
    t5 = cached_fetch(
        "data/mts_table5_raw.csv",
        MTS_OUTLAYS_ENDPOINT,
        {
            "filter": f"record_date:gte:{start},record_date:lte:{end}",
            "fields": "record_date,classification_desc,current_month_net_outly_amt,record_type_cd",
        },
        refresh,
    )
    for df, col in [(t4, "current_month_net_rcpt_amt"), (t5, "current_month_net_outly_amt")]:
        df["record_date"] = pd.to_datetime(df["record_date"])
        df["month"] = df["record_date"].dt.to_period("M")
        df["amt_mn"] = pd.to_numeric(df[col], errors="coerce") / 1e6
    return t4, t5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-03-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--mapping", default="dts_mts_cbo_mapping.csv")
    ap.add_argument("--refresh", action="store_true", help="re-download instead of using data/ cache")
    args = ap.parse_args()

    mapping = pd.read_csv(args.mapping)

    print("Loading DTS Table II...")
    dts = load_dts(args.start, args.end, args.refresh)
    print(f"  {len(dts):,} line rows, {dts['month'].nunique()} months")

    mapped, unmapped, unmapped_share = apply_mapping(dts, mapping)
    print(f"  unmapped share of gross flows: {unmapped_share:.3%}")
    if not unmapped.empty:
        unmapped.to_csv("unmapped_lines.csv", index=False)
        print("=== UNMAPPED LINES (fix keys in mapping CSV) ===")
        print(unmapped.to_string(index=False))
        if unmapped_share > 0.02:
            sys.exit("Unmapped share exceeds 2% - fix the mapping before interpreting results.")

    monthly = build_monthly_buckets(mapped)
    monthly.to_csv("dts_monthly_by_category.csv", index=False)

    print("Loading MTS Tables 4 and 5...")
    t4, t5 = load_mts(args.start, args.end, args.refresh)
    months = sorted(set(t4["month"]))
    print(f"  MTS months: {months[0]} .. {months[-1]} ({len(months)})")

    # ---- DTS pivots ----
    dts_rcpt = monthly[monthly["side"] == "receipt"].pivot_table(
        index="month", columns="recon_bucket", values="amount", aggfunc="sum"
    ).fillna(0.0)
    dts_out = monthly[monthly["side"] == "outlay"].pivot_table(
        index="month", columns="recon_bucket", values="amount", aggfunc="sum"
    ).fillna(0.0)

    # ---- MTS series ----
    mts_rcpt = pd.DataFrame({b: mts_value(t4, n, "amt") for b, n in MTS_T4_MATCH.items()})
    mts_agency = pd.DataFrame({b: mts_value(t5, n, "amt") for b, n in MTS_T5_MATCH.items()})
    mts_total_outlays = mts_value(t5, MTS_TOTAL_OUTLAYS, "amt")
    mts_deficit = -mts_value(t5, MTS_DEFICIT, "amt")  # positive = deficit

    # ---- receipts comparison ----
    rcpt_rows = []
    for b in RECEIPT_BUCKETS:
        cmp = pd.DataFrame({"dts": dts_rcpt.get(b), "mts": mts_rcpt[b]}).dropna()
        cmp["wedge"] = cmp["dts"] - cmp["mts"]
        cmp.insert(0, "bucket", b)
        rcpt_rows.append(cmp.reset_index())
    rcpt_cmp = pd.concat(rcpt_rows, ignore_index=True)
    rcpt_cmp.to_csv("recon_receipts.csv", index=False)

    # ---- outlays comparison: matched agencies + one residual block ----
    out_rows = []
    for b in MTS_T5_MATCH:
        if b not in dts_out.columns:
            continue
        cmp = pd.DataFrame({"dts": dts_out[b], "mts": mts_agency[b]}).dropna()
        cmp["wedge"] = cmp["dts"] - cmp["mts"]
        cmp.insert(0, "bucket", b)
        out_rows.append(cmp.reset_index())
    dts_residual = dts_out[[c for c in RESIDUAL_BUCKETS if c in dts_out.columns]].sum(axis=1)
    mts_residual = mts_total_outlays - mts_agency.sum(axis=1)
    cmp = pd.DataFrame({"dts": dts_residual, "mts": mts_residual}).dropna()
    cmp["wedge"] = cmp["dts"] - cmp["mts"]
    cmp.insert(0, "bucket", "residual_all_other")
    out_rows.append(cmp.reset_index())
    out_cmp = pd.concat(out_rows, ignore_index=True)
    out_cmp.to_csv("recon_outlays.csv", index=False)

    # ---- headline deficit ----
    headline = pd.DataFrame(
        {
            "dts_receipts": dts_rcpt.sum(axis=1),
            "dts_outlays": dts_out.sum(axis=1),
            "mts_receipts": mts_rcpt["total_receipts"],
            "mts_outlays": mts_total_outlays,
            "mts_deficit": mts_deficit,
        }
    ).dropna()
    headline["dts_implied_deficit"] = headline["dts_outlays"] - headline["dts_receipts"]
    headline["wedge"] = headline["dts_implied_deficit"] - headline["mts_deficit"]
    headline["wedge_pct_outlays"] = headline["wedge"] / headline["mts_outlays"] * 100
    headline.to_csv("recon_deficit.csv")

    # ---- console summary ----
    pd.set_option("display.float_format", lambda x: f"{x:,.0f}")
    print("\n=== HEADLINE: DTS-implied vs MTS deficit (USD mn) ===")
    print(headline[["dts_implied_deficit", "mts_deficit", "wedge", "wedge_pct_outlays"]].to_string())
    print(f"\nMean |wedge|: {headline['wedge'].abs().mean():,.0f} mn "
          f"({(headline['wedge'].abs() / headline['mts_outlays']).mean():.1%} of outlays)")
    print(f"Cumulative wedge over window: {headline['wedge'].sum():,.0f} mn on "
          f"cumulative MTS deficit {headline['mts_deficit'].sum():,.0f} mn")

    def bucket_summary(cmp_df, label):
        g = cmp_df.groupby("bucket").agg(
            dts_total=("dts", "sum"), mts_total=("mts", "sum"),
            wedge_total=("wedge", "sum"), mean_abs_wedge=("wedge", lambda s: s.abs().mean()),
        )
        g["wedge_pct"] = g["wedge_total"] / g["mts_total"].abs() * 100
        print(f"\n=== {label}: totals over window (USD mn) ===")
        print(g.sort_values("mean_abs_wedge", ascending=False).to_string())

    bucket_summary(rcpt_cmp, "RECEIPTS by bucket")
    bucket_summary(out_cmp, "OUTLAYS by bucket")

    print("\nWrote: dts_monthly_by_category.csv, recon_receipts.csv, recon_outlays.csv, recon_deficit.csv")


if __name__ == "__main__":
    main()
