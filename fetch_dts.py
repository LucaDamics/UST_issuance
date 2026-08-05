"""Pull DTS Table II for the reconciliation window and cache locally.

Writes data/dts_table2_raw.csv and prints the distinct line universe
(transaction_type x transaction_catg) with dollar totals, so the mapping
CSV can be re-keyed against the live names.
"""

import sys
import time

import pandas as pd
import requests

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
DTS_ENDPOINT = "/v1/accounting/dts/deposits_withdrawals_operating_cash"
PAGE_SIZE = 10000

START, END = "2023-03-01", "2026-06-30"


def fetch_all(endpoint, params):
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
        meta = payload.get("meta", {})
        total_pages = meta.get("total-pages", page)
        print(f"  page {page}/{total_pages} ({len(rows)} rows)", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main():
    df = fetch_all(
        DTS_ENDPOINT,
        {
            "filter": f"record_date:gte:{START},record_date:lte:{END}",
            "fields": (
                "record_date,account_type,transaction_type,transaction_catg,"
                "transaction_today_amt"
            ),
        },
    )
    if df.empty:
        sys.exit("No rows returned")
    df.to_csv("data/dts_table2_raw.csv", index=False)
    print(f"\nCached {len(df):,} rows to data/dts_table2_raw.csv")
    print("Account types:", df["account_type"].unique().tolist())
    print("Transaction types:", df["transaction_type"].unique().tolist())

    df["amt"] = pd.to_numeric(df["transaction_today_amt"], errors="coerce")
    uni = (
        df.groupby(["transaction_type", "transaction_catg"])
        .agg(total_usd_mn=("amt", "sum"), days=("amt", "size"),
             first=("record_date", "min"), last=("record_date", "max"))
        .sort_values("total_usd_mn", ascending=False)
        .reset_index()
    )
    uni.to_csv("data/dts_line_universe.csv", index=False)
    print(f"{len(uni)} distinct (type, line) pairs -> data/dts_line_universe.csv")


if __name__ == "__main__":
    main()
