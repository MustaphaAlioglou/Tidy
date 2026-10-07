import hashlib
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass

from . import fmt
from .packages import installed_packages, package_name
from .plan import HOLD, HOLD_DAYS, MOVE, Group, Item, Plan
from .scan import Cancelled

INSTALLER_EXT = (".pkg.tar.zst", ".pkg.tar.xz", ".appimage", ".deb", ".rpm", ".flatpak",
                 ".flatpakref", ".snap", ".run", ".exe", ".msi", ".dmg")
ARCHIVE_EXT = (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz", ".txz", ".tbz2",
               ".tar", ".zip", ".7z", ".rar")
PARTIAL_EXT = (".part", ".crdownload", ".download", ".partial", ".opdownload")
ARCHIVE_DIR = "Archive"

_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|[ _-]copy(\s*\d+)?|^copy of .+)$", re.I)
_WGET_SUFFIX = re.compile(r"\.\d+$")


@dataclass
class Settings:
    old_days: int = 180
    installer_days: int = 30
    partial_days: int = 1
    fresh_minutes: int = 10


def split_ext(name, exts):
    low = name.lower()
    for ext in exts:
        if low.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)], name[-len(ext):]
    return None


def _looks_like_copy(name):
    if _WGET_SUFFIX.search(name):
        return True
    stem = os.path.splitext(name)[0]
    return bool(_COPY_SUFFIX.search(stem))


def _read(path, limit=None, cancel=None):
    flags = os.O_RDONLY | getattr(os, "O_NOATIME", 0)
    try:
        fd = os.open(path, flags)
    except PermissionError:
        fd = os.open(path, os.O_RDONLY)
    h = hashlib.blake2b(digest_size=20)
    with os.fdopen(fd, "rb") as fh:
        remaining = limit
        while remaining is None or remaining > 0:
            if cancel and cancel.is_set():
                raise Cancelled
            chunk = fh.read(1 << 20 if remaining is None else min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return h.digest()


def find_duplicates(files, cancel=None):
    by_size = defaultdict(list)
    for f in files:
        if f.size > 0:
            by_size[f.size].append(f)
    clusters = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_head = defaultdict(list)
        for f in same_size:
            try:
                by_head[_read(f.path, 65536, cancel)].append(f)
            except OSError:
                pass
        for heads in by_head.values():
            if len(heads) < 2:
                continue
            if heads[0].size <= 65536:
                clusters.append(heads)
                continue
            by_full = defaultdict(list)
            for f in heads:
                try:
                    by_full[_read(f.path, None, cancel)].append(f)
                except OSError:
                    pass
            clusters.extend(c for c in by_full.values() if len(c) > 1)
    return clusters


def _keeper(cluster):
    return min(cluster, key=lambda f: (f.depth == 0, _looks_like_copy(f.name), f.mtime, len(f.name)))


def build_plan(res, settings=None, now=None, installed=None, cancel=None):
    s = settings or Settings()
    now = now or time.time()
    folder = res.folder
    rel = lambda p: os.path.relpath(p, folder)
    fresh = lambda e: now - e.mtime < s.fresh_minutes * 60
    older = lambda ts, days: now - ts > days * 86400
    claimed = set()

    def item(e, reason):
        claimed.add(e.path)
        return Item(e.path, e.size, e.mtime, reason, e.is_dir)

    top_files = [f for f in res.files if f.depth == 0 and not fresh(f)]
    top_dirs = {d.name.lower(): d for d in res.dirs}

    clutter = []
    for f in top_files:
        if split_ext(f.name, PARTIAL_EXT) and older(f.mtime, s.partial_days):
            clutter.append(item(f, "Unfinished download"))
            continue
        if split_ext(f.name, INSTALLER_EXT):
            continue
        arch = split_ext(f.name, ARCHIVE_EXT)
        if arch and arch[0].lower() in top_dirs and not top_dirs[arch[0].lower()].protected:
            clutter.append(item(f, f"Already extracted to {top_dirs[arch[0].lower()].name}/"))
    for d in res.dirs:
        if d.files == 0 and d.other == 0 and not d.protected and now - d.own_mtime > s.fresh_minutes * 60:
            clutter.append(item(d, "Empty folder"))

    dupes = []
    candidates = [f for f in res.files if f.path not in claimed and not fresh(f)]
    for cluster in find_duplicates(candidates, cancel):
        keep = _keeper(cluster)
        for f in sorted(cluster, key=lambda f: f.name):
            if f is not keep and f.depth == 0:
                dupes.append(item(f, f"Copy of {rel(keep.path)}"))

    installers = []
    pkgs = None
    for f in top_files:
        if f.path in claimed or not split_ext(f.name, INSTALLER_EXT):
            continue
        name = package_name(f.name)
        if name:
            if pkgs is None:
                pkgs = installed if installed is not None else installed_packages()
            if name in pkgs:
                installers.append(item(f, "Already installed"))
                continue
        if older(f.mtime, s.installer_days):
            installers.append(item(f, f"Downloaded {fmt.ago(f.mtime, now)}"))

    old = []
    for f in top_files:
        if f.path not in claimed and older(f.last_used, s.old_days):
            old.append(item(f, f"Last used {fmt.ago(f.last_used, now)}"))
    busy_tops = {rel(p).split(os.sep)[0] for p in claimed}
    for d in res.dirs:
        if (d.path not in claimed and d.name not in busy_tops and not d.protected
                and d.files and older(d.last_used, s.old_days)):
            old.append(item(d, f"Last used {fmt.ago(d.last_used, now)}"))

    held = f"Removed items wait in the holding area for {HOLD_DAYS} days before they expire."
    groups = [
        Group("clutter", "Clutter", f"Unfinished downloads, empty folders and archives you already extracted. {held}", HOLD, clutter),
        Group("duplicates", "Duplicates", f"Extra copies of files that exist elsewhere in this folder. The original stays. {held}", HOLD, dupes),
        Group("installers", "Installers", f"Packages that are already installed or were downloaded a while ago. {held}", HOLD, installers),
        Group("old", "Old Files", f"Not opened in {s.old_days // 30} months. They move to the {ARCHIVE_DIR} folder; nothing is removed.",
              MOVE, old, dest=os.path.join(folder, ARCHIVE_DIR)),
    ]
    for g in groups:
        g.items.sort(key=lambda i: -i.size)
    return Plan(folder, len(res.files), sum(f.size for f in res.files),
                [g for g in groups if g.items], list(res.skipped))
