import os
import re
from dataclasses import dataclass, field

_DIGITS = re.compile(r"\d+")
_COPY = re.compile(r"\s*\(#\)$")


def signature(name):
    """A file name with its numbers blanked out, so "IMG_2041.jpg" and
    "IMG_2077.jpg" look alike. None when nothing but numbers is left."""
    stem, ext = os.path.splitext(name.lower())
    stem = _COPY.sub("", _DIGITS.sub("#", stem))
    if not re.search(r"[^\W\d_]", stem):
        return None
    return stem + ext


def family(key):
    """Group keys carry a year for topics ("topic:Receipts:2026"); what is
    learned applies to every year."""
    parts = key.split(":")
    return ":".join(parts[:2]) if parts[0] == "topic" else key


def to_template(dest, folder, year=None):
    rel = os.path.relpath(dest, folder)
    path = dest if rel == ".." or rel.startswith(".." + os.sep) else rel
    return path.replace(str(year), "{year}") if year else path


def from_template(template, folder, year=None):
    path = template.replace("{year}", str(year)) if year else template
    return os.path.normpath(os.path.join(folder, path))


@dataclass
class Learned:
    dests: dict = field(default_factory=dict)   # family -> template
    sigs: dict = field(default_factory=dict)    # signature -> family

    def dest(self, key, folder, default, year=None):
        template = self.dests.get(family(key))
        if template is None:
            return default
        path = from_template(template, folder, year)
        if path == folder or not os.path.isdir(os.path.dirname(path)):
            return default
        return path
