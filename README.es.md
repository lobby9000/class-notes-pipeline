# Pipeline de apuntes de clase

[English version](README.md)

Convierte las grabaciones de clase (y de reuniones) en notas de Obsidian sin pasos manuales:

```
videos .mp4  →  Whisper en tu CPU  →  transcripción  →  claude -p  →  nota + páginas de concepto + índice
```

La transcripción corre local y gratis con [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Solo el texto se manda a Claude, usando tu cuenta de Claude Code.

## Instalación

```bash
git clone https://github.com/lobby9000/class-notes-pipeline.git ~/apuntes-clase
cd ~/apuntes-clase
bash instalar.sh
nano config.toml        # pon la ruta de tu vault de Obsidian
```

## Uso

Los comandos tienen nombres de pizzería; los de siempre (`apuntes`, `apuntes estado`, etc.) también funcionan.

| Comando | Nombre de siempre | Qué hace |
| --- | --- | --- |
| `pizza` | `apuntes` | Hornea las clases nuevas del diplomado (`~/Videos/raw_finance`) |
| `pizza menu` | `apuntes estado` | Qué sesiones ya están listas |
| `pizza pedido clase.mp4 --materia "estadística"` | `apuntes suelto` | Una clase o reunión aparte (`--tipo reunion --titulo "..."`) |
| `pizza recalienta "24 septiembre"` | `apuntes rehacer` | Regenera una nota; guarda la anterior. `--transcribir` rehace también la transcripción |
| `pizza cata` | `apuntes prueba` | Cuánto tarda cada modelo de Whisper en tu laptop |
| `lets make pizza` o `pizza turno` | `apuntes vigilar` | Abre LocalSend y se queda esperando pedidos: hornea cada clase nueva que llegue |

Todos los videos del diplomado grabados el mismo día forman una sola clase y una sola nota, `Clase 24 septiembre`. Si llega una parte después, la nota se rehace con todas.

## Preguntas a tus clases y modo API

Una extensión opcional en [`extensiones/`](extensiones/). Detalles, configuración y decisiones de diseño en [EXTENSIONES.md](EXTENSIONES.md).

```bash
.venv/bin/python aplicar_extensiones.py          # instala sus paquetes y corre todas las pruebas
pizza despensa                                   # arma o actualiza el índice
pizza pregunta "¿cómo se calcula el ISR de un salario?"
```

| Pizzería | Nombre de siempre | Qué hace |
| --- | --- | --- |
| `pizza pregunta "..."` | `apuntes preguntar` | Responde con tus notas y transcripciones, citando clase y minuto |
| `pizza despensa` | `apuntes indexar` | Actualiza el índice (Chroma + BM25) con las notas nuevas o cambiadas |
| `pizza cuenta` | `apuntes costos` | Tokens, costo y latencia de cada llamada a la API |
| `pizza cotiza "24 septiembre"` | `apuntes estimar` | Cuánto costaría esa nota con Opus, Sonnet o Haiku |

Si la respuesta no está en tus apuntes, lo dice en lugar de inventarla. Con `backend = "api"` en la sección `[claude]` de tu `config.toml`, las notas se escriben con la API de Anthropic en lugar de Claude Code y cada llamada queda en `pizza cuenta`.

## Qué aparece en tu vault

| Dónde | Qué |
| --- | --- |
| `Diplomado Finanzas/Clase 24 septiembre.md` | La nota de la clase, con la transcripción completa plegada al final |
| `Diplomado Finanzas/Índice del diplomado.md` | Todas las clases con sus temas y conceptos, se regenera solo |
| `Conceptos/Saldo insoluto.md` | Una página por concepto nuevo, lista para que agregues tu descripción |
| `Notas sueltas/` | Clases aparte y reuniones |

En las notas, el texto normal es lo que se dijo en la sesión y la *cursiva* y los bloques `[!claude]` son lo que agregó o corrigió Claude. Copia `obsidian/claude-callout.css` a `<vault>/.obsidian/snippets/` y actívalo en Ajustes > Apariencia para verlos en color.

## Archivos

| Archivo | Para qué |
| --- | --- |
| `apuntes.py` | El programa completo |
| `config.toml` | Tu configuración (carpetas, nombre de las notas, modelo, vocabulario) |
| `prompts/clase.md`, `prompts/reunion.md` | Las instrucciones que recibe Claude; edítalas para cambiar las notas |
| `obsidian/claude-callout.css` | Colores para distinguir lo que agregó Claude |
| `CLAUDE.md` | Contexto para Claude Code cuando trabajas en el proyecto |
| `.claude/skills/ajustar-apuntes/` | Skill para cambiar el formato de las notas con Claude Code |
| `apuntes-vigilar.service` | Opcional: corre `pizza turno` en segundo plano al iniciar sesión |
| `ejemplos/vault-ejemplo/` | Un vault de muestra con una nota real, sus conceptos y el índice. Ábrelo en Obsidian con "Abrir carpeta como bóveda" |
| `extensiones/`, `EXTENSIONES.md` | Preguntas a tus clases, modo API y la cuenta de costos |
| `requirements-extensiones.txt`, `aplicar_extensiones.py` | Paquetes de la extensión y el script que los instala y corre las pruebas |
| `pruebas/` | Pruebas: `.venv/bin/python -m unittest discover -s pruebas -v` |

## Licencia

[MIT](LICENSE).

---

Parte del portafolio de Luis Ángel Quiroz López · [lobby9000.github.io](https://lobby9000.github.io) · [LinkedIn](https://www.linkedin.com/in/llqq) · [GitHub](https://github.com/lobby9000)
