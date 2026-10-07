import os
import re

_LINE = re.compile(r'^XDG_(\w+)_DIR="(.*)"$')
_WANTED = (("DOWNLOAD", "Downloads", "folder-download"),
           ("DESKTOP", "Desktop", "user-desktop"),
           ("DOCUMENTS", "Documents", "folder-documents"))


def user_dirs():
    home = os.path.expanduser("~")
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    found = {}
    try:
        with open(os.path.join(config, "user-dirs.dirs"), encoding="utf-8") as fh:
            for line in fh:
                m = _LINE.match(line.strip())
                if m:
                    found[m.group(1)] = m.group(2).replace("$HOME", home)
    except OSError:
        pass
    return found


def places():
    home = os.path.expanduser("~")
    dirs = user_dirs()
    out = []
    for key, label, icon in _WANTED:
        path = dirs.get(key) or os.path.join(home, label)
        if os.path.isdir(path) and os.path.realpath(path) != os.path.realpath(home):
            out.append((os.path.basename(path) or label, path, icon))
    return out
