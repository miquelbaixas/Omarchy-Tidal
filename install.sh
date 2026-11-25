#!/bin/bash
# Instala TIDAL Connect para Linux para el usuario actual (sin sudo).
#   - entorno de Python con las dependencias
#   - servicio de usuario systemd (arranca al iniciar sesion)
#   - icono y entrada en el lanzador de aplicaciones
#
# TIDAL_CONNECT_WMCLASS: clase de ventana de Chromium para la web app
# (depende del perfil del navegador; compruebala con `hyprctl clients`).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

echo "==> Entorno de Python"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"

echo "==> Servicio de usuario"
mkdir -p "$SYSTEMD_DIR"
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/packaging/tidal-connect.service" > "$SYSTEMD_DIR/tidal-connect.service"
systemctl --user daemon-reload
systemctl --user enable --now tidal-connect.service
systemctl --user restart tidal-connect.service

echo "==> Iconos"
mkdir -p "$ICON_DIR"
rsvg-convert -w 256 -h 256 "$APP_DIR/web/icon.svg" -o "$ICON_DIR/tidal-connect.png"
rsvg-convert -w 256 -h 256 "$APP_DIR/web/dj/icon.svg" -o "$ICON_DIR/tidal-dj.png"
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" &>/dev/null || true

echo "==> Lanzador"
chmod +x "$APP_DIR/bin/tidal-connect"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/tidal-connect.desktop" <<EOF
[Desktop Entry]
Version=1.0
Name=TIDAL Connect
GenericName=Reproductor de música
Comment=Controla tu equipo de música con TIDAL Connect
Exec=$APP_DIR/bin/tidal-connect
Icon=tidal-connect
StartupWMClass=${TIDAL_CONNECT_WMCLASS:-chrome-127.0.0.1__-Default}
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Player;
Keywords=tidal;music;musica;connect;dmp;
StartupNotify=true
EOF
chmod +x "$APP_DIR/bin/tidal-dj"
cat > "$APPS_DIR/tidal-dj.desktop" <<EOF
[Desktop Entry]
Version=1.0
Name=TIDAL DJ
GenericName=Mezclador de música
Comment=Mezcla música de TIDAL en este ordenador, como un DJ
Exec=$APP_DIR/bin/tidal-dj
Icon=tidal-dj
StartupWMClass=${TIDAL_CONNECT_WMCLASS:-chrome-127.0.0.1__-Default}
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Mixer;
Keywords=tidal;dj;mezcla;music;musica;
StartupNotify=true
EOF
update-desktop-database "$APPS_DIR" &>/dev/null || true

echo "Listo. Abre «TIDAL Connect» desde el lanzador de aplicaciones."
