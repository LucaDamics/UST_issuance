# Stage 2: CBO projections -> monthly path via DTS seasonality

Goal: distribute CBO's projected annual deficit into months (later weeks/days)
using seasonality extracted from the mapped DTS cash flows, so net bill issuance
can fall out as the residual.

## Data

- `data/51142-2026-02-Spending-Projections.xlsx` - CBO Feb 2026 baseline outlays at
  Treasury account (TAFS) level, FY2026-2036, with CBO's own Major spending category,
  agency, and budget function per account. Parsed by `parse_cbo_projections.py`.
- `data/51138-2026-02-Revenue.xlsx` - revenue projections by source, same vintage.
- Both files are DataDome-protected on cbo.gov - download by hand, the scripts only parse.

## Bucket bridge

`parse_cbo_projections.py` assigns every CBO account to one of 10 outlay buckets using
(Major spending category, function): Social Security, Medicare net (premium offsets from
function 570 folded in), Medicaid, Defense, Net interest, Veterans (function 700),
Income security + federal retirement (600), Education/training (500), Health other (550),
Nondefense other (residual incl. remaining offsetting receipts). Receipts: the six
reconciliation buckets, with CBO individual income + payroll combined into
`income_payroll` because the DTS cannot split withheld FICA from income tax.

The same buckets are built on the DTS side by `seasonality.py` from the mapping's
`cbo_category` column. Parse check: FY2026 outlays $7,449bn, receipts $5,596bn,
deficit $1,853bn - matches the workbook totals exactly.

## Seasonality v1 and validation

Month-of-fiscal-year share profiles per bucket, averaged over FY2024 and FY2025 (the
two complete years in the DTS window). Applied to CBO's FY2026 baseline and compared
with the nine MTS months published so far (Oct 2025 - Jun 2026):

- mean absolute monthly deficit error **$98bn** against a $200bn mean actual
- **8 of 9** surplus/deficit signs correct
- the two large misses are known mechanisms, not noise:
  - **Oct/Nov 2025**: Nov 1, 2025 fell on a Saturday, so November benefits paid
    Oct 31 - neither FY2024 nor FY2025 had that shift, so the raw shares miss it.
    The Oct+Nov pair nets to -$44bn. Fix in v2 with a payment-calendar rule
    (SSA/SSI/VA/military pay dated by business-day convention, not raw shares).
  - **May/Jun 2026**: IEEPA tariff refunds push actual customs receipts to ~zero
    (May) and -$25.6bn (June); CBO's smooth $418bn annual customs line cannot see
    this. Handle as an explicit refund scenario, not seasonality.

Outputs: `seasonality_monthly_shares.csv`, `dts_bucket_monthly.csv`,
`fy2026_monthly_split.csv`, and the "payoff" card in `mapping_dashboard.html`.

## Known v1 limitations (ordered fixes for v2)

1. **Payment-date calendar rule** for the 1st/3rd-of-month benefit cycle (the Oct/Nov
   class of error - the biggest fixable one).
2. **Net interest is cash-coupon seasonal but CBO-accrual in level.** The accrual-cash
   gap (mostly bill discount) belongs on the financing side of the model; when the
   issuance tracker is attached, route it there explicitly rather than leaving it in
   the deficit path.
3. Only two complete fiscal years inform the shares; each new month of DTS data
   tightens them. Consider down-weighting months distorted by the 2025 debt-ceiling
   episode and the Oct-Nov 2025 shutdown.
4. Weekly/daily disaggregation is the next layer: within-month patterns (mid-month and
   month-end coupon dates, Wednesday benefit cycles, the 15th corporate tax date) are
   visible in the cached daily DTS data - same bucket structure, finer calendar.

## US-CBO/eval-projections repo - verdict

Useful, but for a different job: it quantifies CBO's own historical projection errors
by category and horizon (year-1 total deficit RMSE ~0.7% of GDP ~ $220bn today,
legislation-adjusted). That is the right source for **uncertainty bands** around the
projected issuance path, not for the seasonal mapping itself. Its summary statistics
are copied under `data/cbo_eval/` for when the model adds scenario bands. Its category
taxonomy matches the projection workbooks' Major spending categories, so no extra
mapping work is needed to use it.
