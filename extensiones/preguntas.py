"""Preguntas a tus clases: un agente busca en la despensa y responde citando clase y minuto.

Cómo evita inventar:
- Claude recibe solo fragmentos de tus notas y transcripciones, con reglas de citar cada oración.
- En modo API puede pedir más búsquedas con la herramienta buscar_apuntes (máximo configurable).
- Si no encuentra la respuesta debe contestar NO_ENCONTRADO; y una respuesta que no cita
  ningún fragmento real tampoco se muestra. Las citas a fragmentos que no existen se quitan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm
from .comun import ErrorExtension, id_modelo, modelo_para
from .indice import Despensa, Fragmento

NO_ENCONTRADO = "NO_ENCONTRADO"

REGLAS = """Respondes preguntas sobre las clases y reuniones de una persona usando sus apuntes de Obsidian \
y las transcripciones de las grabaciones.

Reglas:
1. Usa solo la información de los <fragmentos>. No completes con conocimiento propio, aunque sepas la respuesta.
2. Termina cada oración que dé información con la cita del fragmento que la respalda, así: [F3]. \
Puedes citar varios: [F1][F4].
3. {busqueda}
4. En los apuntes, el texto en cursiva y los fragmentos con fuente="agregado de Claude" los escribió Claude \
al hacer la nota, no el profesor. Si tu respuesta se apoya en ellos, dilo.
5. Si dos fragmentos se contradicen (por ejemplo, una cifra que dijo el profesor y su corrección en la nota), \
menciona ambos con sus citas.
6. Responde en español, directo y breve: un párrafo o una lista corta, sin preámbulo."""

BUSQUEDA_API = ("Si los fragmentos no alcanzan, usa la herramienta buscar_apuntes con otras palabras (sinónimos, "
                f"siglas, el nombre del tema). Si después de buscar sigue sin estar, responde exactamente {NO_ENCONTRADO}.")
BUSQUEDA_CLI = f"Si los fragmentos no alcanzan para responder, responde exactamente {NO_ENCONTRADO}."
INSTRUCCION_CLI = "Responde la <pregunta> siguiendo las <reglas> y usando solo los <fragmentos>."

HERRAMIENTA = {
    "name": "buscar_apuntes",
    "description": ("Busca más fragmentos en los apuntes y transcripciones de la persona. Úsala cuando los "
                    "fragmentos que ya tienes no responden la pregunta: prueba sinónimos, siglas o el nombre del "
                    "tema. Devuelve fragmentos nuevos, cada uno con un id que puedes citar."),
    "input_schema": {
        "type": "object",
        "properties": {"consulta": {"type": "string", "description": "palabras o frase a buscar"}},
        "required": ["consulta"],
    },
}

PATRON_CITA = re.compile(r"\[F(\d+)\]")


@dataclass
class Resultado:
    respuesta: str | None                 # None: no está en los apuntes
    fuentes: list[Fragmento] = field(default_factory=list)   # en el orden [1], [2]...
    cercanos: list[Fragmento] = field(default_factory=list)  # lo más parecido, si no se encontró
    avisos: list[str] = field(default_factory=list)
    busquedas: int = 0
    llamadas: list = field(default_factory=list)
    modelo: str = ""
    vistos: dict = field(default_factory=dict)


class Numeracion:
    """Numera los fragmentos que ve Claude (F1, F2...) a lo largo de varias búsquedas, sin repetir."""

    def __init__(self):
        self.por_clave: dict[str, Fragmento] = {}
        self.ids: set[str] = set()

    def agregar(self, fragmentos) -> list[tuple[str, Fragmento]]:
        lote = []
        for f in fragmentos:
            if f.id in self.ids:
                continue
            clave = f"F{len(self.por_clave) + 1}"
            self.por_clave[clave] = f
            self.ids.add(f.id)
            lote.append((clave, f))
        return lote


def _atributo(valor: str) -> str:
    return str(valor).replace('"', "'")


def formato(lote: list[tuple[str, Fragmento]]) -> str:
    bloques = []
    for clave, f in lote:
        atributos = [f'id="{clave}"', f'nota="{_atributo(f.nota)}"']
        if f.fecha:
            atributos.append(f'fecha="{f.fecha}"')
        atributos.append(f'fuente="{f.etiqueta_fuente()}"')
        if f.minuto:
            atributos.append(f'minuto="{f.minuto}"')
        elif f.seccion:
            atributos.append(f'seccion="{_atributo(f.seccion)}"')
        bloques.append(f"<fragmento {' '.join(atributos)}>\n{f.texto}\n</fragmento>")
    return "\n\n".join(bloques)


def evaluar(texto: str, vistos: Numeracion) -> tuple[str | None, list[Fragmento], list[str]]:
    """Solo deja pasar respuestas que citan fragmentos reales; renumera las citas como [1], [2]..."""
    texto = (texto or "").strip()
    no_encontrado = NO_ENCONTRADO in texto.upper().replace(" ", "_")
    citas = PATRON_CITA.findall(texto)
    orden: list[str] = []
    for numero in citas:
        clave = f"F{numero}"
        if clave in vistos.por_clave and clave not in orden:
            orden.append(clave)
    if not orden:
        avisos = [] if (no_encontrado or not texto) else [
            "Claude respondió sin citar tus apuntes, así que la respuesta no se muestra."]
        return None, [], avisos

    numeros = {clave: i for i, clave in enumerate(orden, 1)}
    invalidas = sorted({f"F{n}" for n in citas if f"F{n}" not in numeros}, key=lambda c: int(c[1:]))
    limpio = PATRON_CITA.sub(lambda m: f"[{numeros[f'F{m.group(1)}']}]" if f"F{m.group(1)}" in numeros else "",
                             texto)
    limpio = re.sub(r"NO[ _]ENCONTRADO", "", limpio, flags=re.I)
    limpio = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", limpio)
    limpio = re.sub(r"[ \t]+([.,;:])", r"\1", limpio).strip()

    avisos = []
    if invalidas:
        avisos.append(f"Se quitaron citas a fragmentos que no existen ({', '.join(invalidas)}).")
    oraciones = [o for o in re.split(r"(?<=[.!?])\s+|\n+", texto)
                 if len(o.strip()) > 40 and not o.strip().endswith(":")]
    sin_cita = [o for o in oraciones if not PATRON_CITA.search(o)]
    if oraciones and len(sin_cita) > len(oraciones) / 2:
        avisos.append("Más de la mitad de la respuesta no cita fragmentos: tómala con cuidado.")
    fuentes = [vistos.por_clave[c] for c in orden]
    if any(f.fuente == "claude" for f in fuentes):
        avisos.append("Parte de la respuesta viene de agregados de Claude en tus notas, no de lo que dijo el profesor.")
    return limpio, fuentes, avisos


def responder(cfg: dict, pregunta: str, *, despensa: Despensa | None = None, clase: str | None = None,
              modelo: str | None = None, k: int | None = None, cliente_api=None, avisar=print) -> Resultado:
    despensa = despensa or Despensa(cfg)
    resumen = despensa.actualizar(avisar=avisar)
    if resumen["fragmentos"] == 0:
        raise ErrorExtension("La despensa está vacía: todavía no hay notas con texto en tu vault.")
    notas = None
    if clase:
        notas = despensa.notas_que_coinciden(clase)
        if not notas:
            raise ErrorExtension(f'No encontré ninguna nota que coincida con "{clase}".')

    ajustes = cfg.get("preguntas", {}) or {}
    k = int(k or ajustes.get("fragmentos", 8))
    max_busquedas = int(ajustes.get("busquedas_extra", 2))
    nombre = modelo or modelo_para(cfg, "pregunta")

    vistos = Numeracion()
    iniciales = [f for f, _ in despensa.buscar(pregunta, k=k, notas=notas)]
    entrada = (f"<fragmentos>\n{formato(vistos.agregar(iniciales))}\n</fragmentos>\n\n"
               f"<pregunta>{pregunta}</pregunta>")

    if llm.backend(cfg) == "cli":
        texto = llm.llamar_cli(cfg, modelo=nombre, instruccion=INSTRUCCION_CLI,
                               entrada=f"<reglas>\n{REGLAS.format(busqueda=BUSQUEDA_CLI)}\n</reglas>\n\n{entrada}")
        respuesta, fuentes, avisos = evaluar(texto, vistos)
        return Resultado(respuesta, fuentes, [] if respuesta else iniciales[:3], avisos,
                         modelo=nombre, vistos=vistos.por_clave)

    # Modo API: un agente con una herramienta de búsqueda. El primer turno ya trae los mejores
    # fragmentos, así que la mayoría de las preguntas se resuelven en una sola llamada.
    modelo_id = id_modelo(cfg, nombre)
    mensajes = [{"role": "user", "content": entrada}]
    llamadas, busquedas = [], 0
    for _ in range(max_busquedas + 2):
        r = llm.llamar_api(cfg, tarea="pregunta", sesion=pregunta, modelo=modelo_id, mensajes=mensajes,
                           sistema=REGLAS.format(busqueda=BUSQUEDA_API), herramientas=[HERRAMIENTA],
                           max_tokens=2048, cliente_api=cliente_api)
        llamadas.append(r)
        usos = [b for b in r.contenido if getattr(b, "type", "") == "tool_use"]
        if r.stop_reason != "tool_use" or not usos:
            respuesta, fuentes, avisos = evaluar(r.texto, vistos)
            return Resultado(respuesta, fuentes, [] if respuesta else iniciales[:3], avisos,
                             busquedas, llamadas, modelo_id, vistos.por_clave)
        mensajes.append({"role": "assistant", "content": r.contenido})
        resultados = []
        for uso in usos:
            if uso.name != HERRAMIENTA["name"] or busquedas >= max_busquedas:
                resultados.append({"type": "tool_result", "tool_use_id": uso.id, "is_error": True,
                                   "content": f"Ya no hay más búsquedas. Responde con los fragmentos que "
                                              f"tienes o con {NO_ENCONTRADO}."})
                continue
            busquedas += 1
            consulta = str((uso.input or {}).get("consulta", "")).strip() or pregunta
            avisar(f'  Buscando también: "{consulta}"')
            nuevos = vistos.agregar(f for f, _ in despensa.buscar(consulta, k=6, notas=notas, excluir=vistos.ids))
            resultados.append({"type": "tool_result", "tool_use_id": uso.id,
                               "content": formato(nuevos) if nuevos else "No hay fragmentos nuevos para esa búsqueda."})
        mensajes.append({"role": "user", "content": resultados})
    return Resultado(None, [], iniciales[:3], ["Claude no terminó de responder dentro del límite de búsquedas."],
                     busquedas, llamadas, modelo_id, vistos.por_clave)


def imprimir(r: Resultado, ver_fragmentos: bool = False) -> None:
    if ver_fragmentos and r.vistos:
        print("\nFragmentos que recibió Claude:")
        for clave, f in r.vistos.items():
            resumen = " ".join(f.texto.split())[:140]
            print(f"  {clave} · {f.referencia()} ({f.etiqueta_fuente()}): {resumen}...")
    print()
    if r.respuesta is None:
        print("No encontré eso en tus apuntes.")
        if r.cercanos:
            print("\nLo más cercano que hay en la despensa:")
            for f in r.cercanos:
                print(f"  · {f.referencia()} ({f.etiqueta_fuente()})")
    else:
        print(r.respuesta)
        print("\nFuentes:")
        for i, f in enumerate(r.fuentes, 1):
            print(f"  [{i}] {f.referencia()} ({f.etiqueta_fuente()})")
    for aviso in r.avisos:
        print(f"\nAviso: {aviso}")
    if r.llamadas:
        tokens = sum(c.entrada + c.salida + c.cache_escritura + c.cache_lectura for c in r.llamadas)
        costos = [c.costo for c in r.llamadas if c.costo is not None]
        segundos = sum(c.segundos for c in r.llamadas)
        costo = f"${sum(costos):.4f} USD" if costos else "costo desconocido"
        print(f"\n{r.modelo} · {len(r.llamadas)} llamada(s), {r.busquedas} búsqueda(s) extra · "
              f"{tokens:,} tokens · {costo} · {segundos:.1f} s")
    elif r.modelo:
        print(f"\nClaude Code ({r.modelo}) · sin costo extra: usa tu suscripción")


def cmd_pregunta(cfg: dict, pregunta: str, *, clase: str | None = None, modelo: str | None = None,
                 k: int | None = None, ver_fragmentos: bool = False, despensa: Despensa | None = None,
                 cliente_api=None) -> int:
    pregunta = pregunta.strip()
    if not pregunta:
        raise ErrorExtension('Escribe la pregunta entre comillas, ej. pizza pregunta "¿cómo se calcula el ISR?"')
    r = responder(cfg, pregunta, despensa=despensa, clase=clase, modelo=modelo, k=k, cliente_api=cliente_api)
    imprimir(r, ver_fragmentos)
    return 0
