# MoneyPro Control Totals: estado de integración

**Estado:** la funcionalidad de la skill `moneypro-control-totals` está integrada y probada en `pipeline/moneypro_control.py`. La skill externa **no se ha retirado**; su retiro queda sujeto a la decisión expresa del usuario (la regresión del gold standard ya pasó).

| Funcionalidad de la skill (Pasos 0–9) | Implementación | Prueba |
|---|---|---|
| Extraer las capturas del Word (Paso 0) | `moneypro_control.py extract-media` (orden del documento) | manual |
| Identificar las 4 donas por título; Expenses → Expense (Paso 1) | `donut_rows()`; la lectura visual sigue siendo de Claude (`donuts_<YYYY_MM>.json`) | m1 |
| Assets: "Other Assets" = subtotal; otras líneas = Type Assets (Paso 2) | `donut_rows()` + validación del subtotal | m1 |
| End Period único por Word, igual al corte (Paso 3) | `check_donuts()`: se hereda si una dona no lo muestra; fechas distintas o vista mensual ⇒ BLOCKED | f6, m3 |
| Valores acumulados al cierre | Regla documentada; nunca se comparan con cifras mensuales | m1, m3 |
| Actualizar valores (Paso 4.2) y End Period de todas las filas (Paso 5) | `update()` | m1 |
| Categorías nuevas en el bloque de su Type con el estilo vecino (Paso 4.3) | `update()` | m1 |
| Categorías no vistas: conservar, nunca borrar ni poner en cero (Paso 4.4) | Se conservan ⇒ HUMAN_CONFIRMATION_REQUIRED | m2 |
| Variaciones > 20 % o cambio de signo (Paso 4.5) | `variation_alerts` | — |
| Reajustar SUMIFS y referencias de NET PROFIT (Paso 6) | `update()` | m1 |
| Cruzar con las donas (Paso 7) | Validación independiente del archivo escrito | m1 |
| Formato (Paso 8) | Copia fuente, borde, relleno y formato; End Period como texto DD/MM/AAAA | m1 (única diferencia: formato de la fecha de inicio, mismo valor) |
| Nunca sobrescribir el original | `update` exige un archivo de salida distinto | m4 |
| Reporte final (Paso 9) | `render_update()` | — |
| Reconciliación con el modelo | `validate.py` (Q8c/Q8d) | v1 |

**Prueba de equivalencia (m1):** el libro del cierre anterior más las donas del cierre del gold standard producen un archivo **idéntico celda por celda** al libro oficial de ese cierre (valores, categorías, orden, End Period y fórmulas). Los fixtures de esta prueba son privados y no se versionan.
