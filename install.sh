#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

ok_gtk=0; ok_qt=0
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null && ok_gtk=1
/usr/bin/python3 -c "import PySide6.QtWidgets" 2>/dev/null && ok_qt=1
if [ $ok_gtk = 0 ] && [ $ok_qt = 0 ]; then
    cat >&2 <<'MSG'
tidy: needs GTK4 + libadwaita (GNOME) or PySide6 (KDE) for the system Python.

  Arch          sudo pacman -S python-gobject libadwaita   # or: pyside6
  Debian/Ubuntu sudo apt install python3-gi gir1.2-adw-1   # or: python3-pyside6.qtwidgets
  Fedora        sudo dnf install python3-gobject libadwaita # or: python3-pyside6
MSG
    exit 1
fi

mkdir -p "$BIN" "$APPS"
ln -sf "$DIR/bin/tidy" "$BIN/tidy"
ln -sf "$DIR/bin/tidy-cli" "$BIN/tidy-cli"
install -m644 "$DIR/data/app.tidy.Tidy.desktop" "$APPS/app.tidy.Tidy.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
echo "Installed. Run 'tidy', or find Tidy in your app menu."
[ $ok_gtk = 1 ] || echo "note: GTK frontend unavailable, Qt will be used everywhere"
[ $ok_qt = 1 ] || echo "note: Qt frontend unavailable, GTK will be used everywhere"
