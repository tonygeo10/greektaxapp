"""Text helpers shared by the rule-based parser (digest.py) and the optional LLM layer (llm.py)."""
import re
import unicodedata
from datetime import date, timedelta


def norm(s):
    """Lowercase, strip Greek accents, fold final sigma. Same length as the NFC input."""
    s = unicodedata.normalize("NFD", unicodedata.normalize("NFC", s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("ς", "σ")


def squash(s):
    """Folded text with all whitespace collapsed, for 'is this text inside that text' checks."""
    return " ".join(norm(s or "").split())


_STEMS = [("ιανουαρι", 1), ("φεβρουαρι", 2), ("μαρτι", 3), ("απριλι", 4), ("μαι", 5), ("ιουνι", 6),
          ("ιουλι", 7), ("αυγουστ", 8), ("σεπτεμβρι", 9), ("οκτωβρι", 10), ("νοεμβρι", 11), ("δεκεμβρι", 12)]
MONTH_WORDS = {stem + end: n for stem, n in _STEMS for end in ("ου", "οσ")}
DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTH_WORDS) + r")(?:\s+(\d{4}))?")
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)")


def dates_in(text, base=None):
    """Every calendar date written in `text`, as a list of {'date': date, 'inferred': bool}.

    A date without a year takes the year of `base` (the publication date), rolling to the next year
    if that would put it more than 30 days in the past.
    """
    t = norm(text or "")
    base = base or date.today()
    found = []
    for m in DATE_TXT.finditer(t):
        found.append((int(m.group(1)), MONTH_WORDS[m.group(2)], int(m.group(3)) if m.group(3) else None))
    for m in DATE_NUM.finditer(t):
        found.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    out = []
    for d, mon, y in found:
        try:
            dt = date(y or base.year, mon, d)
            if y is None and dt < base - timedelta(days=30):
                dt = date(base.year + 1, mon, d)
        except ValueError:
            continue
        out.append({"date": dt, "inferred": y is None})
    return out


def locate(quote, text):
    """Find `quote` inside `text` ignoring case, accents and whitespace differences.

    Returns the exact span as written in `text` (whitespace collapsed), or None if the quote is not there.
    This is what makes LLM output checkable: a quote the model made up cannot be located.
    """
    import unicodedata as _u
    t = _u.normalize("NFC", text or "")
    f = norm(t)
    toks = norm(_u.normalize("NFC", quote or "")).split()
    if not toks:
        return None
    m = re.search(r"\s+".join(re.escape(x) for x in toks), f)
    if not m:
        return None
    if len(f) != len(t):  # folding changed the length (very rare): fall back to the verified quote itself
        return " ".join((quote or "").split())
    return " ".join(t[m.start():m.end()].split())
