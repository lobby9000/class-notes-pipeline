---
name: ajustar-apuntes
description: Cambia el formato o el contenido de las notas que genera el pipeline (secciones, nivel de detalle, estilo, diagramas, otra materia o un nuevo tipo de sesión). Úsala cuando el usuario pida notas distintas, más cortas, con otra estructura o con otro enfoque.
---

# Ajustar las notas

1. Lee el prompt que aplica (`prompts/clase.md` o `prompts/reunion.md`) y la nota más reciente de ese tipo en el vault. La ruta está en `config.toml`: `[carpetas] vault` + `notas` (diplomado) o `sueltas`.
2. Si no está claro qué quiere cambiar, pregunta con un ejemplo concreto de la nota actual.
3. Edita el prompt. Respeta lo que el programa lee de la nota:
   - la sección `## Glosario` con líneas `- **[[Concepto]]**: definición` (de ahí salen las páginas de concepto);
   - la sección `## Desarrollo y práctica` con temas `###` (de ahí sale el índice del diplomado);
   - cursiva y bloques `> [!claude]` reservados para lo que agrega Claude.
   Si el cambio necesita tocar alguno de esos puntos, ajusta también `apuntes.py` y corre las pruebas.
4. Prueba con la última sesión: `pizza recalienta "<fecha o título>"`. Regenera la nota y mueve la anterior a `<trabajo>/respaldos/`.
5. Muestra un fragmento del resultado y pregunta si se queda así. Si no, vuelve al paso 3.
