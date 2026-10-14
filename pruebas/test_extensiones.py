"""Pruebas de las extensiones: modo API, despensa (índice) y preguntas.

No usan internet ni descargan modelos: la API de Anthropic, el modelo de embeddings
y Claude Code se reemplazan por versiones falsas. Las de la despensa necesitan chromadb.

Correr:  .venv/bin/python -m unittest discover -s pruebas -v
"""

import argparse
import contextlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extensiones import cli, comun, cuenta, indice, llm, notas_api, preguntas  # noqa: E402
from extensiones.comun import ErrorExtension  # noqa: E402

HAY_CHROMA = importlib.util.find_spec("chromadb") is not None
HAY_RAICES = importlib.util.find_spec("snowballstemmer") is not None

try:
    import anthropic
    try:
        import httpx2 as http  # las versiones nuevas del SDK usan httpx2
    except ImportError:
        import httpx as http
    HAY_ANTHROPIC = True
except ImportError:
    HAY_ANTHROPIC = False

RELLENO = " Seguimos con el mismo ejemplo del pizarrón para que quede claro." * 11

NOTA_24 = f"""---
fecha: 2026-09-24
dia: jueves
curso: Diplomado en finanzas
duracion: "4:02:10"
tags: [diplomado, clase]
---

# Clase del jueves 24 de septiembre de 2026

## Resumen
Clase sobre [[Amortización]] y el cálculo del ISR de los salarios.

## Desarrollo y práctica
### Tabla de amortización
La tabla tiene pago, interés y capital. El pago fijo se calcula con la fórmula de anualidad.

> [!claude] Agregado de Claude
> *La fórmula de anualidad supone que la tasa no cambia durante todo el plazo.*

## Glosario
- **[[UMA]]**: Unidad de Medida y Actualización, referencia para multas y prestaciones.

> [!quote]- Transcripción completa
> [00:00:00] Buenas noches, hoy vemos amortización.{RELLENO}
>
> [00:47:12] El ISR de los salarios se calcula con la tabla del artículo 96, restando el límite inferior.{RELLENO}
>
> [01:10:05] La UMA de este año vale ciento trece pesos con catorce centavos.{RELLENO}
"""

NOTA_25 = """---
fecha: 2026-09-25
dia: viernes
---

# Clase del viernes 25 de septiembre de 2026

## Desarrollo y práctica
### Valor presente
El valor presente descuenta los flujos futuros con la tasa de interés del periodo.

> [!quote]- Transcripción completa
> === Parte 1 de 2 · grabación iniciada a las 20:35 ===
>
> [P1 00:00:00] Empezamos con valor presente y valor futuro.
>
> === Parte 2 de 2 · grabación iniciada a las 22:40 ===
>
> [P2 00:05:00] Después del descanso vemos la tasa interna de retorno de un proyecto.
"""


# ---------------------------------------------------------------- dobles de prueba

def texto(t):
    return types.SimpleNamespace(type="text", text=t)


def herramienta(id_, consulta):
    return types.SimpleNamespace(type="tool_use", id=id_, name="buscar_apuntes", input={"consulta": consulta})


def mensaje(bloques, stop="end_turn", entrada=1000, salida=200):
    uso = types.SimpleNamespace(input_tokens=entrada, output_tokens=salida,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return types.SimpleNamespace(content=bloques, stop_reason=stop, model=None, usage=uso)


class Flujo:
    def __init__(self, m):
        self.m = m

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @property
    def text_stream(self):
        yield from (b.text for b in self.m.content if b.type == "text")

    def get_final_message(self):
        return self.m


class ClienteFalso:
    """Imita anthropic.Anthropic: create, stream y count_tokens. Las respuestas pueden ser funciones."""

    def __init__(self, respuestas=(), tokens=60_000, error=None):
        self.respuestas, self.tokens, self.error = list(respuestas), tokens, error
        self.llamadas = []
        self.messages = self

    def _siguiente(self, parametros):
        self.llamadas.append(parametros)
        if self.error:
            raise self.error
        r = self.respuestas.pop(0)
        r = r(parametros) if callable(r) else r
        r.model = r.model or parametros["model"]
        return r

    def create(self, **parametros):
        return self._siguiente(parametros)

    def stream(self, **parametros):
        return Flujo(self._siguiente(parametros))

    def count_tokens(self, **parametros):
        self.llamadas.append(parametros)
        return types.SimpleNamespace(input_tokens=self.tokens)


class EmbebedorFalso:
    """Bolsa de palabras con hash: textos que comparten palabras quedan cerca. Cuenta lo que embebe."""
    nombre = "falso-v1"

    def __init__(self, nombre=None):
        self.nombre = nombre or self.nombre
        self.embebidos = 0

    def _vector(self, t):
        v = [0.0] * 128
        for termino in indice.terminos(t):
            v[zlib.crc32(termino.encode()) % 128] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def documentos(self, textos):
        self.embebidos += len(textos)
        return [self._vector(t) for t in textos]

    def consulta(self, t):
        return self._vector(t)


def citar(palabra, respuesta):
    """Respuesta falsa que cita el fragmento que contiene `palabra` (como lo haría Claude)."""
    def responder(parametros):
        textos = []
        for m in parametros["messages"]:
            if m["role"] != "user":
                continue
            contenido = m["content"]
            textos.append(contenido if isinstance(contenido, str)
                          else "\n".join(str(b.get("content", "")) for b in contenido))
        for clave, cuerpo in re.findall(r'<fragmento id="(F\d+)"[^>]*>(.*?)</fragmento>', "\n".join(textos), flags=re.S):
            if palabra in cuerpo:
                return mensaje([texto(respuesta.format(id=clave))])
        return mensaje([texto("NO_ENCONTRADO")])
    return responder


def silencio(funcion, *args, **kwargs):
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        resultado = funcion(*args, **kwargs)
    return resultado, salida.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = self.tmp / "vault"
        (self.vault / "Diplomado Finanzas").mkdir(parents=True)
        (self.vault / "Notas sueltas").mkdir()
        self.trabajo = self.tmp / "trabajo"
        self.trabajo.mkdir()
        self.cfg = {
            "carpetas": {"trabajo": str(self.trabajo), "vault": str(self.vault),
                         "notas": "Diplomado Finanzas", "sueltas": "Notas sueltas"},
            "claude": {"modelo": "opus", "max_tokens_salida": 64000, "tiempo_maximo": 30, "backend": "api",
                       "prompt_clase": "prompts/clase.md"},
            "diplomado": {"indice": "Índice del diplomado"},
            "api": {"archivo_clave": str(self.tmp / "no-existe")},
        }
        self.escribir("Clase 24 septiembre", NOTA_24)
        self.escribir("Clase 25 septiembre", NOTA_25)
        clave = os.environ.pop("ANTHROPIC_API_KEY", None)
        if clave is not None:
            self.addCleanup(os.environ.__setitem__, "ANTHROPIC_API_KEY", clave)

    def escribir(self, nombre, contenido, carpeta="Diplomado Finanzas"):
        (self.vault / carpeta / f"{nombre}.md").write_text(contenido, encoding="utf-8")

    def registros(self):
        return comun.leer_registros(self.cfg)


# ---------------------------------------------------------------- modelos y costos

class TestModelosYCostos(Base):
    def test_cada_tarea_tiene_su_modelo(self):
        self.assertEqual(comun.modelo_para(self.cfg, "clase"), "opus")
        self.assertEqual(comun.modelo_para(self.cfg, "reunion"), "sonnet")
        self.assertEqual(comun.modelo_para(self.cfg, "pregunta"), "haiku")
        self.cfg["claude"]["modelos"] = {"reunion": "haiku"}
        self.assertEqual(comun.modelo_para(self.cfg, "reunion"), "haiku")

    def test_alias_a_id_de_la_api(self):
        self.assertEqual(comun.id_modelo(self.cfg, "Opus"), "claude-opus-5-5")
        self.assertEqual(comun.id_modelo(self.cfg, "claude-sonnet-5-5"), "claude-sonnet-5-5")
        self.cfg["api"]["ids"] = {"opus": "claude-opus-6"}
        self.assertEqual(comun.id_modelo(self.cfg, "opus"), "claude-opus-6")

    def test_costo_con_precios_de_referencia(self):
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-opus-5-5", 60_000, 15_000), 0.54)
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-haiku-4-5-20251001", 1_000_000, 0), 1.0)
        self.assertIsNone(comun.costo_usd(self.cfg, "modelo-raro", 1000, 1000))
        # Haiku 5.5 cobra cinco veces más cuando la entrada pasa de 100 mil tokens, contando la caché.
        self.assertEqual(comun.id_modelo(self.cfg, "haiku"), "claude-haiku-5-5")
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-haiku-5-5", 100_000, 10_000), 0.015)
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-haiku-5-5", 100_001, 10_000),
                               (100_001 * 0.50 + 10_000 * 2.50) / 1e6)
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-haiku-5-5", 60_000, 0, cache_lectura=50_000),
                               (60_000 * 0.50 + 50_000 * 0.05) / 1e6)
        self.cfg["api"]["precios"] = {"claude-opus-5-5": {"entrada": 10.0}}
        self.assertAlmostEqual(comun.costo_usd(self.cfg, "claude-opus-5-5", 100_000, 0), 1.0)

    def test_clave_de_variable_de_entorno_o_archivo(self):
        with self.assertRaisesRegex(ErrorExtension, "console.anthropic.com"):
            llm.clave_api(self.cfg)
        archivo = self.tmp / "clave"
        archivo.write_text("sk-ant-archivo\n")
        self.cfg["api"]["archivo_clave"] = str(archivo)
        self.assertEqual(llm.clave_api(self.cfg), "sk-ant-archivo")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-entorno"
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)
        self.assertEqual(llm.clave_api(self.cfg), "sk-ant-entorno")


# ---------------------------------------------------------------- notas por la API

ENTRADA_REUNION = ('<transcripcion titulo="Junta con Liverpool" tipo="reunion" fecha="1/10/2026" partes="1" '
                   'duracion="45 min">\n[00:00:00] Hola.\n</transcripcion>\n\n<instrucciones>\nMinuta.\n</instrucciones>\n')


class TestNotasPorApi(Base):
    def test_reunion_usa_sonnet_y_queda_en_la_cuenta(self):
        cl = ClienteFalso([mensaje([texto("```markdown\n## Minuta\nAcuerdos.\n```")], entrada=20_000, salida=3_000)])
        nota, _ = silencio(notas_api.hacer_apuntes, ENTRADA_REUNION, self.cfg, cliente_api=cl)
        self.assertEqual(nota, "## Minuta\nAcuerdos.")
        llamada = cl.llamadas[0]
        self.assertEqual(llamada["model"], "claude-sonnet-5-5")
        self.assertIn("Markdown", llamada["system"])
        self.assertTrue(llamada["messages"][0]["content"].endswith(notas_api.INSTRUCCION_CORTA))
        registro = self.registros()[-1]
        self.assertEqual((registro["tarea"], registro["sesion"]), ("reunion", "Junta con Liverpool"))
        self.assertEqual((registro["entrada"], registro["salida"]), (20_000, 3_000))
        self.assertAlmostEqual(registro["costo_usd"], 0.07)

    def test_sin_tipo_usa_el_modelo_de_clase(self):
        cl = ClienteFalso([mensaje([texto("## Nota")])])
        silencio(notas_api.hacer_apuntes, "<transcripcion titulo=\"Clase 1\">\nx\n</transcripcion>", self.cfg,
                 cliente_api=cl)
        self.assertEqual(cl.llamadas[0]["model"], "claude-opus-5-5")

    def test_reconoce_una_reunion_por_su_prompt(self):
        prompt = self.tmp / "reunion.md"
        prompt.write_text("Escribe la minuta de la reunión con acuerdos y pendientes. Tema: {descripcion}.")
        self.cfg["claude"]["prompt_reunion"] = str(prompt)  # ruta absoluta: RAIZ / ruta la respeta
        entrada = ('<transcripcion titulo="Junta">\nHola.\n</transcripcion>\n\n<instrucciones>\n'
                   "Escribe la minuta de la reunión con acuerdos y pendientes. Tema: tablero.\n</instrucciones>\n")
        cl = ClienteFalso([mensaje([texto("## Minuta")])])
        silencio(notas_api.hacer_apuntes, entrada, self.cfg, cliente_api=cl)
        self.assertEqual(cl.llamadas[0]["model"], "claude-sonnet-5-5")

    def test_nota_cortada_da_un_error_claro(self):
        cl = ClienteFalso([mensaje([texto("## Nota a med")], stop="max_tokens")])
        with self.assertRaisesRegex(ErrorExtension, "max_tokens_salida"):
            silencio(notas_api.hacer_apuntes, ENTRADA_REUNION, self.cfg, cliente_api=cl)

    def test_respeta_el_maximo_de_salida_del_modelo(self):
        # Haiku 4.5 acepta 64 mil tokens de salida; los modelos 5.5, 128 mil.
        self.cfg["claude"]["modelos"] = {"reunion": "claude-haiku-4-5"}
        self.cfg["claude"]["max_tokens_salida"] = 128_000
        cl = ClienteFalso([mensaje([texto("## Nota")])])
        silencio(notas_api.hacer_apuntes, ENTRADA_REUNION, self.cfg, cliente_api=cl)
        self.assertEqual(cl.llamadas[0]["max_tokens"], 64_000)

    @unittest.skipUnless(HAY_ANTHROPIC, "requiere el paquete anthropic")
    def test_errores_de_la_api_dicen_que_hacer(self):
        pedido = http.Request("POST", "https://api.anthropic.com/v1/messages")
        casos = [
            (anthropic.AuthenticationError("bad key", response=http.Response(401, request=pedido), body=None),
             "rechazó tu clave"),
            (anthropic.BadRequestError("Your credit balance is too low", response=http.Response(400, request=pedido),
                                       body=None), "no tiene saldo"),
            (anthropic.NotFoundError("model not found", response=http.Response(404, request=pedido), body=None),
             "no reconoce el modelo"),
        ]
        for error, mensaje_esperado in casos:
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(ErrorExtension, mensaje_esperado):
                    silencio(notas_api.hacer_apuntes, ENTRADA_REUNION, self.cfg, cliente_api=ClienteFalso(error=error))


# ---------------------------------------------------------------- partir notas

class TestPartirNotas(unittest.TestCase):
    def test_separa_apuntes_agregados_de_claude_y_transcripcion(self):
        fragmentos = indice.fragmentos_de_nota(NOTA_24, "Diplomado Finanzas/Clase 24 septiembre.md")
        fuentes = {f.fuente for f in fragmentos}
        self.assertEqual(fuentes, {"apuntes", "claude", "transcripcion"})
        agregado = next(f for f in fragmentos if f.fuente == "claude")
        self.assertIn("tasa no cambia", agregado.texto)
        self.assertEqual(agregado.seccion, "Desarrollo y práctica › Tabla de amortización")
        transcripcion = [f for f in fragmentos if f.fuente == "transcripcion"]
        self.assertEqual([f.minuto for f in transcripcion], ["00:00:00", "00:47:12", "01:10:05"])
        self.assertTrue(all(f.nota == "Clase 24 septiembre" and f.fecha == "2026-09-24" for f in fragmentos))
        self.assertFalse(any("Transcripción completa" in f.texto for f in fragmentos))
        self.assertEqual(len({f.id for f in fragmentos}), len(fragmentos))

    def test_clase_en_varias_partes_no_mezcla_partes(self):
        fragmentos = indice.fragmentos_de_nota(NOTA_25, "Diplomado Finanzas/Clase 25 septiembre.md")
        minutos = [f.minuto for f in fragmentos if f.fuente == "transcripcion"]
        self.assertEqual(minutos, ["P1 00:00:00", "P2 00:05:00"])

    def test_partir_respeta_el_maximo_sin_perder_texto(self):
        largo = "\n\n".join(f"Párrafo {i}. " + "palabra " * 40 for i in range(12))
        trozos = indice.partir(largo, maximo=500)
        self.assertTrue(all(len(t) <= 500 for t in trozos))
        self.assertEqual("".join(largo.split()), "".join("".join(trozos).split()))

    @unittest.skipUnless(HAY_RAICES, "requiere snowballstemmer")
    def test_bm25_reduce_las_palabras_a_su_raiz(self):
        self.assertEqual(indice.terminos("Las amortizaciones")[-1], indice.terminos("amortización")[-1])
        self.assertNotIn("las", indice.terminos("Las amortizaciones"))


class TestTranscripcionAparte(Base):
    def test_si_la_nota_no_trae_transcripcion_la_toma_de_la_carpeta_de_trabajo(self):
        sin = NOTA_25.split("> [!quote]-")[0]
        (self.trabajo / "2026-09-25-viernes.txt").write_text("[00:10:00] La TIR iguala el VPN a cero.\n")
        aparte = indice.transcripcion_de_trabajo(self.cfg, sin)
        fragmentos = indice.fragmentos_de_nota(sin, "Diplomado Finanzas/Clase 25 septiembre.md", aparte)
        self.assertIn("00:10:00", [f.minuto for f in fragmentos])


# ---------------------------------------------------------------- despensa (Chroma + BM25)

@unittest.skipUnless(HAY_CHROMA, "requiere chromadb")
class TestDespensa(Base):
    def despensa(self, embebedor=None):
        return indice.Despensa(self.cfg, embebedor or EmbebedorFalso())

    def test_encuentra_por_significado_y_por_siglas(self):
        d = self.despensa()
        resumen, _ = silencio(d.actualizar)
        self.assertEqual((resumen["notas"], resumen["nuevas"]), (2, 2))
        mejor = d.buscar("¿cuánto vale la UMA este año?", k=3)[0][0]
        self.assertIn("UMA", mejor.texto)
        tir = d.buscar("tasa interna de retorno", k=1)[0][0]
        self.assertEqual((tir.nota, tir.minuto), ("Clase 25 septiembre", "P2 00:05:00"))

    def test_solo_vuelve_a_procesar_lo_que_cambio(self):
        embebedor = EmbebedorFalso()
        d = self.despensa(embebedor)
        silencio(d.actualizar)
        antes = embebedor.embebidos
        silencio(d.actualizar)
        self.assertEqual(embebedor.embebidos, antes)
        self.escribir("Clase 25 septiembre", NOTA_25.replace("valor futuro", "valor futuro y anualidades"))
        resumen, _ = silencio(d.actualizar)
        self.assertEqual((resumen["nuevas"], resumen["actualizadas"]), (0, 1))
        self.assertEqual(embebedor.embebidos - antes, len(indice.fragmentos_de_nota(NOTA_25, "x/y.md")))
        (self.vault / "Diplomado Finanzas" / "Clase 24 septiembre.md").unlink()
        resumen, _ = silencio(d.actualizar)
        self.assertEqual(resumen["borradas"], 1)
        self.assertTrue(all(f.nota == "Clase 25 septiembre" for f, _ in d.buscar("UMA ISR", k=8)))

    def test_otro_modelo_de_embeddings_rearma_el_indice(self):
        silencio(self.despensa().actualizar)
        otro = EmbebedorFalso("falso-v2")
        resumen, _ = silencio(self.despensa(otro).actualizar)
        self.assertEqual(resumen["nuevas"], 2)
        self.assertEqual(otro.embebidos, resumen["fragmentos"])

    def test_filtro_por_clase(self):
        d = self.despensa()
        silencio(d.actualizar)
        notas = d.notas_que_coinciden("25 SEPTIEMBRE")
        self.assertEqual(notas, {"Clase 25 septiembre"})
        self.assertEqual(d.notas_que_coinciden("2026-09-24"), {"Clase 24 septiembre"})
        self.assertTrue(all(f.nota == "Clase 25 septiembre" for f, _ in d.buscar("UMA", k=8, notas=notas)))

    def test_no_indexa_el_indice_del_diplomado(self):
        self.escribir("Índice del diplomado", "# Índice\n\n- [[Clase 24 septiembre]]: amortización e ISR\n")
        self.assertEqual(set(self.despensa().archivos()),
                         {"Diplomado Finanzas/Clase 24 septiembre.md", "Diplomado Finanzas/Clase 25 septiembre.md"})


# ---------------------------------------------------------------- preguntas

@unittest.skipUnless(HAY_CHROMA, "requiere chromadb")
class TestPreguntas(Base):
    def preguntar(self, pregunta, cliente, **kwargs):
        d = indice.Despensa(self.cfg, EmbebedorFalso())
        return silencio(preguntas.cmd_pregunta, self.cfg, pregunta, despensa=d, cliente_api=cliente, **kwargs)[1]

    def test_responde_citando_clase_y_minuto(self):
        cl = ClienteFalso([citar("artículo 96", "Se calcula con la tabla del artículo 96 [{id}].")])
        salida = self.preguntar("¿Cómo se calcula el ISR de un salario?", cl)
        self.assertIn("Se calcula con la tabla del artículo 96 [1].", salida)
        self.assertIn("[1] Clase 24 septiembre · minuto 00:47:12 (transcripción)", salida)
        llamada = cl.llamadas[0]
        self.assertEqual(llamada["model"], "claude-haiku-5-5")
        self.assertEqual(llamada["tools"][0]["name"], "buscar_apuntes")
        self.assertEqual(self.registros()[-1]["tarea"], "pregunta")

    def test_si_no_esta_en_los_apuntes_lo_dice(self):
        salida = self.preguntar("¿Qué es un bono cupón cero?", ClienteFalso([mensaje([texto("NO_ENCONTRADO")])]))
        self.assertIn("No encontré eso en tus apuntes.", salida)
        self.assertIn("Lo más cercano que hay en la despensa:", salida)

    def test_una_respuesta_sin_citas_no_se_muestra(self):
        salida = self.preguntar("¿Qué es la UMA?", ClienteFalso([mensaje([texto("Es una unidad de medida del INEGI.")])]))
        self.assertIn("No encontré eso en tus apuntes.", salida)
        self.assertIn("sin citar tus apuntes", salida)
        self.assertNotIn("INEGI", salida)

    def test_las_citas_inventadas_se_quitan(self):
        cl = ClienteFalso([citar("ciento trece", "La UMA vale ciento trece pesos [{id}]. También vale 200 [F99].")])
        salida = self.preguntar("¿Cuánto vale la UMA?", cl)
        self.assertIn("La UMA vale ciento trece pesos [1].", salida)
        self.assertIn("F99", salida.split("Aviso:")[1])
        self.assertNotIn("[F99]", salida.split("Aviso:")[0])

    def test_avisa_cuando_la_respuesta_viene_de_un_agregado_de_claude(self):
        cl = ClienteFalso([citar("tasa no cambia", "La fórmula supone una tasa fija [{id}].")])
        salida = self.preguntar("¿Qué supone la fórmula de anualidad?", cl)
        self.assertIn("(agregado de Claude)", salida)
        self.assertIn("no de lo que dijo el profesor", salida)

    def test_el_agente_busca_otra_vez_con_su_herramienta(self):
        cl = ClienteFalso([mensaje([herramienta("t1", "tasa interna de retorno")], stop="tool_use"),
                           citar("tasa interna", "Se vio después del descanso [{id}].")])
        salida = self.preguntar("¿Qué vimos de la TIR?", cl, k=1)
        self.assertIn("[1] Clase 25 septiembre · minuto P2 00:05:00 (transcripción)", salida)
        segunda = cl.llamadas[1]["messages"]
        self.assertEqual(segunda[1]["role"], "assistant")
        resultado = segunda[2]["content"][0]
        self.assertEqual(resultado["tool_use_id"], "t1")
        self.assertIn('id="F2"', resultado["content"])  # la búsqueda trajo fragmentos nuevos, numerados después
        self.assertNotIn('id="F1"', resultado["content"])
        self.assertEqual(len(self.registros()), 2)
        self.assertIn("1 búsqueda(s) extra", salida)

    def test_el_agente_tiene_un_limite_de_busquedas(self):
        self.cfg["preguntas"] = {"busquedas_extra": 1}
        cl = ClienteFalso([mensaje([herramienta(f"t{i}", "otra cosa")], stop="tool_use") for i in range(3)])
        salida = self.preguntar("¿Algo que no está?", cl)
        self.assertEqual(len(cl.llamadas), 3)
        self.assertTrue(cl.llamadas[2]["messages"][-1]["content"][0]["is_error"])
        self.assertIn("No encontré eso en tus apuntes.", salida)

    def test_en_modo_cli_usa_claude_code_con_haiku(self):
        self.cfg["claude"]["backend"] = "cli"
        carpeta = self.tmp / "bin"
        carpeta.mkdir()
        falso = carpeta / "claude"
        falso.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                         "json.dump({'args': sys.argv[1:], 'stdin': sys.stdin.read()}, "
                         "open(os.environ['CLAUDE_FALSO_LOG'], 'w'))\n"
                         "print('Está en tus apuntes [F1].')\n")
        falso.chmod(0o755)
        bitacora = self.tmp / "claude.json"
        os.environ["CLAUDE_FALSO_LOG"] = str(bitacora)
        self.addCleanup(os.environ.pop, "CLAUDE_FALSO_LOG", None)
        self.addCleanup(os.environ.__setitem__, "PATH", os.environ["PATH"])
        os.environ["PATH"] = f"{carpeta}{os.pathsep}{os.environ['PATH']}"
        salida = self.preguntar("¿Qué es la UMA?", None)
        args = json.loads(bitacora.read_text())["args"]
        self.assertEqual(args[args.index("--model") + 1], "haiku")
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("<reglas>", json.loads(bitacora.read_text())["stdin"])
        self.assertIn("Está en tus apuntes [1].", salida)
        self.assertIn("usa tu suscripción", salida)
        self.assertEqual(self.registros(), [])


# ---------------------------------------------------------------- cuenta y cotización

class TestCuenta(Base):
    def test_la_cuenta_suma_por_modelo_y_por_tarea(self):
        comun.registrar(self.cfg, {"tarea": "clase", "sesion": "Clase 24 septiembre", "modelo": "claude-opus-5-5",
                                   "entrada": 60_000, "salida": 15_000, "costo_usd": 0.54, "segundos": 190,
                                   "primer_token_s": 4.2})
        comun.registrar(self.cfg, {"tarea": "pregunta", "sesion": "¿UMA?", "modelo": "claude-haiku-4-5",
                                   "entrada": 4_000, "salida": 300, "costo_usd": 0.0055, "segundos": 2.5})
        _, salida = silencio(cuenta.cmd_cuenta, self.cfg)
        self.assertIn("Total: $0.55 USD en 2 llamadas.", salida)
        self.assertIn("Por modelo:", salida)
        self.assertIn("primer token en 4.2 s", salida)

    def test_cotiza_cuenta_tokens_por_modelo_y_marca_el_configurado(self):
        cl = ClienteFalso(tokens=60_000)
        _, salida = silencio(cuenta.cmd_cotiza, self.cfg, "24 septiembre", cliente_api=cl)
        self.assertEqual(len(cl.llamadas), 3)
        fila_opus = next(linea for linea in salida.splitlines() if linea.startswith("claude-opus-5-5"))
        self.assertIn("$0.60", fila_opus)       # 60k de entrada y 18k de salida estimada
        self.assertIn("el que usa tu config", fila_opus)
        self.assertIn("los contó la API", salida)

    def test_cotiza_sin_clave_aproxima(self):
        _, salida = silencio(cuenta.cmd_cotiza, self.cfg, "2026-09-25")
        self.assertIn("Sin API key", salida)
        self.assertIn("Clase 25 septiembre", salida)

    def test_cotiza_con_nombre_ambiguo_pide_mas_detalle(self):
        with self.assertRaisesRegex(ErrorExtension, "varias notas"):
            cuenta.cmd_cotiza(self.cfg, "septiembre")


# ---------------------------------------------------------------- con el SDK real (sin red)

def servidor_falso(pedidos):
    """Responde como la API de Mensajes: streaming (SSE), tool_use, texto y conteo de tokens."""
    def manejar(request):
        cuerpo = json.loads(request.content)
        pedidos.append((request.url.path, cuerpo))
        if request.url.path.endswith("/count_tokens"):
            return http.Response(200, json={"input_tokens": 61_240})
        if cuerpo.get("stream"):
            eventos = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "msg_1", "type": "message", "role": "assistant", "model": cuerpo["model"], "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 61_240, "output_tokens": 1}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "## Resumen\n"}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "Amortización."}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                   "usage": {"output_tokens": 18_000}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            sse = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in eventos)
            return http.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        primera = sum(1 for ruta, c in pedidos if not c.get("stream") and not ruta.endswith("count_tokens")) == 1
        if primera:
            contenido = [{"type": "tool_use", "id": "toolu_1", "name": "buscar_apuntes",
                          "input": {"consulta": "tasa interna de retorno"}}]
        else:
            fragmentos = json.dumps(cuerpo["messages"][-1]["content"], ensure_ascii=False)
            clave = re.search(r'id=\\"(F\d+)\\"[^<]*?minuto=\\"P2', fragmentos).group(1)
            contenido = [{"type": "text", "text": f"Se vio después del descanso [{clave}]."}]
        return http.Response(200, json={
            "id": "msg_2", "type": "message", "role": "assistant", "model": cuerpo["model"], "content": contenido,
            "stop_reason": "tool_use" if primera else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 3_000, "output_tokens": 80}})
    return manejar


@unittest.skipUnless(HAY_ANTHROPIC, "requiere el paquete anthropic")
class TestConElSdkReal(Base):
    """Usa anthropic.Anthropic de verdad con un transporte HTTP falso: valida lo que se manda y se recibe."""

    def cliente(self, pedidos):
        return anthropic.Anthropic(api_key="sk-ant-prueba", max_retries=0,
                                   http_client=http.Client(transport=http.MockTransport(servidor_falso(pedidos))))

    def test_nota_en_streaming_con_costo_y_latencia(self):
        pedidos = []
        nota, _ = silencio(notas_api.hacer_apuntes, ENTRADA_REUNION.replace("reunion", "clase"), self.cfg,
                           cliente_api=self.cliente(pedidos))
        self.assertEqual(nota, "## Resumen\nAmortización.")
        ruta, cuerpo = pedidos[0]
        self.assertEqual((ruta, cuerpo["model"], cuerpo["stream"]), ("/v1/messages", "claude-opus-5-5", True))
        registro = self.registros()[-1]
        self.assertAlmostEqual(registro["costo_usd"], 61_240 * 4e-6 + 18_000 * 20e-6)
        self.assertIsNotNone(registro["primer_token_s"])

    def test_cotiza_cuenta_con_el_endpoint_de_tokens(self):
        pedidos = []
        _, salida = silencio(cuenta.cmd_cotiza, self.cfg, "24 septiembre", cliente_api=self.cliente(pedidos))
        self.assertEqual([r for r, _ in pedidos], ["/v1/messages/count_tokens"] * 3)
        self.assertIn("61,240", salida)

    @unittest.skipUnless(HAY_CHROMA, "requiere chromadb")
    def test_el_agente_completo_con_herramienta(self):
        pedidos = []
        d = indice.Despensa(self.cfg, EmbebedorFalso())
        _, salida = silencio(preguntas.cmd_pregunta, self.cfg, "¿Qué vimos de la TIR?", despensa=d, k=1,
                             cliente_api=self.cliente(pedidos))
        self.assertIn("[1] Clase 25 septiembre · minuto P2 00:05:00 (transcripción)", salida)
        segunda = pedidos[1][1]["messages"]
        self.assertEqual(segunda[1]["content"][0]["type"], "tool_use")
        self.assertEqual(segunda[2]["content"][0]["type"], "tool_result")
        self.assertEqual(pedidos[0][1]["tools"][0]["input_schema"]["required"], ["consulta"])


# ---------------------------------------------------------------- subcomandos

class TestSubcomandos(unittest.TestCase):
    def parser_como_el_de_pizza(self):
        p = argparse.ArgumentParser(prog="pizza")
        p.set_defaults(accion="hornea")
        sub = p.add_subparsers(title="comandos", metavar="COMANDO")
        sub.add_parser("menu", aliases=["estado"]).set_defaults(accion="menu")
        return p

    def test_agrega_los_subcomandos_sin_tocar_los_de_siempre(self):
        p = self.parser_como_el_de_pizza()
        cli.registrar_comandos(p)
        args = p.parse_args(["pregunta", "¿qué", "es", "la", "UMA?", "--clase", "24 septiembre"])
        self.assertEqual((args.accion_extension, " ".join(args.pregunta), args.clase),
                         ("pregunta", "¿qué es la UMA?", "24 septiembre"))
        self.assertEqual(p.parse_args(["costos"]).accion_extension, "cuenta")
        menu = p.parse_args(["menu"])
        self.assertEqual(menu.accion, "menu")
        self.assertIsNone(getattr(menu, "accion_extension", None))

    def test_un_parser_sin_subcomandos_no_falla(self):
        cli.registrar_comandos(argparse.ArgumentParser())


if __name__ == "__main__":
    unittest.main()
