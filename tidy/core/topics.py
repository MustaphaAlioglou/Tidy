import os
import re
import time
import unicodedata
import zlib

from .kinds import DOCUMENTS, PICTURES, _head

SCREENSHOTS, RECEIPTS, TAX = "Screenshots", "Receipts", "Tax"
ORDER = (SCREENSHOTS, RECEIPTS, TAX)
DESCRIPTION = {
    SCREENSHOTS: "Screenshots taken in {year}.",
    RECEIPTS: "Receipts and invoices from {year}.",
    TAX: "Tax documents for {year}.",
}

_SCREENSHOT_NAME = re.compile(
    r"^(screenshot|screen ?shot|scrot|bildschirmfoto|capture d.ecran|captura de pantalla|"
    r"schermafbeelding|istantanea|στιγμιοτυπο)", re.I)
_RECEIPT_WORDS = {"receipt", "receipts", "invoice", "invoices", "rechnung", "quittung", "facture",
                  "factura", "fattura", "ricevuta", "αποδειξη", "αποδειξεις", "τιμολογιο", "τιμολογια",
                  "παραστατικο"}
_TAX_WORDS = {"tax", "taxes", "1040", "1099", "w2", "p60", "steuer", "steuererklarung", "steuerbescheid",
              "impots", "εφορια", "ενφια", "εκκαθαριστικο", "φορολογικη", "φορολογικο", "φορολογια"}
# Matched against PDF text with all whitespace removed, so words split across
# text runs still match.
_RECEIPT_TEXT = (b"invoicenumber", b"invoiceno", b"invoicedate", b"receiptnumber", b"receiptno",
                 b"ordernumber", b"amountdue", b"amountpaid", b"totalpaid", b"taxinvoice",
                 b"rechnungsnummer", b"numerodefacture")
_TAX_TEXT = (b"taxreturn", b"form1040", b"formw-2", b"taxyear", b"steuerbescheid", b"einkommensteuer")

_DATED = re.compile(r"(?<!\d)((?:19[89]|20\d)\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_YEAR = re.compile(r"(?<!\d)((?:19[89]|20\d)\d)(?!\d)")
_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_STRING = re.compile(rb"\(((?:\\.|[^\\()]){1,256})\)")
_PDF_READ = 1 << 20


def _plain(text):
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _words(stem):
    return set(re.split(r"[^\w]+|_", stem)) - {""}


def pdf_text(path):
    data = _head(path, _PDF_READ)
    if not data.startswith(b"%PDF"):
        return b""
    chunks, budget = [data], 4 * _PDF_READ
    for m in _STREAM.finditer(data):
        if budget <= 0:
            break
        try:
            chunks.append(zlib.decompressobj().decompress(m.group(1), min(budget, _PDF_READ)))
        except zlib.error:
            continue
        budget -= len(chunks[-1])
    text = b"".join(s for chunk in chunks for s in _STRING.findall(chunk))
    return re.sub(rb"\s+", b"", text.lower())


def year_of(name, mtime, now=None):
    """The year in the name, else the year it was last changed. A bare
    number such as IMG_1990 only counts when it is close to that year."""
    latest = time.localtime(now or time.time()).tm_year + 1
    changed = time.localtime(mtime).tm_year
    for m in _DATED.finditer(name):
        if int(m.group(1)) <= latest:
            return int(m.group(1))
    for m in _YEAR.finditer(name):
        if changed - 5 <= int(m.group(1)) <= min(changed + 1, latest):
            return int(m.group(1))
    return changed


def topic_of(path, kind):
    """Return (topic, why) for a file, or None."""
    name = _plain(os.path.basename(path))
    stem = os.path.splitext(name)[0]
    if kind == PICTURES and _SCREENSHOT_NAME.match(stem):
        return SCREENSHOTS, "Screenshot"
    if kind not in (PICTURES, DOCUMENTS):
        return None
    words = _words(stem)
    if words & _TAX_WORDS or re.search(r"(?<![a-z0-9])w-2(?![0-9])", stem):
        return TAX, "Name mentions tax"
    if words & _RECEIPT_WORDS:
        return RECEIPTS, "Name mentions a receipt or invoice"
    if name.endswith(".pdf"):
        try:
            text = pdf_text(path)
        except OSError:
            return None
        if any(p in text for p in _TAX_TEXT):
            return TAX, "Reads like a tax document"
        if any(p in text for p in _RECEIPT_TEXT):
            return RECEIPTS, "Reads like a receipt or invoice"
    return None
