"""Conectores de archivos JSON y CSV.

Por seguridad solo leen archivos dentro de la carpeta `data/feeds` (evita que
una configuración maliciosa lea archivos arbitrarios del servidor).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from pydantic import ValidationError

from ..config import settings
from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError, TemporaryError

FIELD_ALIASES = {"id": "resource_key", "clave": "resource_key", "nombre": "name", "tipo": "resource_type",
                 "region": "region", "inicio": "starts_at", "capacidad": "capacity", "disponibles": "available",
                 "estado": "status", "cambiado": "changed_at"}


def feeds_dir() -> Path:
    d = settings.data_dir / "feeds"
    d.mkdir(parents=True, exist_ok=True)
    return d


class _FileConnector(Connector):
    def validate_config(self) -> None:
        p = self.config.get("path")
        if not p:
            raise ConnectorError("Falta 'path'")
        base = Path(self.config.get("_base_dir") or feeds_dir()).resolve()
        full = (base / p).resolve()
        if base != full and base not in full.parents:
            raise ConnectorError("La ruta debe estar dentro de data/feeds")
        self.path = full

    def _read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError as e:
            raise TemporaryError(f"archivo no encontrado: {self.path.name}") from e
        except OSError as e:
            raise TemporaryError(f"no se pudo leer: {e}") from e

    @staticmethod
    def _normalize(row: dict) -> Observation:
        data = {}
        for k, v in row.items():
            if k is None:
                continue
            key = FIELD_ALIASES.get(k.strip().lower(), k.strip())
            if v == "" or v is None:
                continue
            data[key] = v
        if "status" in data:
            data["status"] = str(data["status"]).upper()
        try:
            return Observation(**data)
        except ValidationError as e:
            raise ConnectorError(f"fila inválida {row}: {e.errors()[0]['msg']}") from e


class JsonFileConnector(_FileConnector):
    """Espera {"items": [...], "complete": true} o directamente una lista."""
    type_name = "json"

    def fetch(self) -> Snapshot:
        try:
            data = json.loads(self._read())
        except json.JSONDecodeError as e:
            # Un archivo a medio escribir produce JSON inválido: es transitorio.
            raise TemporaryError(f"JSON inválido: {e}") from e
        items = data.get("items", []) if isinstance(data, dict) else data
        complete = data.get("complete", True) if isinstance(data, dict) else True
        return Snapshot(observations=[self._normalize(r) for r in items], complete=bool(complete))


class CsvFileConnector(_FileConnector):
    type_name = "csv"

    def fetch(self) -> Snapshot:
        text = self._read()
        delim = self.config.get("delimiter", ",")
        rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
        return Snapshot(observations=[self._normalize(r) for r in rows], complete=True)
