import os

SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc",
               "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/snap", "/nix")
REMOVABLE_DIRS = ("/media", "/mnt", "/run/media")

VCS_MARKERS = {".git", ".hg", ".svn", ".bzr"}
PROJECT_MARKERS = {"Cargo.toml", "package.json", "pyproject.toml", "setup.py", "go.mod",
                   "CMakeLists.txt", "meson.build", "Makefile", "pom.xml", "build.gradle",
                   "composer.json", "Gemfile", ".project", ".idea", ".vscode"}
TIDY_MARKER = ".tidy-folder"


class Protected(Exception):
    pass


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def protected_reason(path):
    try:
        names = set(os.listdir(path))
    except OSError as e:
        return e.strerror or "unreadable"
    if names & VCS_MARKERS:
        return "git repository" if ".git" in names else "version-controlled folder"
    if names & PROJECT_MARKERS:
        return "project folder"
    if TIDY_MARKER in names:
        return "made by Tidy"
    return None


def dest_problem(dest, folder):
    """Why files may not be moved into dest, or None."""
    real = os.path.realpath(dest)
    home = os.path.realpath(os.path.expanduser("~"))
    if real == os.path.realpath(folder):
        return "it is the folder being tidied"
    if _under(real, home) and real != home:
        rel = os.path.relpath(real, home)
        if any(part.startswith(".") for part in rel.split(os.sep)):
            return "it is a hidden folder"
        return None
    if any(_under(real, r) for r in REMOVABLE_DIRS) and real not in REMOVABLE_DIRS:
        return None
    return "it is outside your home folder"


def check_folder(path, allow_project=False):
    real = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    if not os.path.isdir(real):
        raise Protected(f"{path} is not a folder.")
    if real == home:
        raise Protected("Pick a folder inside your home folder, not the home folder itself.")
    if _under(real, home):
        rel = os.path.relpath(real, home)
        if any(part.startswith(".") for part in rel.split(os.sep)):
            raise Protected("Hidden folders hold settings and app data, so Tidy leaves them alone.")
    elif not any(_under(real, r) for r in REMOVABLE_DIRS) or real in REMOVABLE_DIRS:
        if real == "/" or any(_under(real, s) for s in SYSTEM_DIRS):
            raise Protected("This is a system folder, so Tidy leaves it alone.")
        raise Protected("Tidy only works inside your home folder or on removable drives.")
    if not allow_project:
        reason = protected_reason(real)
        if reason in ("git repository", "version-controlled folder", "project folder"):
            raise Protected(f"This looks like a {reason}, so Tidy leaves it alone.")
    return real
