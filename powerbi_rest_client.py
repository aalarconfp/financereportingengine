"""
Power BI REST Client
====================

Cliente de solo lectura para ejecutar DAX contra un semantic model
publicado en Power BI Service mediante la REST API Execute Queries.

Este archivo NO contiene la biblioteca de DAX Q0-Q8.
Las consultas siguen viviendo en dax_queries.md / Claude Skill.

Arquitectura:
    Azure CLI
        -> delegated access token
        -> Power BI REST API
        -> semantic model
        -> DAX

Requisito:
    pip install requests

Prerequisito:
    Azure CLI autenticado (la sesión existente se reutiliza):
        az login --allow-no-subscriptions
    Configuración (entorno o .env junto a este archivo; ver .env.example):
        POWERBI_DATASET_ID (obligatorio), POWERBI_TENANT_ID (opcional)

Prueba:
    python powerbi_rest_client.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Identifiers are NOT hardcoded: they come from the environment or from a local `.env`
# file next to this module (never committed; see `.env.example`).
DEFAULT_DATASET_ID: Optional[str] = None

DEFAULT_TENANT_ID: Optional[str] = None


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables always win."""
    for folder in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        path = os.path.join(folder, ".env")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


_load_dotenv()

DEFAULT_POWER_BI_RESOURCE = "https://analysis.windows.net/powerbi/api"

DEFAULT_AZURE_CLI_WINDOWS = (
    r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
)

DEFAULT_API_BASE = "https://api.powerbi.com/v1.0/myorg"

DEFAULT_TIMEOUT_SECONDS = 120

# HTTP status codes considered transient (retryable by the caller).
TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}

# In-memory token reuse within one run (never written to disk).
TOKEN_REUSE_SECONDS = 20 * 60

# Azure CLI stderr patterns that indicate a network problem, not an expired session.
_AZ_NETWORK_PATTERNS = re.compile(
    r"getaddrinfo|Failed to resolve|NameResolution|Max retries exceeded|"
    r"Connection(Error| reset| aborted| refused)|timed out|Temporary failure|"
    r"network is unreachable|SSLError|ProxyError",
    re.I,
)


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class PowerBIError(Exception):
    """Error base del cliente Power BI."""


class AzureCLIError(PowerBIError):
    """Error obteniendo el token mediante Azure CLI.

    kind: "auth"    -> sesión inválida/expirada: el usuario debe ejecutar `az login`
          "network" -> Azure CLI no alcanzó login.microsoftonline.com (transitorio)
          "missing" -> Azure CLI no instalado / no encontrado
    """

    def __init__(self, message: str, kind: str = "auth") -> None:
        super().__init__(message)
        self.kind = kind


class PowerBITransportError(PowerBIError):
    """Falla de transporte potencialmente transitoria (DNS, timeout, conexión, HTTP 429/5xx).

    No es un error de datos ni de DAX; el llamador puede reintentar.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PowerBIConfigError(PowerBIError, ValueError):
    """Configuración faltante (p. ej. POWERBI_DATASET_ID). No es transitoria."""


class PowerBIAuthenticationError(PowerBIError):
    """Error de autenticación o autorización contra Power BI."""


class PowerBIQueryError(PowerBIError):
    """Error ejecutando una consulta DAX (no transitorio: HTTP 400, DAX inválido, etc.)."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class DAXResult:
    """Resultado estructurado de una consulta DAX."""

    raw: dict[str, Any]
    rows: list[dict[str, Any]]
    columns: list[str]

    def first_row(self) -> dict[str, Any]:
        """Devuelve la primera fila o un diccionario vacío."""
        return self.rows[0] if self.rows else {}

    def scalar(self, column: Optional[str] = None) -> Any:
        """
        Devuelve un único valor de la primera fila.

        Si column no se especifica, utiliza la primera columna.
        """
        row = self.first_row()

        if not row:
            return None

        if column is None:
            return next(iter(row.values()))

        return row.get(column)

    def as_json(self) -> dict[str, Any]:
        """Devuelve la respuesta JSON completa."""
        return self.raw


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

class PowerBIClient:
    """
    Cliente de solo lectura para Power BI Service.

    El token no se guarda en disco.
    Se obtiene mediante la sesión actual de Azure CLI cada vez que se
    ejecuta una consulta.
    """

    def __init__(
        self,
        dataset_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        api_base: Optional[str] = None,
        power_bi_resource: Optional[str] = None,
        azure_cli: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:

        self.dataset_id = (
            dataset_id
            or os.getenv("POWERBI_DATASET_ID")
            or DEFAULT_DATASET_ID
        )

        if not self.dataset_id:
            raise PowerBIConfigError(
                "POWERBI_DATASET_ID no está configurado. Defínelo en el entorno o en "
                "un archivo .env junto a powerbi_rest_client.py (ver .env.example)."
            )

        self.tenant_id = (
            tenant_id
            or os.getenv("POWERBI_TENANT_ID")
            or DEFAULT_TENANT_ID
            or None
        )

        self.api_base = (
            api_base
            or os.getenv("POWERBI_API_BASE")
            or DEFAULT_API_BASE
        ).rstrip("/")

        self.power_bi_resource = (
            power_bi_resource
            or os.getenv("POWERBI_RESOURCE")
            or DEFAULT_POWER_BI_RESOURCE
        )

        self.azure_cli = (
            azure_cli
            or os.getenv("AZURE_CLI_PATH")
            or self._detect_azure_cli()
        )

        self.timeout = timeout

        self.api_url = (
            f"{self.api_base}/datasets/"
            f"{self.dataset_id}/executeQueries"
        )

        # Token kept only in memory for this client instance (not persisted).
        self._token: Optional[str] = None
        self._token_time: float = 0.0

    # -----------------------------------------------------------------------
    # Azure CLI
    # -----------------------------------------------------------------------

    @staticmethod
    def _detect_azure_cli() -> str:
        """Detecta la instalación de Azure CLI en Windows o PATH."""

        if os.path.exists(DEFAULT_AZURE_CLI_WINDOWS):
            return DEFAULT_AZURE_CLI_WINDOWS

        return "az"

    def get_access_token(self) -> str:
        """
        Obtiene un token delegado de Power BI usando la sesión existente de Azure CLI.

        El token solamente vive en memoria durante la ejecución (se reutiliza
        dentro de la misma instancia hasta TOKEN_REUSE_SECONDS). Nunca se guarda.
        """

        if self._token and (time.monotonic() - self._token_time) < TOKEN_REUSE_SECONDS:
            return self._token

        command = [
            self.azure_cli,
            "account",
            "get-access-token",
            "--resource",
            self.power_bi_resource,
        ]

        if self.tenant_id:
            command.extend(["--tenant", self.tenant_id])

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise AzureCLIError(
                "No se encontró Azure CLI.\n\n"
                f"Ruta intentada: {self.azure_cli}\n\n"
                "Verifica que Azure CLI esté instalado o define "
                "AZURE_CLI_PATH.",
                kind="missing",
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()

            if _AZ_NETWORK_PATTERNS.search(stderr):
                raise AzureCLIError(
                    "Azure CLI no pudo contactar el servicio de identidad (red).\n\n"
                    f"Detalle:\n{stderr[:1500]}",
                    kind="network",
                )

            raise AzureCLIError(
                "Azure CLI no pudo obtener el token de Power BI.\n\n"
                f"Detalle:\n{stderr}\n\n"
                "Si la sesión expiró, ejecuta:\n"
                "az login --allow-no-subscriptions"
            )

        try:
            token_data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AzureCLIError(
                "Azure CLI devolvió una respuesta que no es JSON válido."
            ) from exc

        access_token = token_data.get("accessToken")

        if not access_token:
            raise AzureCLIError(
                "Azure CLI respondió correctamente pero no devolvió "
                "accessToken."
            )

        self._token = access_token
        self._token_time = time.monotonic()
        return access_token

    # -----------------------------------------------------------------------
    # Execute Queries
    # -----------------------------------------------------------------------

    def execute_dax(
        self,
        dax: str,
        *,
        include_nulls: bool = True,
        timeout: Optional[int] = None,
    ) -> DAXResult:
        """
        Ejecuta una consulta DAX de solo lectura.

        No crea, modifica, elimina ni renombra medidas, tablas o relaciones.
        """

        if not isinstance(dax, str) or not dax.strip():
            raise ValueError("La consulta DAX no puede estar vacía.")

        token = self.get_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = {
            "queries": [
                {
                    "query": dax.strip(),
                }
            ],
            "serializerSettings": {
                "includeNulls": include_nulls,
            },
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=body,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise PowerBITransportError(
                "No fue posible conectarse con Power BI Service.\n\n"
                f"URL: {self.api_url}\n"
                f"Detalle: {exc}"
            ) from exc

        # ---------------------------------------------------------------
        # Autenticación / autorización
        # ---------------------------------------------------------------

        if response.status_code in (401, 403):
            raise PowerBIAuthenticationError(
                "Power BI rechazó la autenticación o autorización.\n\n"
                f"HTTP {response.status_code}\n"
                f"Detalle: {self._safe_response_text(response)}\n\n"
                "Verifica que Azure CLI esté autenticado con el usuario "
                "correcto y que el usuario tenga acceso al semantic model."
            )

        # ---------------------------------------------------------------
        # Otros errores HTTP
        # ---------------------------------------------------------------

        if response.status_code in TRANSIENT_HTTP_STATUS:
            raise PowerBITransportError(
                "Power BI devolvió un error HTTP transitorio.\n\n"
                f"HTTP {response.status_code}\n"
                f"Detalle: {self._safe_response_text(response)[:1500]}",
                status_code=response.status_code,
            )

        if not response.ok:
            raise PowerBIQueryError(
                "Power BI devolvió un error HTTP.\n\n"
                f"HTTP {response.status_code}\n"
                f"Detalle: {self._safe_response_text(response)}",
                status_code=response.status_code,
            )

        # ---------------------------------------------------------------
        # JSON
        # ---------------------------------------------------------------

        try:
            payload = response.json()
        except ValueError as exc:
            raise PowerBIQueryError(
                "Power BI devolvió una respuesta que no es JSON válido.\n\n"
                f"Respuesta:\n{response.text[:4000]}"
            ) from exc

        # ---------------------------------------------------------------
        # Errores devueltos dentro de la respuesta
        # ---------------------------------------------------------------

        if "error" in payload:
            raise PowerBIQueryError(
                "Power BI devolvió un error de consulta.\n\n"
                + json.dumps(
                    payload["error"],
                    indent=2,
                    ensure_ascii=False,
                )
            )

        results = payload.get("results")

        if not results:
            raise PowerBIQueryError(
                "Power BI respondió sin 'results'.\n\n"
                + json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
            )

        first_result = results[0]

        if "error" in first_result:
            raise PowerBIQueryError(
                "Power BI devolvió un error dentro de results.\n\n"
                + json.dumps(
                    first_result["error"],
                    indent=2,
                    ensure_ascii=False,
                )
            )

        tables = first_result.get("tables", [])

        if not tables:
            return DAXResult(
                raw=payload,
                rows=[],
                columns=[],
            )

        first_table = tables[0]

        if "error" in first_table:
            raise PowerBIQueryError(
                "Power BI devolvió un error en la tabla.\n\n"
                + json.dumps(
                    first_table["error"],
                    indent=2,
                    ensure_ascii=False,
                )
            )

        rows = first_table.get("rows", [])

        columns = list(rows[0].keys()) if rows else []

        return DAXResult(
            raw=payload,
            rows=rows,
            columns=columns,
        )

    def get_last_refresh(self, timeout: Optional[int] = 30) -> Optional[dict[str, Any]]:
        """
        Lectura (GET) del último refresh del dataset: {status, startTime, endTime, refreshType}.

        Solo lectura; no ejecuta DAX ni dispara refresh. Devuelve None si el
        historial no está disponible (p. ej. dataset publicado sin refresh en el Service).
        """
        token = self.get_access_token()
        url = f"{self.api_base}/datasets/{self.dataset_id}/refreshes?$top=1"
        try:
            response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        except requests.RequestException as exc:
            raise PowerBITransportError(f"No fue posible leer el historial de refresh: {exc}") from exc
        if not response.ok:
            return None
        values = (response.json() or {}).get("value") or []
        if not values:
            return None
        v = values[0]
        return {k: v.get(k) for k in ("status", "startTime", "endTime", "refreshType")}

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def execute_dax_rows(
        self,
        dax: str,
        *,
        include_nulls: bool = True,
        timeout: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Ejecuta DAX y devuelve directamente las filas."""
        result = self.execute_dax(
            dax,
            include_nulls=include_nulls,
            timeout=timeout,
        )
        return result.rows

    def execute_dax_scalar(
        self,
        dax: str,
        column: Optional[str] = None,
        *,
        include_nulls: bool = True,
        timeout: Optional[int] = None,
    ) -> Any:
        """Ejecuta DAX y devuelve un único valor."""
        result = self.execute_dax(
            dax,
            include_nulls=include_nulls,
            timeout=timeout,
        )
        return result.scalar(column)

    @staticmethod
    def _safe_response_text(response: requests.Response) -> str:
        """Extrae texto de error de forma segura."""
        try:
            data = response.json()
            return json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )
        except ValueError:
            return response.text[:4000]


# ---------------------------------------------------------------------------
# Función de conveniencia
# ---------------------------------------------------------------------------

def execute_dax(
    dax: str,
    dataset_id: Optional[str] = None,
) -> DAXResult:
    """
    Ejecuta DAX sin instanciar manualmente PowerBIClient.

    Ejemplo:
        result = execute_dax('EVALUATE ROW("Test", 1)')
        print(result.first_row())
    """
    client = PowerBIClient(dataset_id=dataset_id)
    return client.execute_dax(dax)


# ---------------------------------------------------------------------------
# Prueba directa
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Power BI REST Client - prueba de conexión")
    print("=" * 50)
    print()

    client = PowerBIClient()

    print(f"Dataset ID: {client.dataset_id}")
    print(f"Tenant ID:  {client.tenant_id}")
    print(f"API URL:    {client.api_url}")
    print()

    test_dax = """
EVALUATE
ROW(
    "Test", 1
)
"""

    try:
        result = client.execute_dax(test_dax)

        print("Conexión OK.")
        print()
        print("Resultado:")
        print(
            json.dumps(
                result.raw,
                indent=2,
                ensure_ascii=False,
            )
        )

    except PowerBIError as exc:
        print("ERROR:")
        print(str(exc))
        raise SystemExit(1)
