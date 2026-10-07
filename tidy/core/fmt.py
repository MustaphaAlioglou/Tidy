import time


def size(n):
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000


def ago(ts, now=None):
    secs = max(0, (now or time.time()) - ts)
    days = secs / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 31:
        return f"{int(days)} days ago"
    months = int(days / 30.44)
    if months < 12:
        return "1 month ago" if months == 1 else f"{months} months ago"
    years = int(days / 365.25)
    return "1 year ago" if years == 1 else f"{years} years ago"


def items(n):
    return "1 item" if n == 1 else f"{n} items"
