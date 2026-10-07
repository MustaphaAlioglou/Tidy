import os
import sys

QT_DESKTOPS = {"kde", "lxqt", "deepin", "ukui", "trinity"}


def preferred():
    for flag, ui in (("--qt", "qt"), ("--gtk", "gtk")):
        if flag in sys.argv:
            sys.argv.remove(flag)
            return ui
    if os.environ.get("TIDY_UI") in ("qt", "gtk"):
        return os.environ["TIDY_UI"]
    desktops = {d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")}
    return "qt" if desktops & QT_DESKTOPS else "gtk"


def main():
    first = preferred()
    errors = []
    for ui in (first, "gtk" if first == "qt" else "qt"):
        try:
            if ui == "qt":
                from .qt.app import main as run
            else:
                from .gtk.app import main as run
        except (ImportError, ValueError) as e:
            errors.append(f"{ui}: {e}")
            continue
        return run(sys.argv)
    sys.exit("tidy: no usable toolkit found\n  " + "\n  ".join(errors))


if __name__ == "__main__":
    sys.exit(main())
