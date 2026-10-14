"""La cuenta y la cotización: cuánto se ha gastado en la API y cuánto costaría una nota con cada modelo."""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from . import llm
from .comun import (RAIZ, ErrorExtension, carpetas_de_notas, costo_usd, duracion_legible, id_modelo,
                    leer_registros, modelo_para, normalizar, proporcion_salida)
from .indice import leer_frontmatter, separar_transcripcion, transcripcion_de_trabajo
from .notas_api import INSTRUCCION_CORTA, SISTEMA

CARACTERES_POR_TOKEN = 3.6  # aproximación para español cuando no hay API key para contar de verdad


def _dinero(valor: float | None, decimales: int = 4) -> str:
    return f"${valor:.{decimales}f}" if valor is not None else "?"


def _resumen(registros: list[dict], clave: str) -> dict[str, dict]:
    grupos: dict[str, dict] = defaultdict(lambda: {"llamadas": 0, "entrada": 0, "salida": 0, "costo": 0.0,
                                                   "segundos": [], "primer_token": []})
    for r in registros:
        g = grupos[r.get(clave) or "?"]
        g["llamadas"] += 1
        g["entrada"] += int(r.get("entrada") or 0)
        g["salida"] += int(r.get("salida") or 0)
        g["costo"] += float(r.get("costo_usd") or 0.0)
        g["segundos"].append(float(r.get("segundos") or 0.0))
        if r.get("primer_token_s") is not None:
            g["primer_token"].append(float(r["primer_token_s"]))
    return grupos


def cmd_cuenta(cfg: dict, ultimas: int = 15) -> int:
    registros = leer_registros(cfg)
    if not registros:
        print("Todavía no hay llamadas a la API. En modo cli las notas van contra tu suscripción de Claude "
              "y no aparecen aquí.")
        return 0
    mostradas = registros[-max(1, ultimas):]
    print(f"LA CUENTA · últimas {len(mostradas)} de {len(registros)} llamadas a la API\n")
    print(f"{'FECHA':16}  {'TAREA':8}  {'SESIÓN':26}  {'MODELO':17}  {'ENTRADA':>8}  {'SALIDA':>7}  "
          f"{'COSTO':>8}  {'TIEMPO':>10}")
    for r in mostradas:
        print(f"{str(r.get('fecha', ''))[:16].replace('T', ' '):16}  {str(r.get('tarea', ''))[:8]:8}  "
              f"{str(r.get('sesion', ''))[:26]:26}  {str(r.get('modelo', ''))[:17]:17}  "
              f"{int(r.get('entrada') or 0):>8,}  {int(r.get('salida') or 0):>7,}  "
              f"{_dinero(r.get('costo_usd')):>8}  {duracion_legible(r.get('segundos') or 0):>10}")

    for titulo, clave in (("Por modelo", "modelo"), ("Por tarea", "tarea")):
        print(f"\n{titulo}:")
        for nombre, g in sorted(_resumen(registros, clave).items(), key=lambda x: -x[1]["costo"]):
            latencia = f"{statistics.mean(g['segundos']):.1f} s por llamada"
            if g["primer_token"]:
                latencia += f", primer token en {statistics.mean(g['primer_token']):.1f} s"
            print(f"  {nombre:18} {g['llamadas']:>4} llamadas · {g['entrada']:>9,} entrada · "
                  f"{g['salida']:>8,} salida · {_dinero(g['costo'], 2):>7} · {latencia}")
    total = sum(float(r.get("costo_usd") or 0.0) for r in registros)
    print(f"\nTotal: {_dinero(total, 2)} USD en {len(registros)} llamadas.")
    return 0


# ---------------------------------------------------------------- cotización

def buscar_nota(cfg: dict, texto: str) -> Path:
    buscado = normalizar(texto).strip()
    indice = str(cfg.get("diplomado", {}).get("indice", "")).strip()
    candidatos = []
    for carpeta in carpetas_de_notas(cfg):
        for archivo in sorted(carpeta.rglob("*.md")):
            if archivo.stem == indice or any(p.startswith(".") for p in archivo.relative_to(carpeta).parts):
                continue
            datos, _ = leer_frontmatter(archivo.read_text(encoding="utf-8", errors="replace")[:3000])
            if buscado in normalizar(archivo.stem) or buscado == datos.get("fecha"):
                candidatos.append(archivo)
    if not candidatos:
        raise ErrorExtension(f'No encontré ninguna nota que coincida con "{texto}".')
    exactos = [a for a in candidatos if normalizar(a.stem) == buscado]
    if len(candidatos) > 1 and len(exactos) != 1:
        nombres = ", ".join(a.stem for a in candidatos[:8])
        raise ErrorExtension(f"Hay varias notas que coinciden ({nombres}). Escribe más del nombre.")
    return exactos[0] if exactos else candidatos[0]


def cmd_cotiza(cfg: dict, sesion: str, cliente_api=None) -> int:
    nota = buscar_nota(cfg, sesion)
    md = nota.read_text(encoding="utf-8", errors="replace")
    datos, cuerpo = leer_frontmatter(md)
    transcripcion = separar_transcripcion(cuerpo)[1] or transcripcion_de_trabajo(cfg, md)
    if not transcripcion:
        raise ErrorExtension(f"La nota «{nota.stem}» no trae transcripción y no la encontré en la carpeta de trabajo.")
    tipo = datos.get("tipo") if datos.get("tipo") in ("clase", "reunion") else "clase"
    prompt = RAIZ / str(cfg.get("claude", {}).get(f"prompt_{tipo}", f"prompts/{tipo}.md"))
    instrucciones = prompt.read_text(encoding="utf-8") if prompt.is_file() else ""
    entrada = ("<transcripcion>\n" + "\n".join(transcripcion) + "\n</transcripcion>\n\n"
               f"<instrucciones>\n{instrucciones}\n</instrucciones>\n\n{INSTRUCCION_CORTA}")
    mensajes = [{"role": "user", "content": entrada}]

    try:
        cl = cliente_api or llm.cliente(cfg)
    except ErrorExtension:
        cl = None  # sin API key: se aproxima por número de caracteres
    registros = leer_registros(cfg)
    proporcion, muestras = proporcion_salida(registros, tipo, 0.30)
    configurado = id_modelo(cfg, modelo_para(cfg, tipo))

    filas = []
    for alias in ("opus", "sonnet", "haiku"):
        modelo = id_modelo(cfg, alias)
        if cl is not None:
            tokens_entrada = llm.contar_tokens(cfg, modelo, mensajes, sistema=SISTEMA, cliente_api=cl)
        else:
            tokens_entrada = round((len(entrada) + len(SISTEMA)) / CARACTERES_POR_TOKEN)
        tokens_salida = round(tokens_entrada * proporcion)
        ritmo = [r["segundos"] / r["salida"] for r in registros
                 if r.get("modelo") == modelo and r.get("salida") and r.get("segundos")]
        tiempo = duracion_legible(statistics.median(ritmo) * tokens_salida) if ritmo else "?"
        filas.append((modelo, tokens_entrada, tokens_salida, costo_usd(cfg, modelo, tokens_entrada, tokens_salida),
                      tiempo))

    duracion = f", grabación de {datos['duracion']}" if datos.get("duracion") else ""
    print(f"Cotización para «{nota.stem}» ({tipo}{duracion})\n")
    print(f"{'MODELO':20}  {'ENTRADA':>8}  {'SALIDA EST.':>11}  {'COSTO EST.':>10}  {'TIEMPO EST.':>11}")
    for modelo, tokens_entrada, tokens_salida, costo, tiempo in filas:
        marca = "  <- el que usa tu config" if modelo == configurado else ""
        print(f"{modelo:20}  {tokens_entrada:>8,}  {tokens_salida:>11,}  {_dinero(costo, 2):>10}  {tiempo:>11}{marca}")
    print()
    if cl is not None:
        print("Los tokens de entrada los contó la API (el conteo es gratis).")
    else:
        print(f"Sin API key: la entrada se aproximó a {CARACTERES_POR_TOKEN} caracteres por token.")
    origen = (f"la mediana de tus {muestras} notas de {tipo} anteriores" if muestras
              else "un valor de fábrica, hasta que haya notas hechas por la API")
    print(f"La salida se estima como {proporcion:.2f} tokens por token de entrada ({origen}).")
    return 0
