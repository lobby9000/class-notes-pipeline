Vas a convertir la transcripción de {descripcion} en una nota de Obsidian. El tema general es {materia}.

## Para qué sirve la nota

- Saber qué se decidió, quién quedó de hacer qué y para cuándo.
- Recuperar los argumentos, cifras y datos que se dieron sin volver a escuchar la grabación.
- Conectar la reunión con los temas del vault mediante enlaces.

## De dónde viene el texto

La transcripción la hizo Whisper de forma automática. No distingue quién habla y trae errores de reconocimiento. Si por el contexto se nota quién dice algo (por ejemplo, se presenta o le hacen una pregunta por nombre), atribúyelo; si no, no inventes nombres. Puede venir en varias partes (`=== Parte 2 de 3 ===`); trátalas como una sola reunión. Las marcas `[00:12:30]` o `[P2 00:12:30]` son el minuto de la grabación.

## Regla principal: no perder información

No resumas para acortar. Cada propuesta, argumento, dato, cifra, acuerdo, desacuerdo y pendiente tiene que quedar. Puedes reordenar por tema y quitar muletillas.

## Qué se dijo y qué agregas tú

- **Texto normal**: lo que se dijo en la reunión.
- ***Cursiva***: tus agregados o aclaraciones cortas. Reserva la cursiva solo para esto.
- **Bloques `> [!claude] Título`**: tus agregados más largos, como contexto, una corrección o un riesgo que nadie mencionó.
- **(?)**: algo que no se entiende en la transcripción.

## Estructura

Devuelve estas secciones, en este orden y con estos títulos exactos, sin título principal `#`.

### `## Índice rápido`
Una línea por tema tratado: `- [[#Nombre exacto del tema]]: qué se habló, en una frase`.

### `## Decisiones`
Cada decisión tomada, con la marca de tiempo. Si no hubo: "Ninguna".

### `## Pendientes`
Una tabla con columnas `Pendiente | Responsable | Fecha`. Deja `(?)` donde no se dijo.

### `## Desarrollo`
Una sección `###` por tema, en orden. En la primera línea debajo del título, la marca de tiempo donde empieza (`Desde [00:12:30]`). Dentro, todo lo que se dijo sobre ese tema. Si se explicó un proceso o flujo, agrega un diagrama Mermaid.

### `## Preguntas abiertas`
Lo que quedó sin resolver.

### `## Glosario`
Los conceptos que enlazaste, en orden alfabético: `- **[[Concepto]]**: definición de una línea`, en cursiva si la definición es tuya. Si no enlazaste ninguno, omite esta sección.

## Enlaces y formato

- Enlaza con `[[ ]]` los conceptos, proyectos y herramientas importantes, siempre con el mismo nombre, en singular y con mayúscula inicial. Si aparecen en `<conceptos_existentes>`, usa exactamente ese nombre. Sin `# | ^ : [ ] / \` en los nombres.
- Diagramas en ```` ```mermaid ```` con `flowchart TD`, texto de cada nodo entre comillas dobles y con acentos normales.
- No generes gráficas; si una ayudaría, sugiérela en `> [!tip] Gráfica sugerida`.
- Títulos de sección en texto plano, sin enlaces ni marcas de tiempo.
- Devuelve solo el markdown de la nota, sin saludo ni comentarios y sin envolverla en un bloque de código.
