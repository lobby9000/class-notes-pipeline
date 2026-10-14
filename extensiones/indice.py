"""La despensa: índice de tus notas y transcripciones para responder preguntas.

Cada nota del vault se parte en fragmentos:
- secciones de los apuntes (con su ruta de títulos, ej. "Desarrollo y práctica › Amortización");
- bloques [!claude] aparte, porque son agregados de Claude y no lo que dijo el profesor;
- tramos de la transcripción con el minuto en que empiezan (y la parte, si la clase se grabó en varias).

Los fragmentos viven en Chroma con embeddings multilingües (búsqueda por significado).
Cada búsqueda se combina con BM25 (palabras exactas, útil para siglas como ISR o UMA)
mediante Reciprocal Rank Fusion. El índice se actualiza solo con las notas que cambiaron.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .comun import ErrorExtension, carpeta_trabajo, carpeta_vault, carpetas_de_notas, normalizar, ruta

MODELO_EMBEDDINGS = "jinaai/jina-embeddings-v2-base-es"
VERSION_INDICE = 1
MAX_CARACTERES = 1200
CANDIDATOS = 40   # cuántos resultados aporta cada buscador antes de fusionarlos
RRF_K = 60

PATRON_MARCA = re.compile(r"^\[(?:(P\d+)\s+)?(\d{1,2}:\d{2}:\d{2})\]\s*")
PATRON_MARCA_ALT = re.compile(r"^(P\d+)\s+\[(\d{1,2}:\d{2}:\d{2})\]\s*")
PATRON_PARTE = re.compile(r"^===\s*Parte\s+(\d+)\b.*===\s*$")
PATRON_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
PATRON_CALLOUT = re.compile(r"^>\s*\[!([\w-]+)\]([-+]?)\s*(.*)$")


@dataclass
class Fragmento:
    id: str
    texto: str
    nota: str               # título de la nota (nombre del archivo sin .md)
    ruta: str               # ruta dentro del vault
    fecha: str = ""
    fuente: str = "apuntes"  # apuntes | claude | transcripcion
    seccion: str = ""
    minuto: str = ""        # "00:47:12" o "P2 00:47:12"

    def referencia(self) -> str:
        if self.fuente == "transcripcion":
            return f"{self.nota} · minuto {self.minuto}" if self.minuto else f"{self.nota} · transcripción"
        return f"{self.nota} › {self.seccion}" if self.seccion else self.nota

    def etiqueta_fuente(self) -> str:
        return {"transcripcion": "transcripción", "claude": "agregado de Claude"}.get(self.fuente, "apuntes")

    def documento(self) -> str:
        """Lo que se indexa: el texto con un encabezado de contexto (de qué nota y de qué parte viene)."""
        donde = f"minuto {self.minuto}" if self.minuto else self.seccion
        return f"{self.nota} | {donde}\n{self.texto}" if donde else f"{self.nota}\n{self.texto}"

    def metadatos(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("id", "texto")}


# ---------------------------------------------------------------- partir notas

def leer_frontmatter(md: str) -> tuple[dict, str]:
    if not md.startswith("---"):
        return {}, md
    fin = md.find("\n---", 3)
    if fin < 0:
        return {}, md
    datos = {}
    for linea in md[3:fin].splitlines():
        if ":" in linea and not linea.startswith((" ", "\t", "-")):
            clave, valor = linea.split(":", 1)
            datos[clave.strip()] = valor.strip().strip('"').strip("'")
    resto = md[fin + 4:]
    return datos, resto.lstrip("\n")


def separar_transcripcion(cuerpo: str) -> tuple[str, list[str]]:
    """Saca el bloque plegado `> [!quote]- Transcripción completa` del resto de la nota."""
    lineas = cuerpo.splitlines()
    for i, linea in enumerate(lineas):
        m = PATRON_CALLOUT.match(linea.strip())
        if m and "transcrip" in normalizar(m.group(3)):
            j = i + 1
            transcripcion = []
            while j < len(lineas) and lineas[j].lstrip().startswith(">"):
                transcripcion.append(re.sub(r"^\s*>\s?", "", lineas[j]))
                j += 1
            return "\n".join(lineas[:i] + lineas[j:]), transcripcion
    return cuerpo, []


def parrafos_transcripcion(lineas: list[str]) -> list[tuple[str, str]]:
    """(marca, texto) por párrafo. La marca es "00:47:12", o "P2 00:47:12" en clases de varias partes."""
    parrafos: list[tuple[str, str]] = []
    parte = ""
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        mp = PATRON_PARTE.match(linea)
        if mp:
            parte = f"P{mp.group(1)}"
            continue
        m = PATRON_MARCA.match(linea) or PATRON_MARCA_ALT.match(linea)
        if m:
            marca = f"{m.group(1) or parte} {m.group(2)}".strip()
            parrafos.append((marca, linea[m.end():].strip()))
        elif parrafos:
            parrafos[-1] = (parrafos[-1][0], f"{parrafos[-1][1]} {linea}")
        else:
            parrafos.append(("", linea))
    return parrafos


def _parte(marca: str) -> str:
    return marca.split()[0] if marca.startswith("P") and " " in marca else ""


def tramos_de_transcripcion(parrafos: list[tuple[str, str]], maximo: int = MAX_CARACTERES) -> list[tuple[str, str]]:
    """Junta párrafos seguidos hasta ~maximo caracteres, sin mezclar partes de la grabación."""
    tramos, actual, largo, inicio = [], [], 0, ""
    for marca, texto in parrafos:
        if actual and (largo + len(texto) > maximo or _parte(marca) != _parte(inicio)):
            tramos.append((inicio, "\n".join(actual)))
            actual, largo = [], 0
        if not actual:
            inicio = marca
        actual.append(f"[{marca}] {texto}" if marca else texto)
        largo += len(texto) + 1
    if actual:
        tramos.append((inicio, "\n".join(actual)))
    return tramos


def bloques_de_apuntes(texto: str) -> list[tuple[str, str, str]]:
    """(fuente, sección, texto) siguiendo los títulos. Los bloques [!claude] van aparte."""
    titulos: dict[int, str] = {}
    bloques: list[list] = []
    actual = None
    en_claude = en_codigo = False

    def seccion() -> str:
        return " › ".join(t for n, t in sorted(titulos.items()) if n >= 2)

    def abrir(fuente: str) -> list:
        bloque = [fuente, seccion(), []]
        bloques.append(bloque)
        return bloque

    for linea in texto.splitlines():
        limpia = linea.strip()
        if limpia.startswith("```"):
            en_codigo = not en_codigo
        if not en_codigo:
            mt = PATRON_TITULO.match(linea)
            if mt:
                nivel = len(mt.group(1))
                for n in [n for n in titulos if n >= nivel]:
                    del titulos[n]
                titulos[nivel] = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", r"\1", mt.group(2)).strip()
                actual, en_claude = None, False
                continue
            mc = PATRON_CALLOUT.match(limpia)
            if mc and mc.group(1).lower() == "claude":
                actual, en_claude = abrir("claude"), True
                if mc.group(3):
                    actual[2].append(mc.group(3))
                continue
            if en_claude:
                if limpia.startswith(">"):
                    actual[2].append(re.sub(r"^\s*>\s?", "", linea))
                    continue
                actual, en_claude = None, False
            if limpia.startswith(">"):  # otros callouts ([!info], [!example]...): se quedan sin el ">"
                linea = re.sub(r"^\s*>\s?", "", linea)
                linea = re.sub(r"^\[![\w-]+\][-+]?\s*", "", linea)
        if actual is None or actual[0] != "apuntes":
            actual = abrir("apuntes")
        actual[2].append(linea)

    salida = []
    for fuente, sec, lineas in bloques:
        cuerpo = re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()
        if len(re.sub(r"\W", "", cuerpo)) >= 15:
            salida.append((fuente, sec, cuerpo))
    return salida


def partir(texto: str, maximo: int = MAX_CARACTERES) -> list[str]:
    """Corta en párrafos completos hasta ~maximo caracteres; un párrafo gigante se corta en un espacio."""
    trozos, actual = [], ""
    for parrafo in [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]:
        while len(parrafo) > maximo:
            corte = parrafo.rfind(" ", 0, maximo)
            corte = corte if corte > maximo // 2 else maximo
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(parrafo[:corte].strip())
            parrafo = parrafo[corte:].strip()
        if actual and len(actual) + len(parrafo) + 2 > maximo:
            trozos.append(actual)
            actual = parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        trozos.append(actual)
    return trozos


def transcripcion_de_trabajo(cfg: dict, md: str) -> list[str]:
    """Si la nota no trae la transcripción plegada, la busca en la carpeta de trabajo (fecha-dia.txt)."""
    datos, cuerpo = leer_frontmatter(md)
    if separar_transcripcion(cuerpo)[1] or not datos.get("fecha") or not datos.get("dia"):
        return []
    txt = carpeta_trabajo(cfg) / f"{datos['fecha']}-{datos['dia']}.txt"
    return txt.read_text(encoding="utf-8").splitlines() if txt.is_file() else []


def fragmentos_de_nota(md: str, ruta_rel: str, transcripcion_aparte: list[str] | None = None) -> list[Fragmento]:
    datos, cuerpo = leer_frontmatter(md)
    nota = Path(ruta_rel).stem
    fecha = datos.get("fecha", "")
    apuntes, transcripcion = separar_transcripcion(cuerpo)
    if not transcripcion and transcripcion_aparte:
        transcripcion = transcripcion_aparte
    base = hashlib.sha1(ruta_rel.encode("utf-8")).hexdigest()[:12]
    salida: list[Fragmento] = []
    for fuente, seccion, texto in bloques_de_apuntes(apuntes):
        for trozo in partir(texto):
            salida.append(Fragmento(f"{base}-{len(salida) + 1}", trozo, nota, ruta_rel, fecha, fuente, seccion))
    for marca, texto in tramos_de_transcripcion(parrafos_transcripcion(transcripcion)):
        salida.append(Fragmento(f"{base}-{len(salida) + 1}", texto, nota, ruta_rel, fecha, "transcripcion", "", marca))
    return salida


# ---------------------------------------------------------------- BM25

STOPWORDS = set("""
a al algo algún alguna algunas alguno algunos ante antes aquí así aún cada como con contra cual cuales cuando de del
desde donde dos el él ella ellas ello ellos en entre era eran es esa esas ese eso esos esta está estaba estado están
estar estas este esto estos fue fueron ha había han hasta hay la las le les lo los más me mi mientras muy nada ni no
nos o otra otras otro otros para pero poco por porque qué que quien se sea según ser si sí sin sobre son su sus
también te tiene tienen todo todos tu un una uno unos y ya yo cómo cuál cuándo dónde
""".split())
STOPWORDS = {normalizar(p) for p in STOPWORDS}


class _Raiz:
    """Reduce las palabras a su raíz (amortización, amortizaciones -> amortiz) si snowballstemmer está instalado."""
    _stemmer = None
    _cargado = False

    @classmethod
    def palabras(cls, palabras: list[str]) -> list[str]:
        if not cls._cargado:
            cls._cargado = True
            try:
                import snowballstemmer
                cls._stemmer = snowballstemmer.stemmer("spanish")
            except ImportError:
                cls._stemmer = None
        return cls._stemmer.stemWords(palabras) if cls._stemmer else palabras


def terminos(texto: str) -> list[str]:
    palabras = [p for p in re.findall(r"[a-z0-9ñ]+", normalizar(texto)) if p not in STOPWORDS and len(p) > 1]
    return _Raiz.palabras(palabras)


def ranking_bm25(consulta: str, documentos: dict[str, list[str]], k1: float = 1.5, b: float = 0.75) -> list[str]:
    q = set(terminos(consulta))
    if not q or not documentos:
        return []
    n = len(documentos)
    promedio = sum(len(d) for d in documentos.values()) / n or 1.0
    df = Counter(t for d in documentos.values() for t in set(d) if t in q)
    puntajes = {}
    for clave, doc in documentos.items():
        frecuencia = Counter(t for t in doc if t in q)
        if not frecuencia:
            continue
        puntaje = 0.0
        for t, f in frecuencia.items():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            puntaje += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(doc) / promedio))
        puntajes[clave] = puntaje
    return sorted(puntajes, key=puntajes.get, reverse=True)


def fusionar(listas: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: premia lo que sale arriba en cualquiera de los buscadores."""
    puntaje: Counter = Counter()
    for lista in listas:
        for posicion, clave in enumerate(lista, 1):
            puntaje[clave] += 1.0 / (k + posicion)
    return puntaje.most_common()


# ---------------------------------------------------------------- embeddings

class EmbebedorFastembed:
    """Embeddings locales en CPU con ONNX (sin PyTorch). El modelo se descarga una vez."""

    def __init__(self, modelo: str = MODELO_EMBEDDINGS, carpeta_cache: Path | None = None):
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ErrorExtension("Falta el paquete fastembed. Corre: "
                                 ".venv/bin/pip install -r requirements-extensiones.txt") from None
        self.nombre = modelo
        cache = carpeta_cache or ruta("~/.cache/apuntes-clase/fastembed")
        cache.mkdir(parents=True, exist_ok=True)
        print(f"Cargando el modelo de embeddings {modelo} (la primera vez se descarga)...", flush=True)
        try:
            self._modelo = TextEmbedding(model_name=modelo, cache_dir=str(cache))
        except Exception as e:  # noqa: BLE001
            raise ErrorExtension(f"No pude cargar el modelo de embeddings {modelo}: {str(e)[:300]}") from None

    def documentos(self, textos: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._modelo.embed(textos, batch_size=16)]

    def consulta(self, texto: str) -> list[float]:
        embebe = getattr(self._modelo, "query_embed", None) or self._modelo.embed
        return next(iter(embebe([texto]))).tolist()


# ---------------------------------------------------------------- la despensa

class Despensa:
    def __init__(self, cfg: dict, embebedor=None):
        self.cfg = cfg
        self.carpeta = carpeta_trabajo(cfg) / "indice"
        self.modelo = getattr(embebedor, "nombre", None) or str(
            cfg.get("preguntas", {}).get("modelo_embeddings", MODELO_EMBEDDINGS))
        self._embebedor = embebedor
        self._coleccion = None

    # -- piezas que se cargan solo si hacen falta
    @property
    def embebedor(self):
        if self._embebedor is None:
            self._embebedor = EmbebedorFastembed(self.modelo)
        return self._embebedor

    def coleccion(self):
        if self._coleccion is None:
            try:
                import chromadb
                from chromadb.config import Settings
            except ImportError:
                raise ErrorExtension("Falta el paquete chromadb. Corre: "
                                     ".venv/bin/pip install -r requirements-extensiones.txt") from None
            self.carpeta.mkdir(parents=True, exist_ok=True)
            cliente = chromadb.PersistentClient(path=str(self.carpeta / "chroma"),
                                                settings=Settings(anonymized_telemetry=False))
            self._cliente = cliente
            self._coleccion = cliente.get_or_create_collection(
                name="apuntes", embedding_function=None, configuration={"hnsw": {"space": "cosine"}})
        return self._coleccion

    def _reiniciar(self) -> None:
        self.coleccion()
        self._cliente.delete_collection("apuntes")
        self._coleccion = None

    @property
    def _ruta_manifiesto(self) -> Path:
        return self.carpeta / "manifiesto.json"

    def manifiesto(self) -> dict:
        try:
            return json.loads(self._ruta_manifiesto.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _guardar_manifiesto(self, datos: dict) -> None:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        temporal = self._ruta_manifiesto.with_suffix(".parcial")
        temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        temporal.replace(self._ruta_manifiesto)

    # -- qué hay en el vault
    def archivos(self) -> dict[str, Path]:
        vault = carpeta_vault(self.cfg)
        indice = str(self.cfg.get("diplomado", {}).get("indice", "")).strip()
        encontrados = {}
        for carpeta in carpetas_de_notas(self.cfg):
            for archivo in sorted(carpeta.rglob("*.md")):
                relativa = archivo.relative_to(vault)
                if any(parte.startswith(".") for parte in relativa.parts) or archivo.stem == indice:
                    continue
                encontrados[relativa.as_posix()] = archivo
        return encontrados

    def _transcripcion_aparte(self, md: str) -> list[str]:
        return transcripcion_de_trabajo(self.cfg, md)

    # -- actualizar
    def actualizar(self, reconstruir: bool = False, avisar=print) -> dict:
        archivos = self.archivos()
        manifiesto = self.manifiesto()
        vigente = manifiesto.get("modelo") == self.modelo and manifiesto.get("version") == VERSION_INDICE
        if reconstruir or not vigente:
            # Otro modelo de embeddings (u otra versión del índice) no es comparable: se arma de cero
            if reconstruir or self.coleccion().count() > 0:
                self._reiniciar()
            manifiesto = {"modelo": self.modelo, "version": VERSION_INDICE, "archivos": {}}
        previos = manifiesto["archivos"]
        coleccion = self.coleccion()

        borradas = [r for r in previos if r not in archivos]
        for relativa in borradas:
            if previos[relativa]["ids"]:
                coleccion.delete(ids=previos[relativa]["ids"])
            del previos[relativa]

        nuevas = actualizadas = 0
        pendientes = []
        for relativa, archivo in archivos.items():
            md = archivo.read_text(encoding="utf-8", errors="replace")
            aparte = self._transcripcion_aparte(md)
            huella = hashlib.sha1((md + "\n".join(aparte)).encode("utf-8")).hexdigest()
            if previos.get(relativa, {}).get("huella") != huella:
                pendientes.append((relativa, archivo, md, aparte, huella))
        if pendientes:
            self.embebedor  # carga el modelo antes de empezar (y falla aquí si no se puede)
        for relativa, archivo, md, aparte, huella in pendientes:
            fragmentos = fragmentos_de_nota(md, relativa, aparte)
            if previos.get(relativa, {}).get("ids"):
                coleccion.delete(ids=previos[relativa]["ids"])
                actualizadas += 1
            else:
                nuevas += 1
            if fragmentos:
                avisar(f"  Guardando en la despensa: {archivo.stem} ({len(fragmentos)} fragmentos)")
                documentos = [f.documento() for f in fragmentos]
                coleccion.add(
                    ids=[f.id for f in fragmentos],
                    embeddings=self.embebedor.documentos(documentos),
                    documents=[f.texto for f in fragmentos],
                    metadatas=[{**f.metadatos(), "terminos": " ".join(terminos(d))}
                               for f, d in zip(fragmentos, documentos)])
            previos[relativa] = {"huella": huella, "ids": [f.id for f in fragmentos]}
            self._guardar_manifiesto(manifiesto)  # nota por nota: si se interrumpe, no se repite lo hecho
        self._guardar_manifiesto(manifiesto)
        return {"notas": len(archivos), "nuevas": nuevas, "actualizadas": actualizadas,
                "borradas": len(borradas), "fragmentos": coleccion.count()}

    # -- buscar
    def notas_que_coinciden(self, filtro: str) -> set[str]:
        buscado = normalizar(filtro).strip()
        datos = self.coleccion().get(include=["metadatas"])
        notas = {(m["nota"], m.get("fecha", "")) for m in datos["metadatas"]}
        return {nota for nota, fecha in notas if buscado in normalizar(nota) or buscado == fecha}

    def buscar(self, consulta: str, k: int = 8, notas: set[str] | None = None,
               excluir: set[str] = frozenset()) -> list[tuple[Fragmento, float]]:
        coleccion = self.coleccion()
        if coleccion.count() == 0:
            return []
        datos = coleccion.get(include=["documents", "metadatas"])
        fragmentos, documentos = {}, {}
        for clave, texto, meta in zip(datos["ids"], datos["documents"], datos["metadatas"]):
            if clave in excluir or (notas and meta["nota"] not in notas):
                continue
            meta = dict(meta)
            documentos[clave] = meta.pop("terminos", "").split()
            fragmentos[clave] = Fragmento(id=clave, texto=texto, **meta)
        if not fragmentos:
            return []

        donde = {"nota": {"$in": sorted(notas)}} if notas else None
        cuantos = min(CANDIDATOS + len(excluir), coleccion.count())
        r = coleccion.query(query_embeddings=[self.embebedor.consulta(consulta)], n_results=cuantos,
                            where=donde, include=["distances"])
        semanticos = [c for c in r["ids"][0] if c in fragmentos][:CANDIDATOS]
        exactos = ranking_bm25(consulta, documentos)[:CANDIDATOS]
        return [(fragmentos[c], p) for c, p in fusionar([semanticos, exactos])[:k]]
