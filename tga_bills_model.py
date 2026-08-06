"""The mix: deficit path + coupon engine -> daily TGA path -> weekly net bills.

The financing identity, daily, Oct 2026 - Dec 2027:

  TGA_t = TGA_{t-1} - primary_deficit_t - coupon_interest_t
          - coupon_redemptions_t + coupon_settlements_t + net_bills_t

  primary deficit   the daily model's path MINUS its accrual-interest bucket
                    (cash coupon interest from the CUSIP engine replaces it -
                    this is where the accrual/cash interest wedge finally
                    resolves; bill discount is inside net bills by construction)
  coupon flows      engine_flows_daily.csv (CUSIP engine)
  net bills         solved as the residual: weekly Thursday settlements sized
                    so the TGA glides linearly to Treasury's own anchors -
                    $950bn end-Sep 2026 and $850bn end-Dec 2026 (Aug 2026
                    Sources & Uses table), held at $850bn thereafter (policy
                    assumption, adjustable below)

Cross-checks printed: Q4 2026 net bills vs the ~$328bn implied by Treasury's
$628bn marketable borrowing plan minus the engine's net coupon issuance; and
the TGA path's intra-quarter low (cash adequacy).

Outputs: tga_daily.csv, bills_weekly.csv
"""

from datetime import date

import pandas as pd

from daily_model import (business_days, daily_bucket_flows, predict_month,
                         projected_bucket_monthlies, slot_frame,
                         train_slot_shares, TRAIN_END)

START = date(2026, 9, 30)          # anchor: Treasury's assumed end-Sep TGA
TGA_ANCHORS = {                     # USD mn
    date(2026, 9, 30): 950_000,     # Sources & Uses, Aug 3 2026
    date(2026, 12, 31): 850_000,    # Sources & Uses, Aug 3 2026
    date(2027, 3, 31): 850_000,     # held flat: policy assumption
    date(2027, 6, 30): 850_000,
    date(2027, 9, 30): 850_000,
    date(2027, 12, 31): 850_000,
}
# Other means of financing: cash the budget deficit does not capture - mostly
# credit financing accounts (student/SBA/DOE loan disbursements net of
# repayments). Treasury's Aug 2026 Sources & Uses puts it at -$81bn for Q4 2026
# ("All Other Sources"); held constant per quarter as a spread-evenly daily drain.
OTHER_MEANS_PER_QUARTER = -81_000  # USD mn, negative = consumes cash


def daily_primary_deficit():
    """Dated daily deficit EXCLUDING the accrual net-interest bucket."""
    daily = daily_bucket_flows()
    slots = slot_frame(daily)
    shares = train_slot_shares(daily, slots, TRAIN_END)
    proj = projected_bucket_monthlies()
    months = [m for m in proj.index if m >= pd.Period("2026-10", "M")]
    frames = [predict_month(shares, m, dict(proj.loc[m])) for m in months]
    path = pd.concat(frames)
    outl = path[[c for c in path.columns if c[0] == "outlay"]].sum(axis=1)
    rec = path[[c for c in path.columns if c[0] == "receipt"]].sum(axis=1)
    interest_accrual = path[("outlay", "net_interest")]
    return (outl - rec - interest_accrual).rename("primary_deficit")


def main():
    prim = daily_primary_deficit()
    eng = pd.read_csv("engine_flows_daily.csv", parse_dates=["date"]).set_index("date")

    days = [d for m in pd.period_range("2026-10", "2027-12", freq="M") for d in business_days(m)]
    grid = pd.DataFrame(index=pd.to_datetime(days))
    grid["primary_deficit"] = prim.reindex(grid.index).fillna(0.0)
    for c in ["interest", "redemptions", "settlements"]:
        grid[c] = eng[c].reindex(grid.index).fillna(0.0)
    qdays = grid.index.to_period("Q")
    grid["other_means"] = OTHER_MEANS_PER_QUARTER / qdays.map(qdays.value_counts())
    grid["pre_bills"] = (-grid.primary_deficit - grid.interest
                         - grid.redemptions + grid.settlements + grid.other_means)

    # target path: linear glide between anchors on the business-day grid
    anchors = sorted(TGA_ANCHORS.items())
    target = pd.Series(index=grid.index, dtype=float)
    for (d0, v0), (d1, v1) in zip(anchors, anchors[1:]):
        seg = [d for d in grid.index if pd.Timestamp(d0) < d <= pd.Timestamp(d1)]
        for i, d in enumerate(seg, 1):
            target[d] = v0 + (v1 - v0) * i / len(seg)

    # solve weekly Thursday bill settlements to track the glide path
    thursdays = [d for d in grid.index if d.weekday() == 3]
    grid["net_bills"] = 0.0
    tga = TGA_ANCHORS[START]
    tga_path = []
    prev_thu = {d: max([t for t in thursdays if t <= d], default=None) for d in grid.index}
    i = 0
    while i < len(thursdays):
        thu = thursdays[i]
        nxt = thursdays[i + 1] if i + 1 < len(thursdays) else grid.index[-1]
        upto = [d for d in grid.index if thu <= d <= nxt and d != nxt or d == thu]
        # cash drift from thu (incl) to next thu (excl), no bills
        window = grid.loc[thu:nxt].iloc[:-1] if nxt != thu else grid.loc[thu:thu]
        drift = window.pre_bills.sum()
        # size this Thursday's bills so TGA lands on target at the eve of next Thursday
        need = target[nxt] - (tga + drift) if nxt in target.index else 0.0
        grid.loc[thu, "net_bills"] = need
        # roll TGA forward day by day through the window
        for d in window.index:
            tga += grid.loc[d, "pre_bills"] + grid.loc[d, "net_bills"]
            tga_path.append((d, tga))
        i += 1
    tga_s = pd.Series(dict(tga_path)).sort_index()
    grid["tga"] = tga_s

    grid.round(0).to_csv("tga_daily.csv")
    bills = grid.loc[grid.net_bills != 0, ["net_bills"]].round(0)
    bills.index.name = "settle_date"
    bills.to_csv("bills_weekly.csv")

    q4 = grid.loc["2026-10":"2026-12"]
    cash_def = q4.primary_deficit.sum() + q4.interest.sum()
    borrow = q4.settlements.sum() - q4.redemptions.sum() + q4.net_bills.sum()
    print("Q4 2026 (Oct-Dec), USD bn:")
    print(f"  primary deficit {q4.primary_deficit.sum()/1e3:,.0f}  coupon interest {q4.interest.sum()/1e3:,.0f}"
          f"  other means {q4.other_means.sum()/1e3:,.0f}"
          f"  -> cash need {(cash_def - q4.other_means.sum())/1e3:,.0f}"
          f"  (Treasury's stated need: 646)")
    print(f"  net coupon issuance {(q4.settlements.sum()-q4.redemptions.sum())/1e3:,.0f}")
    print(f"  SOLVED net bills {q4.net_bills.sum()/1e3:,.0f}"
          f"   vs ~328 implied by Treasury's borrowing plan (628 - 300)")
    print(f"  implied total marketable borrowing {borrow/1e3:,.0f}"
          f"   vs Treasury Sources & Uses: 628")
    print("  bridge to Treasury: remaining gap is the deficit view - CBO's baseline"
          " still carries pre-ruling tariff revenue (customs scenario lever)")
    print(f"\nTGA at Dec 31 2026: {grid.tga.loc['2026-12'].iloc[-1]/1e3:,.0f}bn (anchor 850)")
    lows = grid.tga.groupby(grid.index.to_period("Q")).min()
    print("Intra-quarter TGA lows (USD bn):")
    print((lows/1e3).round(0).to_string())
    fy27 = grid.loc["2026-10":"2027-09"]
    print(f"\nFY2027 net bills total: {fy27.net_bills.sum()/1e3:,.0f}bn")


if __name__ == "__main__":
    main()
