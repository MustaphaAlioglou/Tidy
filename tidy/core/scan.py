import os
import stat
from dataclasses import dataclass, field

from .safety import protected_reason


class Cancelled(Exception):
    pass


@dataclass
class Entry:
    path: str
    size: int
    mtime: float
    atime: float
    depth: int
    is_dir: bool = False
    files: int = 0
    other: int = 0
    protected: bool = False
    own_mtime: float = 0

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def last_used(self):
        return max(self.mtime, self.atime)


@dataclass
class ScanResult:
    folder: str
    files: list = field(default_factory=list)
    dirs: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def scan(folder, cancel=None, progress=None):
    folder = os.path.realpath(folder)
    dev = os.stat(folder).st_dev
    res = ScanResult(folder)
    stack = [(folder, 0, None)]
    while stack:
        path, depth, owner = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            res.skipped.append((path, e.strerror or "unreadable"))
            if owner:
                owner.protected = True
            continue
        for de in entries:
            if cancel and cancel.is_set():
                raise Cancelled
            if de.name.startswith(".") or de.is_symlink():
                if owner:
                    owner.other += 1
                continue
            try:
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                reason = "on another drive" if st.st_dev != dev else protected_reason(de.path)
                if reason:
                    res.skipped.append((de.path, reason))
                    if owner:
                        owner.protected = True
                    continue
                child_owner = owner
                if depth == 0:
                    child_owner = Entry(de.path, 0, 0, 0, 0, is_dir=True, own_mtime=st.st_mtime)
                    res.dirs.append(child_owner)
                stack.append((de.path, depth + 1, child_owner))
            elif stat.S_ISREG(st.st_mode):
                res.files.append(Entry(de.path, st.st_size, st.st_mtime, st.st_atime, depth))
                if owner:
                    owner.size += st.st_size
                    owner.files += 1
                    owner.mtime = max(owner.mtime, st.st_mtime)
                    owner.atime = max(owner.atime, st.st_atime)
                if progress and len(res.files) % 250 == 0:
                    progress(len(res.files))
            elif owner:
                owner.other += 1
    if progress:
        progress(len(res.files))
    return res
