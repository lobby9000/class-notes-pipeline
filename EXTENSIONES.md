# Extensiones: modo API y preguntas a tus clases

Dos extensiones para la pizzería de apuntes:

1. **Modo API.** Las notas se pueden escribir con la API de Anthropic en lugar de Claude Code. Cada tarea usa su modelo (Opus para clases, Sonnet para reuniones, Haiku para preguntas) y cada llamada queda registrada con tokens, costo y latencia.
2. **Preguntas a tus clases.** `pizza pregunta "..."` busca en tus notas y transcripciones y responde citando la clase y el minuto. Si la respuesta no está en tus apuntes, lo dice en lugar de inventarla.

Con `[claude] backend = "cli"` (el valor de siempre) el pipeline se comporta exactamente igual que antes. Las preguntas funcionan en los dos modos.

## Instalación

En este repositorio los ganchos ya están en `apuntes.py`, así que solo faltan los paquetes:

```bash
cd ~/apuntes-clase
.venv/bin/python aplicar_extensiones.py      # instala los paquetes y corre todas las pruebas
```

Si tu `apuntes.py` es de antes de las extensiones, el mismo script le agrega los ganchos:

1. Respalda `apuntes.py`, `config.ejemplo.toml`, `README.md` y `CLAUDE.md` en `.respaldo-extensiones/`.
2. Agrega a `apuntes.py` tres ganchos de una o dos líneas y tres funciones cortas antes de `main()`.
3. Instala `anthropic`, `chromadb`, `fastembed` y `snowballstemmer` en tu `.venv`.
4. Corre todas las pruebas, las tuyas y las nuevas. Si alguna falla, regresa todo como estaba.

Si algo no te convence: `.venv/bin/python aplicar_extensiones.py --revertir`.

## Preguntas a tus clases

```bash
pizza despensa                                   # arma o actualiza el índice
pizza pregunta "¿cómo se calcula el ISR de un salario?"
pizza pregunta "¿qué vimos de la TIR?" --clase "25 septiembre"
pizza pregunta "¿qué es la UMA?" --ver-fragmentos   # muestra qué fragmentos recibió Claude
```

La respuesta se ve más o menos así:

```text
El ISR de un salario se calcula con la tabla del artículo 96, restando el límite inferior [1].

Fuentes:
  [1] Clase 24 septiembre · minuto 00:47:12 (transcripción)

claude-haiku-5-5 · 1 llamada(s), 0 búsqueda(s) extra · 4,812 tokens · $0.0006 USD · 2.3 s
```

- `pizza pregunta` actualiza la despensa sola antes de responder: solo procesa las notas nuevas o que cambiaron.
- La primera vez descarga el modelo de embeddings (unos 640 MB) en `~/.cache/apuntes-clase/fastembed`. Después ya no usa internet para buscar.
- El índice vive en `<carpeta de trabajo>/indice/`, nunca en tu vault.
- En modo `cli`, la pregunta la responde Claude Code con Haiku y va contra tu suscripción. En modo `api`, la responde un agente que puede pedir hasta dos búsquedas más si los primeros fragmentos no alcanzan.

## Modo API

1. Crea una API key en [console.anthropic.com](https://console.anthropic.com) y agrega saldo en Billing. La API se cobra aparte de tu suscripción de Claude.
2. Guarda la clave en un archivo que solo tú puedas leer:

   ```bash
   mkdir -p ~/.config/apuntes-clase
   nano ~/.config/apuntes-clase/anthropic_api_key     # pega la clave y guarda
   chmod 600 ~/.config/apuntes-clase/anthropic_api_key
   ```

3. En tu `config.toml`, dentro de la sección `[claude]`, agrega `backend = "api"`.
4. Antes de hornear, si quieres, cotiza: `pizza cotiza "24 septiembre"`. Después de hornear: `pizza cuenta`.

Cada tarea usa su modelo. Se cambia en `config.toml`:

```toml
[claude.modelos]
clase = "opus"        # clases largas: la mejor calidad
reunion = "sonnet"    # reuniones: más cortas, la mitad de precio
pregunta = "haiku"    # preguntas: rápido y barato
```

Precios de referencia por millón de tokens ([precios de la API](https://platform.claude.com/docs/en/about-claude/pricing), octubre 2026):

| Modelo | Entrada | Salida |
| --- | --- | --- |
| Claude Opus 5.5 | $4 | $20 |
| Claude Sonnet 5.5 | $2 | $10 |
| Claude Haiku 5.5, hasta 100 mil tokens de entrada | $0.10 | $0.50 |
| Claude Haiku 5.5, más de 100 mil tokens de entrada | $0.50 | $2.50 |

En Haiku 5.5 el umbral cuenta toda la entrada de la petición, incluida la caché, y `pizza cuenta` aplica el precio que corresponde a cada llamada.

Una clase de cuatro horas son unos 60 mil tokens de entrada. Con unos 18 mil de salida, la nota cuesta cerca de $0.60 USD con Opus, $0.30 con Sonnet y $0.015 con Haiku. Una pregunta con Haiku cuesta alrededor de una décima de centavo. Si los precios cambian, se actualizan en `[api.precios]` sin tocar código.

## Comandos nuevos

| Pizzería | Nombre de siempre | Qué hace |
| --- | --- | --- |
| `pizza pregunta "..."` | `apuntes preguntar` | Responde con tus notas y transcripciones, citando clase y minuto |
| `pizza despensa` | `apuntes indexar` | Actualiza el índice; `--reconstruir` lo arma desde cero |
| `pizza cuenta` | `apuntes costos` | Tokens, costo y latencia de cada llamada, con totales por modelo y por tarea |
| `pizza cotiza "24 septiembre"` | `apuntes estimar` | Cuánto costaría esa nota con Opus, Sonnet o Haiku |

## Cómo funciona

**Modo API.** El gancho en `hacer_apuntes()` solo cambia quién escribe la nota: la entrada es la misma que recibe Claude Code (transcripción, conceptos existentes e instrucciones). La nota se pide en streaming, así no hay límite de tiempo por petición y se mide cuánto tarda el primer token. Los errores de la API se traducen a lo que hay que hacer (clave, saldo, modelo inexistente, límite de uso) y el SDK reintenta solo los errores pasajeros con espera exponencial.

**La despensa.** Cada nota se parte en fragmentos de hasta 1,200 caracteres:

- las secciones de los apuntes, con su ruta de títulos (por ejemplo, "Desarrollo y práctica › Amortización");
- los bloques `[!claude]` aparte, para poder avisar cuando una respuesta se apoya en un agregado de Claude y no en lo que dijo el profesor;
- la transcripción en tramos que conservan el minuto en que empiezan, sin mezclar partes de una clase grabada en varios videos.

**La búsqueda es híbrida.** Los embeddings multilingües (jina-embeddings-v2-base-es, en ONNX y en CPU) encuentran por significado: "¿cuánto vale la UMA?" llega al minuto donde el profesor dijo "unidad de medida". BM25 con raíces en español asegura las coincidencias exactas, como siglas (ISR, UMA, TIR). Las dos listas se combinan con Reciprocal Rank Fusion. El índice es incremental: cada nota guarda una huella SHA-1 y solo se vuelve a procesar lo que cambió. Si cambias el modelo de embeddings, el índice se rearma solo.

**El agente.** El primer turno ya trae los 8 mejores fragmentos, así que casi todas las preguntas se resuelven en una sola llamada. Si no alcanzan, Claude usa la herramienta `buscar_apuntes` con otras palabras (máximo dos búsquedas extra).

**Contra respuestas inventadas:**

- Claude solo recibe fragmentos de tus apuntes, con la regla de citar cada oración.
- Si la respuesta no está, debe contestar `NO_ENCONTRADO` y el programa te muestra lo más cercano que encontró.
- Una respuesta que no cita ningún fragmento real no se muestra.
- Las citas a fragmentos que no existen se quitan y se avisa.
- Hay aviso si más de la mitad de la respuesta no cita, o si se apoya en agregados de Claude.

**La cuenta.** Cada llamada a la API agrega una línea a `<carpeta de trabajo>/costos.jsonl`: tarea, sesión, modelo, tokens, costo, segundos y tiempo al primer token. `pizza cotiza` cuenta los tokens de entrada con el endpoint de conteo de la API, que es gratis, y estima la salida con la proporción de tus notas anteriores.

## Archivos

| Archivo | Para qué |
| --- | --- |
| `extensiones/llm.py` | Llamadas a la API (streaming, herramientas, conteo de tokens, errores) y a Claude Code |
| `extensiones/notas_api.py` | Modo API para las notas |
| `extensiones/indice.py` | La despensa: fragmentos, embeddings, Chroma, BM25 y fusión |
| `extensiones/preguntas.py` | El agente de preguntas y la revisión de citas |
| `extensiones/cuenta.py` | `pizza cuenta` y `pizza cotiza` |
| `extensiones/cli.py` | Los subcomandos nuevos |
| `extensiones/comun.py` | Modelos por tarea, precios, carpetas y registro de costos |
| `pruebas/test_extensiones.py` | 37 pruebas sin red |
| `aplicar_extensiones.py` | Agrega los ganchos a un `apuntes.py` que no los tiene, instala los paquetes y corre las pruebas; `--revertir` los quita |

## Pruebas

```bash
.venv/bin/python -m unittest discover -s pruebas -v
```

Las pruebas nuevas no usan internet ni descargan modelos. Usan una API falsa y también el SDK real de Anthropic con un transporte HTTP falso, que valida el streaming, el uso de herramientas y el conteo de tokens. Los embeddings son falsos, Chroma es real en una carpeta temporal y hay un `claude` falso para el modo cli.

## Problemas comunes

| Mensaje | Qué hacer |
| --- | --- |
| Falta tu API key | Sigue el paso 2 del modo API, o define `ANTHROPIC_API_KEY` |
| La API rechazó tu clave | Revisa que la copiaste completa y que sigue activa |
| Tu cuenta de la API no tiene saldo | Agrega crédito en console.anthropic.com, en Billing |
| No pude cargar el modelo de embeddings | La primera vez necesita internet para descargarlo |
| La laptop se queda sin memoria al indexar | Cierra el navegador, o usa el modelo ligero en `[preguntas] modelo_embeddings` |
| Las respuestas no encuentran algo que sí está | `pizza pregunta "..." --ver-fragmentos`, o `pizza despensa --reconstruir` |
