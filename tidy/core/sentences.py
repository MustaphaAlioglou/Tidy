"""Rules written as plain sentences, one per line, for example

    Move PDFs older than 30 days from Downloads to Documents/Old PDFs
    Put screenshots in Pictures/Screenshots automatically
    Hold installers older than 2 weeks
    Watch Downloads and Desktop, and tell me after 30 new files

The grammar is small and fixed: a verb, what it applies to, then optional
"from <folder>", "older than <n> <unit>", "to <folder>" and "automatically".
"""
import fnmatch
import os
import re
from dataclasses import dataclass, field

from . import topics
from .kinds import ARCHIVES, DOCUMENTS, MUSIC, PICTURES, VIDEOS, _BY_EXT, kind_of
from .places import user_dirs
from .plan import HOLD, MOVE

INSTALLER_EXTS = {"deb", "rpm", "appimage", "flatpak", "flatpakref", "snap", "run", "exe", "msi", "dmg"}
INSTALLER_SUFFIXES = (".pkg.tar.zst", ".pkg.tar.xz") + tuple(f".{e}" for e in INSTALLER_EXTS)

_MOVE_VERBS = ("move", "put", "file", "send", "sort")
_HOLD_VERBS = ("hold", "remove", "delete", "clear", "clean up", "get rid of", "throw away", "trash")
_VERB = re.compile(r"^(%s)\s+" % "|".join(v.replace(" ", r"\s+") for v in _MOVE_VERBS + _HOLD_VERBS), re.I)
_AUTO = re.compile(r"(?:,?\s+(?:automatically|without asking))\s*$|^always\s+", re.I)
_OLDER = re.compile(r"\s+(?:that\s+are\s+|which\s+are\s+)?older\s+than\s+(\w+)\s+(day|week|month|year)s?\b", re.I)
_CLAUSE = re.compile(r"\s+(from|in|into|to)\s+", re.I)
_NAMED = re.compile(r"^(?:files?\s+)?(?:named|called|matching)\s+[\"']?(.+?)[\"']?$", re.I)
_XDG_KEYS = {"downloads": "DOWNLOAD", "desktop": "DESKTOP", "documents": "DOCUMENTS", "music": "MUSIC",
             "pictures": "PICTURES", "videos": "VIDEOS", "templates": "TEMPLATES", "public": "PUBLICSHARE"}
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_KIND_WORDS = {
    PICTURES: ("pictures", "picture", "images", "image", "photos", "photo"),
    DOCUMENTS: ("documents", "document", "docs"),
    MUSIC: ("music", "songs", "audio"),
    VIDEOS: ("videos", "video", "movies"),
    ARCHIVES: ("archives", "compressed files"),
}
_TOPIC_WORDS = {
    topics.SCREENSHOTS: ("screenshots", "screenshot"),
    topics.RECEIPTS: ("receipts", "receipt", "invoices", "invoice", "receipts and invoices"),
    topics.TAX: ("tax documents", "tax files", "tax papers", "taxes"),
}
_ALL_WORDS = ("files", "everything", "anything", "all files", "loose files")
_FILLER = re.compile(r"^(?:all|any|my|the)\s+", re.I)
_SPLIT = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+", re.I)
_WATCH = re.compile(r"^watch\s+(.+)$", re.I)
_NOTIFY = re.compile(r",?\s*(?:and\s+)?(?:tell|notify|ping)\s+me\s+(?:after|at|when\s+there\s+are)\s+(\d+)\s+new\s+(?:files|items|things)\s*$", re.I)
_EVERY = re.compile(r",?\s+every\s+(\d+|an?)\s+(minute|hour)s?\s*$", re.I)
_NOTIFY_ONLY = re.compile(r"^(?:tell|notify|ping)\s+me\s+(?:after|at|when\s+there\s+are)\s+(\d+)\s+new\s+(?:files|items|things)$", re.I)

EXAMPLES = """\
# Tidy rules, one sentence per line. Lines starting with # are ignored.
# Your rules come before Tidy's own groups. Only rules that end in
# "automatically" move files without asking, and only in watched folders.
#
# Move PDFs older than 30 days from Downloads to Documents/Old PDFs
# Put screenshots in Pictures/Screenshots automatically
# Hold installers older than 2 weeks
# Move files named "*.torrent" to Torrents
# Watch Downloads and Desktop, and tell me after 30 new files
"""


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    text: str
    action: str
    kinds: set = field(default_factory=set)
    topics: set = field(default_factory=set)
    exts: set = field(default_factory=set)
    patterns: list = field(default_factory=list)
    everything: bool = False
    installers: bool = False
    source: str | None = None
    older_days: int | None = None
    dest: str | None = None
    auto: bool = False
    line: int = 0

    def applies_to(self, folder):
        return self.source is None or os.path.realpath(resolve_place(self.source)) == os.path.realpath(folder)

    def matches(self, entry, now):
        if self.older_days is not None and now - entry.mtime < self.older_days * 86400:
            return False
        name = entry.name.lower()
        ext = name.rpartition(".")[2] if "." in name else ""
        if self.everything or ext in self.exts:
            return True
        if self.installers and name.endswith(INSTALLER_SUFFIXES):
            return True
        if any(fnmatch.fnmatch(name, p) for p in self.patterns):
            return True
        if self.kinds or self.topics:
            kind = kind_of(entry.path)
            if kind in self.kinds:
                return True
            if self.topics and kind:
                found = topics.topic_of(entry.path, kind)
                return bool(found) and found[0] in self.topics
        return False

    def destination(self, folder):
        return resolve_dest(self.dest, folder) if self.dest else None


@dataclass
class Watch:
    folders: list = field(default_factory=list)
    threshold: int = 20
    interval: int = 15 * 60


@dataclass
class RuleSet:
    rules: list = field(default_factory=list)
    watch: Watch = field(default_factory=Watch)
    errors: list = field(default_factory=list)   # (line number, text, message)


def resolve_place(name):
    """"Downloads" -> the user's Downloads folder (localised names too);
    "~/x" and "/x" as written; anything else relative to home."""
    home = os.path.expanduser("~")
    name = os.path.expanduser(name.strip().rstrip("/"))
    if os.path.isabs(name):
        return name
    first, _, rest = name.partition("/")
    dirs = user_dirs()
    path = dirs.get(_XDG_KEYS.get(first.lower(), ""))
    path = path or next((p for p in dirs.values() if os.path.basename(p).lower() == first.lower()), None)
    if path:
        return os.path.join(path, rest) if rest else path
    if os.path.isdir(os.path.join(home, first)):
        return os.path.join(home, name)
    for entry in _iterdir(home):
        if entry.lower() == first.lower():
            return os.path.join(home, entry, rest) if rest else os.path.join(home, entry)
    return os.path.join(home, name)


def resolve_dest(dest, folder):
    """Destinations that name a folder in home (or a standard folder such as
    Pictures) go there; anything else is made inside the folder being tidied."""
    home = os.path.expanduser("~")
    expanded = os.path.expanduser(dest)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    place = resolve_place(dest)
    first = os.path.relpath(place, home).split(os.sep)[0]
    if os.path.isdir(os.path.join(home, first)):
        return os.path.normpath(place)
    return os.path.normpath(os.path.join(folder, dest))


def _iterdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def _number(word):
    word = word.lower()
    if word.isdigit():
        return int(word)
    if word in _NUMBERS:
        return _NUMBERS[word]
    raise RuleError(f'"{word}" is not a number I understand. Write it as digits, like 30.')


def _parse_what(rule, what):
    what = _FILLER.sub("", what.strip())
    named = _NAMED.match(what)
    if named:
        pattern = named.group(1).lower()
        rule.patterns.append(pattern if any(c in pattern for c in "*?[") else f"*{pattern}*")
        return
    for part in _SPLIT.split(what):
        word = _FILLER.sub("", part.strip()).lower()
        if not word:
            continue
        if word in _ALL_WORDS:
            rule.everything = True
            continue
        if word in ("installers", "installer", "packages", "installer files"):
            rule.installers = True
            continue
        hit = next((k for k, words in _KIND_WORDS.items() if word in words), None)
        if hit:
            rule.kinds.add(hit)
            continue
        hit = next((t for t, words in _TOPIC_WORDS.items() if word in words), None)
        if hit:
            rule.topics.add(hit)
            continue
        ext = re.sub(r"^\*?\.|(?:'?s)?(?:\s+files?)?$", "", word)
        if ext in _BY_EXT or ext in INSTALLER_EXTS or ext in ("torrent", "iso", "json", "html", "log"):
            rule.exts.add(ext)
            continue
        if re.fullmatch(r"\*?\.?[a-z0-9]{1,8}\s+files?", word) or word.startswith((".", "*.")):
            rule.exts.add(re.sub(r"^\*?\.|\s+files?$", "", word))
            continue
        raise RuleError(f'I don\'t know which files "{part.strip()}" means. Try pictures, documents, '
                        'screenshots, receipts, installers, PDFs, or files named "*.torrent".')


def parse_rule(text):
    rule_text = text.strip().rstrip(".")
    body = rule_text
    auto = bool(_AUTO.search(body))
    body = _AUTO.sub("", body).strip()
    m = _VERB.match(body)
    if not m:
        raise RuleError('Start with what to do: Move, Put, Hold, or Watch.')
    verb = re.sub(r"\s+", " ", m.group(1).lower())
    rule = Rule(rule_text, HOLD if verb in _HOLD_VERBS else MOVE, auto=auto)
    body = body[m.end():]
    older = _OLDER.search(body)
    if older:
        rule.older_days = _number(older.group(1)) * _UNIT_DAYS[older.group(2).lower()]
        body = body[:older.start()] + body[older.end():]
    parts = _CLAUSE.split(" " + body.strip())
    what, clauses = parts[0], list(zip(parts[1::2], parts[2::2]))
    has_to = any(kw.lower() in ("to", "into") for kw, _ in clauses)
    for kw, value in clauses:
        kw, value = kw.lower(), value.strip().strip("\"'")
        if kw in ("to", "into") or (kw == "in" and not has_to and verb in ("put", "file", "sort")):
            rule.dest = value
        else:
            rule.source = value
    if rule.action == MOVE and not rule.dest:
        raise RuleError('Say where they should go, for example "to Documents/Old PDFs".')
    if rule.action == HOLD and rule.dest:
        raise RuleError(f'"{verb.capitalize()}" sends files to the holding area, so it takes no destination. '
                        'Use "Move" to send them somewhere.')
    _parse_what(rule, what)
    return rule


def _parse_watch(watch, body):
    every = _EVERY.search(body)
    if every:
        n = 1 if every.group(1).lower() in ("a", "an") else int(every.group(1))
        watch.interval = max(60, n * (60 if every.group(2).lower() == "minute" else 3600))
        body = body[:every.start()]
    notify = _NOTIFY.search(body)
    if notify:
        watch.threshold = max(1, int(notify.group(1)))
        body = body[:notify.start()]
    quoted = re.findall(r'"([^"]+)"', body)
    rest = re.sub(r'"[^"]+"', "\0", body)
    names = []
    for part in _SPLIT.split(rest.strip().strip(",")):
        name = part.strip().strip("'")
        names.append(quoted.pop(0) if name == "\0" else name)
    for name in names:
        if name.strip() and name not in watch.folders:
            watch.folders.append(name.strip())


def parse(text):
    out = RuleSet()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            sentence = line.rstrip(".")
            if _NOTIFY_ONLY.match(sentence):
                out.watch.threshold = max(1, int(_NOTIFY_ONLY.match(sentence).group(1)))
            elif _WATCH.match(sentence):
                _parse_watch(out.watch, _WATCH.match(sentence).group(1))
            else:
                rule = parse_rule(line)
                rule.line = n
                out.rules.append(rule)
        except RuleError as e:
            out.errors.append((n, line, str(e)))
    return out


def _quote(name):
    return f'"{name}"' if re.search(r"[\s,'\"]", name) else name


def watch_sentence(watch):
    """The one "Watch ..." line that the settings screen writes."""
    names = [_quote(n) for n in watch.folders]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    minutes = watch.interval // 60
    every = f"{minutes // 60} hours" if minutes % 60 == 0 and minutes > 60 else (
        "1 hour" if minutes == 60 else f"{minutes} minutes")
    return f"Watch {listed}, and tell me after {watch.threshold} new files every {every}"


def save_watch(watch, path=None):
    """Replace the Watch and "tell me after" lines, keeping every other line."""
    path = path or rules_path()
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = EXAMPLES.splitlines()
    keep = [line for line in lines
            if not (_WATCH.match(line.strip().rstrip(".")) or _NOTIFY_ONLY.match(line.strip().rstrip(".")))]
    if watch.folders:
        keep.append(watch_sentence(watch))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(keep) + "\n")
    os.replace(tmp, path)


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "tidy")


def rules_path():
    return os.path.join(config_dir(), "rules.txt")


def load(path=None):
    try:
        with open(path or rules_path(), encoding="utf-8") as fh:
            return parse(fh.read())
    except FileNotFoundError:
        return RuleSet()


def describe(rule):
    what = sorted(rule.kinds) + sorted(rule.topics) + [f".{e}" for e in sorted(rule.exts)] + rule.patterns
    if rule.installers:
        what.append("installers")
    if rule.everything:
        what = ["all files"]
    out = ", ".join(w.lower() if not w.startswith((".", "*")) else w for w in what)
    if rule.older_days is not None:
        out += f" older than {rule.older_days} days"
    out += f" in {resolve_place(rule.source)}" if rule.source else " in any folder"
    out += f" -> {rule.dest}" if rule.dest else " -> holding area"
    return out + (" (automatic)" if rule.auto else "")
