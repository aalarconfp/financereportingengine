# Methodology (sanitized)

The full operating procedure lives in the Claude skill `personal-finance-monthly-report`. This page keeps the rules the code depends on, without any personal data. Changing any of them requires explicit approval.

## Accounting conventions

1. **Cash Basis.** Income and expense are recognized when the movement is recorded in MoneyPro. Financial income that was not withdrawn/recorded is not income of the period.
2. **Income composition.** Financial Income = Interest Income + Dividends + Capital Gain. Other Income = extraordinary. Benefits Income = cashback/points (non-cash). Active Income = wages/payroll. The four add up to Income.
3. **Result = Income − Expense** (main result). `[Net Income]` excludes Other Income and is labelled "operating".
4. **Historical Cost.** Asset Amount is historical cost; never mixed with market value.
5. **Internal Money Transfers** neither create nor destroy net worth.
6. **Capital Gain** is recognized when the cash is recorded; differences are recording characteristics, not double counting, unless proven mathematically.

## Liquidity

- **Official Liquid Cash Runway** = Σ balances of accounts with Asset Type "Cash" ÷ historical Average Monthly Expense (cumulative expense ÷ calendar months with real expense, up to the close). Always computed independently (Q3 + Q4).
- The model measure is a technical reference only: in a yearly context it divides by 12 months, including months without real expense.
- Three liquidity levels are never summed into the runway: Cash-type (Power BI), Short Investments and Treasury (Net Worth Tracker).

## Net Worth Tracker

A complementary perspective at market value (liabilities, retirement, investments). It is never reconciled mathematically with the Cash Basis net worth; differences are methodological.

## MoneyPro month-close controls

- Each Word close holds the four donuts (Income, Expenses, Other Assets, Assets) **accumulated from the Beginning Period to its End Period**. They are control snapshots, never monthly movements.
- A close is identified by its End Period, not by its file name. One Word = one End Period; a donut without a visible date inherits it; different dates or a monthly view block.
- In the Assets donut, "Other Assets" is the subtotal of the Other Assets donut; any other line (e.g. payment accounts) is Type "Assets".
- Categories that disappear from a close are kept (never deleted or zeroed) and require human confirmation.

## Identities (monthly context)

1. Net Assets Purchase − Net Assets Sale + ΔWallet = Income − Expense.
2. Cumulative Wallet + Σ Asset Amount = cumulative Net Worth.
3. Σ Cash-type balances = `[Cash Balance EOM]` (when the measure exists).
4. Active + Financial + Other + Benefits = Income.

## Reconciliation and data quality

- Model reconciliation statuses (OK / CHECK / SIN MAPEO, possibly with emoji) are normalized, then classified as **CONFIRMADO** (no difference), **EXPLICADO POR METODOLOGÍA** (documented definition difference or presentation rounding below 1 currency unit) or **REQUIERE REVISIÓN**.
- Identical records are potential duplicates, never confirmed duplicates without evidence.
- Transport failures are never data-quality findings.

## Validation gate

- **BLOCKED:** core query unavailable after retries, authentication, wrong period, broken identity, model older than the latest export.
- **REQUIRES_REVIEW:** unexplained reconciliation differences, potential duplicates, inconsistent control totals, export integrity issues (documented in the report).
- **PASS_WITH_WARNINGS:** Q0 unavailable via REST, known measure-context differences, results obtained after retry.

## Report structure (gold standard)

Each dashboard page followed by an independent financial commentary page, then: Executive Summary · P&L / Cash Flow · Budget vs Actual · Balance / Net Worth · Financial Analysis · Credit Cards · Liquidity · Net Worth Tracker · Cash Basis vs Net Worth Tracker · Conclusions (Positive, Attention, Opportunities, Outlook) · Sources / Methodology · QA.
