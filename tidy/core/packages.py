import re
import shutil
import subprocess

_PATTERNS = (
    re.compile(r"^(?P<name>[^_]+)_[^_]+_[^_]+\.deb$"),
    re.compile(r"^(?P<name>.+)-[^-]+-[^-]+\.[^.]+\.rpm$"),
    re.compile(r"^(?P<name>.+)-[^-]+-[^-]+-[^-]+\.pkg\.tar\.(?:zst|xz|gz)$"),
)

_QUERIES = (
    ["pacman", "-Qq"],
    ["dpkg-query", "-W", "-f=${Package}\n"],
    ["rpm", "-qa", "--qf", "%{NAME}\n"],
)


def package_name(filename):
    for pattern in _PATTERNS:
        m = pattern.match(filename)
        if m:
            return m.group("name")
    return None


def installed_packages():
    names = set()
    for cmd in _QUERIES:
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        names.update(line.strip() for line in out.splitlines() if line.strip())
    return names
