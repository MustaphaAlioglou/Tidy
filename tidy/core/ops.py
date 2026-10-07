import ctypes
import errno
import os
import shutil

from .rules import ARCHIVE_EXT

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_UNSUPPORTED = {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}

try:
    _renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    _renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
except (AttributeError, OSError):
    _renameat2 = None


def unique_path(path):
    if not os.path.lexists(path):
        return path
    parent, name = os.path.split(path)
    low = name.lower()
    ext = next((e for e in ARCHIVE_EXT if low.endswith(e) and e.count(".") > 1), None)
    stem, ext = (name[: -len(ext)], name[-len(ext):]) if ext else os.path.splitext(name)
    n = 2
    while os.path.lexists(candidate := os.path.join(parent, f"{stem} ({n}){ext}")):
        n += 1
    return candidate


def _rename_noreplace(src, dst):
    if _renameat2:
        if _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE) == 0:
            return
        err = ctypes.get_errno()
        if err not in _UNSUPPORTED:
            raise OSError(err, os.strerror(err), src, None, dst)
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
    os.rename(src, dst)


def remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        _rename_noreplace(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
    try:
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
            if os.lstat(src).st_size != os.lstat(dst).st_size:
                raise OSError(errno.EIO, "copy is incomplete", dst)
    except BaseException:
        if os.path.lexists(dst):
            remove(dst)
        raise
    remove(src)


def prune_empty(path, stop):
    stop = os.path.realpath(stop)
    path = os.path.realpath(path)
    while path != stop and path.startswith(stop + os.sep):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)
