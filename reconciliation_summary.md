# DTS -> MTS reconciliation: results summary

Run: 2023-03-01 to 2026-06-30 (40 months), live fiscaldata API, executed 2026-08-05.

## Definition-of-done checklist

| Criterion | Result |
|---|---|
| Script runs end to end on live data | Yes. Endpoints in the original script were already correct; no 404s. |
| Unmapped lines under 2% of gross flows | **0.000%** - the mapping covers all 196 live (type, line) pairs, with a coverage assertion in `build_mapping.py`. |
| Deficit tracks MTS with explained wedges | Yes - see decomposition below. Cumulative wedge -$867bn on a cumulative MTS deficit of $5,942bn, of which -$1,410bn is the interest accrual wedge and +$329bn is Education credit-reform reestimates; the unattributed residual is +$214bn (~3.6% of the deficit, ~$5bn/month, no trend). |
| Updated mapping committed with key corrections noted | Done - see "What changed in the mapping". |

## What actually needed fixing

1. **The API endpoints and field names were correct as written.** The expected failure mode #1 in the brief did not materialise.
2. **The line names were the real problem, and bigger than expected.** The live post-Feb-2023 dataset uses agency-prefixed names (`SSA - Benefits Payments`, not `Social Security Benefits (EFT)`), and there are **two further format breaks inside the window**: an FY2024 rename wave (Oct 2023: e.g. `Defense Vendor Payments (EFT)` -> `Dept of Defense (DoD) - misc`, `Postal Service` -> `United States Postal Service (USPS)`, subtotal rows dropped) and smaller renames in Oct/Nov 2025 (`DHS - Customs and Certain Excise Taxes` -> `DHS - Customs Duties, Taxes, and Fees`, `HHS - Payments to States` -> `HHS - Payments to Children and Families`, `USDA - Loan Payments` -> `USDA - Rural Services`). Rather than patch 52 keys, the mapping is now **generated** by `build_mapping.py`, which classifies every observed line and *errors* if Treasury introduces a new name - future renames surface loudly instead of leaking into "unmapped".
3. **Structural rows**: daily `Total Deposits/Withdrawals` account-type rows and the Mar-Sep 2023 `Sub-Total` rows must be dropped. Verified additivity: TGA line rows sum exactly to the published daily totals in every format era.

## What changed in the mapping (treatments)

- **All keys re-written** to exact live API strings; treatments and CBO categories preserved from the design.
- **New lines classified** per the design doc's logic: agency deposit lines -> `offsetting` (net against that agency's outlays, matching MTS Table 5 netting); `FCC - Universal Service Fund` deposits -> excise (MTS counts USF under Excise Taxes); `Federal Retirement Thrift Savings Plan` both sides -> `exclude` (fiduciary, validated: nets to $0.3bn over 40 months).
- **One deliberate treatment change, flagged**: USPS was `exclude` (off-budget) in the design, but MTS Total Outlays and the deficit - and the CBO total-deficit target - include off-budget postal. USPS is now included (deposits offsetting, withdrawals mapped). Its net is material: +$181bn of deposits over withdrawals in-window. Note the USPS withdrawal line (~$42bn/yr) is far below USPS operating costs - postal payroll evidently flows outside this line pair - so postal is only partially capturable from DTS; this is part of the residual noise.
- Original file preserved as `dts_mts_cbo_mapping_original.csv`.

## Headline fit

DTS-implied monthly deficit vs published MTS deficit (`recon_deficit.csv`):
- Mean |monthly wedge| $51bn (12.4% of outlays); cumulative wedge -$867bn over 40 months.
- The cumulative wedge decomposes almost entirely into two known accounting mechanisms:
  - **Interest cash vs accrual: -$1,410bn.** DTS cash interest $1,800bn vs MTS net accrued interest $3,210bn. The dominant driver is **bill discount accrual**: bills pay their interest inside redemption amounts, which the mapping strips as financing; MTS accrues it as interest outlays monthly. TIPS inflation accrual and savings-bond accrual add to it. This wedge is structural and will grow with bill issuance - do not try to fix it in the mapping; model interest on the MTS/accrual basis directly.
  - **Education credit reform: +$329bn** net, concentrated in reestimate months: Aug 2023 (+$341bn wedge - the forgiveness reversal made MTS ED -$320bn that month), Jul 2023 (-$71bn, SAVE plan booking), Jun 2024 (-$73bn), Sep 2025 (+$127bn), Jun 2026 (+$45bn). Non-cash MTS entries; DTS correctly shows none of them.
  - Remainder: +$214bn (~$5bn/month), timing/cutoff noise with no trend.

## Per-bucket wedges, one line each

**Receipts** (`recon_receipts.csv`):
- `income_payroll` -2%: refund timing plus MTS's classification of refundable EITC/CTC as outlays (DTS refund cash is all negative receipts). Stable.
- `corporate` -1%: clean.
- `excise` +10%: DTS misc excise leads MTS slightly (trust-fund excise timing); stable.
- `estate_gift` +5%: clean (the misc component of `Taxes - IRS Collected Estate, Gift, misc` sits here).
- `customs` +29%: clean at ~+$2.5bn/month (fees timing) until **May-Jun 2026, when MTS goes negative (-$25.6bn in June) on IEEPA tariff refunds**; the refund cash appears in DTS as `DHS - Customs & Border Protection (CBP)` withdrawals, so it cancels at deficit level but blows up both the customs and DHS buckets. If tariff refunds persist, consider reclassifying part of CBP withdrawals as negative customs receipts.
- `misc_receipts` -73%: most MTS "All Other" misc receipts arrive through agency deposit lines we net against outlays. Composition choice, not an error; deficit-neutral.

**Outlays** (`recon_outlays.csv`):
- `ssa` -11% (-$15-16bn/month, steady): **Medicare Part B/D premiums withheld from benefit checks** - DTS pays net, MTS records gross benefits. Mirrored by:
- `hhs` +12%: MTS Medicare is net of those same withheld premiums (plus direct premiums, which the mapping already nets). The pair largely cancels at total level.
- `treasury` -58% and `residual_all_other` +409%: read together - accrued gross interest and refundable credits sit in MTS Treasury; trust-fund interest received and employer-share retirement offsets sit in MTS's undistributed block (our residual); DTS's `Federal Salaries (EFT)` ($812bn) and `Unclassified` ($1,089bn) pools sit in our residual but belong across MTS agencies.
- `dod` -36%, `doj` -73%, `dos` -45%, `doc` -54%, `va` -20%, `dhs` -21%: agency payroll flows through the pooled `Federal Salaries (EFT)` line, and (for DoD) classified/check payments through `Unclassified`. Structural DTS limitation - agency-level comparisons are only meaningful after allocating the salary pool (e.g. by MTS payroll shares).
- `ed` +148%: credit reform (see above). DTS is the better cash signal here.
- `opm` +36%: employer-share retirement contributions are intragovernmental (never touch DTS) and net out in MTS's undistributed block.
- `usda`, `dol`, `doe`, `dot` within ±8%: clean.

## Payment-date shifts

Benefit payments (SSA, VA, SSI, military pay) shift into the prior month when the 1st/3rd falls on a weekend - but MTS records cash the same way, so **these do not create DTS-vs-MTS wedges**. They matter for the next stage (seasonality estimation), not for this reconciliation.

## Files

- `build_mapping.py` - generates `dts_mts_cbo_mapping.csv` from the observed line universe; full-coverage assertion.
- `fetch_dts.py` - raw DTS pull + line universe enumeration.
- `reconcile_dts_mts.py` - reconciliation; caches API pulls under `data/`, `--refresh` to re-pull.
- Outputs: `recon_deficit.csv`, `recon_receipts.csv`, `recon_outlays.csv`, `dts_monthly_by_category.csv`.

## Watch-list for the model build

1. Handle the **Oct 2023 rename wave** when estimating seasonality on agency lines (splice old/new names; `build_mapping.py` maps both to the same buckets, so bucket-level series are already continuous).
2. Interest must be modelled on the **accrual basis** (CBO/MTS), with DTS giving intra-month cash timing only.
3. The withheld income/FICA **split rule** (`mts_share_withheld`) still needs calibrating from MTS Table 4 history before the CBO distribution stage.
4. Debt-ceiling windows (Jan-Jun 2023 in-sample edge, 2025 episode) distort line-level flows - dummy them in seasonal estimation.
5. Fed remittances: DTS shows $15.6bn over the whole window (deferred asset era); do not project from this level mechanically.
