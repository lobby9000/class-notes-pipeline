"""Configuración, rutas, modelos, precios y registro de costos que comparten las extensiones."""

from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent  # carpeta del proyecto, donde vive apuntes.py


class ErrorExtension(Exception):
    """Error con un mensaje pensado para mostrarse tal cual al usuario."""


# ---------------------------------------------------------------- modelos y precios

# Alias que se escriben en config.toml -> id de la API. Se pueden cambiar en [api.ids].
IDS_BASE = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}

# USD por millón de tokens, de platform.claude.com/docs/en/about-claude/pricing (octubre 2026).
# Se pueden cambiar en [api.precios]. Haiku 5.5 cobra cinco veces más cuando la entrada de una
# petición (incluida la caché) pasa de 100,000 tokens: esos precios van en "largo".
PRECIOS_BASE = {
    "claude-opus-5-5": {"entrada": 4.00, "salida": 20.00, "cache_escritura": 5.00, "cache_lectura": 0.20},
    "claude-sonnet-5-5": {"entrada": 2.00, "salida": 10.00, "cache_escritura": 2.50, "cache_lectura": 0.10},
    "claude-haiku-5-5": {"entrada": 0.10, "salida": 0.50, "cache_escritura": 0.125, "cache_lectura": 0.01,
                         "umbral_largo": 100_000,
                         "largo": {"entrada": 0.50, "salida": 2.50, "cache_escritura": 0.625, "cache_lectura": 0.05}},
    "claude-haiku-4-5": {"entrada": 1.00, "salida": 5.00, "cache_escritura": 1.25, "cache_lectura": 0.10},
}

# Máximo de tokens de salida que acepta cada modelo
SALIDA_MAXIMA = {"claude-opus-5-5": 128_000, "claude-sonnet-5-5": 128_000, "claude-haiku-5-5": 128_000,
                 "claude-haiku-4-5": 64_000}

# Qué modelo usa cada tarea si config.toml no dice otra cosa. Las clases usan [claude] modelo.
MODELO_POR_TAREA = {"clase": "opus", "reunion": "sonnet", "pregunta": "haiku"}


def modelo_para(cfg: dict, tarea: str) -> str:
    """Alias o id del modelo para una tarea: [claude.modelos] manda; si no, los valores de fábrica."""
    claude = cfg.get("claude", {}) or {}
    propios = claude.get("modelos", {}) or {}
    if propios.get(tarea):
        return str(propios[tarea]).strip()
    if tarea == "clase" and claude.get("modelo"):
        return str(claude["modelo"]).strip()
    return MODELO_POR_TAREA.get(tarea, MODELO_POR_TAREA["clase"])


def id_modelo(cfg: dict, nombre: str) -> str:
    """Convierte un alias (opus, sonnet, haiku) en el id de la API; un id completo pasa tal cual."""
    ids = {**IDS_BASE, **{k.lower(): v for k, v in (cfg.get("api", {}).get("ids", {}) or {}).items()}}
    nombre = str(nombre).strip()
    return ids.get(nombre.lower(), nombre)


def precio(cfg: dict, modelo: str) -> dict | None:
    propios = cfg.get("api", {}).get("precios", {}) or {}
    for clave in (modelo, *[base for base in PRECIOS_BASE if modelo.startswith(base)]):
        if clave in propios or clave in PRECIOS_BASE:
            return {**PRECIOS_BASE.get(clave, {}), **(propios.get(clave) or {})}
    return None


def costo_usd(cfg: dict, modelo: str, entrada: int, salida: int,
              cache_escritura: int = 0, cache_lectura: int = 0) -> float | None:
    """Costo de una llamada. input_tokens de la API no incluye los tokens de caché: se suman aparte."""
    p = precio(cfg, modelo)
    if not p or "entrada" not in p or "salida" not in p:
        return None
    if p.get("umbral_largo") and entrada + cache_escritura + cache_lectura > p["umbral_largo"]:
        p = {**p, **(p.get("largo") or {})}
    return (entrada * p["entrada"] + salida * p["salida"]
            + cache_escritura * p.get("cache_escritura", p["entrada"] * 1.25)
            + cache_lectura * p.get("cache_lectura", p["entrada"] * 0.1)) / 1_000_000


# ---------------------------------------------------------------- carpetas

def ruta(texto: str | Path) -> Path:
    return Path(os.path.expanduser(str(texto)))


def carpeta_trabajo(cfg: dict) -> Path:
    return ruta(cfg["carpetas"]["trabajo"])


def carpeta_vault(cfg: dict) -> Path:
    texto = str(cfg["carpetas"].get("vault", "")).strip()
    if not texto:
        raise ErrorExtension("Falta la ruta de tu vault de Obsidian: edita [carpetas] vault en config.toml")
    vault = ruta(texto)
    if not vault.is_dir():
        raise ErrorExtension(f"No encuentro tu vault de Obsidian en {vault}")
    return vault


def carpetas_de_notas(cfg: dict) -> list[Path]:
    """Las carpetas del vault con notas de clase y notas sueltas (las de conceptos no: salen de las notas)."""
    vault = carpeta_vault(cfg)
    carpetas = []
    for clave in ("notas", "sueltas"):
        nombre = str(cfg["carpetas"].get(clave, "")).strip()
        if nombre and (vault / nombre).is_dir():
            carpetas.append(vault / nombre)
    return carpetas


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres y buscar palabras."""
    sin_acentos = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_acentos if not unicodedata.combining(c)).lower()


def duracion_legible(segundos: float) -> str:
    segundos = max(0, int(round(segundos)))
    if segundos < 60:
        return f"{segundos} s"
    if segundos < 3600:
        return f"{segundos // 60} min {segundos % 60:02d} s"
    return f"{segundos // 3600} h {segundos % 3600 // 60:02d} min"


# ---------------------------------------------------------------- registro de costos

def archivo_costos(cfg: dict) -> Path:
    return carpeta_trabajo(cfg) / "costos.jsonl"


def registrar(cfg: dict, registro: dict) -> None:
    """Una línea JSON por llamada a la API, en la carpeta de trabajo (no en el vault)."""
    destino = archivo_costos(cfg)
    destino.parent.mkdir(parents=True, exist_ok=True)
    registro = {"fecha": dt.datetime.now().isoformat(timespec="seconds"), **registro}
    with open(destino, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def leer_registros(cfg: dict) -> list[dict]:
    destino = archivo_costos(cfg)
    if not destino.exists():
        return []
    registros = []
    for linea in destino.read_text(encoding="utf-8").splitlines():
        try:
            registros.append(json.loads(linea))
        except json.JSONDecodeError:
            continue  # una línea a medias (por ejemplo, si se apagó la laptop) no tumba la cuenta
    return registros


def proporcion_salida(registros: list[dict], tarea: str, predeterminada: float) -> tuple[float, int]:
    """Mediana de tokens de salida por token de entrada en notas anteriores de esa tarea."""
    valores = [r["salida"] / r["entrada"] for r in registros
               if r.get("tarea") == tarea and r.get("entrada") and r.get("salida")]
    return (statistics.median(valores), len(valores)) if valores else (predeterminada, 0)
