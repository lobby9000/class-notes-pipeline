"""Llamadas a Claude: por la API de Anthropic (con registro de tokens, costo y latencia)
o por Claude Code en modo headless (`claude -p`), que usa tu suscripción."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .comun import SALIDA_MAXIMA, ErrorExtension, costo_usd, duracion_legible, registrar, ruta

ARCHIVO_CLAVE = "~/.config/apuntes-clase/anthropic_api_key"


@dataclass
class Respuesta:
    texto: str
    modelo: str
    stop_reason: str | None = None
    contenido: list = field(default_factory=list)  # bloques crudos (texto y tool_use)
    entrada: int = 0
    salida: int = 0
    cache_escritura: int = 0
    cache_lectura: int = 0
    costo: float | None = None
    segundos: float = 0.0
    primer_token: float | None = None


def backend(cfg: dict) -> str:
    valor = str(cfg.get("claude", {}).get("backend", "cli")).strip().lower()
    if valor not in ("cli", "api"):
        raise ErrorExtension(f'[claude] backend debe ser "cli" o "api", no "{valor}".')
    return valor


# ---------------------------------------------------------------- API de Anthropic

def clave_api(cfg: dict) -> str:
    clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if clave:
        return clave
    archivo = ruta(cfg.get("api", {}).get("archivo_clave", ARCHIVO_CLAVE))
    if archivo.is_file():
        clave = archivo.read_text(encoding="utf-8").strip()
        if clave:
            return clave
    raise ErrorExtension(
        "Falta tu API key de Anthropic. Créala en console.anthropic.com y guárdala así:\n"
        f"  mkdir -p {archivo.parent} && nano {archivo}    (pega la clave y guarda)\n"
        f"  chmod 600 {archivo}\n"
        "También sirve la variable de entorno ANTHROPIC_API_KEY.")


def cliente(cfg: dict):
    try:
        import anthropic
    except ImportError:
        raise ErrorExtension("Falta el paquete anthropic. Corre: "
                             ".venv/bin/pip install -r requirements-extensiones.txt") from None
    minutos = float(cfg.get("claude", {}).get("tiempo_maximo", 30))
    # El SDK reintenta solo los errores pasajeros (429, 5xx, conexión) con espera exponencial
    return anthropic.Anthropic(api_key=clave_api(cfg), max_retries=3, timeout=minutos * 60)


def _error_legible(e: Exception, modelo: str) -> ErrorExtension | None:
    """Traduce los errores del SDK a mensajes que dicen qué hacer. None si no es error de la API."""
    try:
        import anthropic
    except ImportError:
        return None
    if not isinstance(e, anthropic.APIError):
        return None
    if isinstance(e, anthropic.AuthenticationError):
        return ErrorExtension("La API rechazó tu clave (401). Revisa que esté completa y activa en console.anthropic.com.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return ErrorExtension(f"Tu cuenta de la API no tiene permiso para usar {modelo} (403).")
    if isinstance(e, anthropic.NotFoundError):
        return ErrorExtension(f"La API no reconoce el modelo {modelo}. Revisa [claude.modelos] o [api.ids] en config.toml.")
    if isinstance(e, anthropic.RateLimitError):
        return ErrorExtension("Llegaste al límite de uso de tu cuenta de la API (429). Espera un poco o revisa tus límites.")
    if isinstance(e, anthropic.BadRequestError):
        texto = str(e)
        if "credit balance" in texto.lower():
            return ErrorExtension("Tu cuenta de la API no tiene saldo. Agrega crédito en console.anthropic.com (Billing).")
        return ErrorExtension(f"La API rechazó la petición (400): {texto[:400]}")
    if isinstance(e, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return ErrorExtension("No hubo conexión con la API de Anthropic. Revisa tu internet; se reintentará la próxima vez.")
    if isinstance(e, anthropic.APIStatusError):
        return ErrorExtension(f"La API respondió con error {e.status_code}: {str(e)[:300]}")
    return ErrorExtension(f"Error con la API de Anthropic: {str(e)[:300]}")


def llamar_api(cfg: dict, *, tarea: str, sesion: str, modelo: str, mensajes: list, sistema: str | None = None,
               herramientas: list | None = None, max_tokens: int = 4096, streaming: bool = False,
               avance: bool = False, cliente_api=None) -> Respuesta:
    """Una llamada a la API de Mensajes. Siempre queda registrada en costos.jsonl."""
    cl = cliente_api or cliente(cfg)
    max_tokens = min(int(max_tokens), SALIDA_MAXIMA.get(modelo, int(max_tokens)))
    parametros = {"model": modelo, "max_tokens": max_tokens, "messages": mensajes}
    if sistema:
        parametros["system"] = sistema
    if herramientas:
        parametros["tools"] = herramientas
    inicio = time.monotonic()
    primer_token = None
    try:
        if streaming:
            # Las notas largas se piden en streaming: así no hay límite de tiempo por petición
            # y se puede medir cuánto tarda en llegar el primer token.
            en_terminal = avance and sys.stdout.isatty()
            recibidos, ultimo_aviso = 0, 0.0
            with cl.messages.stream(**parametros) as flujo:
                for trozo in flujo.text_stream:
                    ahora = time.monotonic()
                    if primer_token is None:
                        primer_token = ahora - inicio
                    recibidos += len(trozo)
                    if en_terminal and ahora - ultimo_aviso >= 2:
                        ultimo_aviso = ahora
                        print(f"\r  {recibidos:,} caracteres · {duracion_legible(ahora - inicio)}".ljust(50),
                              end="", flush=True)
                mensaje = flujo.get_final_message()
            if en_terminal:
                print("\r".ljust(51) + "\r", end="", flush=True)
        else:
            mensaje = cl.messages.create(**parametros)
    except ErrorExtension:
        raise
    except Exception as e:  # noqa: BLE001 - se traducen solo los errores de la API
        legible = _error_legible(e, modelo)
        if legible is None:
            raise
        raise legible from None
    segundos = time.monotonic() - inicio

    uso = mensaje.usage
    entrada = int(getattr(uso, "input_tokens", 0) or 0)
    salida = int(getattr(uso, "output_tokens", 0) or 0)
    cache_escritura = int(getattr(uso, "cache_creation_input_tokens", 0) or 0)
    cache_lectura = int(getattr(uso, "cache_read_input_tokens", 0) or 0)
    modelo_real = getattr(mensaje, "model", None) or modelo
    costo = costo_usd(cfg, modelo_real, entrada, salida, cache_escritura, cache_lectura)
    contenido = list(mensaje.content or [])
    texto = "".join(getattr(b, "text", "") for b in contenido if getattr(b, "type", "") == "text")
    registrar(cfg, {
        "tarea": tarea, "sesion": sesion[:120], "modelo": modelo_real,
        "entrada": entrada, "salida": salida,
        "cache_escritura": cache_escritura, "cache_lectura": cache_lectura,
        "costo_usd": round(costo, 6) if costo is not None else None,
        "segundos": round(segundos, 2),
        "primer_token_s": round(primer_token, 2) if primer_token is not None else None,
        "stop_reason": mensaje.stop_reason,
    })
    return Respuesta(texto, modelo_real, mensaje.stop_reason, contenido, entrada, salida,
                     cache_escritura, cache_lectura, costo, segundos, primer_token)


def contar_tokens(cfg: dict, modelo: str, mensajes: list, sistema: str | None = None, cliente_api=None) -> int:
    """Tokens de entrada que cobraría la API, sin hacer la llamada (el conteo es gratis)."""
    cl = cliente_api or cliente(cfg)
    parametros = {"model": modelo, "messages": mensajes}
    if sistema:
        parametros["system"] = sistema
    try:
        return int(cl.messages.count_tokens(**parametros).input_tokens)
    except Exception as e:  # noqa: BLE001
        legible = _error_legible(e, modelo)
        if legible is None:
            raise
        raise legible from None


# ---------------------------------------------------------------- Claude Code (modo cli)

def ruta_claude() -> str | None:
    encontrado = shutil.which("claude")
    if encontrado:
        return encontrado
    local = Path.home() / ".local/bin/claude"  # por si corre desde systemd sin ese PATH
    return str(local) if local.exists() else None


def llamar_cli(cfg: dict, *, modelo: str, entrada: str, instruccion: str) -> str:
    """Misma llamada que usa el pipeline: sin herramientas, sin MCP y sin guardar la sesión."""
    ejecutable = ruta_claude()
    if not ejecutable:
        raise ErrorExtension('No encuentro el comando `claude` (Claude Code). Instálalo o usa [claude] backend = "api".')
    cmd = [ejecutable, "-p", instruccion, "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "text"]
    if modelo:
        cmd += ["--model", modelo]
    minutos = float(cfg.get("claude", {}).get("tiempo_maximo", 30))
    try:
        r = subprocess.run(cmd, input=entrada, capture_output=True, text=True, timeout=minutos * 60,
                           cwd=tempfile.gettempdir())  # fuera del proyecto: no carga su CLAUDE.md
    except subprocess.TimeoutExpired:
        raise ErrorExtension(f"Claude Code tardó más de {minutos:.0f} min en responder.") from None
    if r.returncode != 0 or not r.stdout.strip():
        detalle = (r.stderr.strip() or r.stdout.strip() or "sin detalle")[:400]
        raise ErrorExtension(f"Claude Code no pudo responder (código {r.returncode}): {detalle}")
    return r.stdout.strip()
