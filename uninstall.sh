#!/usr/bin/env bash
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
rm -f "$BIN/tidy" "$BIN/tidy-cli" "$APPS/app.tidy.Tidy.desktop"
echo "Removed. Your history and holding area stay in ~/.local/share/tidy until you delete it."
