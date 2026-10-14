"""Extensiones del pipeline de apuntes.

- Modo API: escribe las notas con la API de Anthropic, elige Opus, Sonnet o Haiku
  según la tarea y registra tokens, costo y latencia de cada llamada.
- Preguntas a tus clases: indexa notas y transcripciones del vault (Chroma + BM25)
  y un agente responde citando clase y minuto, o dice que no lo encontró.

Estos módulos no importan apuntes.py ni cargan sus dependencias (anthropic,
chromadb, fastembed) hasta que se usan, así que el pipeline funciona igual sin ellas.
"""

__version__ = "1.0.0"
