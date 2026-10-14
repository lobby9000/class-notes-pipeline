#!/usr/bin/env python3
"""pizza: transcribe tus clases y reuniones y las convierte en notas de Obsidian.

Uso (los nombres de la izquierda son los de la pizzería; los de siempre también funcionan):
  pizza                 (procesar)   hornea las clases pendientes del diplomado
  lets make pizza                    abre la pizzería: lo mismo que `pizza turno`
  pizza menu            (estado)     qué clases y notas sueltas ya están listas
  pizza pedido VIDEO... (suelto)     una clase o reunión aparte; varios videos = una sola sesión
      --tipo clase|reunion   --titulo "Junta de equipo"   --materia "estadística"
  pizza recalienta CLASE (rehacer)   regenera una nota, ej. pizza recalienta "24 septiembre"
      --transcribir                  también rehace la transcripción (por ejemplo, con otro modelo)
  pizza cata [VIDEO]    (prueba)     mide qué tan rápido corre cada modelo de Whisper en tu laptop
  pizza turno           (vigilar)    abre LocalSend y se queda esperando pedidos: hornea cada clase nueva
      --sin-localsend                no abrir LocalSend

El comando `apuntes` sigue existiendo y acepta los mismos subcomandos.

Diplomado: todos los videos de un mismo día (grabación cortada, pausa en el descanso)
forman una sola clase y producen una sola nota, "Clase 24 septiembre".

Flujo de cada sesión:
  1. Whisper (faster-whisper) transcribe cada video en tu CPU (se guarda en caché por video)
  2. Las partes se juntan en una transcripción con marcas [P2 00:12:30]
  3. `claude -p` escribe la nota siguiendo prompts/clase.md o prompts/reunion.md
  4. Se crean las páginas de los conceptos nuevos y se actualiza el índice del diplomado

Cada paso se salta si ya está hecho, así que puedes correr `pizza` cuantas veces quieras.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ARCHIVO_CONFIG = RAIZ / "config.toml"

EXTENSIONES = {".mp4", ".mkv", ".mov", ".webm", ".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
PROHIBIDOS_EN_NOMBRE = set('\\/:*?"<>|#^[]')

# Frases que Whisper a veces "inventa" en los silencios (vienen de subtítulos de YouTube
# con los que se entrenó). Si un segmento es solo esto, se descarta.
FRASES_FANTASMA = {
    "subtítulos realizados por la comunidad de amaraorg",
    "subtítulos por la comunidad de amaraorg",
    "suscríbete",
    "suscríbete al canal",
    "gracias por ver el video",
    "gracias por ver",
    "gracias por su atención",
}

CONFIG_BASE = {
    "carpetas": {
        "videos": "~/Videos/raw_finance",
        "trabajo": "~/Videos/apuntes_finance",
        "vault": "",
        "notas": "Diplomado Finanzas",
        "sueltas": "Notas sueltas",
        "conceptos": "Conceptos",
    },
    "whisper": {
        "modelo": "small",
        "idioma": "es",
        "precision": "int8",
        "hilos": 0,
        "beam_size": 5,
        "saltar_silencios": True,
        "usar_contexto": True,
        "minutos_por_tramo": 10,
        "vocabulario": [],
    },
    "claude": {
        "activar": True,
        "modelo": "sonnet",
        "prompt_clase": "prompts/clase.md",
        "prompt_reunion": "prompts/reunion.md",
        "tiempo_maximo": 30,
        "max_tokens_salida": 64000,
    },
    "diplomado": {
        "curso": "Diplomado en finanzas",
        "materia": "finanzas",
        "titulo": "Clase {dia} {mes}",
        "indice": "Índice del diplomado",
        "etiquetas": ["diplomado", "clase"],
        "corte_madrugada": 5,
        "espera_minutos": 10,
    },
    "notas": {
        "incluir_transcripcion": True,
        "crear_conceptos": True,
    },
    "localsend": {
        "abrir": True,
        "comando": "",
    },
}


class ErrorApuntes(Exception):
    """Error con un mensaje pensado para mostrarse tal cual al usuario."""


# ---------------------------------------------------------------- configuración

def cargar_config(ruta: Path | None = None) -> dict:
    ruta = ruta or ARCHIVO_CONFIG
    if not ruta.exists():
        raise ErrorApuntes(
            f"No encuentro {ruta}.\n"
            f"Cópialo de la plantilla y ajústalo:  cp {RAIZ / 'config.ejemplo.toml'} {ruta}"
        )
    with open(ruta, "rb") as f:
        propio = tomllib.load(f)
    cfg = {seccion: dict(valores) for seccion, valores in CONFIG_BASE.items()}
    for seccion, valores in propio.items():
        cfg.setdefault(seccion, {}).update(valores)
    return cfg


def carpeta(cfg: dict, clave: str) -> Path:
    return Path(os.path.expanduser(cfg["carpetas"][clave]))


def vault(cfg: dict) -> Path:
    texto = cfg["carpetas"]["vault"].strip()
    if not texto:
        raise ErrorApuntes("Falta la ruta de tu vault de Obsidian: edita [carpetas] vault en config.toml")
    ruta = Path(os.path.expanduser(texto))
    if not ruta.is_dir():
        raise ErrorApuntes(
            f"No encuentro tu vault de Obsidian en {ruta}\n"
            "Edita [carpetas] vault en config.toml con la carpeta que abres en Obsidian."
        )
    return ruta


def carpeta_en_vault(cfg: dict, clave: str, crear: bool = True) -> Path:
    destino = vault(cfg) / cfg["carpetas"][clave]
    if crear:
        destino.mkdir(parents=True, exist_ok=True)
    return destino


# ---------------------------------------------------------------- fechas y nombres

def fecha_del_video(video: Path) -> dt.datetime:
    """Screen_Recording_20260924_203554_Zoom.mp4 -> 2026-09-24 20:35:54.
    Si el nombre no trae fecha, usa la fecha de modificación del archivo."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})", video.name)
    if m:
        try:
            return dt.datetime(*map(int, m.groups()))
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(video.stat().st_mtime)


def dia_de_clase(fecha: dt.datetime, corte_madrugada: int) -> dt.date:
    """Una grabación que empieza a las 00:40 pertenece a la clase del día anterior."""
    return (fecha - dt.timedelta(hours=corte_madrugada)).date()


def aplicar_formato_titulo(formato: str, dia: dt.date) -> str:
    return (formato.replace("{dia}", str(dia.day)).replace("{mes}", MESES[dia.month - 1])
            .replace("{semana}", DIAS[dia.weekday()]).replace("{año}", str(dia.year)))


def limpiar_titulo(titulo: str) -> str:
    return "".join("-" if c in PROHIBIDOS_EN_NOMBRE else c for c in titulo).strip() or "Sin título"


def fecha_larga(fecha: dt.date) -> str:
    return f"{DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"


def reloj(segundos: float) -> str:
    s = int(segundos)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def duracion_legible(segundos: float) -> str:
    minutos = round(segundos / 60)
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60:02d} min"


# ---------------------------------------------------------------- sesiones y registro

@dataclass
class Sesion:
    """Una clase o reunión: uno o varios videos que terminan en una sola nota."""
    clave: str                 # "2026-09-24" en el diplomado, "suelto:<título>" en las sueltas
    titulo: str                # nombre de la nota: "Clase 24 septiembre"
    tipo: str                  # "clase" o "reunion"
    fecha: dt.datetime         # inicio de la primera parte
    videos: list[Path]         # en orden de grabación
    carpeta: str               # clave de [carpetas]: "notas" o "sueltas"
    descripcion: str           # "una clase del Diplomado en finanzas"
    materia: str               # "finanzas"
    etiquetas: list[str] = field(default_factory=list)
    diplomado: bool = False

    def a_registro(self) -> dict:
        return {"titulo": self.titulo, "tipo": self.tipo, "fecha": self.fecha.isoformat(timespec="minutes"),
                "videos": [str(v) for v in self.videos], "carpeta": self.carpeta,
                "descripcion": self.descripcion, "materia": self.materia,
                "etiquetas": self.etiquetas, "diplomado": self.diplomado}

    @classmethod
    def desde_registro(cls, clave: str, e: dict) -> "Sesion":
        return cls(clave=clave, titulo=e["titulo"], tipo=e["tipo"], fecha=dt.datetime.fromisoformat(e["fecha"]),
                   videos=[Path(v) for v in e["videos"]], carpeta=e["carpeta"], descripcion=e["descripcion"],
                   materia=e["materia"], etiquetas=e.get("etiquetas", []), diplomado=e.get("diplomado", False))


class Registro:
    """Recuerda cada sesión: su título, qué videos entraron en la nota y si falló algo."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.datos = {"sesiones": {}}
        if ruta.exists():
            self.datos = json.loads(ruta.read_text(encoding="utf-8"))
            self.datos.setdefault("sesiones", {})

    @property
    def sesiones(self) -> dict:
        return self.datos["sesiones"]

    def guardar(self) -> None:
        temporal = self.ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8")
        temporal.replace(self.ruta)

    def titulo_libre(self, titulo: str, clave: str, año: int | None = None) -> str:
        """Evita que dos sesiones distintas terminen con el mismo nombre de nota."""
        usados = {e["titulo"].lower() for c, e in self.sesiones.items() if c != clave}
        if titulo.lower() not in usados:
            return titulo
        if año and f"{titulo} {año}".lower() not in usados:
            return f"{titulo} {año}"
        n = 2
        while f"{titulo} ({n})".lower() in usados:
            n += 1
        return f"{titulo} ({n})"

    def buscar(self, texto: str) -> list[str]:
        t = texto.lower().strip()
        return [c for c, e in self.sesiones.items() if c.lower() == t or c.lower().startswith(t)
                or t in e["titulo"].lower()]


def videos_en_carpeta(cfg: dict) -> list[Path]:
    origen = carpeta(cfg, "videos")
    if not origen.is_dir():
        raise ErrorApuntes(f"No existe la carpeta de videos {origen} (revisa [carpetas] videos en config.toml)")
    return sorted(p for p in origen.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES)


def sesiones_diplomado(cfg: dict, registro: Registro) -> list[Sesion]:
    d = cfg["diplomado"]
    grupos: dict[dt.date, list] = defaultdict(list)
    for video in videos_en_carpeta(cfg):
        fecha = fecha_del_video(video)
        grupos[dia_de_clase(fecha, int(d["corte_madrugada"]))].append((fecha, video))
    # Un video que ya transcribiste y luego sacaste de la carpeta sigue contando como parte
    # de su clase (se usa su transcripción guardada). Así, quitar videos para liberar espacio
    # nunca hace que una nota se rehaga incompleta.
    presentes = {v.name for partes in grupos.values() for _, v in partes}
    for clave, e in registro.sesiones.items():
        if not e.get("diplomado"):
            continue
        for texto in e.get("videos", []):
            v = Path(texto)
            if v.name in presentes or not cache_de(v, cfg).exists():
                continue
            grupos[dt.date.fromisoformat(clave)].append((inicio_guardado(v, cfg) or dt.datetime.fromisoformat(e["fecha"]), v))
            presentes.add(v.name)
    sesiones = []
    for dia, partes in sorted(grupos.items()):
        partes.sort()
        clave = dia.isoformat()
        titulo = registro.sesiones.get(clave, {}).get("titulo") or registro.titulo_libre(
            limpiar_titulo(aplicar_formato_titulo(d["titulo"], dia)), clave, dia.year)
        sesiones.append(Sesion(clave=clave, titulo=titulo, tipo="clase", fecha=partes[0][0],
                               videos=[v for _, v in partes], carpeta="notas",
                               descripcion=f"una clase del {d['curso']}", materia=d["materia"],
                               etiquetas=list(d["etiquetas"]), diplomado=True))
    return sesiones


def sesion_suelta(archivos: list[Path], tipo: str, titulo: str | None, materia: str | None,
                  registro: Registro) -> Sesion:
    partes = sorted((fecha_del_video(v), v) for v in archivos)
    inicio = partes[0][0]
    cuando = f"{inicio.day} {MESES[inicio.month - 1]}"
    if tipo == "reunion":
        base = titulo or f"Reunión {cuando}"
        descripcion, tema = "una reunión", materia or "el tema de la reunión"
    else:
        base = titulo or (f"Clase de {materia} {cuando}" if materia else f"Clase suelta {cuando}")
        descripcion = f"una clase de {materia}" if materia else "una clase"
        tema = materia or "la materia de la clase"
    base = limpiar_titulo(base)
    clave = f"suelto:{base.lower()}"
    existente = registro.sesiones.get(clave)
    if existente and sorted(Path(v).name for v in existente["videos"]) != sorted(v.name for v in archivos):
        raise ErrorApuntes(f"Ya hay una nota «{base}» hecha con otros videos. Usa otro --titulo.")
    etiquetas = ["reunion"] if tipo == "reunion" else ["clase"]
    return Sesion(clave=clave, titulo=registro.titulo_libre(base, clave) if not existente else existente["titulo"],
                  tipo=tipo, fecha=inicio, videos=[v for _, v in partes], carpeta="sueltas",
                  descripcion=descripcion, materia=tema, etiquetas=etiquetas)


# ---------------------------------------------------------------- transcripción

def es_fantasma(texto: str) -> bool:
    limpio = re.sub(r"[^\w\s]", "", texto.lower()).strip()
    return limpio in FRASES_FANTASMA or "amaraorg" in limpio.replace(" ", "")


def texto_inicial(cfg: dict) -> str | None:
    vocabulario = cfg["whisper"].get("vocabulario") or []
    if not vocabulario:
        return None
    return "Términos que se usan en la grabación: " + ", ".join(vocabulario) + "."


def opciones_whisper(cfg: dict) -> dict:
    w = cfg["whisper"]
    return {
        "language": w["idioma"] or None,
        "beam_size": int(w["beam_size"]),
        "vad_filter": bool(w["saltar_silencios"]),
        "condition_on_previous_text": bool(w["usar_contexto"]),
        "initial_prompt": texto_inicial(cfg),
    }


def cargar_modelo(nombre: str, cfg: dict):
    from faster_whisper import WhisperModel  # se importa aquí para que `estado` arranque rápido

    print(f"Cargando el modelo de Whisper «{nombre}» (la primera vez se descarga, puede tardar)...", flush=True)
    try:
        return WhisperModel(nombre, device="cpu", compute_type=cfg["whisper"]["precision"],
                            cpu_threads=int(cfg["whisper"]["hilos"]))
    except ValueError as e:
        raise ErrorApuntes(f"«{nombre}» no es un modelo válido de Whisper ({e}). Revisa [whisper] modelo.")
    except Exception as e:  # noqa: BLE001 - casi siempre es la descarga
        raise ErrorApuntes(
            f"No pude cargar el modelo «{nombre}». La primera vez se descarga de huggingface.co, "
            f"así que necesitas internet. Detalle: {type(e).__name__}: {str(e)[:300]}")


class Cargador:
    """Carga el modelo de Whisper solo si de verdad hay algo que transcribir, y una sola vez."""

    def __init__(self, cfg: dict, modelo=None):
        self.cfg, self._modelo = cfg, modelo

    def modelo(self):
        if self._modelo is None:
            self._modelo = cargar_modelo(self.cfg["whisper"]["modelo"], self.cfg)
        return self._modelo


def duracion_audio(ruta: Path) -> float | None:
    """Duración en segundos leyendo solo el encabezado del archivo (no decodifica el audio)."""
    try:
        import av
        contenedor = av.open(str(ruta))
        try:
            if contenedor.duration:
                return contenedor.duration / 1_000_000
            audio = next((st for st in contenedor.streams if st.type == "audio"), None)
            if audio is not None and audio.duration and audio.time_base:
                return float(audio.duration * audio.time_base)
        finally:
            contenedor.close()
    except Exception:  # noqa: BLE001 - si no se puede leer, se transcribe completo
        return None
    return None


def silencio_cercano(ruta: Path, objetivo: float, ventana: float = 60.0) -> float:
    """El punto de silencio más cercano a `objetivo` (±ventana), para no cortar a media palabra."""
    desde = max(0.0, objetivo - ventana)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{desde:.2f}", "-t", f"{2 * ventana:.2f}",
                        "-i", str(ruta), "-vn", "-ac", "1", "-af", "silencedetect=noise=-35dB:d=0.4",
                        "-f", "null", "-"], capture_output=True, text=True)
    inicios = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    fines = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", r.stderr)]
    candidatos = [desde + (max(a, 0.0) + (fines[i] if i < len(fines) else 2 * ventana)) / 2
                  for i, a in enumerate(inicios)]
    return min(candidatos, key=lambda c: abs(c - objetivo)) if candidatos else objetivo


def puntos_de_corte(ruta: Path, total: float, tramo: float) -> list[float]:
    """[0, corte1, corte2, ..., total] con tramos de unos `tramo` segundos cortados en silencios."""
    cortes = [0.0]
    objetivo = tramo
    while objetivo < total - tramo / 2:
        corte = silencio_cercano(ruta, objetivo)
        if corte <= cortes[-1] + 1 or corte >= total - 1:
            corte = objetivo
        cortes.append(corte)
        objetivo = corte + tramo
    cortes.append(total)
    return cortes


def recortar_audio(ruta: Path, desde: float, hasta: float):
    """Devuelve el audio de [desde, hasta) como arreglo de numpy a 16 kHz, sin cargar el archivo completo."""
    from faster_whisper import decode_audio

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tramo.wav"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{desde:.3f}", "-t", f"{hasta - desde:.3f}",
                            "-i", str(ruta), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
        if r.returncode != 0:
            raise ErrorApuntes(f"ffmpeg no pudo recortar {ruta.name}: {r.stderr.strip()[:300]}")
        return decode_audio(str(wav))


def transcribir(ruta_audio: Path, cfg: dict, modelo, etiqueta: str = "") -> tuple[list, float]:
    """Devuelve ([(inicio, fin, texto), ...], duración_total_en_segundos).

    faster-whisper arma en memoria el espectrograma de todo el audio que recibe: dos horas
    de una sola vez no caben en 8 GB de RAM. Por eso las grabaciones largas se transcriben
    en tramos (10 min por defecto) cortados en silencios, y luego se juntan."""
    tramo = float(cfg["whisper"].get("minutos_por_tramo", 10)) * 60
    total = duracion_audio(ruta_audio)
    if total and total > tramo * 1.5 and shutil.which("ffmpeg"):
        cortes = puntos_de_corte(ruta_audio, total, tramo)
        tramos = list(zip(cortes[:-1], cortes[1:]))
    else:
        tramos = [(0.0, None)]  # corto, o no se pudo medir: de una sola vez
    en_terminal = sys.stdout.isatty()
    inicio_reloj = time.monotonic()
    ultimo_aviso = 0.0
    resultado = []
    for desde, hasta in tramos:
        entrada = str(ruta_audio) if hasta is None else recortar_audio(ruta_audio, desde, hasta)
        segmentos, info = modelo.transcribe(entrada, **opciones_whisper(cfg))
        total = total or info.duration or 1.0
        for seg in segmentos:
            texto = seg.text.strip()
            fin_seg = desde + seg.end
            if texto and not es_fantasma(texto):
                resultado.append((round(desde + seg.start, 2), round(fin_seg, 2), texto))
            ahora = time.monotonic()
            if ahora - ultimo_aviso >= (2 if en_terminal else 300):
                ultimo_aviso = ahora
                avance = min(fin_seg / total, 1.0)
                transcurrido = ahora - inicio_reloj
                falta = transcurrido / avance - transcurrido if avance > 0.02 else None
                linea = (f"{etiqueta} {avance:4.0%} · {reloj(fin_seg)} de {reloj(total)}"
                         + (f" · faltan ~{duracion_legible(falta)}" if falta is not None else ""))
                print(("\r" + linea.ljust(78)) if en_terminal else linea, end="" if en_terminal else "\n", flush=True)
        del entrada, segmentos
    fin = f"{etiqueta} listo en {duracion_legible(time.monotonic() - inicio_reloj)}"
    print(("\r" + fin.ljust(78)) if en_terminal else fin, flush=True)
    return resultado, total


def transcripcion_video(video: Path, cfg: dict, cargador: Cargador, etiqueta: str = "") -> dict:
    """Transcribe un video o reutiliza su transcripción guardada."""
    carpeta_cache = carpeta(cfg, "trabajo") / "transcripciones"
    carpeta_cache.mkdir(parents=True, exist_ok=True)
    cache = carpeta_cache / f"{video.stem}.json"
    if cache.exists():
        datos = json.loads(cache.read_text(encoding="utf-8"))
        if not video.exists() or datos.get("tamano") == video.stat().st_size:
            print(f"Transcripción guardada: {video.name}")
            return datos
    if not video.exists():
        raise ErrorApuntes(f"No encuentro {video} ni una transcripción guardada.")
    segmentos, duracion = transcribir(video, cfg, cargador.modelo(), etiqueta)
    datos = {"video": video.name, "tamano": video.stat().st_size, "inicio": fecha_del_video(video).isoformat(),
             "duracion": duracion, "modelo": cfg["whisper"]["modelo"], "segmentos": segmentos}
    escribir_atomico(cache.with_suffix(".srt"), a_srt(segmentos))
    escribir_atomico(cache, json.dumps(datos, ensure_ascii=False))
    return datos


def cache_de(video: Path, cfg: dict) -> Path:
    return carpeta(cfg, "trabajo") / "transcripciones" / f"{video.stem}.json"


def inicio_guardado(video: Path, cfg: dict) -> dt.datetime | None:
    """Hora de inicio de un video según su transcripción guardada (sirve aunque ya no exista)."""
    try:
        inicio = json.loads(cache_de(video, cfg).read_text(encoding="utf-8")).get("inicio")
        return dt.datetime.fromisoformat(inicio) if inicio else None
    except (OSError, ValueError):
        return None


def en_parrafos(segmentos: list, prefijo: str = "", pausa: float = 1.5, max_caracteres: int = 700) -> list[str]:
    """Junta los segmentos en párrafos; cada párrafo empieza con su marca de tiempo."""
    parrafos, actual, inicio, fin_anterior = [], [], 0.0, None
    for ini, fin, texto in segmentos:
        largo = sum(len(t) + 1 for t in actual)
        if actual and (ini - fin_anterior >= pausa or largo >= max_caracteres):
            parrafos.append(f"[{prefijo}{reloj(inicio)}] " + " ".join(actual))
            actual = []
        if not actual:
            inicio = ini
        actual.append(texto)
        fin_anterior = fin
    if actual:
        parrafos.append(f"[{prefijo}{reloj(inicio)}] " + " ".join(actual))
    return parrafos


def texto_de_sesion(partes: list[dict]) -> list[str]:
    """Párrafos de todas las partes en orden. Con varias partes, las marcas llevan P1, P2..."""
    varias = len(partes) > 1
    bloques = []
    for i, p in enumerate(partes, 1):
        if varias:
            hora = dt.datetime.fromisoformat(p["inicio"]).strftime("%H:%M") if p.get("inicio") else "?"
            bloques.append(f"=== Parte {i} de {len(partes)} · grabación iniciada a las {hora} ===")
        bloques += en_parrafos(p["segmentos"], prefijo=f"P{i} " if varias else "")
    return bloques


def a_srt(segmentos: list) -> str:
    def marca(s: float) -> str:
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"
    bloques = [f"{i}\n{marca(a)} --> {marca(b)}\n{t}\n" for i, (a, b, t) in enumerate(segmentos, 1)]
    return "\n".join(bloques)


def escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".parcial")
    temporal.write_text(contenido, encoding="utf-8")
    temporal.replace(ruta)


# ---------------------------------------------------------------- Claude

def ruta_claude() -> str | None:
    encontrado = shutil.which("claude")
    if encontrado:
        return encontrado
    local = Path.home() / ".local/bin/claude"  # por si corre desde systemd sin ese PATH
    return str(local) if local.exists() else None


INSTRUCCION_CORTA = ("Escribe la nota de Obsidian siguiendo las <instrucciones> que vienen al final "
                     "de la entrada, a partir de la <transcripcion>.")


def comando_claude(ejecutable: str, cfg: dict) -> list[str]:
    cmd = [ejecutable, "-p", INSTRUCCION_CORTA,
           "--tools", "",                  # sin herramientas: solo lee y escribe texto
           "--strict-mcp-config",          # sin servidores MCP: arranca más rápido
           "--no-session-persistence",     # no guarda estas sesiones en tu historial
           "--output-format", "text"]
    if cfg["claude"].get("modelo"):
        cmd += ["--model", cfg["claude"]["modelo"]]
    return cmd


def conceptos_existentes(cfg: dict) -> list[str]:
    try:
        destino = carpeta_en_vault(cfg, "conceptos", crear=False)
    except ErrorApuntes:
        return []
    return sorted(p.stem for p in destino.glob("*.md")) if destino.is_dir() else []


def entrada_claude(sesion: Sesion, cfg: dict, parrafos: list[str], duracion: float, partes: int) -> str:
    """Transcripción primero y las instrucciones al final: así rinde mejor Claude con textos largos."""
    prompt = (RAIZ / cfg["claude"][f"prompt_{sesion.tipo}"]).read_text(encoding="utf-8").strip()
    prompt = prompt.replace("{descripcion}", sesion.descripcion).replace("{materia}", sesion.materia)
    existentes = conceptos_existentes(cfg)
    return (
        f'<transcripcion titulo="{sesion.titulo}" fecha="{fecha_larga(sesion.fecha.date())}" '
        f'partes="{partes}" duracion="{duracion_legible(duracion)}">\n'
        + "\n\n".join(parrafos)
        + "\n</transcripcion>\n\n<conceptos_existentes>\n"
        + ("\n".join(existentes) if existentes else "(todavía no hay ninguno)")
        + f"\n</conceptos_existentes>\n\n<instrucciones>\n{prompt}\n</instrucciones>\n"
    )


def limpiar_respuesta(texto: str) -> str:
    texto = texto.strip()
    m = re.fullmatch(r"```(?:markdown|md)?[ \t]*\n(.*)\n```", texto, flags=re.S)
    return (m.group(1) if m else texto).strip()


def hacer_apuntes(entrada: str, cfg: dict) -> str:
    if cfg["claude"].get("backend", "cli") == "api":  # extensiones: notas con la API de Anthropic
        return notas_por_api(entrada, cfg)
    ejecutable = ruta_claude()
    if not ejecutable:
        raise ErrorApuntes("No encuentro el comando `claude`. Instala Claude Code (ver la guía) "
                           "o pon [claude] activar = false en config.toml.")
    print("Escribiendo la nota con Claude (unos minutos)...", flush=True)
    entorno = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(cfg["claude"]["max_tokens_salida"]))
    try:
        r = subprocess.run(comando_claude(ejecutable, cfg), input=entrada, capture_output=True, text=True,
                           timeout=float(cfg["claude"]["tiempo_maximo"]) * 60, env=entorno,
                           cwd=tempfile.gettempdir())  # fuera del proyecto: no carga su CLAUDE.md
    except subprocess.TimeoutExpired:
        raise ErrorApuntes(f"Claude tardó más de {cfg['claude']['tiempo_maximo']} min; se reintentará la próxima vez.")
    if "output token maximum" in r.stdout:
        raise ErrorApuntes("La nota salió más larga que el máximo de respuesta y se cortó. "
                           "Sube [claude] max_tokens_salida o divide la sesión.")
    respuesta = limpiar_respuesta(r.stdout)
    if r.returncode != 0 or not respuesta:
        detalle = (r.stderr.strip() or r.stdout.strip() or "sin detalle")[:600]
        raise ErrorApuntes(f"Claude no pudo escribir la nota (código {r.returncode}): {detalle}")
    return respuesta


# ---------------------------------------------------------------- nota, conceptos e índice

PATRON_ENLACE = re.compile(r"\[\[([^\[\]|#^]+?)(?:[#|][^\[\]]*)?\]\]")
PATRON_GLOSARIO = re.compile(  # - **[[Concepto]]**: definición   (la definición puede venir en *cursiva*)
    r"^\s*[-*]\s*(?:\*\*)?\s*\[\[([^\[\]|#^]+?)(?:\|[^\[\]]*)?\]\]\s*(?:\*\*)?\s*[:：]\s*(?:\*\*\s+)?(.+?)\s*$")


def seccion(md: str, titulo: str) -> str:
    """Texto de la sección `## titulo` hasta la siguiente sección `## `."""
    m = re.search(rf"^##[ \t]+{re.escape(titulo)}[^\n]*\n(.*?)(?=^##[ \t]|\Z)", md, flags=re.M | re.S | re.I)
    return m.group(1) if m else ""


def conceptos_de_nota(cuerpo: str, excluir: set[str]) -> dict[str, str | None]:
    """{concepto: definición del glosario o None} para cada [[enlace]] de la nota."""
    conceptos: dict[str, str | None] = {}
    for linea in seccion(cuerpo, "Glosario").splitlines():
        m = PATRON_GLOSARIO.match(linea)
        if m:
            conceptos[m.group(1).strip()] = m.group(2).strip()
    for m in PATRON_ENLACE.finditer(cuerpo):
        conceptos.setdefault(m.group(1).strip(), None)
    excluir_min = {e.lower() for e in excluir}
    return {c: d for c, d in conceptos.items()
            if c.lower() not in excluir_min and not re.match(r"(clase|reuni[oó]n) ", c, re.I)
            and not (PROHIBIDOS_EN_NOMBRE & set(c)) and len(c) <= 80}


def notas_del_vault(raiz: Path) -> set[str]:
    return {p.stem.lower() for p in raiz.rglob("*.md")
            if not any(parte.startswith(".") for parte in p.relative_to(raiz).parts)}


def crear_paginas_concepto(cuerpo: str, sesion: Sesion, cfg: dict) -> list[str]:
    """Crea una página por cada concepto enlazado que todavía no exista en el vault."""
    raiz = vault(cfg)
    existentes = notas_del_vault(raiz)
    excluir = {sesion.titulo, cfg["diplomado"]["indice"]}
    destino = carpeta_en_vault(cfg, "conceptos")
    creados = []
    for nombre, definicion in conceptos_de_nota(cuerpo, excluir).items():
        if nombre.lower() in existentes:
            continue
        lineas = ["---", "tipo: concepto", f"creado: {dt.date.today().isoformat()}", "tags: [concepto]", "---", ""]
        if definicion:
            de_claude = re.fullmatch(r"\*[^*].*[^*]\*|\*[^*]\*", definicion) is not None
            if de_claude:
                lineas += ["> [!claude] Definición inicial (escrita por Claude)", f"> {definicion.strip('*')}"]
            else:
                lineas += ["> [!quote] Definición dada en clase", f"> {definicion}"]
            lineas.append("")
        lineas += ["## Mi descripción", "", "", "## Primera mención", f"[[{sesion.titulo}]]", ""]
        escribir_atomico(destino / f"{nombre}.md", "\n".join(lineas))
        existentes.add(nombre.lower())
        creados.append(nombre)
    return creados


def armar_nota(cfg: dict, sesion: Sesion, partes: list[dict], parrafos: list[str], cuerpo: str | None) -> str:
    duracion = sum(p["duracion"] for p in partes)
    modelos = sorted({p.get("modelo", "?") for p in partes})
    videos = ", ".join(json.dumps(v.name, ensure_ascii=False) for v in sesion.videos)
    fm = ["---", f"fecha: {sesion.fecha:%Y-%m-%d}", f"dia: {DIAS[sesion.fecha.weekday()]}", f"tipo: {sesion.tipo}"]
    if sesion.diplomado:
        fm.append(f"curso: {cfg['diplomado']['curso']}")
    fm += [f"materia: {sesion.materia}", f"partes: {len(partes)}", f'duracion: "{reloj(duracion)}"',
           f"videos: [{videos}]", f"modelo_whisper: {', '.join(modelos)}",
           f"apuntes: {'claude' if cuerpo else 'pendientes'}", f"tags: [{', '.join(sesion.etiquetas)}]",
           "cssclasses: [apuntes-claude]", "---", ""]
    encabezado = f"{fecha_larga(sesion.fecha.date())} · "
    encabezado += f"{len(partes)} partes · " if len(partes) > 1 else ""
    encabezado += duracion_legible(duracion)
    if sesion.diplomado:
        encabezado += f" · [[{cfg['diplomado']['indice']}]]"
    lineas = fm + [encabezado, ""]
    if cuerpo:
        lineas += ["> [!info]- Cómo leer esta nota",
                   "> Texto normal: lo que se dijo en la sesión. *Cursiva* y bloques «Claude»: lo que agregó o "
                   "corrigió Claude. (?): no se entendió en la grabación.", "", cuerpo, ""]
    else:
        lineas += ["> [!info] Solo transcripción. Activa Claude en config.toml y corre `pizza` para generar la nota.", ""]
    if parrafos and (cfg["notas"]["incluir_transcripcion"] or not cuerpo):
        lineas.append("> [!quote]- Transcripción completa")
        for p in parrafos:
            p = re.sub(r"^=== (Parte \d+ de \d+) · (.*) ===$", r"**\1** · \2", p)
            lineas += [f"> {p}", ">"]
        lineas.pop()
    return "\n".join(lineas) + "\n"


def temas_de_nota(md: str) -> list[str]:
    desarrollo = seccion(md, "Desarrollo") or md
    return [m.group(1).strip() for m in re.finditer(r"^###[ \t]+(.+)$", desarrollo, flags=re.M)]


def actualizar_indice(cfg: dict, registro: Registro) -> None:
    """Regenera el índice del diplomado: una entrada por clase con sus temas y conceptos."""
    destino = carpeta_en_vault(cfg, "notas")
    nombre = cfg["diplomado"]["indice"]
    clases = sorted(((c, e) for c, e in registro.sesiones.items() if e.get("diplomado")),
                    key=lambda ce: ce[1]["fecha"], reverse=True)
    lineas = ["---", "tipo: indice", "tags: [diplomado, indice]", "---", "",
              "> [!info] Este índice se regenera solo cada vez que se procesa una clase. "
              "Lo que escribas aquí se pierde; anota en las clases o en los conceptos.", ""]
    for _, e in clases:
        nota = destino / f"{e['titulo']}.md"
        if not nota.exists():
            continue
        md = nota.read_text(encoding="utf-8")
        fecha = dt.datetime.fromisoformat(e["fecha"])
        lineas.append(f"## [[{e['titulo']}]] · {DIAS[fecha.weekday()]}")
        temas = []
        for t in temas_de_nota(md):
            limpio = re.sub(r"\s*\(\s*(P\d+\s*)?\d{2}:\d{2}(:\d{2})?\s*\)\s*$", "", t)
            temas.append(limpio if set("[]|#^") & set(limpio) else f"[[{e['titulo']}#{limpio}|{limpio}]]")
        if temas:
            lineas += ["", "**Temas:** " + " · ".join(temas)]
        conceptos = [c for c in conceptos_de_nota(seccion(md, "Glosario"), {e["titulo"]})]
        if conceptos:
            lineas += ["", "**Conceptos:** " + " · ".join(f"[[{c}]]" for c in conceptos)]
        lineas.append("")
    escribir_atomico(destino / f"{nombre}.md", "\n".join(lineas))


def respaldar(nota: Path, cfg: dict) -> Path:
    carpeta_respaldos = carpeta(cfg, "trabajo") / "respaldos"
    carpeta_respaldos.mkdir(parents=True, exist_ok=True)
    destino = carpeta_respaldos / f"{nota.stem}.{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    shutil.move(str(nota), destino)
    return destino


# ---------------------------------------------------------------- procesar una sesión

def ruta_nota(sesion: Sesion, cfg: dict, crear: bool = True) -> Path:
    return carpeta_en_vault(cfg, sesion.carpeta, crear) / f"{sesion.titulo}.md"


def esta_completa(sesion: Sesion, cfg: dict, registro: Registro) -> bool:
    e = registro.sesiones.get(sesion.clave, {})
    if e.get("en_nota") != [v.name for v in sesion.videos]:
        return False
    if not (e.get("apuntes") == "claude" or (e.get("apuntes") == "sin_claude" and not cfg["claude"]["activar"])):
        return False
    try:
        return ruta_nota(sesion, cfg, crear=False).exists()
    except ErrorApuntes:
        return False


def procesar_sesion(sesion: Sesion, cfg: dict, registro: Registro, cargador: Cargador,
                    rehacer: bool = False, escribir_nota: bool = True) -> str:
    """Hace lo que falte de una sesión. Devuelve "ok", "espera" (faltan partes) o "error"."""
    n = len(sesion.videos)
    print(f"\n== {sesion.titulo}  ({n} parte{'s' if n > 1 else ''})", flush=True)
    anterior = registro.sesiones.get(sesion.clave, {})
    entrada = {**anterior, **sesion.a_registro()}
    registro.sesiones[sesion.clave] = entrada
    registro.guardar()

    # 1. Transcripción de cada parte (o la guardada)
    partes = []
    for i, video in enumerate(sesion.videos, 1):
        etiqueta = f"{sesion.titulo} [{i}/{n}]" if n > 1 else sesion.titulo
        partes.append(transcripcion_video(video, cfg, cargador, etiqueta))
    parrafos = texto_de_sesion(partes)
    duracion = sum(p["duracion"] for p in partes)
    txt = carpeta(cfg, "trabajo") / "sesiones" / f"{sesion.titulo}.txt"
    escribir_atomico(txt, "\n\n".join(parrafos) + "\n")
    print(f"Transcripción completa: {txt}")
    if not escribir_nota:
        return "espera"

    # 2. Nota
    nota = ruta_nota(sesion, cfg)
    nombres = [v.name for v in sesion.videos]
    usar_claude = bool(cfg["claude"]["activar"])
    if anterior.get("en_nota") and anterior["en_nota"] != nombres and nota.exists():
        print("Llegaron partes nuevas de esta sesión: se rehace la nota con todas.")
    al_dia = (nota.exists() and entrada.get("en_nota") == nombres
              and (entrada.get("apuntes") == "claude" or not usar_claude))
    if al_dia and not rehacer:
        print(f"Nota: ya existe ({nota})")
        return "ok"

    cuerpo = None
    if usar_claude:
        try:
            cuerpo = hacer_apuntes(entrada_claude(sesion, cfg, parrafos, duracion, n), cfg)
        except ErrorApuntes as e:
            entrada["error"] = str(e)
            registro.guardar()
            print(f"AVISO: {e}\nLa transcripción quedó guardada; corre `pizza` otra vez para reintentar.")
            return "error"

    if nota.exists():
        print(f"La nota anterior se guardó en {respaldar(nota, cfg)}")
    escribir_atomico(nota, armar_nota(cfg, sesion, partes, parrafos, cuerpo))
    print(f"Nota: {nota}")
    if cuerpo and cfg["notas"]["crear_conceptos"]:
        nuevos = crear_paginas_concepto(cuerpo, sesion, cfg)
        if nuevos:
            print(f"Conceptos nuevos ({len(nuevos)}): {', '.join(nuevos)}")
    entrada.update(en_nota=nombres, apuntes="claude" if cuerpo else "sin_claude",
                   hecha=dt.datetime.now().isoformat(timespec="seconds"))
    entrada.pop("error", None)
    registro.guardar()
    if sesion.diplomado:
        actualizar_indice(cfg, registro)
    return "ok"


# ---------------------------------------------------------------- utilidades de ejecución

class Candado:
    """Evita que dos `apuntes` trabajen al mismo tiempo (por ejemplo, vigilar y uno manual)."""

    def __init__(self, ruta: Path):
        self.archivo = open(ruta, "w")

    def tomar(self) -> bool:
        try:
            fcntl.flock(self.archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def soltar(self) -> None:
        fcntl.flock(self.archivo, fcntl.LOCK_UN)

    def __del__(self):
        self.archivo.close()


def evitar_suspension() -> subprocess.Popen | None:
    """Mientras corre, pide a Ubuntu que no se suspenda por inactividad (útil de noche)."""
    if not shutil.which("systemd-inhibit"):
        return None
    try:
        return subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=apuntes",
                                 "--why=Transcribiendo clases", "sleep", "infinity"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def terminar(proceso: subprocess.Popen | None) -> None:
    if proceso:
        proceso.terminate()
        proceso.wait()


def preparar(cfg: dict) -> tuple[Registro, Candado]:
    trabajo = carpeta(cfg, "trabajo")
    trabajo.mkdir(parents=True, exist_ok=True)
    return Registro(trabajo / "registro.json"), Candado(trabajo / ".candado")


def procesar_varias(sesiones: list[Sesion], cfg: dict, registro: Registro, cargador: Cargador,
                    rehacer: bool = False) -> int:
    inhibidor = evitar_suspension()
    fallos = 0
    try:
        for s in sesiones:
            fallos += procesar_sesion(s, cfg, registro, cargador, rehacer=rehacer) == "error"
    finally:
        terminar(inhibidor)
    return 1 if fallos else 0


# ---------------------------------------------------------------- subcomandos

def cmd_procesar(cfg: dict, cargador: Cargador | None = None) -> int:
    vault(cfg)  # valida el vault antes de gastar horas transcribiendo
    registro, candado = preparar(cfg)
    if not candado.tomar():
        raise ErrorApuntes("Ya hay otra pizza en el horno (¿está corriendo `pizza turno`?).")
    pendientes = [s for s in sesiones_diplomado(cfg, registro) if not esta_completa(s, cfg, registro)]
    if not pendientes:
        print("No hay clases pendientes.")
        return 0
    print(f"Clases por procesar: {len(pendientes)}")
    return procesar_varias(pendientes, cfg, registro, cargador or Cargador(cfg))


def cmd_suelto(cfg: dict, archivos: list[str], tipo: str, titulo: str | None, materia: str | None,
               cargador: Cargador | None = None) -> int:
    vault(cfg)
    rutas = [Path(a).expanduser().resolve() for a in archivos]
    for r in rutas:
        if not r.is_file():
            raise ErrorApuntes(f"No existe el archivo {r}")
        if r.parent == carpeta(cfg, "videos").resolve():
            raise ErrorApuntes(f"{r.name} está en la carpeta del diplomado; muévelo a otra carpeta para "
                               "procesarlo como sesión suelta.")
    registro, candado = preparar(cfg)
    if not candado.tomar():
        raise ErrorApuntes("Ya hay otra pizza en el horno; espera a que termine.")
    sesion = sesion_suelta(rutas, tipo, titulo, materia, registro)
    if esta_completa(sesion, cfg, registro):
        print(f"La nota «{sesion.titulo}» ya existe. Para regenerarla: pizza recalienta \"{sesion.titulo}\"")
        return 0
    cfg_suelto = {**cfg, "whisper": {**cfg["whisper"], "vocabulario": []}}  # el vocabulario es del diplomado
    return procesar_varias([sesion], cfg_suelto, registro, cargador or Cargador(cfg_suelto))


def cmd_rehacer(cfg: dict, texto: str, retranscribir: bool = False, cargador: Cargador | None = None) -> int:
    vault(cfg)
    registro, candado = preparar(cfg)
    if not candado.tomar():
        raise ErrorApuntes("Ya hay otra pizza en el horno; espera a que termine.")
    claves = registro.buscar(texto)
    if not claves:
        raise ErrorApuntes(f"No encuentro ninguna sesión que coincida con «{texto}». Mira `pizza menu`.")
    if len(claves) > 1:
        titulos = ", ".join(registro.sesiones[c]["titulo"] for c in claves)
        raise ErrorApuntes(f"«{texto}» coincide con varias sesiones: {titulos}. Sé más específico.")
    clave = claves[0]
    entrada = registro.sesiones[clave]
    sesion = None
    if entrada.get("diplomado"):  # vuelve a leer la carpeta por si llegaron partes nuevas
        sesion = next((s for s in sesiones_diplomado(cfg, registro) if s.clave == clave), None)
    sesion = sesion or Sesion.desde_registro(clave, entrada)
    if not sesion.diplomado:
        cfg = {**cfg, "whisper": {**cfg["whisper"], "vocabulario": []}}  # el vocabulario es del diplomado
    if retranscribir:
        for v in sesion.videos:
            if not v.exists():
                raise ErrorApuntes(f"No puedo volver a transcribir: ya no existe {v}")
            cache_de(v, cfg).unlink(missing_ok=True)
    return procesar_varias([sesion], cfg, registro, cargador or Cargador(cfg), rehacer=True)


def cmd_estado(cfg: dict) -> int:
    registro = Registro(carpeta(cfg, "trabajo") / "registro.json")
    sesiones = sesiones_diplomado(cfg, registro)
    sueltas = [Sesion.desde_registro(c, e) for c, e in registro.sesiones.items() if not e.get("diplomado")]
    if not sesiones and not sueltas:
        print(f"No hay videos en {carpeta(cfg, 'videos')} ni notas sueltas.")
        return 0
    print(f"{'SESIÓN':28} {'PARTES':7} {'TRANSCRITAS':12} NOTA")
    for s in sesiones + sueltas:
        e = registro.sesiones.get(s.clave, {})
        transcritas = sum(cache_de(v, cfg).exists() for v in s.videos)
        if e.get("error"):
            nota = "ERROR"
        elif esta_completa(s, cfg, registro):
            nota = "lista" if e.get("apuntes") == "claude" else "solo transcripción"
        elif e.get("en_nota") and e["en_nota"] != [v.name for v in s.videos]:
            nota = "faltan partes nuevas"
        else:
            nota = "pendiente"
        titulo = s.titulo if s.diplomado else f"{s.titulo} (suelta)"
        print(f"{titulo[:28]:28} {len(s.videos):<7} {transcritas}/{len(s.videos):<10} {nota}")
        if e.get("error"):
            print(f"{'':28} └ {e['error'][:200]}")
    return 0


def cmd_prueba(cfg: dict, video_txt: str | None, modelos: list[str], minutos: float) -> int:
    from faster_whisper import decode_audio

    if not shutil.which("ffmpeg"):
        raise ErrorApuntes("La prueba necesita ffmpeg para recortar el fragmento: sudo apt install ffmpeg")
    if video_txt:
        video = Path(video_txt).expanduser()
    else:
        videos = videos_en_carpeta(cfg)
        if not videos:
            raise ErrorApuntes("No hay videos en la carpeta; pasa uno: pizza cata RUTA_DEL_VIDEO")
        video = max(videos, key=lambda p: p.stat().st_mtime)
    carpeta_prueba = carpeta(cfg, "trabajo") / "prueba"
    carpeta_prueba.mkdir(parents=True, exist_ok=True)

    # Recorta un fragmento del minuto 10 en adelante (el inicio suele ser espera o saludo)
    fragmento = carpeta_prueba / "fragmento.wav"
    print(f"Recortando {minutos:g} min de {video.name}...", flush=True)
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "600", "-t", str(minutos * 60),
                        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(fragmento)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not fragmento.exists() or fragmento.stat().st_size < 1000:
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(minutos * 60), "-i", str(video),
                            "-vn", "-ac", "1", "-ar", "16000", str(fragmento)], capture_output=True, text=True)
        if r.returncode != 0:
            raise ErrorApuntes(f"ffmpeg no pudo leer {video}: {r.stderr.strip()[:300]}")
    segundos_audio = len(decode_audio(str(fragmento))) / 16000

    filas = []
    for nombre in modelos:
        t0 = time.monotonic()
        modelo = cargar_modelo(nombre, cfg)
        carga = time.monotonic() - t0
        t1 = time.monotonic()
        segmentos, _ = transcribir(fragmento, cfg, modelo, etiqueta=f"[{nombre}]")
        tiempo = time.monotonic() - t1
        (carpeta_prueba / f"{nombre}.txt").write_text("\n\n".join(en_parrafos(segmentos)) + "\n", encoding="utf-8")
        velocidad = segundos_audio / tiempo
        filas.append((nombre, carga, tiempo, velocidad, 7200 / velocidad))
        del modelo

    print(f"\nFragmento de {duracion_legible(segundos_audio)} · CPU con {cfg['whisper']['precision']}\n")
    print(f"{'MODELO':10} {'CARGA':>8} {'TIEMPO':>9} {'VELOCIDAD':>11}   2 H DE GRABACIÓN TARDARÍAN")
    for nombre, carga, tiempo, velocidad, estimado in filas:
        print(f"{nombre:10} {carga:7.0f}s {tiempo:8.0f}s {velocidad:9.1f}x   ~{duracion_legible(estimado)}")
    print(f"\nCompara la calidad leyendo los textos en {carpeta_prueba}/")
    print("Elige el modelo en config.toml ([whisper] modelo). La estimación es aproximada: "
          "los silencios reales de la clase la hacen variar.")
    return 0


APP_LOCALSEND_FLATPAK = "org.localsend.localsend_app"


def comando_localsend(configurado: str) -> list[str] | None:
    """Cómo abrir LocalSend en esta compu: el comando de config.toml, o lo busca (deb, Snap, Flatpak, AppImage)."""
    if configurado.strip():
        return shlex.split(os.path.expanduser(configurado))
    for nombre in ("localsend_app", "localsend"):
        encontrado = shutil.which(nombre) or (f"/snap/bin/{nombre}" if Path(f"/snap/bin/{nombre}").exists() else None)
        if encontrado:
            return [encontrado]
    if shutil.which("flatpak") and subprocess.run(["flatpak", "info", APP_LOCALSEND_FLATPAK],
                                                  capture_output=True).returncode == 0:
        return ["flatpak", "run", APP_LOCALSEND_FLATPAK]
    for carpeta_app in ("Applications", "Aplicaciones", "Downloads", "Descargas", ".local/bin"):
        imagenes = sorted((Path.home() / carpeta_app).glob("*[Ll]ocal[Ss]end*.AppImage"))
        if imagenes:
            return [str(imagenes[-1])]
    return None


def localsend_abierto() -> bool:
    """Busca por nombre de proceso (localsend_app), no por texto en la línea de comandos."""
    return shutil.which("pgrep") is not None and subprocess.run(
        ["pgrep", "-i", "localsend"], capture_output=True).returncode == 0


def abrir_localsend(cfg: dict) -> None:
    """Abre LocalSend (si no está abierto ya) para recibir los videos de la tablet."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("LocalSend: no hay pantalla disponible (¿corre como servicio?), así que no lo abro.", flush=True)
        return
    if localsend_abierto():
        print("LocalSend ya está abierto.", flush=True)
        return
    comando = comando_localsend(cfg["localsend"].get("comando", ""))
    if not comando:
        print("No encontré LocalSend. Instálalo (Flathub, Snap o .deb desde localsend.org) o pon cómo abrirlo "
              "en [localsend] comando de config.toml.", flush=True)
        return
    try:
        subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)  # sigue abierto aunque cierres `pizza turno`
    except OSError as e:
        print(f"No pude abrir LocalSend con {' '.join(comando)}: {e}", flush=True)
        return
    print(f"Abriendo LocalSend. Manda los videos desde la tablet a {carpeta(cfg, 'videos')}", flush=True)


def cmd_vigilar(cfg: dict, intervalo: int, abrir_ls: bool = True) -> int:
    vault(cfg)
    registro, candado = preparar(cfg)
    espera = float(cfg["diplomado"]["espera_minutos"]) * 60
    if abrir_ls and cfg["localsend"].get("abrir", True):
        abrir_localsend(cfg)
    print(f"Pizza de turno: vigilando {carpeta(cfg, 'videos')} cada {intervalo} s. Ctrl+C para cerrar.", flush=True)
    tamanos: dict[Path, int] = {}
    fallidas: dict[str, float] = {}
    cargador = Cargador(cfg)
    while True:
        registro = Registro(registro.ruta)  # relee por si corriste `apuntes` a mano
        for sesion in sesiones_diplomado(cfg, registro):
            if esta_completa(sesion, cfg, registro) or time.time() - fallidas.get(sesion.clave, 0) < 3600:
                continue
            quietos = True
            for v in (v for v in sesion.videos if v.exists()):
                tamano = v.stat().st_size
                quietos &= tamanos.get(v) == tamano and time.time() - v.stat().st_mtime > 30
                tamanos[v] = tamano
            # La nota se escribe cuando la última parte lleva un rato sin cambios:
            # así no se hace una nota con la parte 1 mientras copias la parte 2.
            ultima = max((v.stat().st_mtime for v in sesion.videos if v.exists()), default=0)
            nota_permitida = time.time() - ultima > espera
            if not quietos or (not nota_permitida and all(cache_de(v, cfg).exists() for v in sesion.videos)):
                continue  # se está copiando, o ya está transcrita y falta esperar
            if not candado.tomar():
                continue  # hay un `apuntes` manual corriendo
            inhibidor = evitar_suspension()
            try:
                estado = procesar_sesion(sesion, cfg, registro, cargador, escribir_nota=nota_permitida)
                if estado == "error":
                    fallidas[sesion.clave] = time.time()
            except Exception as e:  # noqa: BLE001 - vigilar no debe morir por un video roto
                print(f"ERROR con {sesion.titulo}: {e}", flush=True)
                fallidas[sesion.clave] = time.time()
            finally:
                candado.soltar()
                terminar(inhibidor)
        time.sleep(intervalo)


# --- extensiones (aplicar_extensiones.py) ---
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["make", "pizza"]:  # lets make pizza = abrir LocalSend y quedarse de turno
        argv = ["turno"] + argv[2:]
    p = argparse.ArgumentParser(prog="pizza", description="Transcribe clases y reuniones y hornea notas de Obsidian.")
    p.set_defaults(accion="hornea")
    sub = p.add_subparsers(title="comandos", metavar="COMANDO")
    sub.add_parser("hornea", aliases=["procesar"], help="hornea las clases pendientes del diplomado (lo mismo que solo `pizza`)"
                   ).set_defaults(accion="hornea")
    sub.add_parser("menu", aliases=["estado"], help="qué sesiones ya están listas").set_defaults(accion="menu")
    ss = sub.add_parser("pedido", aliases=["suelto"], help="una clase o reunión aparte; varios videos = una sola sesión")
    ss.set_defaults(accion="pedido")
    ss.add_argument("videos", nargs="+")
    ss.add_argument("--tipo", choices=["clase", "reunion"], default="clase")
    ss.add_argument("--titulo", help="nombre de la nota, ej. \"Junta de equipo\"")
    ss.add_argument("--materia", help="tema o materia, ej. \"estadística\"")
    sr = sub.add_parser("recalienta", aliases=["rehacer"], help="vuelve a generar la nota de una sesión")
    sr.set_defaults(accion="recalienta")
    sr.add_argument("sesion", help="parte del nombre o la fecha, ej. \"24 septiembre\" o 2026-09-24")
    sr.add_argument("--transcribir", action="store_true", help="también vuelve a transcribir los videos")
    spr = sub.add_parser("cata", aliases=["prueba"], help="mide la velocidad de los modelos en tu laptop")
    spr.set_defaults(accion="cata")
    spr.add_argument("video", nargs="?")
    spr.add_argument("--modelos", default="small,turbo", help="lista separada por comas (default: small,turbo)")
    spr.add_argument("--minutos", type=float, default=3)
    sv = sub.add_parser("turno", aliases=["vigilar"], help="abre LocalSend y hornea cada clase nueva que llegue")
    sv.set_defaults(accion="turno")
    sv.add_argument("--cada", type=int, default=60, help="segundos entre revisiones (default: 60)")
    sv.add_argument("--sin-localsend", action="store_true", help="no abrir LocalSend al empezar")
    registrar_extensiones(p)  # extensiones: pregunta, cuenta, cotiza, despensa
    args = p.parse_args(argv)

    try:
        cfg = cargar_config()
        if getattr(args, "accion_extension", None):  # extensiones
            return comando_extension(args, cfg)
        if args.accion == "hornea":
            return cmd_procesar(cfg)
        if args.accion == "menu":
            return cmd_estado(cfg)
        if args.accion == "pedido":
            return cmd_suelto(cfg, args.videos, args.tipo, args.titulo, args.materia)
        if args.accion == "recalienta":
            return cmd_rehacer(cfg, args.sesion, args.transcribir)
        if args.accion == "cata":
            return cmd_prueba(cfg, args.video, [m.strip() for m in args.modelos.split(",") if m.strip()], args.minutos)
        if args.accion == "turno":
            return cmd_vigilar(cfg, args.cada, abrir_ls=not args.sin_localsend)
    except ErrorApuntes as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDetenido. Lo que quedó a medias se repite la próxima vez.")
        return 130
    return 0


if __name__ == "__main__":
    try:
        os.nice(10)  # prioridad baja: la laptop sigue usable mientras transcribe
    except OSError:
        pass
    sys.exit(main())
