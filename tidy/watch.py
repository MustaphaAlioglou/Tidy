"""Watch mode: look at the watched folders every so often and send a gentle
notification when enough new files have piled up. Only rules that say
"automatically" move anything without asking, and each of those runs can be
undone from the notification or from History."""
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from .core import History, Protected, check_folder, make_plan, sentences
from .core import fmt
from .core.history import data_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _launcher(name):
    path = os.path.join(ROOT, "bin", name)
    return path if os.path.exists(path) else name


def autostart_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", "app.tidy.Watch.desktop")


def pid_path():
    return os.path.join(data_dir(), "watch.pid")


def notify(summary, body="", actions=(), on_action=None):
    """Show a desktop notification. actions is [(key, label)]; on_action(key)
    runs in a background thread when one is clicked."""
    if not shutil.which("notify-send"):
        print(f"tidy: {summary} {body}", file=sys.stderr)
        return
    cmd = ["notify-send", "--app-name=Tidy", "--icon=edit-clear-all", summary, body]
    cmd += [f"--action={key}={label}" for key, label in actions]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return

    def wait():
        out = proc.communicate()[0].strip()
        if out and on_action:
            on_action(out)

    threading.Thread(target=wait, daemon=True).start()


def open_tidy(folder):
    subprocess.Popen([_launcher("tidy"), folder], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _changed_at(path):
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    return max(st.st_mtime, st.st_ctime)


class Watcher:
    def __init__(self, history=None, notifier=notify, opener=open_tidy):
        self.history = history or History()
        self.notify = notifier
        self.open = opener
        self.reported_errors = None

    def check(self, folder, ruleset, now=None):
        """One look at one folder. Returns (auto-tidied result or None,
        number of new items, whether a notification was sent)."""
        now = now or time.time()
        name = os.path.basename(folder)
        plan = make_plan(folder, history=self.history, rules=ruleset)
        result = None
        if any(g.auto for g in plan.groups):
            for g in plan.groups:
                g.enabled = g.auto
            moved = [(g.title, len(g.selected)) for g in plan.groups if g.auto]
            result = self.history.apply(plan)
            if result.done:
                run = result.run
                body = "\n".join(f"{title}: {fmt.items(n)}" for title, n in moved)
                self.notify(f"Tidied {fmt.items(result.done)} in {name}", body, [("undo", "Undo")],
                            lambda key: self.history.undo(run=run) if key == "undo" else None)
            for g in plan.groups:
                g.enabled = not g.auto
        since = self.history.watched_since(folder, now)
        new = [i for g, i in plan.selected if _changed_at(i.path) > since]
        told = len(new) >= ruleset.watch.threshold
        if told:
            self.notify(f"{fmt.items(len(new)).replace('item', 'new file')} in {name}. Tidy up?",
                        "Nothing moves until you approve the plan.", [("open", "Tidy Up")],
                        lambda key: self.open(folder) if key == "open" else None)
            self.history.set_watched_since(folder, now)
        return result, len(new), told

    def check_all(self, ruleset=None):
        ruleset = ruleset or sentences.load()
        errors = [(n, msg) for n, _line, msg in ruleset.errors]
        if errors and errors != self.reported_errors:
            n, msg = errors[0]
            self.notify("Tidy could not read one of your rules", f"Line {n}: {msg}")
        self.reported_errors = errors
        for place in ruleset.watch.folders:
            try:
                folder = check_folder(sentences.resolve_place(place))
                self.check(folder, ruleset)
            except (Protected, OSError) as e:
                print(f"tidy watch: {place}: {e}", file=sys.stderr)
        self.history.purge()
        return ruleset


def _take_lock():
    os.makedirs(data_dir(), exist_ok=True)
    fh = open(pid_path(), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def running_pid():
    try:
        with open(pid_path()) as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
    except BlockingIOError:
        with open(pid_path()) as fh:
            return int(fh.read().strip() or 0) or None
    except (OSError, ValueError):
        return None


def run(once=False):
    lock = _take_lock()
    if lock is None:
        print("tidy: already watching", file=sys.stderr)
        return 1
    try:
        os.nice(10)
    except OSError:
        pass
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    watcher = Watcher()
    while True:
        ruleset = watcher.check_all()
        if once:
            return 0
        _sleep(ruleset.watch.interval)


def _rules_mtime():
    try:
        return os.stat(sentences.rules_path()).st_mtime
    except OSError:
        return None


def _sleep(seconds, step=30):
    """Wait, but wake early when the rules change so new settings apply
    straight away."""
    start, end = _rules_mtime(), time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(min(step, max(0, end - time.monotonic())))
        if _rules_mtime() != start:
            return


def enable():
    """Start watching now and with every session. With no watched folders
    yet, Downloads is added."""
    ruleset = sentences.load()
    if not ruleset.watch.folders:
        ruleset.watch.folders = ["Downloads"]
        sentences.save_watch(ruleset.watch)
    path = autostart_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=Tidy Watch\n"
                 "Comment=Tells you when watched folders need tidying\n"
                 f'Exec="{_launcher("tidy-cli")}" watch\nIcon=edit-clear-all\n'
                 "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
    if not running_pid():
        os.makedirs(data_dir(), exist_ok=True)
        log = open(os.path.join(data_dir(), "watch.log"), "a")
        subprocess.Popen([_launcher("tidy-cli"), "watch"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def is_enabled():
    return os.path.exists(autostart_path())


def disable():
    try:
        os.unlink(autostart_path())
    except FileNotFoundError:
        pass
    pid = running_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
    return pid


class WatchSettings:
    """What the settings screens edit: the watched folders, how many new
    files make a notification, how often to look, and whether watching is on."""

    def __init__(self):
        self.watch = sentences.load().watch

    def folders(self):
        return [(name, sentences.resolve_place(name)) for name in self.watch.folders]

    def add(self, path):
        """Add a folder; raises Protected for folders Tidy won't touch.
        Returns False when it is already watched."""
        real = check_folder(path)
        if any(os.path.realpath(p) == real for _, p in self.folders()):
            return False
        home = os.path.realpath(os.path.expanduser("~"))
        self.watch.folders.append("~" + real[len(home):] if real.startswith(home + os.sep) else real)
        self._save()
        return True

    def remove(self, name):
        self.watch.folders.remove(name)
        self._save()

    @property
    def threshold(self):
        return self.watch.threshold

    @threshold.setter
    def threshold(self, n):
        self.watch.threshold = max(1, int(n))
        self._save()

    @property
    def minutes(self):
        return self.watch.interval // 60

    @minutes.setter
    def minutes(self, n):
        self.watch.interval = max(1, int(n)) * 60
        self._save()

    @property
    def enabled(self):
        return is_enabled()

    @enabled.setter
    def enabled(self, on):
        if on:
            enable()
            self.watch = sentences.load().watch
        else:
            disable()

    def _save(self):
        sentences.save_watch(self.watch)
