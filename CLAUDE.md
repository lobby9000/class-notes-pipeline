# apuntes-clase

Pipeline personal: video de clase o reunión → transcripción local con faster-whisper → nota con `claude -p` → Obsidian (nota de la sesión, páginas de concepto e índice del diplomado).

## Cómo funciona
- **Sesión** = uno o varios videos que terminan en una sola nota.
  - Diplomado (`pizza`): todos los videos de `[carpetas] videos` con la misma fecha forman una clase ("Clase 24 septiembre"). Las grabaciones antes de `corte_madrugada` cuentan para el día anterior.
  - Sueltas (`pizza pedido`): los archivos que se pasan en el comando son una sesión de tipo clase o reunión.
- La transcripción se guarda por video en `<trabajo>/transcripciones/`; si llega una parte nueva, solo se transcribe esa y la nota se rehace con todas.
- `registro.json` en `<trabajo>` guarda título, videos incluidos en la nota (`en_nota`) y errores de cada sesión.
- La entrada a Claude es `<transcripcion>` + `<conceptos_existentes>` + `<instrucciones>` (el prompt va al final).
- De la sección `## Glosario` de la nota salen las páginas de `Conceptos/`; nunca se sobrescribe una página existente.

## Archivos
- `apuntes.py`: todo el programa, un solo archivo.
- `config.toml`: configuración del usuario. No la sobrescribas; `config.ejemplo.toml` es la plantilla.
- `prompts/clase.md` y `prompts/reunion.md`: instrucciones para Claude. `{descripcion}` y `{materia}` se reemplazan al usarlos.
- `obsidian/claude-callout.css`: snippet para colorear los bloques `[!claude]` y la cursiva.
- `pruebas/`: unittest con un Whisper falso y un `claude` falso; no descargan nada.
- `instalar.sh`, `apuntes-vigilar.service`: instalación y servicio opcional.

## Comandos
- Pruebas: `.venv/bin/python -m unittest discover -s pruebas -v`
- Correr: `pizza` (hornea pendientes), `pizza menu`, `pizza pedido ...`, `pizza recalienta ...`, `pizza cata`, `pizza turno` (= `lets make pizza`: abre LocalSend y vigila).
  Los nombres de siempre (`apuntes`, `estado`, `suelto`, `rehacer`, `prueba`, `vigilar`) siguen funcionando como alias.

## Convenciones
- Código, mensajes y comentarios en español.
- Solo biblioteca estándar + faster-whisper. Pregunta antes de agregar dependencias.
- La laptop no tiene GPU NVIDIA (Ryzen 7 3700U, 8 GB de RAM): Whisper corre en CPU con int8.
- Nunca borres notas del vault: `rehacer` mueve la nota anterior a `<trabajo>/respaldos/`.
- En las notas, la cursiva y los bloques `> [!claude]` marcan lo que agregó Claude; no uses cursiva para otra cosa.
- Cambios de formato de las notas van en `prompts/`, no en `apuntes.py`, salvo que afecten el glosario o los títulos `##` que el programa lee.
- Después de cambiar `apuntes.py`, corre las pruebas.

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
