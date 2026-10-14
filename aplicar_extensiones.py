#!/usr/bin/env python3
"""Instala las extensiones (modo API y preguntas a tus clases) en tu proyecto apuntes-clase.

Uso, desde la carpeta del proyecto:
    .venv/bin/python aplicar_extensiones.py              aplica, instala dependencias y corre las pruebas
    .venv/bin/python aplicar_extensiones.py --revertir   deja todo como estaba antes de aplicarlas

Qué hace:
1. Respalda apuntes.py, config.ejemplo.toml, README.md y CLAUDE.md en .respaldo-extensiones/.
2. Agrega a apuntes.py tres ganchos y tres funciones cortas. No cambia nada de lo que ya hace:
   con [claude] backend = "cli" (el valor de siempre) el pipeline se comporta igual que antes.
3. Instala anthropic, chromadb, fastembed y snowballstemmer en tu .venv.
4. Corre todas las pruebas (las tuyas y las nuevas). Si alguna falla, regresa todo como estaba.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MARCA = "# --- extensiones (aplicar_extensiones.py) ---"
RESPALDOS = RAIZ / ".respaldo-extensiones"
A_RESPALDAR = ["apuntes.py", "config.ejemplo.toml", "README.md", "CLAUDE.md"]


class Falta(Exception):
    """No se encontró en apuntes.py una línea que los ganchos necesitan."""


FUNCIONES = f'''{MARCA}
# Cargan extensiones/ solo cuando se usan: sin esa carpeta o sin sus paquetes, todo sigue igual.

def notas_por_api(entrada: str, cfg: dict) -> str:
    from extensiones import notas_api
    from extensiones.comun import ErrorExtension
    try:
        return notas_api.hacer_apuntes(entrada, cfg)
    except ErrorExtension as e:
        raise ErrorApuntes(str(e)) from None


def registrar_extensiones(parser) -> None:
    try:
        from extensiones import cli
    except ImportError:
        return
    cli.registrar_comandos(parser)


def comando_extension(args, cfg: dict) -> int:
    from extensiones import cli
    from extensiones.comun import ErrorExtension
    try:
        return cli.ejecutar(args, cfg)
    except ErrorExtension as e:
        raise ErrorApuntes(str(e)) from None


'''


def _despues_del_docstring(s: str, posicion: int) -> int:
    """Si la función empieza con docstring, el gancho va después de él."""
    resto = s[posicion:]
    m = re.match(r'[ \t]*("""|\'\'\')', resto)
    if not m:
        return posicion
    cierre = resto.find(m.group(1), m.end())
    if cierre < 0:
        return posicion
    fin_linea = resto.find("\n", cierre)
    return posicion + (fin_linea + 1 if fin_linea >= 0 else len(resto))


def parchear_apuntes(s: str) -> str:
    if MARCA in s:
        return s

    # 1) Modo API: al inicio de hacer_apuntes(entrada, cfg)
    m = re.search(r"^def hacer_apuntes\((\w+)[^,()]*,\s*(\w+)[^,()]*\)[^\n]*:[ \t]*\n", s, re.M)
    if not m:
        raise Falta("def hacer_apuntes(entrada, cfg)")
    entrada, cfg = m.group(1), m.group(2)
    gancho = (f'    if {cfg}["claude"].get("backend", "cli") == "api":  # extensiones: notas con la API de Anthropic\n'
              f"        return notas_por_api({entrada}, {cfg})\n")
    donde = _despues_del_docstring(s, m.end())
    s = s[:donde] + gancho + s[donde:]

    # 2) Funciones de las extensiones, justo antes de main()
    m = re.search(r"^def main\(", s, re.M)
    if not m:
        raise Falta("def main(...)")
    inicio_main = m.start()
    s = s[:inicio_main] + FUNCIONES + s[inicio_main:]
    inicio_main += len(FUNCIONES)

    # 3) Subcomandos nuevos, antes de leer los argumentos
    patron = re.compile(r"^(?P<sangria>[ \t]+)args = (?P<parser>\w+)\.parse_args\(argv\)[ \t]*$", re.M)
    m = patron.search(s, inicio_main)
    if not m:
        raise Falta("args = p.parse_args(argv) dentro de main()")
    registro = (f"{m['sangria']}registrar_extensiones({m['parser']})  "
                "# extensiones: pregunta, cuenta, cotiza, despensa\n")
    s = s[:m.start()] + registro + s[m.start():]

    # 4) Despacho, justo después de cargar la configuración en main()
    patron = re.compile(r"^(?P<sangria>[ \t]+)cfg = cargar_config\(\)[ \t]*$", re.M)
    m = patron.search(s, inicio_main)
    if not m:
        raise Falta("cfg = cargar_config() dentro de main()")
    sangria = m["sangria"]
    despacho = (f"\n{sangria}if getattr(args, \"accion_extension\", None):  # extensiones\n"
                f"{sangria}    return comando_extension(args, cfg)")
    s = s[:m.end()] + despacho + s[m.end():]

    compile(s, "apuntes.py", "exec")  # si algo quedó mal, falla aquí y no se escribe nada
    return s


CONFIG_CLAUDE = '''
# "cli": las notas las escribe Claude Code con tu suscripción (como siempre).
# "api": las escribe la API de Anthropic; cada llamada se cobra y queda en `pizza cuenta`.
backend = "cli"'''

CONFIG_EXTENSIONES = '''
# ------------------------------------------------------------------ extensiones (EXTENSIONES.md)

# Modelo por tarea: opus, sonnet, haiku o un id completo de la API.
# En modo cli las notas usan [claude] modelo; las preguntas usan "pregunta" en los dos modos.
[claude.modelos]
clase = "opus"        # clases largas: la mejor calidad
reunion = "sonnet"    # reuniones: más cortas, la mitad de precio
pregunta = "haiku"    # preguntas a tus clases: rápido y barato

[api]
# Tu API key va en este archivo (con chmod 600) o en la variable ANTHROPIC_API_KEY.
archivo_clave = "~/.config/apuntes-clase/anthropic_api_key"
# Precios de referencia en USD por millón de tokens (claude.com/pricing, octubre 2026).
# Si cambian, agrega por ejemplo:
# [api.precios."claude-opus-5-5"]
# entrada = 4.0
# salida = 20.0

[preguntas]
# Modelo de embeddings de la despensa. Se descarga la primera vez (unos 640 MB).
# Más ligero (220 MB, un poco menos preciso): "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
modelo_embeddings = "jinaai/jina-embeddings-v2-base-es"
fragmentos = 8        # cuántos fragmentos recibe Claude de entrada
busquedas_extra = 2   # cuántas búsquedas más puede pedir el agente (modo api)
'''


def parchear_config(s: str) -> str:
    if "[claude.modelos]" in s:
        return s
    m = re.search(r"^\[claude\][ \t]*$", s, re.M)
    if m and not re.search(r"^backend\s*=", s, re.M):
        s = s[:m.end()] + CONFIG_CLAUDE + s[m.end():]
    return s.rstrip() + "\n" + CONFIG_EXTENSIONES


README = '''

## Extensiones: modo API y preguntas a tus clases

Detalles, configuración y decisiones de diseño en [EXTENSIONES.md](EXTENSIONES.md).

| Pizzería | Nombre de siempre | Qué hace |
| --- | --- | --- |
| `pizza pregunta "..."` | `apuntes preguntar` | Responde con tus notas y transcripciones, citando clase y minuto |
| `pizza despensa` | `apuntes indexar` | Actualiza el índice (Chroma + BM25) con las notas nuevas o cambiadas |
| `pizza cuenta` | `apuntes costos` | Tokens, costo y latencia de cada llamada a la API |
| `pizza cotiza "24 septiembre"` | `apuntes estimar` | Cuánto costaría esa nota con Opus, Sonnet o Haiku |

| Archivo | Para qué |
| --- | --- |
| `extensiones/` | Modo API de Anthropic, despensa, agente de preguntas y cuenta |
| `EXTENSIONES.md` | Cómo funcionan y cómo se configuran las extensiones |
| `aplicar_extensiones.py` | Instala o revierte los ganchos de las extensiones en `apuntes.py` |
| `requirements-extensiones.txt` | Paquetes de las extensiones |
| `pruebas/test_extensiones.py` | Pruebas de las extensiones, sin red ni descargas |
'''

CLAUDE_MD = '''

## Extensiones (`extensiones/`, ver EXTENSIONES.md)
- `notas_api.py` + `llm.py`: modo API de Anthropic (streaming, modelo por tarea, costos en `<trabajo>/costos.jsonl`).
- `indice.py`: la despensa. Parte las notas en fragmentos (apuntes, bloques `[!claude]` aparte y transcripción
  con minuto), los guarda en Chroma (`<trabajo>/indice/`) y busca con embeddings + BM25 fusionados con RRF.
- `preguntas.py`: agente con la herramienta `buscar_apuntes`; solo muestra respuestas que citan fragmentos reales.
- `cuenta.py` (`pizza cuenta`, `pizza cotiza`) y `cli.py` (subcomandos).
- Las extensiones no importan `apuntes.py`: lanzan `ErrorExtension` y los ganchos la convierten en `ErrorApuntes`.
- Excepción a "solo biblioteca estándar + faster-whisper": las extensiones usan anthropic, chromadb, fastembed y
  snowballstemmer, importados dentro de las funciones para que el núcleo funcione sin ellos.
- Nunca guardes índices ni costos en el vault.
- Pruebas: `pruebas/test_extensiones.py` (API, embeddings y `claude` falsos; sin red).
'''


def agregar_seccion(s: str, seccion: str, marca: str) -> str:
    return s if marca in s else s.rstrip() + seccion


# ---------------------------------------------------------------- pasos

def python_del_proyecto() -> str:
    venv = RAIZ / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def respaldar() -> Path:
    destino = RESPALDOS / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    destino.mkdir(parents=True, exist_ok=True)
    for nombre in A_RESPALDAR:
        if (RAIZ / nombre).exists():
            shutil.copy2(RAIZ / nombre, destino / nombre)
    return destino


def restaurar(origen: Path) -> None:
    for nombre in A_RESPALDAR:
        if (origen / nombre).exists():
            shutil.copy2(origen / nombre, RAIZ / nombre)


def instalar(python: str) -> bool:
    print("Instalando anthropic, chromadb, fastembed y snowballstemmer (puede tardar un par de minutos)...", flush=True)
    r = subprocess.run([python, "-m", "pip", "install", "-q", "-r", str(RAIZ / "requirements-extensiones.txt")])
    return r.returncode == 0


def correr_pruebas(python: str) -> tuple[bool, str]:
    print("Corriendo todas las pruebas...", flush=True)
    r = subprocess.run([python, "-m", "unittest", "discover", "-s", "pruebas"], cwd=RAIZ,
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def revertir() -> int:
    if not RESPALDOS.is_dir() or not any(RESPALDOS.iterdir()):
        print("No hay respaldos que restaurar.")
        return 1
    ultimo = sorted(p for p in RESPALDOS.iterdir() if p.is_dir())[-1]
    restaurar(ultimo)
    print(f"Listo: apuntes.py, config.ejemplo.toml, README.md y CLAUDE.md quedaron como en {ultimo.name}.")
    print("La carpeta extensiones/ se queda, pero sin los ganchos no hace nada.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--revertir", action="store_true", help="restaura el último respaldo")
    p.add_argument("--sin-instalar", action="store_true", help="no instala los paquetes (ya los tienes)")
    args = p.parse_args()

    if not (RAIZ / "apuntes.py").exists() or not (RAIZ / "extensiones").is_dir():
        print("Corre este script dentro de tu proyecto: la carpeta donde están apuntes.py y extensiones/.")
        return 1
    if args.revertir:
        return revertir()

    original = (RAIZ / "apuntes.py").read_text(encoding="utf-8")
    try:
        nuevo = parchear_apuntes(original)
    except Falta as e:
        print(f"No encontré esta línea en apuntes.py: {e}")
        print("No cambié nada. Tu apuntes.py es distinto del que conozco: mándamelo y ajusto los ganchos.")
        return 1
    except SyntaxError as e:
        print(f"Los ganchos dejarían apuntes.py con un error de sintaxis ({e}). No cambié nada.")
        return 1

    ya_estaban = nuevo == original
    respaldo = None
    if ya_estaban:
        print("Los ganchos ya estaban aplicados en apuntes.py.")
    else:
        respaldo = respaldar()  # solo se respalda el original, para que --revertir regrese al estado previo
        (RAIZ / "apuntes.py").write_text(nuevo, encoding="utf-8")
        for nombre, cambio in (("config.ejemplo.toml", parchear_config),
                               ("README.md", lambda s: agregar_seccion(s, README, "## Extensiones: modo API")),
                               ("CLAUDE.md", lambda s: agregar_seccion(s, CLAUDE_MD, "## Extensiones ("))):
            archivo = RAIZ / nombre
            if archivo.exists():
                archivo.write_text(cambio(archivo.read_text(encoding="utf-8")), encoding="utf-8")
        print("Ganchos aplicados en apuntes.py.")

    python = python_del_proyecto()
    if not args.sin_instalar and not instalar(python):
        print("AVISO: no se pudieron instalar los paquetes. Revisa tu internet y corre:\n"
              "  .venv/bin/pip install -r requirements-extensiones.txt")

    bien, salida = correr_pruebas(python)
    total = re.search(r"^Ran (\d+) tests?", salida, re.M)
    if not bien:
        print("\n".join(salida.splitlines()[-40:]))
        if respaldo:
            restaurar(respaldo)
            print("\nAlguna prueba falló, así que regresé apuntes.py y los demás archivos a como estaban.")
        else:
            print("\nAlguna prueba falló.")
        print("Mándame este mensaje completo y lo ajusto.")
        return 1
    print(f"Todo en orden: las {total.group(1) if total else ''} pruebas pasan.".replace("las  ", "las "))
    if respaldo:
        print(f"Respaldo de los archivos originales: {respaldo.relative_to(RAIZ)}")
    print("\nSiguientes pasos (detalles en EXTENSIONES.md):")
    print('  pizza despensa                      arma el índice (la primera vez descarga el modelo)')
    print('  pizza pregunta "¿qué es la UMA?"    pregúntale a tus clases')
    print('  Modo API: agrega backend = "api" en la sección [claude] de tu config.toml y guarda tu API key.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
