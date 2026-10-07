import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import ops
from .plan import HOLD, HOLD_DAYS
from .safety import ARCHIVE_MARKER

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    folder TEXT NOT NULL,
    started REAL NOT NULL,
    finished REAL
);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY,
    run INTEGER NOT NULL REFERENCES runs(id),
    src TEXT NOT NULL,
    dst TEXT,
    action TEXT NOT NULL,
    size INTEGER NOT NULL,
    is_dir INTEGER NOT NULL,
    status TEXT NOT NULL,
    at REAL NOT NULL,
    restored_to TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS moves_run ON moves(run);
CREATE INDEX IF NOT EXISTS moves_status ON moves(status);
"""


def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "tidy")


def _mount_point(path):
    path = os.path.realpath(path)
    dev = os.lstat(path).st_dev
    while path != "/":
        parent = os.path.dirname(path)
        if os.lstat(parent).st_dev != dev:
            break
        path = parent
    return path


@dataclass
class Result:
    run: int | None = None
    done: int = 0
    size: int = 0
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)


class History:
    def __init__(self, root=None):
        self.root = root or data_dir()
        self.holding = os.path.join(self.root, "holding")
        os.makedirs(self.holding, mode=0o700, exist_ok=True)
        self.db_path = os.path.join(self.root, "tidy.db")
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        self.recover()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _holding_base(self, src):
        try:
            if os.lstat(src).st_dev == os.stat(self.holding).st_dev:
                return self.holding
            alt = os.path.join(_mount_point(src), f".tidy-holding-{os.getuid()}")
            os.makedirs(alt, mode=0o700, exist_ok=True)
            return alt
        except OSError:
            return self.holding

    def _holding_roots(self):
        with self._db() as db:
            rows = db.execute("SELECT DISTINCT dst FROM moves WHERE action=? AND dst IS NOT NULL", (HOLD,))
            roots = {self.holding}
            for r in rows:
                roots.add(os.path.dirname(os.path.dirname(os.path.dirname(r["dst"]))))
        return roots

    @staticmethod
    def _unchanged(item):
        try:
            st = os.lstat(item.path)
        except FileNotFoundError:
            return "no longer there"
        if os.path.isdir(item.path) != item.is_dir:
            return "changed since the scan"
        if item.is_dir:
            if item.reason == "Empty folder" and os.listdir(item.path):
                return "no longer empty"
        elif st.st_size != item.size or abs(st.st_mtime - item.mtime) > 0.001:
            return "changed since the scan"
        return None

    def apply(self, plan, progress=None, cancel=None):
        todo = plan.selected
        res = Result()
        with self._db() as db:
            res.run = db.execute("INSERT INTO runs(folder, started) VALUES (?, ?)",
                                 (plan.folder, time.time())).lastrowid
        for n, (group, item) in enumerate(todo, 1):
            if cancel and cancel.is_set():
                break
            why = self._unchanged(item)
            if why:
                res.skipped.append((item.path, why))
                continue
            with self._db() as db:
                mid = db.execute(
                    "INSERT INTO moves(run, src, action, size, is_dir, status, at) VALUES (?,?,?,?,?,'pending',?)",
                    (res.run, item.path, group.action, item.size, int(item.is_dir), time.time())).lastrowid
                name = os.path.basename(item.path)
                if group.action == HOLD:
                    dst = os.path.join(self._holding_base(item.path), str(res.run), str(mid), name)
                else:
                    os.makedirs(group.dest, exist_ok=True)
                    open(os.path.join(group.dest, ARCHIVE_MARKER), "a").close()
                    dst = ops.unique_path(os.path.join(group.dest, name))
                db.execute("UPDATE moves SET dst=? WHERE id=?", (dst, mid))
            try:
                ops.move(item.path, dst)
            except OSError as e:
                self._set(mid, "failed", error=e.strerror or str(e))
                res.failed.append((item.path, e.strerror or str(e)))
            else:
                self._set(mid, "moved")
                res.done += 1
                res.size += item.size
            if progress:
                progress(n, len(todo))
        with self._db() as db:
            if res.done:
                db.execute("UPDATE runs SET finished=? WHERE id=?", (time.time(), res.run))
            else:
                db.execute("DELETE FROM moves WHERE run=?", (res.run,))
                db.execute("DELETE FROM runs WHERE id=?", (res.run,))
                res.run = None
        return res

    def _set(self, mid, status, **extra):
        cols = ", ".join(f"{k}=?" for k in ("status", *extra))
        with self._db() as db:
            db.execute(f"UPDATE moves SET {cols} WHERE id=?", (status, *extra.values(), mid))

    def _tidy_after(self, row):
        dst = row["dst"]
        if row["action"] == HOLD:
            run_dir = os.path.dirname(os.path.dirname(dst))
            ops.prune_empty(os.path.dirname(dst), os.path.dirname(run_dir))
        else:
            parent = os.path.dirname(dst)
            try:
                if os.listdir(parent) == [ARCHIVE_MARKER]:
                    os.unlink(os.path.join(parent, ARCHIVE_MARKER))
                    os.rmdir(parent)
            except OSError:
                pass

    def undo(self, run=None, ids=None):
        with self._db() as db:
            if ids:
                marks = ",".join("?" * len(ids))
                rows = db.execute(f"SELECT * FROM moves WHERE status='moved' AND id IN ({marks}) ORDER BY id DESC",
                                  list(ids)).fetchall()
            else:
                rows = db.execute("SELECT * FROM moves WHERE status='moved' AND run=? ORDER BY id DESC",
                                  (run,)).fetchall()
        res = Result(run=run)
        for row in rows:
            if not os.path.lexists(row["dst"]):
                self._set(row["id"], "missing")
                res.failed.append((row["src"], "no longer in the holding area"))
                continue
            target = ops.unique_path(row["src"])
            try:
                ops.move(row["dst"], target)
            except OSError as e:
                res.failed.append((row["src"], e.strerror or str(e)))
                continue
            self._set(row["id"], "restored", restored_to=target)
            self._tidy_after(row)
            res.done += 1
            res.size += row["size"]
        return res

    def purge(self, days=HOLD_DAYS, now=None):
        cutoff = (now or time.time()) - days * 86400
        with self._db() as db:
            rows = db.execute("SELECT * FROM moves WHERE status='moved' AND action=? AND at < ?",
                              (HOLD, cutoff)).fetchall()
        for row in rows:
            try:
                ops.remove(row["dst"])
            except OSError:
                continue
            self._set(row["id"], "expired")
            self._tidy_after(row)
        return len(rows)

    def recover(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM moves WHERE status='pending'").fetchall()
        roots = self._holding_roots() if rows else ()
        for row in rows:
            src, dst = row["src"], row["dst"]
            src_there = os.path.lexists(src)
            dst_there = bool(dst) and os.path.lexists(dst)
            if dst_there and not src_there:
                self._set(row["id"], "moved")
            elif not src_there:
                self._set(row["id"], "missing")
            else:
                if dst_there and any(dst.startswith(r + os.sep) for r in roots):
                    ops.remove(dst)
                self._set(row["id"], "failed", error="interrupted")

    def runs(self):
        with self._db() as db:
            rows = db.execute("""
                SELECT r.id, r.folder, r.started,
                       COUNT(m.id) AS total,
                       SUM(m.status='moved') AS active,
                       SUM(m.status='restored') AS restored,
                       COALESCE(SUM(CASE WHEN m.status='moved' THEN m.size END), 0) AS size
                FROM runs r LEFT JOIN moves m ON m.run = r.id
                GROUP BY r.id ORDER BY r.id DESC""").fetchall()
        return [dict(r) for r in rows]

    def moves(self, run):
        with self._db() as db:
            rows = db.execute("SELECT * FROM moves WHERE run=? AND status != 'pending' ORDER BY id",
                              (run,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["expires"] = d["at"] + HOLD_DAYS * 86400 if d["action"] == HOLD else None
            out.append(d)
        return out
