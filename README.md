# UST issuance model — fiscal pipeline

Turn CBO's annual deficit projections into a monthly (eventually weekly/daily) cash
deficit path, using seasonality extracted from actual Treasury flows, so that net bill
issuance can be modeled as the residual:

```
CBO annual projections (by category)
        │  seasonal shares per category        ← this repo
        ▼
monthly deficit path
        │  + TGA cash-balance assumption
        │  − net coupon issuance (issuance tracker, separate)
        ▼
net bill issuance (residual)
```

Before trusting any seasonal split, the category series themselves have to be trusted.
That is what most of this repo does: it builds and **validates the full chain**

**DTS** (Daily Treasury Statement, daily cash) → **MTS** (Monthly Treasury Statement,
published budget accounting) → **CBO** (historical actuals and projections),

so that every wedge between daily cash and budget accounting is either eliminated by
the mapping or named, quantified, and understood.

## Results at a glance

| Check | Result |
|---|---|
| DTS lines mapped | **100.0%** of gross flows (196 of 196 live lines; unmapped = 0.000%) |
| DTS-built vs MTS monthly deficit | cumulative wedge −$867bn on $5,942bn deficit (Mar 2023–Jun 2026), of which −$1,410bn interest cash-vs-accrual and +$329bn Education credit reform; unattributed residual +$214bn (~3.6%, trendless) |
| MTS vs CBO historical actuals | ≤ **0.4%** in every category, FY2024 & FY2025 — the MTS→CBO hop is essentially free |
| DTS-built vs CBO actuals | Medicaid −0.3%, Medicare net −2%, total receipts −2%; large gaps only where accounting differs by construction (net interest, premium withholding) |
| FY2026 monthly split (the payoff) | mean absolute error **$30bn**/month on a $200bn mean actual over 9 published months, **9/9** surplus/deficit signs, zero bias (v2 shares + payment-calendar rule) |
| Honest out-of-sample yardstick | leave-one-year-out CV over 7 held-out years: mean \|error\| **$43bn/month ≈ 30%** of the mean monthly deficit ($17–40bn in ordinary years; ~$96bn in FY2023, the debt-ceiling + student-loan-reversal year) |

Interactive dashboard: `mapping_dashboard.html` (self-contained; open in a browser).

## Data sources

| Source | Access | Used for |
|---|---|---|
| [fiscaldata.treasury.gov](https://fiscaldata.treasury.gov) API — DTS Table II (`deposits_withdrawals_operating_cash`) | scripted, cached under `data/` | daily cash flows by line, Mar 2023 → |
| fiscaldata API — MTS Tables 4, 5 | scripted, cached | published monthly receipts by source / outlays by agency, for reconciliation |
| fiscaldata API — MTS Table 9 | scripted, cached | monthly receipts by source / outlays by *function*, Mar 2015 → ; the long history behind seasonality v2 |
| CBO historical budget data (`51134`) | **manual download** (cbo.gov is DataDome-protected; scripts only parse) | FY actuals by category |
| CBO Feb 2026 baseline: spending (`51142`, account-level) and revenue (`51138`) | **manual download**, committed under `data/` | projected annual levels to distribute |
| [US-CBO/eval-projections](https://github.com/US-CBO/eval-projections) summary stats | copied to `data/cbo_eval/` | CBO's own historical projection errors — future uncertainty bands |

## Repository layout

| File | Role |
|---|---|
| `fetch_dts.py` | pull the full DTS Table II window, cache it, enumerate the live line universe |
| `build_mapping.py` | **generates** `dts_mts_cbo_mapping.csv` from an explicit per-line classification; errors loudly if Treasury introduces a line name it doesn't know |
| `dts_mts_cbo_mapping.csv` | the mapping: every live DTS line → treatment → reconciliation bucket → CBO category (`dts_mts_cbo_mapping_original.csv` is the pre-validation draft, kept for history) |
| `reconcile_dts_mts.py` | the DTS→MTS reconciliation: aggregates mapped daily flows to monthly buckets, pulls MTS 4/5, writes `recon_*.csv` |
| `cbo_reconcile.py` | the annual three-way check: DTS-built vs MTS vs CBO historical actuals → `cbo_comparison.csv` |
| `parse_cbo_projections.py` | CBO account-level baseline → 16 seasonality buckets → `cbo_projection_buckets.csv` |
| `seasonality.py` | v1 shares (DTS, FY2024–25) + FY2026 monthly split → `fy2026_monthly_split.csv` |
| `seasonality_mts_history.py` | **v2 shares** (MTS Table 9, median over FY2016–19 + FY2023–25) → `seasonality_shares_v2.csv`, `fy2026_monthly_split_v2.csv`; prints v1-vs-v2 validation |
| `build_charts.py` | regenerates `mapping_dashboard.html` from the CSV outputs |
| `reconciliation_summary.md` | detailed write-up of the DTS→MTS reconciliation and every wedge |
| `projections_seasonality_notes.md` | the projection bridge, seasonality method, validation, and v-next plan |
| `data/` | API caches (gitignored, re-pullable) + committed CBO workbooks (not scriptable) |

## How to run

```bash
python3 -m venv .venv && .venv/bin/pip install pandas requests openpyxl
.venv/bin/python fetch_dts.py                  # ~150k rows, cached after first run
.venv/bin/python build_mapping.py              # regenerate mapping from line universe
.venv/bin/python reconcile_dts_mts.py          # DTS→MTS reconciliation (--refresh to re-pull)
.venv/bin/python cbo_reconcile.py              # annual three-way vs CBO actuals
.venv/bin/python parse_cbo_projections.py      # CBO baseline → buckets
.venv/bin/python seasonality.py                # v1 shares + FY2026 split
.venv/bin/python seasonality_mts_history.py    # v2 shares + v1-vs-v2 validation
.venv/bin/python build_charts.py               # regenerate the dashboard
```

Each script prints its own validation summary; `build_mapping.py` exits nonzero if any
DTS line in the cache is unclassified (that is the designed failure mode for Treasury
renames — fix the classification table, rerun).

## Methodology

### 1. The DTS mapping (`build_mapping.py`)

The post-Feb-2023 DTS publishes ~150 agency-labelled cash lines per day. Every
`(transaction_type, transaction_catg)` pair observed in the window is classified with a
**treatment** and a **reconciliation bucket**:

- `map` — a budget flow, kept on its natural side (e.g. `SSA - Benefits Payments`).
- `split` — the withheld and non-withheld tax lines, which mix individual income tax
  and FICA; they carry a split rule for the CBO stage (income vs payroll), but
  reconcile combined (`income_payroll`) because the IRS itself only allocates later.
- `negative_receipt` — IRS refund lines. Cash out, but MTS/CBO treat refunds as
  negative receipts, not outlays. Misclassifying this one distorts both sides by
  ~$500bn/yr and wrecks Feb–May seasonality.
- `offsetting` — agency deposit lines (Medicare premiums, FHA fees, FDIC premiums,
  student-loan repayments…). MTS nets these against agency outlays
  (`current_month_app_rcpt_amt` in Table 5), so the mapping does the same.
- `strip` — public debt issues/redemptions (Table IIIB lines): financing, not deficit;
  owned by the issuance tracker.
- `exclude` — TSP fiduciary flows (validated: they net to $0.3bn over 40 months).
  Postal was originally excluded as off-budget but is **included** (flagged treatment
  change): MTS Total Outlays and the CBO total deficit include off-budget postal.
- `total_row` — the daily `Sub-Total`/account-type total rows; structural, dropped.
  Additivity is verified: line rows sum exactly to the published daily totals.

Three format eras exist inside the window (Feb 2023 redesign; an FY2024 rename wave,
e.g. `Defense Vendor Payments (EFT)` → `Dept of Defense (DoD) - misc`; smaller
renames Oct/Nov 2025). Old and new names map to the same buckets, so bucket-level
series are continuous. The mapping is *generated* rather than hand-kept so that any
future rename fails the build instead of silently leaking into "unmapped".

### 2. DTS→MTS reconciliation (`reconcile_dts_mts.py`)

Monthly, per bucket: receipts = mapped deposits − refunds; outlays = mapped
withdrawals − offsetting deposits; deficit = outlays − receipts. Compared against MTS
Table 4 (net receipts by source) and Table 5 (net outlays by agency, plus the
published `Total Surplus (+) or Deficit (-)`).

The cumulative wedge (−$867bn over 40 months) decomposes almost entirely into two
accounting mechanisms, not mapping errors:

- **Interest cash vs accrual, −$1,410bn.** DTS records coupon cash; MTS accrues
  interest monthly — including **bill discount**, which in cash terms is paid inside
  redemptions (stripped as financing) and never appears as DTS interest. Grows with
  bill issuance. Not fixable in a mapping; handled by modeling interest on the
  accrual basis and routing the accrual-cash gap to the financing side.
- **Education credit reform, +$329bn.** Non-cash MTS reestimates (Aug 2023 forgiveness
  reversal −$320bn in one month; Jun 2024; Sep 2025). DTS correctly shows none of it.
- Residual +$214bn (~$5bn/month, trendless): timing/cutoff noise.

Two structural patterns worth knowing:
- **The premium mirror.** Medicare Part B/D premiums are withheld from Social Security
  checks. DTS pays SSA benefits *net* (≈ −$16bn/month vs MTS gross), while MTS
  Medicare is net of those same premiums (HHS wedge ≈ mirror image). Cancels at total.
- **The salary pool.** `Federal Salaries (EFT)` ($812bn) and `Unclassified` ($1,089bn)
  span agencies, so DTS *agency-level* outlays under-count payroll-heavy agencies
  (DOJ −73%, State −45%, DoD −36%). Bucket-level and total-level fits are the
  reliable ones; agency comparisons need a payroll allocation first.

### 3. MTS→CBO (annual check, `cbo_reconcile.py`)

MTS fiscal-year aggregates match CBO's historical actuals to 0.4% or better in every
category (CBO's actuals *are* Treasury's numbers). Consequence: the MTS→CBO hop needs
no modeling; all reconciliation risk lives in DTS→MTS, which is where the mapping and
its validation sit. DTS-built categories vs CBO actuals inherit exactly the known
wedges and nothing else.

### 4. CBO projections → buckets (`parse_cbo_projections.py`)

CBO's Feb 2026 spending baseline is published at Treasury-account level with CBO's own
`Major spending category` plus budget function per account. Each account maps to a
seasonality bucket (Social Security, Medicare net — premium offsets from function 570
folded in —, Medicaid, Defense, Net interest, Veterans = function 700, Income security
+ federal retirement = 600, Education/training = 500, Health other = 550, Nondefense
other). Revenue projections map to the six receipt buckets; individual income and
payroll are combined because the DTS cannot split withheld FICA. Parse check
reproduces the workbook totals exactly (FY2026: outlays $7,449bn, receipts $5,596bn,
deficit $1,853bn).

### 5. Seasonality and the FY2026 validation

**v1** (`seasonality.py`): month-of-fiscal-year shares from the mapped DTS cash, mean
over FY2024–25 — the only two complete fiscal years on the redesigned DTS format.
Validation against the nine published FY2026 months: mean |error| $98bn, 8/9 signs.

**v2** (`seasonality_mts_history.py`, adopted): monthly shares belong on longer
history, and MTS Table 9 (receipts by source, outlays by function) is format-stable
back to Mar 2015. Median month-of-FY share per bucket over FY2016–2019 + FY2023–2025
(COVID years excluded; the median damps one-off reestimate months). Interest takes its
MTS-accrual shape — correct when the target is the published deficit. Validation:
mean |error| **$67bn**, mean error ≈ 0, **9/9** signs.

**v2.1 — the payment-calendar rule** (in `seasonality_mts_history.py`, adopted):
benefit streams paid on the 1st of the month (Medicare Advantage/Part D capitation,
VA compensation, SSI, military active-duty pay) move to the last business day of the
*prior* month whenever the 1st falls on a weekend — or on Labor Day, the one floating
holiday that can land on Sep 1 (January is excluded: Jan 1 shifts every year, so it
already lives in the shares). The size of the shifting block is *measured from the
daily DTS*: the affected lines' month-first payment net of their daily run-rate —
$64bn (FY2023) growing to $89bn (FY2026). Effect on the FY2026 validation:
mean |error| **$67bn → $30bn**, zero bias, 9/9 signs. Oct 2025's miss shrank from
−$148bn to −$59bn (the remainder is the government shutdown), Nov from +$134bn to
+$45bn, Mar from +$126bn to +$37bn.

The same model reconstructs FY2023–25 history (shares × each year's actual totals +
calendar rule) with mean |error| $53bn/month — the residual dominated by
credit-reform months no seasonal model can see (`seasonal_model_monthly.csv`,
charted in the dashboard's final card).

**Withheld split calibration** (`calibrate_withheld_split.py`): the DTS withheld line
mixes individual income tax and FICA; MTS Table 4 publishes the allocated gross
pieces monthly back to 2015. Current trailing-12m split: **54.6% income / 45.4%
FICA** (stable within 0.50–0.58 over 11 years; non-withheld: 92.4% income vs SECA).
The share has real within-year seasonality (bonus season skews toward income tax) —
use month-specific shares if CBO-category composition ever matters at monthly
frequency. Output: `withheld_split_calibration.csv`.

**How out-of-sample is the validation?** Candidly, in layers. The FY2023–25 segment
of the payoff chart is in-sample fit (those years are in the profile and use their own
actual totals). FY2026 is *shape*-out-of-sample (its months are not in the profile),
but the CBO level was published mid-year, and design choices (median, year set,
accrual interest) were made while watching the FY2026 scoreboard. The clean yardstick
is the **leave-one-year-out cross-validation** in `seasonality_mts_history.py`:
shares re-estimated from the other years only, applied to the held-out year's actual
totals, with the calendar block *extrapolated* rather than measured on the held-out
year. Result (`loyo_validation.csv`): $17–40bn/month in ordinary years, ~$96bn in
FY2023 (debt ceiling + the Aug 2023 student-loan reversal), ~30% of the mean monthly
deficit overall. FY2026's 15% should be read as the friendly end of that honest range.
The same discipline is applied to the live projection: FY2026+ calendar blocks are
extrapolated from pre-FY2026 measurements (the leak this closes was worth $0.2bn —
immaterial, but now zero).

The projection currently runs through **December 2027** (FY2027 baseline, then the
first quarter of FY2028). Forecast months carry two risks on top of seasonal-shape
error: CBO's own level risk (year-1 deficit RMSE ≈ 0.7% of GDP — `data/cbo_eval/`)
and customs/tariff policy risk.

Tariff-refund episodes (May–Jun 2026 customs) are policy events, handled as
scenarios, not seasonality.

The division of labor going forward: **MTS history carries monthly shape; DTS carries
intra-month shape** (mid-month/month-end coupon dates, Wednesday benefit cycles, the
15th corporate tax date) for the weekly/daily layer.

## Known limitations / roadmap

1. ~~Payment-calendar rule~~ — **done** (v2.1, above).
2. **Weekly/daily disaggregation** from the cached daily DTS (same buckets, finer calendar).
3. **Interest accrual-vs-cash routing**: the projected deficit uses accrual interest;
   when the issuance tracker is attached, the bill-discount component moves to the
   financing identity explicitly.
4. ~~Withheld income/FICA split calibration~~ — **done** (above); wire the
   month-specific shares in when composition matters.
5. **Debt-ceiling and shutdown windows** (2023 and 2025 episodes) should be dummied
   in any re-estimated shares; the Oct 2025 shutdown residual (−$59bn) is the live example.
6. **Uncertainty bands**: CBO's own year-1 deficit RMSE is ~0.7% of GDP
   (`data/cbo_eval/`); wrap the monthly path in those bands for scenario work.
7. Extending *daily* history before Feb 2023 requires the old DTS format (separate
   Table IV federal tax deposits, TT&L era before that) — deliberately deferred; the
   MTS route made it unnecessary for monthly shares.

## Dashboard

`mapping_dashboard.html` — regenerate with `build_charts.py` after any data refresh.
Cards: monthly deficit fit; cumulative wedge attribution; receipts by bucket (small
multiples); outlay wedges with named mechanisms; the SSA/HHS premium mirror; DTS-built
fiscal years vs CBO actuals (dumbbells); FY2025 deviation-from-CBO; and the FY2026
projected-vs-actual monthly path. Light/dark themes; every chart has hover tooltips
and a data-table twin.
