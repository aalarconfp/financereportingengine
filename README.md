# Personal Finance Monthly Reporting Engine

Motor controlado para producir cada mes un reporte financiero personal (Cash Basis, costo histórico) a partir de MoneyPro y un modelo Power BI. Primero valida los datos con reglas deterministas y después genera el PDF. La publicación en Notion requiere una autorización humana.

> Este repositorio contiene **solo código y documentación**. Los datos financieros personales viven fuera del repositorio (`FINANCE_DATA_ROOT`) y están excluidos por `.gitignore`.

## Problema que resuelve

Cerrar el mes "a mano" mezcla capturas, exports, un dashboard y un tracker de patrimonio, y es fácil publicar cifras que no cuadran. El engine:

- verifica que todos los insumos del cierre existan y sean consistentes (*Data Readiness*);
- obtiene las cifras del modelo **solo por consultas DAX de lectura** (Power BI REST);
- valida identidades contables, un runway de liquidez calculado de forma independiente y la reconciliación contra los totales de control de MoneyPro;
- genera el PDF solo cuando los gates lo permiten y ejecuta un QA;
- exige *Human Release* antes de escribir en Notion.

## Arquitectura (resumen)

```
MoneyPro ─► Power BI (Service) ─► REST executeQueries ─► DAX Q0–Q8 (dax_queries.md)
                                                             │
Data Readiness ─► MoneyPro Control ─► Validation ─► Regression (gold standard)
                                                             │
                                  Report Engine (PDF) ─► QA ─► Human Release ─► Notion
```

Detalle en [`docs/architecture.md`](docs/architecture.md) y metodología en [`docs/methodology.md`](docs/methodology.md).

## Tecnología

Python ≥ 3.10 (probado con 3.13) · `requests` · `openpyxl` · `pypdf` · `reportlab` · `matplotlib` · Azure CLI (sesión delegada para Power BI REST). Power BI Desktop solo como fallback opcional. Ver [`requirements.txt`](requirements.txt).

## Estructura

```
├── CLAUDE.md                 reglas para Claude Code
├── README.md
├── requirements.txt · .env.example · .gitignore · .gitattributes
├── dax_queries.md            ÚNICA fuente de DAX (Q0–Q8, solo lectura)
├── powerbi_rest_client.py    cliente REST de solo lectura
├── engine.py · rlib.py · charts.py   presentación (report_data → PDF)
├── pipeline/
│   ├── readiness.py  moneypro_control.py  dax_bridge.py
│   ├── validate.py   regression.py        pipeline.py   common.py
│   └── tests/run_tests.py
├── scripts/repo_safety_check.py
├── docs/                     arquitectura, metodología, operación, seguridad, workflow, roadmap
└── examples/                 ejemplos sintéticos (sin datos reales)
```

Carpetas locales no versionadas: `regression/` (baseline y resultados privados), `work/<YYYY_MM>/` (artefactos del mes), `report_data_*.json`, `.env`.

## Workflow mensual

1. Export de MoneyPro y refresh del modelo.
2. Data Readiness → MoneyPro Control → DAX vía REST → Validation.
3. Regression (una vez, con el gold standard) → Report Engine → QA.
4. Human Release → Notion.

Comandos, prerequisitos y recuperación de errores en [`docs/operations.md`](docs/operations.md).

## Testing

```
python pipeline/tests/run_tests.py
```

La suite no usa red, Power BI ni Notion. Con los fixtures privados presentes ejecuta 23 tests. En un clon limpio, los tests que dependen de datos privados se reportan como **SKIPPED** (nunca como PASS) y la suite no sirve para certificar el pipeline.

Antes de cada commit:

```
python scripts/repo_safety_check.py
```

## Seguridad

Sin credenciales en el código: los identificadores de Power BI van en `.env` (no versionado) y la autenticación usa la sesión existente de Azure CLI. Nunca se versionan CSV, Excel, PDF, Word, capturas ni JSON de resultados. Ver [`docs/security.md`](docs/security.md).

## Estado actual

- Transporte REST con retry/backoff y clasificación de errores: operativo.
- Data Readiness con human-in-the-loop, control MoneyPro integrado, validación con gates: operativos.
- Regresión del gold standard: PASS con datos reales (verificar con `pipeline.py certify` en el entorno local).
- Flujo mensual real: se habilita solo cuando `pipeline.py certify` declara `MONTHLY PIPELINE READY`.

## Roadmap

- CI (GitHub Actions) solo con tests que no usan datos privados, lint y escaneo de secretos.
- Evolución opcional hacia una arquitectura agentic con el motor determinista como núcleo: [`docs/future-agent-architecture.md`](docs/future-agent-architecture.md).
