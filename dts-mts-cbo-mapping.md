# DTS → MTS → CBO Mapping for the Fiscal Pipeline

Purpose: turn DTS Table II daily cash flows into a monthly deficit consistent with MTS budget accounting, then aggregate to CBO's projection categories so the CBO annual numbers can be distributed onto your DTS-derived seasonality.

Pipeline: **DTS Table II line → MTS category (Table 4 receipts / Table 5 agency) → MTS function → CBO category.**

Line names below follow the published DTS format (post-Feb 2023 redesign). Verify exact strings against your downloaded dataset - Treasury occasionally renames lines, and the redesign moved federal tax deposits from the old Table IV into Table II, so pre-2023 history needs splicing.

---

## 1. Receipts

### 1.1 Mapping table

| DTS Table II deposit line | MTS Table 4 category | CBO revenue category |
|---|---|---|
| Taxes - Withheld Individual/FICA | Individual Income Taxes (withheld) **+** Social Insurance (Employment & General Retirement) | Individual income taxes **+** Payroll taxes (must be split - see 1.2) |
| Taxes - Non Withheld Ind/SECA Electronic | Individual Income Taxes (other) + SECA portion to Social Insurance | Individual income taxes + Payroll taxes (split) |
| Taxes - Non Withheld Ind/SECA Other | Same as above | Same as above |
| Taxes - Corporate Income | Corporation Income Taxes | Corporate income taxes |
| Taxes - Miscellaneous Excise | Excise Taxes | Other revenues |
| Taxes - Federal Unemployment (FUTA) | Social Insurance: Unemployment Insurance | Payroll taxes |
| Deposits by States: Unemployment | Social Insurance: Unemployment Insurance (state UI deposits) | Payroll taxes |
| Taxes - Railroad Retirement | Social Insurance: Other Retirement | Payroll taxes |
| Taxes - Estate and Gift | Estate and Gift Taxes | Other revenues |
| Customs and Certain Excise Taxes | Customs Duties (+ small excise) | Other revenues |
| Federal Reserve Earnings | Miscellaneous Receipts: Deposits of Earnings, FR Banks | Other revenues |
| Foreign Military Sales Program | Offsetting receipt (nets against Defence outlays in budget terms) | Not a CBO revenue - see 1.2 |
| Agency deposit lines (Agriculture, Education, Energy, HUD, Justice, Interior, etc.) | Mostly offsetting collections/receipts, small misc receipts | Mostly net against outlays; small tail to Other revenues |
| Postal Service | Off-budget postal revenue | Excluded / nets within off-budget postal |
| Interest recd from cash investments | Miscellaneous Receipts | Other revenues (tiny) |
| Public Debt Cash Issues (Table IIIB) | Financing, not a receipt | **Strip out** (your issuance tracker owns this) |
| Other Deposits (footnoted) | Case by case | Mostly Other revenues; watch for one-offs |

### 1.2 Receipts adjustments and caveats

**The withheld split is the big one.** DTS cannot distinguish income-tax withholding from FICA within "Withheld Individual/FICA" - the IRS allocates between the general fund and the trust funds only later. CBO and MTS treat these as separate categories (individual income vs payroll). Practical fix: split the DTS withheld line using historical MTS shares of withheld income tax vs social insurance receipts (the ratio is stable, roughly 55/45 in favour of income tax, but calibrate it yourself from MTS history rather than taking my figure). Same logic for non-withheld/SECA.

**IRS tax refunds** appear in DTS as withdrawals (and in DTS Table V detail), but MTS and CBO treat refunds as *negative receipts*, not outlays. You must reclassify: subtract refunds from gross receipts rather than adding them to outlays, otherwise both your receipts and outlays will be overstated relative to CBO and your deficit seasonality around Feb-May will be distorted.

**Fed remittances** have been abnormal since 2022 - the Fed ran losses and built a deferred asset, so remittances were near zero for an extended period. Don't let pre-2022 seasonality on this line inform your forward path; check the current state of the deferred asset before projecting.

**Offsetting collections.** Many agency deposit lines are offsetting collections that MTS nets against outlays rather than counting as receipts (Foreign Military Sales is the clearest case). If you count them as receipts and their matching outlays gross, your deficit is still right but both sides are overstated vs CBO. Decide once: either net them (matches CBO) or track gross consistently and only reconcile at deficit level.

---

## 2. Outlays

### 2.1 Mapping table

| DTS Table II withdrawal line | MTS Table 5 agency | MTS function | CBO category |
|---|---|---|---|
| Social Security Benefits (EFT) | Social Security Administration | Social Security (650) | Social Security (mandatory) |
| Supple. Security Income Benefits | SSA | Income Security (600) | Income security (mandatory) |
| Medicare and Other CMS Payments | Health and Human Services | Medicare (570) | Medicare (mandatory) |
| Medicare Advantage - Part C&D Payments | HHS | Medicare (570) | Medicare (mandatory) |
| Grants to States for Medicaid | HHS | Health (550) | Medicaid (mandatory) |
| Marketplace Payments | HHS | Health (550) | Health insurance subsidies (mandatory) |
| Health and Human Services Grants (misc) | HHS | Health (550) | Mostly nondefence discretionary |
| Temporary Assistance for Needy Families (HHS) | HHS | Income Security (600) | Income security (mandatory) |
| Defense Vendor Payments (EFT) | Defence - Military Programs | National Defense (050) | Defence discretionary |
| Dept of Defense - Military Active Duty Pay | Defence - Military Programs | National Defense (050) | Defence discretionary |
| Military Retirement / DFAS lines | Other Defence Civil Programs | Income Security (600) | Federal retirement (mandatory) |
| Interest on Treasury Securities | Treasury | Net Interest (900) | Net interest (see 2.2 - timing) |
| IRS Tax Refunds Individual / Business (EFT) | - | - | **Reclassify as negative receipts** |
| Unemployment Insurance Benefits | Labor | Income Security (600) | Income security (mandatory) |
| Labor Dept. prgms (excl. unemployment) | Labor | Education/Training (500) | Nondefence discretionary |
| Supple. Nutrition Assist. Program (SNAP) | Agriculture | Income Security (600) | Income security (mandatory) |
| Food and Nutrition Service / Child Nutrition | Agriculture | Income Security (600) | Income security (mandatory) |
| Farm Service Agency / CCC | Agriculture | Agriculture (350) | Other mandatory |
| Veterans Affairs programs / Veterans Benefits (EFT) | Veterans Affairs | Veterans (700) | Split: benefits mandatory, medical care discretionary |
| Education Department programs | Education | Education (500) | Split: student aid largely mandatory, rest discretionary |
| Housing and Urban Development programs | HUD | Income Security / Community Dev (600/450) | Mostly nondefence discretionary |
| Fed. Highway Administration programs | Transportation | Transportation (400) | Nondefence discretionary (trust-fund financed) |
| Federal Aviation Administration | Transportation | Transportation (400) | Nondefence discretionary |
| Federal Salaries (EFT) | **Spans many agencies** | Multiple | Mostly nondefence discretionary (see 2.2) |
| Federal Employees Insurance Payments | OPM | Health/Income Security | Federal retirement/health (mandatory) |
| Civil service retirement lines (OPM) | OPM | Income Security (600) | Federal retirement (mandatory) |
| Emergency Prep & Response / FEMA (DHS) | Homeland Security | Community Dev (450) | Nondefence discretionary |
| Transportation Security Admin. (DHS) | Homeland Security | Transportation (400) | Nondefence discretionary |
| Justice Department programs | Justice | Admin of Justice (750) | Nondefence discretionary |
| Energy Department programs | Energy | Energy / Defence (270/050) | Split: NNSA is defence discretionary |
| NASA programs | NASA | General Science (250) | Nondefence discretionary |
| GSA programs | GSA | General Government (800) | Nondefence discretionary |
| Postal Service Money Orders and Other | Postal (off-budget) | - | Excluded from CBO on-budget |
| Public Debt Cash Redemp. (Table IIIB) | Financing | - | **Strip out** (issuance tracker owns it) |
| Other Withdrawals (footnoted) | Case by case | - | Watch for lumpy one-offs (FDIC, settlements, student-loan actions) |

### 2.2 Outlays adjustments and caveats

**Interest is cash in DTS, accrual in MTS.** DTS "Interest on Treasury Securities" spikes on coupon payment dates (15th and month-end, semi-annual cycles). MTS recognises interest on public issues on an accrual basis. For monthly deficit purposes use the MTS treatment as your target and accept that the DTS line gives you cash timing, not the budget number. Since interest is large and growing, this is one of the biggest single DTS-vs-MTS wedges.

**Federal Salaries spans agencies.** DTS pools payroll across the government; MTS attributes it to each agency. Either allocate by MTS agency payroll shares or, simpler, keep it as its own block in your model and only allocate at the CBO-category level (mostly discretionary).

**Gross vs net.** MTS outlays are net of offsetting collections - Medicare premiums, for instance, net against Medicare outlays. DTS shows gross cash. So your DTS-built Medicare will run higher than MTS Medicare by roughly the premium take. Same decision as receipts: net or gross, consistently.

**Intragovernmental items never touch the DTS.** Undistributed offsetting receipts, employer-share retirement contributions, trust fund interest - these move the MTS/CBO composition but not operating cash. They largely wash at the total deficit level, which is another reason to reconcile at the deficit line, not line by line.

**Payment-date shifts.** When the 1st or 3rd of a month falls on a weekend/holiday, Social Security, veterans, SSI and military pay shift into the prior month. This creates large mechanical month-to-month swings (MTS footnotes flag them each time). Build a calendar rule for it or your monthly seasonality will be noisy for no economic reason.

---

## 3. MTS function → CBO category (the nearly-free hop)

| MTS function | CBO projection category |
|---|---|
| Social Security (650) | Social Security |
| Medicare (570) | Medicare (check gross vs net-of-premiums convention in the specific CBO table) |
| Health (550) | Medicaid + health subsidies (mandatory) + health discretionary |
| Income Security (600) | Income security programs + federal retirement (mandatory) |
| National Defense (050) | Defence discretionary (+ small mandatory) |
| Net Interest (900) | Net interest |
| Veterans (700) | Split mandatory (benefits) / discretionary (medical) |
| All remaining functions | Nondefence discretionary + other mandatory |

---

## 4. Practical build advice

1. **Map the head, pool the tail.** The top ~15 DTS lines (withheld taxes, non-withheld, corporate, Social Security, Medicare/Medicaid, defence, interest, refunds, SNAP, UI, VA, salaries, OPM) cover the large majority of flows. Map those explicitly; pool everything else into "other receipts" and "other outlays" and distribute the CBO residual pro rata. Chasing the tail line by line has terrible effort-to-accuracy payoff.
2. **Reconcile at three levels**: (a) monthly DTS-built receipts/outlays vs published MTS (aim for small, stable, explainable wedges - refunds, interest accrual, netting); (b) fiscal-year-to-date total vs MTS deficit; (c) DTS public-debt table vs your issuance tracker as a cross-check.
3. **Handle the Feb 2023 break** in tax deposit lines before estimating seasonality across it.
4. **Debt-ceiling episodes** distort both the TGA and extraordinary-measures flows; consider dummying those windows out of your seasonal estimation.
5. Once the mapping is stable, the monthly model is: CBO annual receipts/outlays by category → distributed monthly by your DTS-derived seasonal shape per category → monthly deficit → plus TGA assumption → minus net coupon issuance → **net bills**.

---

*Line names are from the published DTS/MTS formats and should be verified against your downloaded data. The one number quoted (withheld split ratio) is indicative only - calibrate from MTS history.*
