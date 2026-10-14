"""Pruebas de apuntes.py. No descargan el modelo de Whisper ni llaman a Claude de verdad:
usan un modelo falso y un `claude` falso que se ponen en su lugar.

Correr:  .venv/bin/python -m unittest discover -s pruebas -v
"""

import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apuntes  # noqa: E402

Seg = namedtuple("Seg", "start end text")
Info = namedtuple("Info", "duration")

PARTE_1 = [
    Seg(0.0, 4.0, " Buenas noches, hoy vemos amortización."),
    Seg(4.2, 9.0, " La tabla tiene pago, interés y capital."),
    Seg(9.1, 12.0, " Subtítulos realizados por la comunidad de Amara.org"),
]
PARTE_2 = [Seg(1.0, 6.0, " Regresando del descanso: el ISR de los salarios.")]

NOTA_CLAUDE = """## Índice rápido
- [[#Amortización]]: tabla de pagos

## Desarrollo y práctica
### Amortización
Desde [P1 00:00:00]
El [[Sistema francés de amortización|sistema francés]] usa [[Saldo insoluto]].

### ISR de salarios
Desde [P2 00:00:01]
Se usa la [[Tarifa del artículo 96]].

## Glosario
- **[[Saldo insoluto]]**: lo que todavía se debe.
- **[[Sistema francés de amortización]]**: *pago constante y abono creciente.*
- **[[Tarifa del artículo 96]]**: tabla para el ISR mensual.
"""

CLAUDE_FALSO = f"""#!/usr/bin/env python3
import json, os, sys
entrada = sys.stdin.read()
with open(os.environ["CLAUDE_FALSO_LOG"], "w") as f:
    json.dump({{"args": sys.argv[1:], "stdin": entrada,
               "max_tokens": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")}}, f)
if os.environ.get("CLAUDE_FALSO_FALLA"):
    print("Not logged in", file=sys.stderr)
    sys.exit(1)
if os.environ.get("CLAUDE_FALSO_CORTA"):
    print("## Índice rápido\\nAPI Error: Claude's response exceeded the 32000 output token maximum.")
    sys.exit(0)
print("```markdown\\n" + {NOTA_CLAUDE!r} + "```")
"""


class ModeloFalso:
    """Imita WhisperModel. Revisa que las opciones existan en el transcribe() real."""

    def __init__(self):
        from faster_whisper import WhisperModel
        self.validas = set(inspect.signature(WhisperModel.transcribe).parameters)
        self.transcritos: list[str] = []

    def transcribe(self, audio, **opciones):
        desconocidas = set(opciones) - self.validas
        if desconocidas:
            raise TypeError(f"opciones que faster-whisper no conoce: {desconocidas}")
        self.transcritos.append(Path(audio).name)
        self.ultimas_opciones = opciones
        segs = PARTE_2 if "2140" in audio else PARTE_1
        return iter(segs), Info(segs[-1].end + 5)


class ModeloQueNoDebeUsarse:
    def transcribe(self, *a, **k):
        raise AssertionError("se volvió a transcribir un video ya transcrito")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for d in ("videos", "otros", "trabajo", "vault", "bin"):
            (self.tmp / d).mkdir()
        self.config = self.tmp / "config.toml"
        self.escribir_config(activar=True)
        self.cfg = apuntes.cargar_config(self.config)
        falso = self.tmp / "bin" / "claude"
        falso.write_text(CLAUDE_FALSO)
        falso.chmod(0o755)
        self.log = self.tmp / "claude_log.json"
        entorno = {"PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "CLAUDE_FALSO_LOG": str(self.log)}
        parche = unittest.mock.patch.dict(os.environ, entorno)
        parche.start()
        self.addCleanup(parche.stop)
        self.salida = io.StringIO()
        silencio = contextlib.redirect_stdout(self.salida)
        silencio.__enter__()
        self.addCleanup(silencio.__exit__, None, None, None)

    def escribir_config(self, activar: bool):
        self.config.write_text(f"""
[carpetas]
videos = "{self.tmp / 'videos'}"
trabajo = "{self.tmp / 'trabajo'}"
vault = "{self.tmp / 'vault'}"
[whisper]
vocabulario = ["ISR", "amortización"]
[claude]
activar = {'true' if activar else 'false'}
""")

    def video(self, nombre="Screen_Recording_20260924_183012_Zoom.mp4", carpeta="videos") -> Path:
        v = self.tmp / carpeta / nombre
        v.write_bytes(b"no es un video real " + nombre.encode())
        return v

    def dos_partes(self):
        return self.video(), self.video("Screen_Recording_20260924_214005_Zoom.mp4")

    def registro(self):
        return apuntes.Registro(self.tmp / "trabajo" / "registro.json")

    def nota(self, titulo="Clase 24 septiembre", carpeta="Diplomado Finanzas") -> Path:
        return self.tmp / "vault" / carpeta / f"{titulo}.md"

    def concepto(self, nombre) -> Path:
        return self.tmp / "vault" / "Conceptos" / f"{nombre}.md"

    def procesar(self, modelo=None, cfg=None):
        return apuntes.cmd_procesar(cfg or self.cfg, apuntes.Cargador(cfg or self.cfg, modelo or ModeloFalso()))

    def llamada_claude(self) -> dict:
        return json.loads(self.log.read_text())


class TestSesiones(Base):
    def test_titulo_sale_de_la_fecha_del_archivo(self):
        self.video()
        (s,) = apuntes.sesiones_diplomado(self.cfg, self.registro())
        self.assertEqual(s.titulo, "Clase 24 septiembre")

    def test_videos_del_mismo_dia_son_una_clase_en_orden(self):
        a, b = self.dos_partes()
        (s,) = apuntes.sesiones_diplomado(self.cfg, self.registro())
        self.assertEqual(s.videos, [a, b])

    def test_grabacion_despues_de_medianoche_es_de_la_clase_anterior(self):
        self.video()
        self.video("Screen_Recording_20260925_004500_Zoom.mp4")
        sesiones = apuntes.sesiones_diplomado(self.cfg, self.registro())
        self.assertEqual([len(s.videos) for s in sesiones], [2])

    def test_jueves_y_viernes_son_dos_clases(self):
        self.video()
        self.video("Screen_Recording_20260925_183000_Zoom.mp4")
        titulos = [s.titulo for s in apuntes.sesiones_diplomado(self.cfg, self.registro())]
        self.assertEqual(titulos, ["Clase 24 septiembre", "Clase 25 septiembre"])

    def test_sin_fecha_en_el_nombre_usa_la_del_archivo(self):
        v = self.video("clase.mp4")
        os.utime(v, (1790283600, 1790283600))  # 2026-09-24 alrededor de las 20 h
        (s,) = apuntes.sesiones_diplomado(self.cfg, self.registro())
        self.assertTrue(s.titulo.startswith("Clase 2"))

    def test_mismo_titulo_otro_año(self):
        reg = self.registro()
        reg.sesiones["2025-09-24"] = {"titulo": "Clase 24 septiembre"}
        self.assertEqual(reg.titulo_libre("Clase 24 septiembre", "2026-09-24", 2026), "Clase 24 septiembre 2026")

    def test_buscar_por_fecha_o_por_nombre(self):
        reg = self.registro()
        reg.sesiones["2026-09-24"] = {"titulo": "Clase 24 septiembre"}
        self.assertEqual(reg.buscar("24 septiembre"), ["2026-09-24"])
        self.assertEqual(reg.buscar("2026-09-24"), ["2026-09-24"])


class TestTexto(Base):
    def test_frases_fantasma(self):
        self.assertTrue(apuntes.es_fantasma("Subtítulos realizados por la comunidad de Amara.org"))
        self.assertTrue(apuntes.es_fantasma("¡Suscríbete!"))
        self.assertFalse(apuntes.es_fantasma("Gracias, sigamos con la tabla de amortización."))

    def test_parrafos_por_pausas_con_marca_de_tiempo(self):
        segs = [(0, 2, "Uno."), (2.1, 4, "Dos."), (10, 12, "Tres.")]
        self.assertEqual(apuntes.en_parrafos(segs), ["[00:00:00] Uno. Dos.", "[00:00:10] Tres."])
        self.assertEqual(apuntes.en_parrafos(segs, "P2 ")[1], "[P2 00:00:10] Tres.")

    def test_texto_de_sesion_marca_las_partes(self):
        partes = [{"inicio": "2026-09-24T18:30:12", "segmentos": [(0, 1, "Hola.")]},
                  {"inicio": "2026-09-24T21:40:05", "segmentos": [(5, 6, "Seguimos.")]}]
        self.assertEqual(apuntes.texto_de_sesion(partes), [
            "=== Parte 1 de 2 · grabación iniciada a las 18:30 ===", "[P1 00:00:00] Hola.",
            "=== Parte 2 de 2 · grabación iniciada a las 21:40 ===", "[P2 00:00:05] Seguimos."])
        self.assertEqual(apuntes.texto_de_sesion(partes[:1]), ["[00:00:00] Hola."])

    def test_srt(self):
        self.assertEqual(apuntes.a_srt([(3661.5, 3662.25, "Hola")]), "1\n01:01:01,500 --> 01:01:02,250\nHola\n")

    def test_limpia_bloque_de_codigo(self):
        self.assertEqual(apuntes.limpiar_respuesta("```markdown\n## A\nb\n```"), "## A\nb")
        self.assertEqual(apuntes.limpiar_respuesta("## A\n```mermaid\nx\n```\nb"), "## A\n```mermaid\nx\n```\nb")

    def test_conceptos_del_glosario_y_de_los_enlaces(self):
        conceptos = apuntes.conceptos_de_nota(NOTA_CLAUDE + "\nVer [[Clase 18 septiembre]] y [[#Amortización]].",
                                              {"Clase 24 septiembre"})
        self.assertEqual(conceptos, {
            "Saldo insoluto": "lo que todavía se debe.",
            "Sistema francés de amortización": "*pago constante y abono creciente.*",
            "Tarifa del artículo 96": "tabla para el ISR mensual.",
        })

    def test_temas(self):
        self.assertEqual(apuntes.temas_de_nota(NOTA_CLAUDE), ["Amortización", "ISR de salarios"])


class TestDiplomado(Base):
    def test_flujo_completo_con_dos_partes(self):
        self.dos_partes()
        self.assertEqual(self.procesar(), 0)

        txt = (self.tmp / "trabajo" / "sesiones" / "Clase 24 septiembre.txt").read_text()
        self.assertIn("=== Parte 2 de 2 · grabación iniciada a las 21:40 ===", txt)
        self.assertIn("[P1 00:00:00] Buenas noches, hoy vemos amortización. La tabla", txt)
        self.assertNotIn("Amara", txt)
        self.assertTrue((self.tmp / "trabajo" / "transcripciones" / "Screen_Recording_20260924_214005_Zoom.srt").exists())

        nota = self.nota().read_text()
        self.assertTrue(nota.startswith("---\nfecha: 2026-09-24\ndia: jueves\ntipo: clase\n"))
        self.assertIn("partes: 2\n", nota)
        self.assertIn("cssclasses: [apuntes-claude]", nota)
        self.assertIn("2 partes", nota)
        self.assertIn("[[Índice del diplomado]]", nota)
        self.assertIn("## Glosario\n- **[[Saldo insoluto]]**", nota)
        self.assertNotIn("```markdown", nota)
        self.assertIn("> [!quote]- Transcripción completa\n> **Parte 1 de 2** · grabación iniciada a las 18:30", nota)

        llamada = self.llamada_claude()
        self.assertEqual(llamada["args"][:2], ["-p", apuntes.INSTRUCCION_CORTA])
        self.assertEqual(llamada["args"][llamada["args"].index("--tools") + 1], "")
        self.assertEqual(llamada["args"][llamada["args"].index("--model") + 1], "sonnet")
        self.assertEqual(llamada["max_tokens"], "64000")
        entrada = llamada["stdin"]
        self.assertTrue(entrada.startswith('<transcripcion titulo="Clase 24 septiembre"'))
        self.assertIn('partes="2"', entrada)
        self.assertLess(entrada.index("</transcripcion>"), entrada.index("<instrucciones>"))
        self.assertIn("una clase del Diplomado en finanzas", entrada)
        self.assertNotIn("{descripcion}", entrada)

    def test_crea_paginas_de_concepto_sin_pisar_las_existentes(self):
        (self.tmp / "vault" / "Conceptos").mkdir()
        self.concepto("saldo insoluto").write_text("Mi página, escrita a mano")
        self.video()
        self.procesar()
        self.assertEqual(self.concepto("saldo insoluto").read_text(), "Mi página, escrita a mano")
        self.assertFalse(self.concepto("Saldo insoluto").exists() and
                         self.concepto("Saldo insoluto").read_text() != "Mi página, escrita a mano")
        frances = self.concepto("Sistema francés de amortización").read_text()
        self.assertIn("> [!claude] Definición inicial (escrita por Claude)\n> pago constante y abono creciente.", frances)
        self.assertIn("## Primera mención\n[[Clase 24 septiembre]]", frances)
        tarifa = self.concepto("Tarifa del artículo 96").read_text()
        self.assertIn("> [!quote] Definición dada en clase\n> tabla para el ISR mensual.", tarifa)
        self.assertIn("saldo insoluto", self.llamada_claude()["stdin"].split("<conceptos_existentes>")[1])

    def test_indice_del_diplomado(self):
        self.video()
        self.video("Screen_Recording_20260925_183000_Zoom.mp4")
        self.procesar()
        indice = (self.tmp / "vault" / "Diplomado Finanzas" / "Índice del diplomado.md").read_text()
        self.assertLess(indice.index("[[Clase 25 septiembre]] · viernes"), indice.index("[[Clase 24 septiembre]] · jueves"))
        self.assertIn("[[Clase 24 septiembre#Amortización|Amortización]]", indice)
        self.assertIn("**Conceptos:** [[Saldo insoluto]]", indice)

    def test_segunda_vez_no_repite_nada(self):
        self.video()
        self.procesar()
        self.log.unlink()
        self.assertEqual(self.procesar(ModeloQueNoDebeUsarse()), 0)
        self.assertFalse(self.log.exists(), "no debió llamar a Claude otra vez")
        self.assertIn("No hay clases pendientes", self.salida.getvalue())

    def test_parte_nueva_rehace_la_nota_con_todas(self):
        self.video()
        self.procesar()
        self.nota().write_text(self.nota().read_text() + "\nMis comentarios\n")
        self.video("Screen_Recording_20260924_214005_Zoom.mp4")
        modelo = ModeloFalso()
        self.procesar(modelo)
        self.assertEqual(modelo.transcritos, ["Screen_Recording_20260924_214005_Zoom.mp4"])
        self.assertIn("partes: 2", self.nota().read_text())
        (respaldo,) = (self.tmp / "trabajo" / "respaldos").iterdir()
        self.assertIn("Mis comentarios", respaldo.read_text())

    def test_si_claude_falla_se_conserva_la_transcripcion_y_se_reintenta(self):
        self.video()
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_FALSO_FALLA": "1"}):
            self.assertEqual(self.procesar(), 1)
        self.assertFalse(self.nota().exists())
        self.assertIn("Not logged in", self.registro().sesiones["2026-09-24"]["error"])
        self.assertEqual(self.procesar(ModeloQueNoDebeUsarse()), 0)
        self.assertIn("[[Saldo insoluto]]", self.nota().read_text())
        self.assertNotIn("error", self.registro().sesiones["2026-09-24"])

    def test_nota_cortada_por_largo_es_error(self):
        self.video()
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_FALSO_CORTA": "1"}):
            self.assertEqual(self.procesar(), 1)
        self.assertFalse(self.nota().exists())
        self.assertIn("max_tokens_salida", self.registro().sesiones["2026-09-24"]["error"])

    def test_sin_claude_y_luego_con_claude(self):
        self.video()
        self.escribir_config(activar=False)
        self.procesar(cfg=apuntes.cargar_config(self.config))
        self.assertIn("apuntes: pendientes", self.nota().read_text())
        self.escribir_config(activar=True)
        self.procesar(ModeloQueNoDebeUsarse(), cfg=apuntes.cargar_config(self.config))
        self.assertIn("apuntes: claude", self.nota().read_text())
        self.assertEqual(len(list((self.tmp / "trabajo" / "respaldos").iterdir())), 1)

    def test_rehacer_por_nombre_guarda_la_anterior(self):
        self.video()
        self.procesar()
        self.nota().write_text(self.nota().read_text() + "\nMis comentarios a mano\n")
        cargador = apuntes.Cargador(self.cfg, ModeloQueNoDebeUsarse())
        self.assertEqual(apuntes.cmd_rehacer(self.cfg, "24 septiembre", cargador=cargador), 0)
        (respaldo,) = (self.tmp / "trabajo" / "respaldos").iterdir()
        self.assertIn("Mis comentarios a mano", respaldo.read_text())
        self.assertNotIn("Mis comentarios a mano", self.nota().read_text())

    def test_rehacer_con_transcribir(self):
        self.video()
        self.procesar()
        modelo = ModeloFalso()
        apuntes.cmd_rehacer(self.cfg, "2026-09-24", retranscribir=True, cargador=apuntes.Cargador(self.cfg, modelo))
        self.assertEqual(len(modelo.transcritos), 1)

    def test_vocabulario_va_a_whisper(self):
        self.video()
        modelo = ModeloFalso()
        self.procesar(modelo)
        self.assertIn("ISR, amortización", modelo.ultimas_opciones["initial_prompt"])
        self.assertTrue(modelo.ultimas_opciones["vad_filter"])
        self.assertEqual(modelo.ultimas_opciones["language"], "es")

    def test_vault_inexistente_da_error_claro(self):
        self.cfg["carpetas"]["vault"] = str(self.tmp / "no-existe")
        with self.assertRaisesRegex(apuntes.ErrorApuntes, "vault de Obsidian"):
            apuntes.cmd_procesar(self.cfg)


class TestSuelto(Base):
    def test_reunion_en_dos_partes(self):
        a = self.video("junta_1.mp4", "otros")
        b = self.video("junta_2.mp4", "otros")
        os.utime(b, (1790300000, 1790300000))
        os.utime(a, (1790290000, 1790290000))
        modelo = ModeloFalso()
        codigo = apuntes.cmd_suelto(self.cfg, [str(b), str(a)], "reunion", "Junta de equipo", None,
                                    cargador=apuntes.Cargador(self.cfg, modelo))
        self.assertEqual(codigo, 0)
        self.assertEqual(modelo.transcritos, ["junta_1.mp4", "junta_2.mp4"])
        self.assertIsNone(modelo.ultimas_opciones["initial_prompt"], "el vocabulario del diplomado no aplica")
        nota = self.nota("Junta de equipo", "Notas sueltas").read_text()
        self.assertIn("tipo: reunion", nota)
        self.assertNotIn("Índice del diplomado", nota)
        self.assertIn("una reunión", self.llamada_claude()["stdin"])
        self.assertIn("## Pendientes", self.llamada_claude()["stdin"])
        self.assertFalse((self.tmp / "vault" / "Diplomado Finanzas" / "Índice del diplomado.md").exists())

    def test_clase_suelta_con_materia_y_repetir(self):
        v = self.video("Screen_Recording_20261003_100000.mp4", "otros")
        cargador = apuntes.Cargador(self.cfg, ModeloFalso())
        apuntes.cmd_suelto(self.cfg, [str(v)], "clase", None, "estadística", cargador=cargador)
        self.assertTrue(self.nota("Clase de estadística 3 octubre", "Notas sueltas").exists())
        self.assertIn("una clase de estadística", self.llamada_claude()["stdin"])
        self.log.unlink()
        apuntes.cmd_suelto(self.cfg, [str(v)], "clase", None, "estadística", cargador=cargador)
        self.assertFalse(self.log.exists())
        self.assertIn("ya existe", self.salida.getvalue())

    def test_no_acepta_videos_de_la_carpeta_del_diplomado(self):
        v = self.video()
        with self.assertRaisesRegex(apuntes.ErrorApuntes, "carpeta del diplomado"):
            apuntes.cmd_suelto(self.cfg, [str(v)], "clase", None, None)

    def test_mismo_titulo_con_otros_videos(self):
        a = self.video("a.mp4", "otros")
        b = self.video("b.mp4", "otros")
        apuntes.cmd_suelto(self.cfg, [str(a)], "reunion", "Junta", None, cargador=apuntes.Cargador(self.cfg, ModeloFalso()))
        with self.assertRaisesRegex(apuntes.ErrorApuntes, "otro --titulo"):
            apuntes.cmd_suelto(self.cfg, [str(b)], "reunion", "Junta", None)


@unittest.skipUnless(shutil.which("ffmpeg"), "necesita ffmpeg")
class TestAudioReal(Base):
    def crear_mp4(self, segundos=5) -> Path:
        destino = self.tmp / "videos" / "Screen_Recording_20260924_203554_Zoom.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={segundos}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}",
                        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(destino)], check=True)
        return destino

    def test_faster_whisper_lee_el_audio_del_mp4(self):
        from faster_whisper import decode_audio
        audio = decode_audio(str(self.crear_mp4(5)))
        self.assertAlmostEqual(len(audio) / 16000, 5, delta=0.2)

    def test_prueba_de_velocidad(self):
        self.crear_mp4(8)
        with unittest.mock.patch.object(apuntes, "cargar_modelo", side_effect=lambda n, c: ModeloFalso()):
            self.assertEqual(apuntes.cmd_prueba(self.cfg, None, ["small", "turbo"], 0.1), 0)
        self.assertIn("2 H DE GRABACIÓN TARDARÍAN", self.salida.getvalue())
        self.assertTrue((self.tmp / "trabajo" / "prueba" / "turbo.txt").exists())


class TestCLI(Base):
    def test_estado(self):
        self.dos_partes()
        with unittest.mock.patch.object(apuntes, "ARCHIVO_CONFIG", self.config):
            self.assertEqual(apuntes.main(["estado"]), 0)
        self.assertIn("Clase 24 septiembre", self.salida.getvalue())
        self.assertIn("pendiente", self.salida.getvalue())

    def test_sin_config(self):
        with self.assertRaisesRegex(apuntes.ErrorApuntes, "config.ejemplo.toml"):
            apuntes.cargar_config(self.tmp / "no-hay.toml")


class TestQuitarYVolverAMeter(Base):
    def test_quitar_todos_los_videos_no_cambia_nada(self):
        a, b = self.dos_partes()
        self.procesar()
        nota = self.nota().read_text()
        a.unlink(); b.unlink()
        self.log.unlink()
        self.assertEqual(self.procesar(ModeloQueNoDebeUsarse()), 0)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.nota().read_text(), nota)
        indice = (self.tmp / "vault" / "Diplomado Finanzas" / "Índice del diplomado.md").read_text()
        self.assertIn("[[Clase 24 septiembre]]", indice)

    def test_quitar_una_parte_no_rehace_la_nota_incompleta(self):
        a, b = self.dos_partes()
        self.procesar()
        a.unlink()
        self.log.unlink()
        self.assertEqual(self.procesar(ModeloQueNoDebeUsarse()), 0)
        self.assertFalse(self.log.exists(), "no debió rehacer la nota")
        (s,) = apuntes.sesiones_diplomado(self.cfg, self.registro())
        self.assertEqual([v.name for v in s.videos], [a.name, b.name])

    def test_volver_a_meter_el_mismo_video_no_hace_otra_nota(self):
        a = self.video()
        self.procesar()
        contenido = a.read_bytes()
        a.unlink()
        a.write_bytes(contenido)
        self.log.unlink()
        self.assertEqual(self.procesar(ModeloQueNoDebeUsarse()), 0)
        self.assertFalse(self.log.exists())
        notas = list((self.tmp / "vault" / "Diplomado Finanzas").glob("Clase*.md"))
        self.assertEqual([n.name for n in notas], ["Clase 24 septiembre.md"])

    def test_parte_nueva_despues_de_quitar_otra_usa_todas(self):
        a, b = self.dos_partes()
        self.procesar()
        a.unlink()
        modelo = ModeloFalso()
        self.video("Screen_Recording_20260924_230000_Zoom.mp4")
        self.procesar(modelo)
        self.assertEqual(modelo.transcritos, ["Screen_Recording_20260924_230000_Zoom.mp4"])
        self.assertIn("partes: 3", self.nota().read_text())
        self.assertIn("=== Parte 1 de 3", self.llamada_claude()["stdin"])

    def test_rehacer_sin_el_video(self):
        a = self.video()
        self.procesar()
        a.unlink()
        cargador = apuntes.Cargador(self.cfg, ModeloQueNoDebeUsarse())
        self.assertEqual(apuntes.cmd_rehacer(self.cfg, "24 septiembre", cargador=cargador), 0)
        self.assertEqual(len(list((self.tmp / "trabajo" / "respaldos").iterdir())), 1)


@unittest.skipUnless(shutil.which("ffmpeg"), "necesita ffmpeg")
class TestTramos(Base):
    """Las grabaciones largas se transcriben por tramos cortados en silencios."""

    def crear_audio(self, segundos=60) -> Path:
        # 5 s de tono y 1 s de silencio, repetido: hay silencios en 5.5, 11.5, 17.5, ...
        destino = self.tmp / "otros" / "largo.m4a"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"aevalsrc=if(lt(mod(t\\,6)\\,5)\\,0.5*sin(2*PI*440*t)\\,0):s=16000:d={segundos}",
                        "-c:a", "aac", str(destino)], check=True)
        return destino

    def test_duracion_sin_decodificar(self):
        self.assertAlmostEqual(apuntes.duracion_audio(self.crear_audio(60)), 60, delta=0.5)
        self.assertIsNone(apuntes.duracion_audio(self.video()))

    def test_corta_en_silencios(self):
        audio = self.crear_audio(60)
        cortes = apuntes.puntos_de_corte(audio, 60.0, 15.0)
        self.assertEqual(cortes[0], 0.0)
        self.assertEqual(cortes[-1], 60.0)
        for c in cortes[1:-1]:
            self.assertAlmostEqual(c % 6, 5.5, delta=0.3, msg=f"el corte {c} no cae en un silencio")

    def test_transcribe_por_tramos_con_tiempos_correctos(self):
        audio = self.crear_audio(60)

        class PorTramos:
            def __init__(self):
                self.muestras = []

            def transcribe(self, entrada, **opciones):
                self.muestras.append(len(entrada))
                return iter([Seg(0.5, 2.0, " hola")]), Info(len(entrada) / 16000)

        modelo = PorTramos()
        self.cfg["whisper"]["minutos_por_tramo"] = 0.25  # tramos de 15 s
        segmentos, total = apuntes.transcribir(audio, self.cfg, modelo)
        cortes = apuntes.puntos_de_corte(audio, total, 15.0)
        self.assertGreaterEqual(len(modelo.muestras), 3)
        self.assertAlmostEqual(sum(modelo.muestras) / 16000, 60, delta=0.5)
        self.assertEqual([s[0] for s in segmentos], [round(c + 0.5, 2) for c in cortes[:-1]])

    def test_audio_corto_va_completo(self):
        audio = self.crear_audio(20)
        modelo = ModeloFalso()
        modelo.transcribe = lambda entrada, **k: (iter([Seg(0, 1, " x")]), Info(20)) if isinstance(entrada, str) else self.fail("se partió")
        apuntes.transcribir(audio, self.cfg, modelo)


class TestComandosDePizzeria(Base):
    def correr(self, *argv):
        llamadas = {}
        def registrar(nombre):
            return lambda *a, **k: llamadas.setdefault(nombre, a) and 0
        parches = [unittest.mock.patch.object(apuntes, f, registrar(f)) for f in
                   ("cmd_procesar", "cmd_estado", "cmd_suelto", "cmd_rehacer", "cmd_prueba", "cmd_vigilar")]
        with unittest.mock.patch.object(apuntes, "ARCHIVO_CONFIG", self.config):
            for parche in parches:
                parche.start()
            try:
                self.assertEqual(apuntes.main(list(argv)), 0)
            finally:
                for parche in parches:
                    parche.stop()
        return llamadas

    def test_nombres_nuevos_y_de_siempre(self):
        casos = {
            (): "cmd_procesar", ("hornea",): "cmd_procesar", ("procesar",): "cmd_procesar",
            ("make", "pizza"): "cmd_vigilar", ("make", "pizza", "--sin-localsend"): "cmd_vigilar",
            ("menu",): "cmd_estado", ("estado",): "cmd_estado",
            ("pedido", "a.mp4"): "cmd_suelto", ("suelto", "a.mp4"): "cmd_suelto",
            ("recalienta", "24 septiembre"): "cmd_rehacer", ("rehacer", "24 septiembre"): "cmd_rehacer",
            ("cata",): "cmd_prueba", ("prueba",): "cmd_prueba",
            ("turno",): "cmd_vigilar", ("vigilar",): "cmd_vigilar",
        }
        for argv, esperada in casos.items():
            with self.subTest(argv=argv):
                self.assertEqual(list(self.correr(*argv)), [esperada])

    def test_recalienta_con_transcribir(self):
        llamadas = self.correr("recalienta", "24 septiembre", "--transcribir")
        self.assertEqual(llamadas["cmd_rehacer"][1:], ("24 septiembre", True))


class TestLocalSend(Base):
    def setUp(self):
        super().setUp()
        parche = unittest.mock.patch.object(apuntes.subprocess, "Popen")
        self.abierto = parche.start()
        self.addCleanup(parche.stop)

    def con_pantalla(self):
        return unittest.mock.patch.dict(os.environ, {"DISPLAY": ":0"})

    def test_abre_el_comando_configurado(self):
        self.cfg["localsend"]["comando"] = "~/Applications/LocalSend.AppImage --hidden"
        with self.con_pantalla(), unittest.mock.patch.object(apuntes, "localsend_abierto", return_value=False):
            apuntes.abrir_localsend(self.cfg)
        comando = self.abierto.call_args.args[0]
        self.assertEqual(comando, [str(Path.home() / "Applications/LocalSend.AppImage"), "--hidden"])
        self.assertTrue(self.abierto.call_args.kwargs["start_new_session"])

    def test_lo_encuentra_instalado(self):
        programa = self.tmp / "bin" / "localsend_app"
        programa.write_text("#!/bin/sh\n")
        programa.chmod(0o755)
        self.assertEqual(apuntes.comando_localsend(""), [str(programa)])

    def test_no_lo_abre_dos_veces(self):
        with self.con_pantalla(), unittest.mock.patch.object(apuntes, "localsend_abierto", return_value=True):
            apuntes.abrir_localsend(self.cfg)
        self.abierto.assert_not_called()
        self.assertIn("ya está abierto", self.salida.getvalue())

    def test_sin_pantalla_no_lo_abre(self):
        with unittest.mock.patch.dict(os.environ, {}):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            apuntes.abrir_localsend(self.cfg)
        self.abierto.assert_not_called()

    def test_si_no_esta_instalado_avisa(self):
        with self.con_pantalla(), unittest.mock.patch.object(apuntes, "localsend_abierto", return_value=False), \
             unittest.mock.patch.object(apuntes, "comando_localsend", return_value=None):
            apuntes.abrir_localsend(self.cfg)
        self.abierto.assert_not_called()
        self.assertIn("No encontré LocalSend", self.salida.getvalue())

    def test_turno_y_sin_localsend(self):
        with unittest.mock.patch.object(apuntes, "ARCHIVO_CONFIG", self.config), \
             unittest.mock.patch.object(apuntes, "cmd_vigilar", return_value=0) as vigilar:
            apuntes.main(["turno"])
            self.assertTrue(vigilar.call_args.kwargs["abrir_ls"])
            apuntes.main(["turno", "--sin-localsend"])
            self.assertFalse(vigilar.call_args.kwargs["abrir_ls"])


if __name__ == "__main__":
    unittest.main()
