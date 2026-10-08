cp -r /src ~/tidy
cd ~/tidy || exit 1
. /etc/os-release
echo "== $PRETTY_NAME, $(/usr/bin/python3 --version)"

Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99
for _ in $(seq 50); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.1; done

py() { /usr/bin/python3 -W ignore::DeprecationWarning "$@"; }
probe() { out=$(py -c "import tidy.$1.app" 2>&1) || { echo "$out" | tail -1; return 1; }; }

core=fail; gtk=fail; qt=fail; cli=fail; install=fail
py -m unittest tests.test_core tests.test_watch tests.test_cli 2>&1 | tail -3 && py -m unittest tests.test_core tests.test_watch tests.test_cli >/dev/null 2>&1 && core=pass

if err=$(probe gtk); then
    py -m unittest -v tests.test_gui.GtkFrontend 2>&1 | grep -E "\.\.\. |^(OK|FAILED)|Error|assert" && \
    py -m unittest tests.test_gui.GtkFrontend >/dev/null 2>&1 && gtk=pass
else
    gtk="n/a"; echo "gtk unavailable: $err"
fi
if err=$(probe qt); then
    py -m unittest -v tests.test_gui.QtFrontend 2>&1 | grep -E "\.\.\. |^(OK|FAILED)|Error|assert" && \
    py -m unittest tests.test_gui.QtFrontend >/dev/null 2>&1 && qt=pass
else
    qt="n/a"; echo "qt unavailable: $err"
fi

mkdir -p ~/Downloads && printf x > ~/Downloads/a.png && printf x > ~/Downloads/"a (1).png"
touch -d "3 days ago" ~/Downloads/*
./install.sh && ~/.local/bin/tidy-cli scan ~/Downloads --apply | tail -2 && \
    [ -f ~/Downloads/Pictures/a.png ] && ~/.local/bin/tidy-cli undo 1 && [ -f ~/Downloads/"a (1).png" ] && cli=pass
[ -L ~/.local/bin/tidy ] && [ -f ~/.local/share/applications/app.tidy.Tidy.desktop ] && install=pass

ui=$(XDG_CURRENT_DESKTOP=GNOME py -c "import sys; sys.argv=['t']; from tidy.__main__ import preferred; print(preferred())")
echo "RESULT core=$core gtk=$gtk qt=$qt cli=$cli install=$install gnome-picks=$ui"
[ $core = pass ] && [ $cli = pass ] && [ $install = pass ] && [ $gtk != fail ] && [ $qt != fail ] && [ "$gtk$qt" != "n/an/a" ]
