import mimetypes
import os

PICTURES, DOCUMENTS, MUSIC, VIDEOS, ARCHIVES = "Pictures", "Documents", "Music", "Videos", "Archives"
ORDER = (PICTURES, DOCUMENTS, MUSIC, VIDEOS, ARCHIVES)

_EXT = {
    PICTURES: "jpg jpeg png gif webp heic heif avif bmp tif tiff svg ico raw cr2 cr3 nef arw dng orf rw2 xcf psd kra",
    DOCUMENTS: "pdf doc docx odt rtf txt md epub mobi djvu xls xlsx ods csv ppt pptx odp tex pages key numbers",
    MUSIC: "mp3 flac ogg oga opus m4a wav aac wma aiff aif ape mka mid midi",
    VIDEOS: "mp4 mkv webm mov avi m4v wmv flv mpg mpeg 3gp ogv ts",
    ARCHIVES: "zip 7z rar tar gz tgz xz txz bz2 tbz2 zst iso",
}
_BY_EXT = {ext: kind for kind, exts in _EXT.items() for ext in exts.split()}

_MAGIC = (
    (b"\x89PNG", PICTURES), (b"\xff\xd8\xff", PICTURES), (b"GIF8", PICTURES), (b"BM", PICTURES),
    (b"%PDF", DOCUMENTS), (b"ID3", MUSIC), (b"fLaC", MUSIC), (b"OggS", MUSIC),
    (b"\x1aE\xdf\xa3", VIDEOS), (b"PK\x03\x04", ARCHIVES), (b"7z\xbc\xaf", ARCHIVES),
    (b"Rar!", ARCHIVES), (b"\x1f\x8b", ARCHIVES), (b"\xfd7zXZ", ARCHIVES), (b"(\xb5/\xfd", ARCHIVES),
)
_MIME_PREFIX = {"image/": PICTURES, "audio/": MUSIC, "video/": VIDEOS}


def _head(path, n=16):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOATIME", 0))
    except PermissionError:
        fd = os.open(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        return fh.read(n)


def sniff(path):
    try:
        head = _head(path)
    except OSError:
        return None
    if head[4:8] == b"ftyp":
        return PICTURES if head[8:12] in (b"heic", b"heix", b"avif", b"mif1") else VIDEOS
    if head[:4] == b"RIFF":
        return {b"WEBP": PICTURES, b"WAVE": MUSIC, b"AVI ": VIDEOS}.get(head[8:12])
    return next((kind for magic, kind in _MAGIC if head.startswith(magic)), None)


def kind_of(path):
    name = os.path.basename(path).lower()
    _, dot, ext = name.rpartition(".")
    if dot and ext in _BY_EXT:
        return _BY_EXT[ext]
    mime, _ = mimetypes.guess_type(name, strict=False)
    if mime:
        for prefix, kind in _MIME_PREFIX.items():
            if mime.startswith(prefix):
                return kind
    if not dot:
        return sniff(path)
    return None
