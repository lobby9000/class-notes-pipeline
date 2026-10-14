"""Modo API para escribir las notas.

Recibe exactamente la misma entrada que el pipeline le pasa a Claude Code
(<transcripcion>, <conceptos_existentes> e <instrucciones>), elige el modelo
según el tipo de sesión y registra tokens, costo y latencia en costos.jsonl.
"""

from __future__ import annotations

import re

from . import llm
from .comun import RAIZ, ErrorExtension, duracion_legible, id_modelo, modelo_para

INSTRUCCION_CORTA = ("Escribe la nota de Obsidian siguiendo las <instrucciones> que vienen al final "
                     "de la entrada, a partir de la <transcripcion>.")

# Claude Code trae su propio prompt de sistema; por la API hay que decir el formato de salida.
SISTEMA = ("Escribes notas de Obsidian en Markdown a partir de transcripciones de clases y reuniones. "
           "Responde únicamente con el contenido de la nota, sin preámbulo ni comentarios fuera de ella.")

PATRON_TRANSCRIPCION = re.compile(r"<transcripcion\b([^>]*)>")
PATRON_ATRIBUTO = re.compile(r'(\w+)="([^"]*)"')


def datos_de_entrada(entrada: str) -> dict:
    """Atributos de la etiqueta <transcripcion ...>: título, fecha, partes, duración."""
    m = PATRON_TRANSCRIPCION.search(entrada[:3000])
    return dict(PATRON_ATRIBUTO.findall(m.group(1))) if m else {}


def tipo_de_sesion(entrada: str, cfg: dict) -> str:
    """clase o reunion: lo dice la etiqueta si trae tipo; si no, se reconoce por el prompt que va adentro."""
    tipo = datos_de_entrada(entrada).get("tipo")
    if tipo in ("clase", "reunion"):
        return tipo
    instrucciones = entrada[entrada.rfind("<instrucciones>"):]
    for candidato in ("reunion", "clase"):
        archivo = RAIZ / str(cfg.get("claude", {}).get(f"prompt_{candidato}", f"prompts/{candidato}.md"))
        if archivo.is_file():
            inicio = archivo.read_text(encoding="utf-8").strip().split("{")[0].strip()[:200]
            if len(inicio) >= 20 and inicio in instrucciones:
                return candidato
    return "clase"


def limpiar_respuesta(texto: str) -> str:
    texto = texto.strip()
    m = re.fullmatch(r"```(?:markdown|md)?[ \t]*\n(.*)\n```", texto, flags=re.S)
    return (m.group(1) if m else texto).strip()


def hacer_apuntes(entrada: str, cfg: dict, cliente_api=None) -> str:
    datos = datos_de_entrada(entrada)
    tarea = tipo_de_sesion(entrada, cfg)
    modelo = id_modelo(cfg, modelo_para(cfg, tarea))
    print(f"Escribiendo la nota con la API de Anthropic ({modelo}, {tarea})...", flush=True)
    r = llm.llamar_api(
        cfg, tarea=tarea, sesion=datos.get("titulo", ""), modelo=modelo,
        mensajes=[{"role": "user", "content": entrada.rstrip() + "\n\n" + INSTRUCCION_CORTA}],
        sistema=SISTEMA, max_tokens=int(cfg.get("claude", {}).get("max_tokens_salida", 64000)),
        streaming=True, avance=True, cliente_api=cliente_api)
    if r.stop_reason == "max_tokens":
        raise ErrorExtension("La nota salió más larga que el máximo de respuesta y se cortó. "
                             "Sube [claude] max_tokens_salida o divide la sesión.")
    if r.stop_reason == "refusal":
        raise ErrorExtension("Claude se negó a escribir esta nota. Revisa la transcripción o el prompt.")
    texto = limpiar_respuesta(r.texto)
    if not texto:
        raise ErrorExtension(f"La API respondió sin texto (stop_reason: {r.stop_reason}).")
    costo = f"${r.costo:.2f} USD" if r.costo is not None else "costo sin precio en [api.precios]"
    print(f"Nota escrita con {r.modelo}: {r.entrada:,} tokens de entrada, {r.salida:,} de salida, "
          f"{costo}, {duracion_legible(r.segundos)}.", flush=True)
    return texto
