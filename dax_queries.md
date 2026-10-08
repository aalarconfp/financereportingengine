# DAX de validación — solo lectura (EVALUATE)

Placeholders: `{Y}` año, `{M}` mes (1–12), `{PY}`/`{PM}` año/mes anterior, `{EOM}` = DATE(Y,M,último día), `{PEOM}` = fin del mes anterior, `{SOY}` = DATE(Y,1,1).
Transporte principal: **Power BI REST** (`pipeline/dax_bridge.py run`, Azure CLI, Power BI Service; Power BI Desktop NO es requisito). Fallback opcional: Power BI Desktop local → `connection_operations ListLocalInstances` → `Connect` (ventana "Dashboard Personal Finance") → `dax_query_operations Execute` (`dax_bridge.py plan` + `ingest`). Mismo texto DAX en ambos caminos. **Nunca** `measure_operations` Create/Update/Delete/Rename/Move, ni `table_operations`/`relationship_operations` de escritura.

## Q0 — Inventario de medidas (detectar cambios de fórmula/descripción)
```
EVALUATE SELECTCOLUMNS(INFO.MEASURES(), "Name",[Name], "Expr",[Expression], "Folder",[DisplayFolder], "Desc",[Description], "Mod",[ModifiedTime])
```
Comparar contra el mes anterior: si cambió una medida usada en el reporte (Liquid Cash Runway, Financial Income, Average Monthly Expense, Net Worth, Cash, Asset Amount…), documentarlo como nota técnica.

## Q1 — KPIs mes actual, mes anterior y mes −2
```
EVALUATE ADDCOLUMNS(FILTER(SUMMARIZE(Calendario, Calendario[Year], Calendario[Month]),
  (Calendario[Year]={Y} && Calendario[Month]={M}) || (Calendario[Year]={PY} && Calendario[Month]={PM})),
 "Income",[Income],"Expense",[Expense],"NetExpense",[Net Expense],"Budget",[Budget],"NetBudget",[Net Budget],"BudgetPct",[Budget %],
 "Target",[Target],"IncTgtPct",[Income Target %],"NetIncome",[Net Income],"NAP",[Net Assets Purchase],"NAS",[Net Assets Sale],
 "CashFlow",[Cash],"NW",[Net Worth],"SavBud",[Savings Budget],"NWTgt",[Net Worth Target %],"SR",[Savings Rate],"EIR",[Expense to Income Ratio],
 "AME",[Average Monthly Expense],"CashEOM",[Cash Balance EOM],"LCR",[Liquid Cash Runway],"Active",[Active Income],"Fin",[Financial Income],
 "Other",[Other Income],"Benef",[Benefits Income],"IncCC",[Income Credit Cards],"ExpCC",[Expenses Credit Cards],"ProfCC",[Profit Credit Cards],
 "PctCC",[% Profit Credit Cards],"Largest",[Largest Asset %],"Top3",[Top 3 Assets %])
```
(Si [Cash Balance EOM] ya no existe, quitar esa columna y anotarlo.)

## Q2 — YTD, anuales y tarjetas por año
```
EVALUATE VAR _ytd = FILTER(ALL(Calendario[Date]), Calendario[Date]>={SOY} && Calendario[Date]<={EOM}) RETURN ROW(
 "IncYTD",CALCULATE([Income],_ytd),"TgtYTD",CALCULATE([Target],_ytd),"TgtYear",CALCULATE([Target],Calendario[Year]={Y}),
 "NetExpYTD",CALCULATE([Net Expense],_ytd),"NetBudYTD",CALCULATE([Net Budget],_ytd),"NetBudYear",CALCULATE([Net Budget],Calendario[Year]={Y}),
 "ExpYTD",CALCULATE([Expense],_ytd),"ActYTD",CALCULATE([Active Income],_ytd),"FinYTD",CALCULATE([Financial Income],_ytd),
 "OthYTD",CALCULATE([Other Income],_ytd),"BenYTD",CALCULATE([Benefits Income],_ytd),
 "CGYTD",CALCULATE([Total Finanzas],_ytd,'Money Pro Transactions'[Subcategory]="Capital Gain"),
 "IntYTD",CALCULATE([Total Finanzas],_ytd,'Money Pro Transactions'[Subcategory]="Interest Income"),
 "DivYTD",CALCULATE([Total Finanzas],_ytd,'Money Pro Transactions'[Subcategory]="Dividends"),
 "CashbackYTD",CALCULATE([Total Finanzas],_ytd,'Money Pro Transactions'[Subcategory]="Credit Card Cashback"),
 "ActTgtYTD",CALCULATE([Target],_ytd,Budget[Subcategory] IN {"Wages & Tips","Payroll / Cesantias"}),
 "NWYTD",CALCULATE([Net Worth],_ytd),"SavBudYTD",CALCULATE([Savings Budget],_ytd),"SR_YTD",CALCULATE([Savings Rate],_ytd),
 "IncCC_Y",CALCULATE([Income Credit Cards],Calendario[Year]={Y}),"ExpCC_Y",CALCULATE([Expenses Credit Cards],Calendario[Year]={Y}),
 "IncCC_PY",CALCULATE([Income Credit Cards],Calendario[Year]={Y}-1),"ExpCC_PY",CALCULATE([Expenses Credit Cards],Calendario[Year]={Y}-1),
 "OtherObligYTD",CALCULATE([Total Finanzas],_ytd,'Money Pro Transactions'[Subcategory]="Other Obligations"),
 "NWAcumPrevYearEnd",CALCULATE([Net Worth],Calendario[Date]<=DATE({Y}-1,12,31)),
 "NWAcum",CALCULATE([Net Worth],Calendario[Date]<={EOM}),"NWAcumPrev",CALCULATE([Net Worth],Calendario[Date]<={PEOM}),
 "Wallet",CALCULATE([Cash],Calendario[Date]<={EOM}),"WalletPrev",CALCULATE([Cash],Calendario[Date]<={PEOM}))
```

## Q3 — Cash-type balances (cálculo independiente del runway)
Ejecutar dos veces: `{D}` = `{EOM}` y `{D}` = `{PEOM}`. Incluye Wallet.
```
EVALUATE ADDCOLUMNS(FILTER(ALL('Assets Accounts'), 'Assets Accounts'[Asset Type]="Cash"),
 "Bal", VAR d={D} VAR a='Assets Accounts'[Asset Accounts] RETURN
   CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account (to)]=a,'Money Pro Transactions'[Type] IN {"Asset Purchase","Money Transfer"},'Money Pro Transactions'[Date]<=d)+0
 - CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account (to)]=a,'Money Pro Transactions'[Type]="Asset Sale",'Money Pro Transactions'[Date]<=d)+0
 + CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account]=a,'Money Pro Transactions'[Type] IN {"Income","Asset Sale"},'Money Pro Transactions'[Date]<=d)+0
 - CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account]=a,'Money Pro Transactions'[Type] IN {"Expense","Asset Purchase","Money Transfer"},'Money Pro Transactions'[Date]<=d)+0)
```
Σ Bal = **Cash-type total**. Debe igualar [Cash Balance EOM] (si la medida existe) y la fila Wallet debe igualar [Cash] acumulado al corte.

## Q4 — Average Monthly Expense independiente
```
EVALUATE VAR _exp = SELECTCOLUMNS(FILTER(ALL('Money Pro Transactions'),'Money Pro Transactions'[Type]="Expense"),
  "D",'Money Pro Transactions'[Date],"A",'Money Pro Transactions'[Amount],"S",'Money Pro Transactions'[Subcategory],
  "YM",YEAR('Money Pro Transactions'[Date])*100+MONTH('Money Pro Transactions'[Date]))
RETURN ROW(
 "ExpAll",SUMX(FILTER(_exp,[D]<={EOM}),[A]),"MonthsAll",COUNTROWS(DISTINCT(SELECTCOLUMNS(FILTER(_exp,[D]<={EOM}),"ym",[YM]))),
 "ExpAllPrev",SUMX(FILTER(_exp,[D]<={PEOM}),[A]),"MonthsAllPrev",COUNTROWS(DISTINCT(SELECTCOLUMNS(FILTER(_exp,[D]<={PEOM}),"ym",[YM]))),
 "ExpYTD",SUMX(FILTER(_exp,[D]>={SOY}&&[D]<={EOM}),[A]),"MonthsYTD",{M},
 "AME_meas_all",CALCULATE([Average Monthly Expense],ALL(Calendario)),"AME_meas_Y",CALCULATE([Average Monthly Expense],Calendario[Year]={Y}),
 "LCR_meas_Y",CALCULATE([Liquid Cash Runway],Calendario[Year]={Y}),"LCR_meas_all",CALCULATE([Liquid Cash Runway],ALL(Calendario)))
```
**AME oficial = ExpAll / MonthsAll** (meses con gasto real hasta el corte). Runway validado = Cash-type / AME oficial. Sensibilidades: AME YTD (÷ meses transcurridos) y AME YTD sin eventos extraordinarios identificados.

## Q5 — Asset Amount por activo a una fecha
Ejecutar con `{D}` = `{EOM}` y `{D}` = `{PEOM}`.
```
EVALUATE ADDCOLUMNS(FILTER(ALL('Assets Accounts'),'Assets Accounts'[Asset Accounts]<>"Wallet"),
 "AmtModel",CALCULATE([Asset Amount]),
 "Bal", VAR a='Assets Accounts'[Asset Accounts] VAR d={D} RETURN
   CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account (to)]=a,'Money Pro Transactions'[Type] IN {"Asset Purchase","Money Transfer"},'Money Pro Transactions'[Date]<=d)+0
 - CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account (to)]=a,'Money Pro Transactions'[Type]="Asset Sale",'Money Pro Transactions'[Date]<=d)+0
 - CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Account]=a,'Money Pro Transactions'[Type]="Money Transfer",'Money Pro Transactions'[Date]<=d)+0)
```
[Asset Amount] usa REMOVEFILTERS y devuelve el saldo MÁS RECIENTE del modelo; si existen transacciones posteriores al corte, el saldo del corte es "Bal". Largest/Top 3 al cierre anterior se recalculan con "Bal" de {PEOM}.

## Q6 — Transacciones del mes (todas menos Expense) y gastos del mes
```
EVALUATE CALCULATETABLE(SELECTCOLUMNS(FILTER('Money Pro Transactions','Money Pro Transactions'[Type]<>"Expense"),
 "Date",[Date],"Type",[Type],"Cat",[Category],"Sub",[Subcategory],"Agent",[Agent],"Acc",[Account],"To",[Account (to)],"Amt",[Amount]),
 Calendario[Year]={Y},Calendario[Month]={M}) ORDER BY [Type],[Date]

EVALUATE CALCULATETABLE(SELECTCOLUMNS(FILTER('Money Pro Transactions','Money Pro Transactions'[Type]="Expense"),
 "Date",[Date],"Cat",[Category],"Sub",[Subcategory],"Agent",[Agent],"Amt",[Amount]),
 Calendario[Year]={Y},Calendario[Month]={M}) ORDER BY [Cat],[Sub],[Date]
```
Usar para: composición del ingreso, Capital Gain (fecha + reinversión), movimientos patrimoniales, puente de caja, drivers de gasto, intereses de tarjeta (Other Obligations), transferencias hacia/desde brokers.

## Q7 — Budget vs Actual por subcategoría (mes y mes anterior)
```
EVALUATE VAR _subs = DISTINCT(UNION(
  SELECTCOLUMNS(FILTER(ALL(Budget),YEAR(Budget[Date])={Y}&&MONTH(Budget[Date]) IN {{PM},{M}}),"S",Budget[Subcategory]),
  SELECTCOLUMNS(FILTER(ALL('Money Pro Transactions'),'Money Pro Transactions'[Type]="Expense"&&'Money Pro Transactions'[Date]>DATE({PY},{PM},1)-1&&'Money Pro Transactions'[Date]<={EOM}),"S",'Money Pro Transactions'[Subcategory])))
VAR _t = ADDCOLUMNS(_subs,
 "Cat",VAR s=[S] RETURN CALCULATE(MAX('Money Pro Transactions'[Category]),REMOVEFILTERS(),'Money Pro Transactions'[Subcategory]=s),
 "Exp",VAR s=[S] RETURN CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Type]="Expense",'Money Pro Transactions'[Subcategory]=s,'Money Pro Transactions'[Date]>=DATE({Y},{M},1),'Money Pro Transactions'[Date]<={EOM})+0,
 "ExpPrev",VAR s=[S] RETURN CALCULATE(SUM('Money Pro Transactions'[Amount]),REMOVEFILTERS(),'Money Pro Transactions'[Type]="Expense",'Money Pro Transactions'[Subcategory]=s,'Money Pro Transactions'[Date]>=DATE({PY},{PM},1),'Money Pro Transactions'[Date]<={PEOM})+0,
 "Bud",VAR s=[S] RETURN CALCULATE(SUM(Budget[Amount]),REMOVEFILTERS(),Budget[Type]="Budget",Budget[Subcategory]=s,YEAR(Budget[Date])={Y},MONTH(Budget[Date])={M})+0,
 "Tgt",VAR s=[S] RETURN CALCULATE(SUM(Budget[Amount]),REMOVEFILTERS(),Budget[Type]="Target",Budget[Subcategory]=s,YEAR(Budget[Date])={Y},MONTH(Budget[Date])={M})+0)
RETURN FILTER(_t,[Exp]<>0||[ExpPrev]<>0||[Bud]<>0||[Tgt]<>0) ORDER BY [Cat]
```

## Q8 — Data Quality
```
-- Gastos/ingresos sin subcategoría en el año
EVALUATE SELECTCOLUMNS(FILTER(ALL('Money Pro Transactions'),(ISBLANK('Money Pro Transactions'[Subcategory])||TRIM('Money Pro Transactions'[Subcategory])="")
  && 'Money Pro Transactions'[Type] IN {"Expense","Income"} && 'Money Pro Transactions'[Date]>={SOY}),
 "Date",[Date],"Type",[Type],"Cat",[Category],"Agent",[Agent],"Amt",[Amount])

-- Registros idénticos (misma fecha, tipo, subcategoría, agente y monto) en el mes
EVALUATE FILTER(SUMMARIZECOLUMNS('Money Pro Transactions'[Date],'Money Pro Transactions'[Type],'Money Pro Transactions'[Subcategory],
  'Money Pro Transactions'[Agent],'Money Pro Transactions'[Amount],
  FILTER(ALL(Calendario),Calendario[Year]={Y}&&Calendario[Month]={M}),"N",COUNTROWS('Money Pro Transactions')),[N]>1)

-- Reconciliación contra totales de control Money Pro
EVALUATE ROW("Overall",[Reconciliation Overall Status])
EVALUATE ADDCOLUMNS(SUMMARIZE('PLCategoryTotals','PLCategoryTotals'[Type],'PLCategoryTotals'[Category]),
 "PBI",[Power BI Amount],"MP",[MoneyPro Control Amount],"Diff",[Reconciliation Difference],"St",[Reconciliation Status])
```
Clasificar cada hallazgo como **CONFIRMADO**, **REQUIERE REVISIÓN** o **EXPLICADO POR METODOLOGÍA**. Un registro idéntico NO es duplicado confirmado (puede ser automático/mensual).

## Identidades a verificar (contexto mensual)
1. NAP − NAS + ΔWallet = Income − Expense (= [Net Worth] del mes).
2. Wallet acumulado + Σ Asset Amount (Curr) = Net Worth acumulado al corte (diferencia $0).
3. Σ Cash-type (Q3) = [Cash Balance EOM] (si existe).
4. Active + Financial + Other + Benefits Income = Income.
5. Net Expense (pág. 1) = Net Expense con slicer (pág. 5) + categorías desmarcadas en ese slicer.
Si una identidad no cuadra: investigar contexto, documentar y marcar **Pending validation**. No corregir.
