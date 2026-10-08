#!/usr/bin/env bash
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
[ -x "$BIN/tidy-cli" ] && "$BIN/tidy-cli" watch --off >/dev/null 2>&1
rm -f "$BIN/tidy" "$BIN/tidy-cli" "$APPS/app.tidy.Tidy.desktop"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/app.tidy.Watch.desktop"
echo "Removed. Your history and holding area stay in ~/.local/share/tidy until you delete it."
