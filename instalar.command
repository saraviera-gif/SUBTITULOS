#!/bin/bash
# Instalador del revisor de subtítulos (macOS con chip Apple M1/M2/M3/M4).
# Doble clic para ejecutarlo. Se puede repetir sin problema.
set -e
cd "$(dirname "$0")"
echo "=== Instalando el revisor de subtítulos ==="

if [ "$(uname -m)" != "arm64" ]; then
  echo "⚠️  Este Mac no tiene chip Apple (M1/M2/M3/M4). La transcripción irá mucho más lenta."
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "▶ Instalando uv (gestor de Python)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "▶ Creando el entorno de Python…"
"$UV" venv --python 3.11 --allow-existing .venv
echo "▶ Instalando librerías (la primera vez tarda unos minutos)…"
"$UV" pip install --python .venv/bin/python -r requirements.txt

if [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "⚠️  No encuentro Google Chrome: el informe se generará, pero no el PDF automático."
fi

echo ""
echo "✅ Instalación terminada. Abre esta carpeta en Claude Code y pégale el enlace de Dropbox del vídeo."
echo "   (Puedes cerrar esta ventana.)"
