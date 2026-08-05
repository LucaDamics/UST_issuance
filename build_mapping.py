"""Rebuild dts_mts_cbo_mapping.csv keyed to the LIVE DTS Table II line names.

The original mapping CSV was written from the published DTS format before the
line universe was pulled; the live API names differ (agency-prefixed format,
plus renames at FY2024 and Nov 2025). This script classifies every distinct
(transaction_type, transaction_catg) pair observed in data/dts_line_universe.csv
and errors if any line is left uncovered, so new Treasury renames surface
loudly instead of leaking into "unmapped".

Treatments (same taxonomy as the original mapping):
  map              budget flow, kept on its natural side
  split            withheld/SECA lines that mix individual income tax and FICA
  pooled           kept as a block (Federal Salaries, Unclassified)
  strip            financing flows - public debt issues/redemptions
  exclude          non-budget flows (off-budget postal per design; TSP fiduciary)
  negative_receipt tax refunds: cash withdrawals that MTS nets off receipts
  offsetting       deposits that MTS nets against agency outlays
  total_row        DTS subtotal rows - structural, dropped before aggregation

recon_bucket groups lines for the DTS-vs-MTS comparison:
  receipts: income_payroll, corporate, excise, estate_gift, customs, misc_receipts
  outlays:  one bucket per MTS Table 5 agency total (hhs, ssa, dod, ...) plus
            salaries / unclassified / other_agencies, which have no single MTS
            counterpart and are compared as a residual.
"""

import sys

import pandas as pd

# (transaction_type, exact live transaction_catg) -> (treatment, recon_bucket, cbo_category, notes)
D, W = "Deposits", "Withdrawals"

C = {
    # ---------- structural rows ----------
    (D, "Sub-Total Deposits"): ("total_row", "", "", "Mar-Sep 2023 format subtotal; drop"),
    (W, "Sub-Total Withdrawals"): ("total_row", "", "", "Mar-Sep 2023 format subtotal; drop"),

    # ---------- financing ----------
    (D, "Public Debt Cash Issues (Table IIIB)"): ("strip", "financing", "financing", "Issuance tracker owns this"),
    (W, "Public Debt Cash Redemp. (Table IIIB)"): ("strip", "financing", "financing", "Issuance tracker owns this"),

    # ---------- receipts: taxes ----------
    (D, "Taxes - Withheld Individual/FICA"): ("split", "income_payroll", "individual_income+payroll", "Split via MTS withheld shares for CBO stage"),
    (D, "Taxes - Non Withheld Ind/SECA Electronic"): ("split", "income_payroll", "individual_income+payroll", "SECA portion to payroll"),
    (D, "Taxes - Non Withheld Ind/SECA Other"): ("split", "income_payroll", "individual_income+payroll", "SECA portion to payroll"),
    (D, "Taxes - Corporate Income"): ("map", "corporate", "corporate_income", ""),
    (D, "Taxes - Miscellaneous Excise"): ("map", "excise", "other_revenue", ""),
    (D, "Taxes - Federal Unemployment (FUTA)"): ("map", "income_payroll", "payroll", ""),
    (D, "State Unemployment Insurance Deposits"): ("map", "income_payroll", "payroll", "MTS 'Deposits by States' under Social Insurance"),
    (D, "Taxes - Railroad Retirement"): ("map", "income_payroll", "payroll", ""),
    (D, "RRB - Unemployment Insurance"): ("map", "income_payroll", "payroll", "Tiny"),
    (D, "Taxes - Estate and Gift"): ("map", "estate_gift", "other_revenue", ""),
    (D, "Taxes - IRS Collected Estate, Gift, misc"): ("map", "estate_gift", "other_revenue", "Contains some misc IRS collections"),
    (D, "DHS - Customs and Certain Excise Taxes"): ("map", "customs", "other_revenue", "Name until Nov 2025"),
    (D, "DHS - Customs Duties, Taxes, and Fees"): ("map", "customs", "other_revenue", "Renamed line from 10 Nov 2025"),
    (D, "Federal Reserve Earnings"): ("map", "misc_receipts", "other_revenue", "Suppressed by Fed deferred asset in part of sample"),
    (D, "FCC - Universal Service Fund"): ("map", "excise", "other_revenue", "MTS counts USF under Excise Taxes"),
    (D, "Other Deposits"): ("pooled", "misc_receipts", "other_revenue", "Mar-Sep 2023 footnoted line"),
    (D, "Unclassified - Deposits"): ("pooled", "misc_receipts", "other_revenue", ""),

    # ---------- refunds: negative receipts ----------
    (W, "Taxes - Individual Tax Refunds (EFT)"): ("negative_receipt", "income_payroll", "individual_income", "MTS nets refunds off receipts"),
    (W, "Taxes - Business Tax Refunds (EFT)"): ("negative_receipt", "corporate", "corporate_income", "MTS nets refunds off receipts"),
    (W, "TREAS - IRS Refunds for Puerto Rico"): ("negative_receipt", "income_payroll", "individual_income", "Covered-over PR refunds"),

    # ---------- postal: TREATMENT CHANGE vs original design (was exclude) ----------
    # MTS Total Outlays and the deficit INCLUDE off-budget postal, and the CBO
    # target is the total deficit, so postal must stay in the aggregate.
    (D, "Postal Service"): ("offsetting", "other_agencies", "net_against_outlays", "TREATMENT CHANGE: was exclude; postal revenue nets against outlays. Name until Sep 2023"),
    (W, "Postal Service Money Orders and Other"): ("map", "other_agencies", "other_mandatory", "TREATMENT CHANGE: was exclude. Name until Sep 2023"),
    (D, "United States Postal Service (USPS)"): ("offsetting", "other_agencies", "net_against_outlays", "TREATMENT CHANGE: was exclude; MTS/CBO total deficit includes postal. Note: USPS payroll appears to flow outside this line pair"),
    (W, "United States Postal Service (USPS)"): ("map", "other_agencies", "other_mandatory", "TREATMENT CHANGE: was exclude; MTS/CBO total deficit includes postal"),
    (D, "Federal Retirement Thrift Savings Plan"): ("exclude", "tsp_fiduciary", "excluded", "TSP fiduciary flows, not federal budget; near-wash both sides"),
    (W, "Federal Retirement Thrift Savings Plan"): ("exclude", "tsp_fiduciary", "excluded", "TSP fiduciary flows, not federal budget; near-wash both sides"),

    # ---------- interest ----------
    (W, "Interest on Treasury Securities"): ("map", "treasury", "net_interest", "Cash on coupon dates; MTS accrues - the big wedge"),

    # ---------- SSA ----------
    (W, "SSA - Benefits Payments"): ("map", "ssa", "social_security", "Shifts when 3rd falls on weekend"),
    (W, "SSA - Supplemental Security Income"): ("map", "ssa", "income_security", "Shifts when 1st falls on weekend"),
    (W, "Social Security Admin (SSA) - misc"): ("map", "ssa", "nondefence_discretionary", "Admin"),
    (D, "Social Security Admin (SSA) - misc"): ("offsetting", "ssa", "net_against_outlays", ""),
    (D, "SSA - Supplemental Security Income"): ("offsetting", "ssa", "net_against_outlays", "State SSI supplement payments"),

    # ---------- HHS ----------
    (W, "HHS - Grants to States for Medicaid"): ("map", "hhs", "medicaid", ""),
    (W, "HHS - Federal Supple Med Insr Trust Fund"): ("map", "hhs", "medicare", "SMI - Parts B/D"),
    (W, "HHS - Federal Hospital Insr Trust Fund"): ("map", "hhs", "medicare", "HI - Part A"),
    (W, "HHS - Medicare Prescription Drugs"): ("map", "hhs", "medicare", ""),
    (W, "HHS - Othr Cent Medicare & Medicaid Serv"): ("map", "hhs", "medicare", "CMS other"),
    (W, "HHS - Marketplace Payments"): ("map", "hhs", "health_subsidies", ""),
    (W, "HHS - National Institutes of Health"): ("map", "hhs", "nondefence_discretionary", ""),
    (W, "HHS - Centers for Disease Control (CDC)"): ("map", "hhs", "nondefence_discretionary", ""),
    (W, "HHS - Health Resources & Services Admin"): ("map", "hhs", "nondefence_discretionary", ""),
    (W, "HHS - Indian Health Service"): ("map", "hhs", "nondefence_discretionary", ""),
    (W, "HHS - Other Public Health Services"): ("map", "hhs", "nondefence_discretionary", ""),
    (W, "HHS - Payments to States"): ("map", "hhs", "income_security", "TANF/child care/foster block; name until Sep 2025"),
    (W, "HHS - Payments to Children and Families"): ("map", "hhs", "income_security", "Successor of Payments to States from Oct 2025"),
    (W, "HHS - Temp Assistance for Needy Families"): ("map", "hhs", "income_security", ""),
    (W, "HHS - Othr Admin for Children & Families"): ("map", "hhs", "income_security", ""),
    (W, "Dept of Health & Human Serv (HHS) - misc"): ("map", "hhs", "nondefence_discretionary", ""),
    (D, "HHS - Medicare Premiums"): ("offsetting", "hhs", "net_against_outlays", "MTS Medicare is net of premiums; DTS gross - this restores comparability"),
    (D, "HHS - Marketplace Receipts"): ("offsetting", "hhs", "net_against_outlays", ""),
    (D, "Dept of Health & Human Serv (HHS) - misc"): ("offsetting", "hhs", "net_against_outlays", ""),

    # ---------- DoD ----------
    (W, "Defense Vendor Payments (EFT)"): ("map", "dod", "defence_discretionary", "Name until Sep 2023"),
    (W, "Dept of Defense (DoD) - misc"): ("map", "dod", "defence_discretionary", "Successor line from Oct 2023"),
    (W, "DoD - Military Active Duty Pay"): ("map", "dod", "defence_discretionary", "Shifts when 1st is non-business day"),
    (W, "DoD - Health"): ("map", "dod", "defence_discretionary", "Split out from Oct 2024"),
    (W, "DoD - Military Retirement"): ("map", "odcp", "federal_retirement", "MTS: Other Defense Civil Programs"),
    (D, "Dept of Defense (DoD)"): ("offsetting", "dod", "net_against_outlays", "FY2024-only deposit line name"),
    (D, "Dept of Defense (DoD) - misc"): ("offsetting", "dod", "net_against_outlays", ""),
    (D, "DoD - Health"): ("offsetting", "dod", "net_against_outlays", ""),

    # ---------- veterans ----------
    (W, "VA - Benefits"): ("map", "va", "veterans_mandatory", "Shifts when 1st is non-business day"),
    (W, "Dept of Veterans Affairs (VA)"): ("map", "va", "veterans_mixed", "Mostly medical care (discretionary)"),
    (D, "Dept of Veterans Affairs (VA)"): ("offsetting", "va", "net_against_outlays", ""),

    # ---------- education ----------
    (W, "Dept of Education (ED)"): ("map", "ed", "education_mixed", "Student aid largely mandatory. MTS ED swings negative on credit reestimates - expect big wedges"),
    (D, "Dept of Education (ED)"): ("offsetting", "ed", "net_against_outlays", "Student loan repayments; credit-reform cash vs accrual wedge"),

    # ---------- labor ----------
    (W, "DOL - Unemployment Benefits"): ("map", "dol", "income_security", ""),
    (W, "DOL - Pension Benefit Guaranty Corp."): ("map", "dol", "other_mandatory", ""),
    (W, "Dept of Labor (DOL) - misc"): ("map", "dol", "nondefence_discretionary", ""),
    (D, "DOL - Pension Benefit Guaranty Corp."): ("offsetting", "dol", "net_against_outlays", "PBGC premiums/investment income"),
    (D, "Dept of Labor (DOL) - misc"): ("offsetting", "dol", "net_against_outlays", ""),

    # ---------- agriculture ----------
    (W, "USDA - Supp Nutrition Assist Prog (SNAP)"): ("map", "usda", "income_security", ""),
    (W, "USDA - Child Nutrition"): ("map", "usda", "income_security", ""),
    (W, "USDA - Supp Nutrition Assist Prog (WIC)"): ("map", "usda", "income_security", ""),
    (W, "USDA - Other Farm Service"): ("map", "usda", "other_mandatory", ""),
    (W, "USDA - Commodity Credit Corporation"): ("map", "usda", "other_mandatory", "Lumpy"),
    (W, "USDA - Federal Crop Insurance Corp Fund"): ("map", "usda", "other_mandatory", ""),
    (W, "USDA - Loan Payments"): ("map", "usda", "other_mandatory", "Name until Sep 2025"),
    (W, "USDA - Rural Services"): ("map", "usda", "other_mandatory", "Successor from Oct 2025"),
    (W, "Dept of Agriculture (USDA) - misc"): ("map", "usda", "nondefence_discretionary", ""),
    (D, "Dept of Agriculture (USDA) - misc"): ("offsetting", "usda", "net_against_outlays", ""),
    (D, "USDA - Commodity Credit Corporation"): ("offsetting", "usda", "net_against_outlays", ""),
    (D, "USDA - Loan Repayments"): ("offsetting", "usda", "net_against_outlays", "Name until Sep 2025"),
    (D, "USDA - Rural Services"): ("offsetting", "usda", "net_against_outlays", "Successor from Oct 2025"),
    (D, "USDA - Federal Crop Insurance Corp Fund"): ("offsetting", "usda", "net_against_outlays", ""),

    # ---------- transportation ----------
    (W, "DOT - Federal Highway Administration"): ("map", "dot", "nondefence_discretionary", "Trust-fund financed"),
    (W, "DOT - Federal Transit Administration"): ("map", "dot", "nondefence_discretionary", ""),
    (W, "DOT - Federal Aviation Administration"): ("map", "dot", "nondefence_discretionary", ""),
    (W, "DOT - Federal Railroad Administration"): ("map", "dot", "nondefence_discretionary", ""),
    (W, "Dept of Transportation (DOT) - misc"): ("map", "dot", "nondefence_discretionary", ""),
    (D, "Dept of Transportation (DOT)"): ("offsetting", "dot", "net_against_outlays", ""),

    # ---------- homeland security ----------
    (W, "DHS - Fed Emergency Mgmt Agency (FEMA)"): ("map", "dhs", "nondefence_discretionary", "Lumpy around disasters"),
    (W, "DHS - Customs & Border Protection (CBP)"): ("map", "dhs", "nondefence_discretionary", "Includes tariff refunds/drawback - drives customs wedge in 2026"),
    (W, "DHS - Transportation Security Admn (TSA)"): ("map", "dhs", "nondefence_discretionary", ""),
    (W, "Dept of Homeland Security (DHS) - misc"): ("map", "dhs", "nondefence_discretionary", ""),
    (D, "Dept of Homeland Security (DHS) - misc"): ("offsetting", "dhs", "net_against_outlays", ""),
    (D, "DHS - Transportation Security Admn (TSA)"): ("offsetting", "dhs", "net_against_outlays", "TSA fees"),
    (D, "DHS - Fed Emergency Mgmt Agency (FEMA)"): ("offsetting", "dhs", "net_against_outlays", ""),
    (D, "DHS - Seized Assets"): ("offsetting", "dhs", "net_against_outlays", ""),

    # ---------- other cabinet departments ----------
    (W, "Dept of Justice (DOJ)"): ("map", "doj", "nondefence_discretionary", ""),
    (D, "Dept of Justice (DOJ)"): ("offsetting", "doj", "net_against_outlays", "Fines/settlements"),
    (W, "Dept of State (DOS)"): ("map", "dos", "nondefence_discretionary", ""),
    (D, "Dept of State (DOS)"): ("offsetting", "dos", "net_against_outlays", "Consular fees"),
    (W, "Dept of Commerce (DOC)"): ("map", "doc", "nondefence_discretionary", ""),
    (D, "Dept of Commerce (DOC)"): ("offsetting", "doc", "net_against_outlays", ""),
    (W, "Dept of Energy (DOE)"): ("map", "doe", "energy_mixed", "NNSA share is defence"),
    (D, "Dept of Energy (DOE)"): ("offsetting", "doe", "net_against_outlays", "Power marketing receipts"),
    (W, "Dept of Housing & Urban Dev (HUD) - misc"): ("map", "hud", "nondefence_discretionary", ""),
    (W, "HUD - Federal Housing Admin (FHA)"): ("map", "hud", "other_mandatory", "Credit programs"),
    (D, "Dept of Housing & Urban Dev (HUD) - misc"): ("offsetting", "hud", "net_against_outlays", ""),
    (D, "HUD - Federal Housing Admin (FHA)"): ("offsetting", "hud", "net_against_outlays", "FHA premiums"),
    (W, "Dept of Interior (DOI) - misc"): ("map", "doi", "nondefence_discretionary", ""),
    (W, "DOI - Fish and Wildlife and Parks"): ("map", "doi", "nondefence_discretionary", ""),
    (W, "DOI - Land and Minerals Management"): ("map", "doi", "nondefence_discretionary", ""),
    (W, "DOI - Water and Science"): ("map", "doi", "nondefence_discretionary", ""),
    (D, "Dept of Interior (DOI) - misc"): ("offsetting", "doi", "net_against_outlays", ""),
    (D, "DOI - Fish and Wildlife and Parks"): ("offsetting", "doi", "net_against_outlays", ""),
    (D, "DOI - Land and Minerals Management"): ("offsetting", "doi", "net_against_outlays", ""),
    (D, "DOI - Water and Science"): ("offsetting", "doi", "net_against_outlays", ""),
    (D, "DOI - Gas and Oil Lease Sales Proceeds"): ("offsetting", "other_agencies", "net_against_outlays", "MTS books as undistributed offsetting receipts, not under Interior"),

    # ---------- treasury (non-interest) ----------
    (W, "Dept of Treasury (TREAS) - misc"): ("map", "treasury", "nondefence_discretionary", ""),
    (W, "TREAS - Federal Financing Bank"): ("map", "treasury", "other_mandatory", ""),
    (W, "TREAS - United States Mint"): ("map", "treasury", "nondefence_discretionary", ""),
    (W, "TREAS - Bureau of Engraving and Printing"): ("map", "treasury", "nondefence_discretionary", ""),
    (W, "TREAS - Comptroller of the Currency"): ("map", "treasury", "nondefence_discretionary", ""),
    (W, "TREAS - Claims Judgments and Relief Acts"): ("map", "treasury", "other_mandatory", ""),
    (W, "TREAS - Pmt to Resolution Funding Corp"): ("map", "treasury", "other_mandatory", ""),
    (W, "ESF - Economic Recovery Programs"): ("map", "treasury", "other_mandatory", "Runs negative (recoveries)"),
    (W, "Coronavirus Relief Fund"): ("map", "treasury", "other_mandatory", "Residual pandemic flows"),
    (W, "Emergency Rental Assistance"): ("map", "treasury", "other_mandatory", "Residual pandemic flows"),
    (D, "Dept of Treasury (TREAS) - misc"): ("offsetting", "treasury", "net_against_outlays", ""),
    (D, "TREAS - Federal Financing Bank"): ("offsetting", "treasury", "net_against_outlays", ""),
    (D, "TREAS - United States Mint"): ("offsetting", "treasury", "net_against_outlays", "Seigniorage etc."),
    (D, "TREAS - Bureau of Engraving and Printing"): ("offsetting", "treasury", "net_against_outlays", ""),
    (D, "TREAS - Comptroller of the Currency"): ("offsetting", "treasury", "net_against_outlays", ""),
    (D, "TREAS - GSE Proceeds"): ("offsetting", "treasury", "net_against_outlays", "GSE dividends - offsetting receipts"),

    # ---------- OPM ----------
    (W, "OPM - Civil Serv Retirement & Disability"): ("map", "opm", "federal_retirement", ""),
    (W, "OPM - Federal Employee Insurance Payment"): ("map", "opm", "federal_retirement", "FEHB etc."),
    (W, "Office of Personnel Mgmt (OPM) - misc"): ("map", "opm", "federal_retirement", ""),
    (D, "OPM - Federal Employee Insurance Receipt"): ("offsetting", "opm", "net_against_outlays", ""),
    (D, "Office of Personnel Mgmt (OPM) - misc"): ("offsetting", "opm", "net_against_outlays", ""),

    # ---------- international ----------
    (W, "IAP - Foreign Military Sales"): ("map", "iap", "nondefence_discretionary", "FMS trust outlays"),
    (D, "IAP - Foreign Military Sales"): ("offsetting", "iap", "net_against_outlays", "FMS advances net against the trust outlays"),
    (D, "Foreign Military Sales Program"): ("offsetting", "iap", "net_against_outlays", "Name until Sep 2023"),
    (W, "IAP - Agency for Int'l Development (AID)"): ("map", "iap", "nondefence_discretionary", "Line ends Sep 2025"),
    (D, "IAP - Agency for Int'l Development (AID)"): ("offsetting", "iap", "net_against_outlays", ""),
    (W, "IAP - Multilateral Assistance"): ("map", "iap", "nondefence_discretionary", ""),
    (W, "IAP - US Int'l Devlop Finance Corp (DFC)"): ("map", "iap", "nondefence_discretionary", ""),
    (D, "IAP - US Int'l Devlop Finance Corp (DFC)"): ("offsetting", "iap", "net_against_outlays", ""),
    (W, "Int'l Assistance Programs (IAP) - misc"): ("map", "iap", "nondefence_discretionary", ""),
    (D, "Int'l Assistance Programs (IAP) - misc"): ("offsetting", "iap", "net_against_outlays", ""),
    (W, "International Monetary Fund (IMF)"): ("map", "iap", "other_mandatory", "Quota/NAB exchanges - mostly monetary asset swaps; watch wedge"),
    (D, "International Monetary Fund (IMF)"): ("offsetting", "iap", "net_against_outlays", ""),

    # ---------- corps of engineers ----------
    (W, "US Army Corps of Engineers"): ("map", "corps", "nondefence_discretionary", ""),
    (D, "US Army Corps of Engineers"): ("offsetting", "corps", "net_against_outlays", ""),

    # ---------- cross-agency pools ----------
    (W, "Federal Salaries (EFT)"): ("pooled", "salaries", "nondefence_discretionary", "Cross-agency payroll; no single MTS counterpart"),
    (W, "Unclassified"): ("pooled", "unclassified", "other_outlays", "Successor of Other Withdrawals from Oct 2023; ~$1bn/day"),
    (W, "Other Withdrawals"): ("pooled", "unclassified", "other_outlays", "Mar-Sep 2023 footnoted line"),

    # ---------- independent agencies (compared as residual block) ----------
    (W, "General Services Administration (GSA)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "General Services Administration (GSA)"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "NASA"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (W, "National Science Foundation (NSF)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "National Science Foundation (NSF)"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Environmental Protection Agency (EPA)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Environmental Protection Agency (EPA)"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Securities and Exchange Commission (SEC)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Securities and Exchange Commission (SEC)"): ("offsetting", "other_agencies", "net_against_outlays", "Registration fees"),
    (W, "Federal Trade Commission (FTC)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Federal Trade Commission (FTC)"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Federal Communications Commission (FCC)"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Federal Communications Commission (FCC)"): ("offsetting", "other_agencies", "net_against_outlays", "Spectrum/regulatory fees"),
    (W, "FCC - Universal Service Fund"): ("map", "other_agencies", "other_mandatory", "USF payouts; receipts side sits in excise"),
    (W, "Small Business Administration (SBA)"): ("map", "other_agencies", "other_mandatory", "Credit programs"),
    (D, "Small Business Administration (SBA)"): ("offsetting", "other_agencies", "net_against_outlays", "Loan repayments/fees"),
    (W, "Federal Deposit Insurance Corp (FDIC)"): ("map", "other_agencies", "other_mandatory", "Resolution outflows"),
    (D, "Federal Deposit Insurance Corp (FDIC)"): ("offsetting", "other_agencies", "net_against_outlays", "Premiums/recoveries"),
    (W, "National Credit Union Admin (NCUA)"): ("map", "other_agencies", "other_mandatory", ""),
    (D, "National Credit Union Admin (NCUA)"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Export-Import Bank"): ("map", "other_agencies", "other_mandatory", ""),
    (D, "Export-Import Bank"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "RRB - Benefit Payments"): ("map", "other_agencies", "federal_retirement", "Railroad retirement benefits"),
    (W, "Railroad Retirement Board (RRB) - misc"): ("map", "other_agencies", "federal_retirement", ""),
    (D, "RRB - Natl Railroad Retirement Inv Trust"): ("offsetting", "other_agencies", "net_against_outlays", "NRRIT transfers fund benefits"),
    (W, "Judicial Branch - Courts"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Judicial Branch - Courts"): ("offsetting", "other_agencies", "net_against_outlays", "Court fees/deposits"),
    (W, "Legislative Branch - misc"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Legislative Branch - misc"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Legislative Branch - Library of Congress"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Legislative Branch - Library of Congress"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "District of Columbia"): ("map", "other_agencies", "nondefence_discretionary", "Federal payments to DC"),
    (D, "District of Columbia"): ("offsetting", "other_agencies", "net_against_outlays", ""),
    (W, "Corporation for Public Broadcasting"): ("map", "other_agencies", "nondefence_discretionary", "Line ends Sep 2025"),
    (W, "Independent Agencies - misc"): ("map", "other_agencies", "nondefence_discretionary", ""),
    (D, "Independent Agencies - misc"): ("offsetting", "other_agencies", "net_against_outlays", ""),
}

RECEIPT_BUCKETS = {"income_payroll", "corporate", "excise", "estate_gift", "customs", "misc_receipts"}


def main():
    uni = pd.read_csv("data/dts_line_universe.csv")
    # 'null' names (read as NaN) are the account_type Total rows - structural, not lines
    uni = uni[uni["transaction_catg"].notna()]

    missing = []
    rows = []
    for _, r in uni.iterrows():
        key = (r["transaction_type"], r["transaction_catg"])
        if key not in C:
            missing.append(key)
            continue
        treatment, bucket, cbo, notes = C[key]
        rows.append(
            {
                "dts_line": r["transaction_catg"],
                "flow_type": "deposit" if r["transaction_type"] == D else "withdrawal",
                "treatment": treatment,
                "recon_bucket": bucket,
                "mts_side": ("receipt" if bucket in RECEIPT_BUCKETS else "outlay") if bucket else "",
                "cbo_category": cbo,
                "split_rule": "mts_share_withheld" if treatment == "split" else "",
                "total_usd_mn_2023_2026": round(r["total_usd_mn"]),
                "first_seen": r["first"],
                "last_seen": r["last"],
                "notes": notes,
            }
        )

    if missing:
        print("UNCLASSIFIED LINES - add to C:", file=sys.stderr)
        for k in missing:
            print(f"  {k}", file=sys.stderr)
        sys.exit(1)

    out = pd.DataFrame(rows).sort_values(
        ["flow_type", "total_usd_mn_2023_2026"], ascending=[True, False]
    )
    out.to_csv("dts_mts_cbo_mapping.csv", index=False)
    print(f"Wrote dts_mts_cbo_mapping.csv with {len(out)} rows, full coverage of the live line universe.")
    unused = set(C) - {(r["transaction_type"], r["transaction_catg"]) for _, r in uni.iterrows()}
    if unused:
        print(f"Note: {len(unused)} classification entries not present in this window (fine):")
        for k in sorted(unused):
            print(f"  {k}")


if __name__ == "__main__":
    main()
