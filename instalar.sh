#!/usr/bin/env bash
# Instala apuntes-clase: crea un entorno de Python aislado (.venv), instala faster-whisper
# y deja el comando `apuntes` disponible en la terminal. Se puede correr varias veces.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "1/5 Revisando Python..."
# Busca un Python 3.11+ aunque haya un entorno de conda activo con uno más viejo
PY=""
for candidato in python3.13 python3.12 python3.11 /usr/bin/python3 python3; do
  if command -v "$candidato" >/dev/null 2>&1 &&
     "$candidato" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$candidato")"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Necesitas Python 3.11 o más nuevo (Ubuntu 24.04 trae 3.12 en /usr/bin/python3)." >&2
  exit 1
fi
echo "   Usando $PY ($("$PY" --version))"

echo "2/5 Creando el entorno .venv..."
if [ ! -x .venv/bin/python ]; then
  if ! "$PY" -m venv .venv 2>/dev/null; then
    rm -rf .venv
    echo "Falta el paquete para crear entornos. Corre esto y vuelve a ejecutar ./instalar.sh:" >&2
    echo "  sudo apt install python3-venv" >&2
    exit 1
  fi
fi

echo "3/5 Instalando faster-whisper (unos minutos la primera vez)..."
.venv/bin/pip install --quiet --upgrade pip
# av 19 (sept. 2026) quitó un parámetro que faster-whisper usa para leer el audio
.venv/bin/pip install --quiet "faster-whisper>=1.1,<2" "av<19"

echo "4/5 Creando los comandos 'pizza', 'lets' y 'apuntes'..."
mkdir -p "$HOME/.local/bin"
# Los tres llaman al mismo programa: `pizza`, `lets make pizza` (= pizza turno) y el nombre de siempre, `apuntes`
for comando in pizza lets apuntes; do
  cat > "$HOME/.local/bin/$comando" <<EOF
#!/usr/bin/env bash
exec "$DIR/.venv/bin/python" "$DIR/apuntes.py" "\$@"
EOF
  chmod +x "$HOME/.local/bin/$comando"
done

echo "5/5 Preparando la configuración..."
if [ ! -f config.toml ]; then
  cp config.ejemplo.toml config.toml
  echo "   Se creó config.toml. Ábrelo y pon la ruta de tu vault de Obsidian."
else
  echo "   config.toml ya existía; no se tocó."
fi
mkdir -p "$HOME/Videos/raw_finance"

echo
echo "Listo."
command -v ffmpeg >/dev/null || echo "Aviso: pizza necesita ffmpeg:  sudo apt install ffmpeg"
command -v claude >/dev/null || echo "Aviso: no encontré Claude Code. Instálalo con:  curl -fsSL https://claude.ai/install.sh | bash"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Aviso: ~/.local/bin no está en tu PATH todavía. Corre esto y vuelve a abrir la terminal:"
     echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
echo
echo "Siguientes pasos:"
echo "  1. Edita $DIR/config.toml (sobre todo la ruta del vault)"
echo "  2. pizza cata         # mide la velocidad de los modelos en tu laptop"
echo "  3. pizza              # hornea las clases de ~/Videos/raw_finance"
echo "     lets make pizza    # o abre LocalSend y hornea cada clase que llegue"
