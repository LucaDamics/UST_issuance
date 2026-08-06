"""Forecast-grade daily deficit model: per-bucket slot shares + calendars.

Each business day of a month is classified into a SLOT by what the day is:
  BD1/BD2/BD3   first three business days (benefit block, SSA legacy 3rd)
  BD1S          BD1 of a month whose 1st is non-business (block paid prior EOM;
                includes every January - Jan 1 is always a holiday)
  D15/D15P1     first business day on/after the 15th and the next one
                (tax dates, mid-month coupons, military mid-month pay)
  EOM/EOM1      last two business days (month-end coupons, late outlays)
  EOMS          EOM of a month whose NEXT month starts non-business
                (carries the neighbour's benefit block)
  W2/W3/W4      2nd/3rd/4th Wednesdays (SSA benefit cycles)
  OMON..OFRI    everything else, by day of week

Per bucket, the model learns the mean share of the month's total falling on
each slot (months with |total| < $2bn skipped); a forecast month's calendar is
slotted the same way and shares renormalized to sum to one, so daily paths
always aggregate exactly to the monthly layer above.

Validation is walk-forward with a real benchmark: trained on Mar 2023-Sep 2025,
tested on the nine FY2026 months of held-out daily DTS actuals, using actual
monthly bucket totals (isolating daily-shape skill from monthly-level skill,
which the previous layer already measures). Baseline: uniform spread.

Outputs: slot_shares.csv, daily_validation.csv, daily_forecast.csv
"""

from datetime import date, timedelta

import pandas as pd

from reconcile_dts_mts import apply_mapping, load_dts
from seasonality_mts_history import (PROFILE_FYS, bucket_monthly, load_t9,
                                     share_profiles)

RECEIPT_BUCKETS = ["income_payroll", "corporate", "excise", "estate_gift", "customs", "misc_receipts"]
OUTLAY_BUCKETS = ["social_security", "medicare_net", "health_550", "defence",
                  "income_security_retirement", "veterans", "education_training",
                  "net_interest", "nondefense_other"]
CAT_TO_BUCKET = {
    "social_security": "social_security", "medicare": "medicare_net",
    "medicaid": "health_550", "health_subsidies": "health_550",
    "defence_discretionary": "defence", "net_interest": "net_interest",
    "veterans_mandatory": "veterans", "veterans_mixed": "veterans",
    "income_security": "income_security_retirement",
    "federal_retirement": "income_security_retirement",
    "education_mixed": "education_training",
}
TRAIN_END = pd.Period("2025-09", "M")
MIN_TOTAL = 2000.0  # USD mn: skip share estimation on near-zero months

# The month-start benefit block per bucket (which lines shift when the 1st is
# non-business). Used to move projected monthly totals across the boundary so
# the monthly anchor agrees with the BD1S/EOMS slot behaviour.
BLOCK_LINES = {
    "income_security_retirement": ["SSA - Supplemental Security Income"],
    "veterans": ["VA - Benefits"],
    "defence": ["DoD - Military Active Duty Pay"],
    "medicare_net": ["HHS - Federal Supple Med Insr Trust Fund",
                     "HHS - Federal Hospital Insr Trust Fund",
                     "HHS - Medicare Prescription Drugs"],
}


# ---------------- calendar ----------------

def us_federal_holidays(year):
    def nth_weekday(m, weekday, n):
        d = date(year, m, 1)
        return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    def last_monday(m):
        d = date(year, m + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() - 0) % 7)
    hol = {date(year, 1, 1), nth_weekday(1, 0, 3), nth_weekday(2, 0, 3),
           last_monday(5), date(year, 6, 19), date(year, 7, 4),
           nth_weekday(9, 0, 1), nth_weekday(10, 0, 2), date(year, 11, 11),
           nth_weekday(11, 3, 4), date(year, 12, 25)}
    # DTS follows Fed banking days: Sunday holidays observed Monday, but
    # Saturday holidays are NOT observed Friday (verified: DTS published on
    # 2023-11-10, the federal Veterans Day observance).
    obs = set()
    for h in hol:
        obs.add(h + timedelta(days=1) if h.weekday() == 6 else h)
    return {d for d in obs if d.weekday() < 5}


def business_days(month: pd.Period):
    hol = us_federal_holidays(month.year) | us_federal_holidays(month.year + 1)
    d0 = month.to_timestamp().date()
    days = []
    d = d0
    while d.month == month.month:
        if d.weekday() < 5 and d not in hol:
            days.append(d)
        d += timedelta(days=1)
    return days


def first_nonbusiness(month: pd.Period) -> bool:
    d = month.to_timestamp().date()
    return d.weekday() >= 5 or d in us_federal_holidays(month.year)


def slots_for_month(days, own_shift, next_shift):
    n = len(days)
    weds = [d for d in days if d.weekday() == 2]
    d15i = next((i for i, d in enumerate(days) if d.day >= 15), None)
    slots = []
    for i, d in enumerate(days):
        if i == 0:
            s = "BD1S" if own_shift else "BD1"
        elif i == 1:
            s = "BD2"
        elif i == 2:
            s = "BD3"
        elif i == n - 1:
            s = "EOMS" if next_shift else "EOM"
        elif i == n - 2:
            s = "EOM1"
        elif d15i is not None and i == d15i:
            s = "D15"
        elif d15i is not None and i == d15i + 1:
            s = "D15P1"
        elif d.weekday() == 2 and d in weds and weds.index(d) in (1, 2, 3):
            s = f"W{weds.index(d) + 1}"
        else:
            s = "O" + "MTWRF"[d.weekday()]
        slots.append(s)
    return slots


# ---------------- data ----------------

def daily_bucket_flows():
    mapping = pd.read_csv("dts_mts_cbo_mapping.csv")
    dts = load_dts("2023-03-01", "2026-06-30", refresh=False)
    mapped, _, _ = apply_mapping(dts, mapping)
    live = mapped[~mapped.treatment.isin(["strip", "exclude", "total_row"])].copy()
    refunds = live.treatment.eq("negative_receipt")
    offsetting = live.treatment.eq("offsetting")
    live.loc[refunds | offsetting, "amount"] *= -1

    rec = live[live.mts_side.eq("receipt")].copy()
    rec["bucket"] = rec["recon_bucket"]
    out = live[live.mts_side.eq("outlay")].copy()
    out["bucket"] = out["cbo_category"].map(CAT_TO_BUCKET)
    out.loc[out.line.eq("HHS - Medicare Premiums"), "bucket"] = "medicare_net"
    out["bucket"] = out["bucket"].fillna("nondefense_other")

    frames = []
    for side, df in [("receipt", rec), ("outlay", out)]:
        g = df.groupby(["record_date", "bucket"])["amount"].sum().unstack().fillna(0.0)
        g.columns = pd.MultiIndex.from_product([[side], g.columns])
        frames.append(g)
    daily = pd.concat(frames, axis=1).fillna(0.0)
    daily.index = pd.to_datetime(daily.index)
    return daily


def slot_frame(daily):
    """Slot label for every observed date (using DTS's own business days)."""
    rows = []
    months = sorted({pd.Period(d, "M") for d in daily.index})
    for m in months:
        days = sorted(d.date() for d in daily.index if pd.Period(d, "M") == m)
        slots = slots_for_month(days, first_nonbusiness(m), first_nonbusiness(m + 1))
        rows += [{"date": pd.Timestamp(d), "month": m, "slot": s} for d, s in zip(days, slots)]
    return pd.DataFrame(rows).set_index("date")


# ---------------- training / prediction ----------------

def train_slot_shares(daily, slots, train_end):
    train = slots[slots.month <= train_end]
    shares = {}
    for col in daily.columns:
        per_slot = {}
        for m, g in train.groupby("month"):
            vals = daily.loc[g.index, col]
            total = vals.sum()
            if abs(total) < MIN_TOTAL:
                continue
            for d, s in g["slot"].items():
                per_slot.setdefault(s, []).append(vals[d] / total)
        shares[col] = {s: sum(v) / len(v) for s, v in per_slot.items()}
    return shares


def predict_month(shares, month, bucket_totals, days=None):
    """Daily bucket flows for one month = totals x renormalized slot shares."""
    if days is None:
        days = business_days(month)
    slots = slots_for_month(days, first_nonbusiness(month), first_nonbusiness(month + 1))
    out = pd.DataFrame(index=pd.to_datetime(days))
    for col, total in bucket_totals.items():
        raw = pd.Series([shares.get(col, {}).get(s, 1 / len(days)) for s in slots],
                        index=out.index)
        if raw.sum() <= 0:
            raw = pd.Series(1 / len(days), index=out.index)
        out[col] = total * raw / raw.sum()
    return out


def deficit_of(df):
    o = df[[c for c in df.columns if c[0] == "outlay"]].sum(axis=1)
    r = df[[c for c in df.columns if c[0] == "receipt"]].sum(axis=1)
    return o - r


# ---------------- validation ----------------

def validate(daily, slots, shares):
    test_months = sorted({m for m in slots.month.unique() if m > TRAIN_END})
    rows, cum_err_model, cum_err_naive = [], [], []
    for m in test_months:
        g = slots[slots.month.eq(m)]
        days = [d.date() for d in g.index]
        totals = {col: daily.loc[g.index, col].sum() for col in daily.columns}
        pred = predict_month(shares, m, totals, days=days)
        naive = pd.DataFrame({col: totals[col] / len(days) for col in daily.columns},
                             index=pred.index)
        actual = daily.loc[g.index]
        dm, dn, da = deficit_of(pred), deficit_of(naive), deficit_of(actual)
        cum_err_model.append((dm.cumsum() - da.cumsum()).abs().mean())
        cum_err_naive.append((dn.cumsum() - da.cumsum()).abs().mean())
        rows.append(pd.DataFrame({"model": dm, "naive": dn, "actual": da}))
    val = pd.concat(rows)
    val.index.name = "date"
    val.round(0).to_csv("daily_validation.csv")
    stats = {
        "daily_mae_model": (val.model - val.actual).abs().mean(),
        "daily_mae_naive": (val.naive - val.actual).abs().mean(),
        "cum_mae_model": sum(cum_err_model) / len(cum_err_model),
        "cum_mae_naive": sum(cum_err_naive) / len(cum_err_naive),
        "n_days": len(val), "n_months": len(test_months),
    }
    wk_m = val.resample("W-FRI").sum()
    stats["weekly_mae_model"] = (wk_m.model - wk_m.actual).abs().mean()
    stats["weekly_mae_naive"] = (wk_m.naive - wk_m.actual).abs().mean()
    return val, stats


# ---------------- forward path ----------------

def projected_bucket_monthlies():
    """Monthly bucket totals Jul 2026 - Dec 2027 from CBO x v2 shares, with the
    per-bucket benefit block moved across shifted month boundaries."""
    t9 = load_t9()
    monthly = bucket_monthly(t9)
    prof = share_profiles(monthly)
    cbo = pd.read_csv("cbo_projection_buckets.csv")

    months = pd.period_range("2026-07", "2027-12", freq="M")
    rows = {}
    for m in months:
        fy = m.year + (1 if m.month >= 10 else 0)
        c = cbo[cbo.fy.eq(fy)].set_index(["side", "bucket"])["usd_mn"]
        row = {}
        for b in RECEIPT_BUCKETS:
            row[("receipt", b)] = c.get(("receipt", b)) * prof.loc[m.month, ("receipt", b)]
        for b in OUTLAY_BUCKETS:
            a = (c.get(("outlay", "medicaid"), 0) + c.get(("outlay", "health_other"), 0)) \
                if b == "health_550" else c.get(("outlay", b))
            row[("outlay", b)] = a * prof.loc[m.month, ("outlay", b)]
        rows[m] = row
    proj = pd.DataFrame.from_dict(rows, orient="index")

    # per-bucket benefit blocks, measured like the monthly calendar rule
    dts = pd.read_csv("data/dts_table2_raw.csv")
    dts = dts[dts.account_type == "Treasury General Account (TGA)"].copy()
    dts["date"] = pd.to_datetime(dts["record_date"])
    dts["amt"] = pd.to_numeric(dts["transaction_today_amt"], errors="coerce").fillna(0.0)
    blocks = {}
    for bucket, lines in BLOCK_LINES.items():
        sub = dts[dts.transaction_catg.isin(lines) & dts.transaction_type.eq("Withdrawals")]
        d = sub.groupby("date")["amt"].sum()
        vals = []
        for m in pd.period_range("2025-07", "2026-06", freq="M"):  # latest 12m, normal 1sts
            first = m.to_timestamp()
            if first_nonbusiness(m) or m.month == 1 or first not in d.index:
                continue
            mdays = d[(d.index >= first) & (d.index < (m + 1).to_timestamp())]
            vals.append(d[first] - (mdays.iloc[1:].median() if len(mdays) > 1 else 0.0))
        blocks[("outlay", bucket)] = pd.Series(vals).median()
    for m in months:
        if first_nonbusiness(m) and m.month != 1 and (m - 1) in proj.index:
            for key, a in blocks.items():
                proj.loc[m, key] -= a
                proj.loc[m - 1, key] += a
    return proj


def forecast(shares):
    proj = projected_bucket_monthlies()
    frames = [predict_month(shares, m, dict(proj.loc[m])) for m in proj.index]
    path = pd.concat(frames)
    receipts = path[[c for c in path.columns if c[0] == "receipt"]].sum(axis=1)
    outlays = path[[c for c in path.columns if c[0] == "outlay"]].sum(axis=1)
    out = pd.DataFrame({"receipts": receipts, "outlays": outlays,
                        "deficit": outlays - receipts}).round(0)
    out["cum_deficit"] = out["deficit"].cumsum()
    out.index.name = "date"
    out.to_csv("daily_forecast.csv")

    wk = out[["receipts", "outlays", "deficit"]].resample("W-FRI").sum()
    wk = wk[wk.deficit.notna()]
    wk["cum_deficit"] = wk["deficit"].cumsum()
    wk.index.name = "week_ending"
    wk.round(0).to_csv("weekly_forecast.csv")
    return out


def main():
    daily = daily_bucket_flows()
    slots = slot_frame(daily)

    # calendar self-check: generated business days vs DTS's actual reporting days
    mism = []
    for m in sorted(slots.month.unique()):
        gen = set(business_days(m))
        obs = {d.date() for d in slots[slots.month.eq(m)].index}
        mism += sorted(gen ^ obs)
    print(f"Calendar self-check: {len(mism)} day mismatches vs DTS reporting days "
          f"{[str(d) for d in mism] if mism else ''}")

    shares = train_slot_shares(daily, slots, TRAIN_END)
    flat = pd.DataFrame(shares).T
    flat.index = [f"{s}:{b}" for s, b in flat.index]
    flat.round(4).to_csv("slot_shares.csv")

    val, st = validate(daily, slots, shares)
    print(f"\nWalk-forward validation on FY2026 ({st['n_months']} months, {st['n_days']} days),"
          " actual monthly totals (shape-only skill):")
    print(f"  daily deficit MAE:   model {st['daily_mae_model']:,.0f} mn  vs naive {st['daily_mae_naive']:,.0f} mn")
    print(f"  weekly deficit MAE:  model {st['weekly_mae_model']:,.0f} mn  vs naive {st['weekly_mae_naive']:,.0f} mn")
    print(f"  within-month cumulative-path MAE: model {st['cum_mae_model']:,.0f} mn  vs naive {st['cum_mae_naive']:,.0f} mn")

    fc = forecast(shares)
    print(f"\nDaily forecast written: {fc.index[0].date()} .. {fc.index[-1].date()} "
          f"({len(fc)} business days), cumulative deficit {fc['cum_deficit'].iloc[-1]/1e3:,.0f} bn")


if __name__ == "__main__":
    main()
