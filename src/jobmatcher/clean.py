"""Turn posting HTML into short, clean text, and pull numbers out in code.

The classifier is less accurate on long, noisy input, so this strips markup, equal-opportunity
boilerplate, benefits lists and application instructions. Salary and years of experience are
parsed here, never asked of the classifier.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    """HTML (possibly entity-escaped, as Greenhouse returns it) to plain text with line breaks."""
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


# A section whose heading matches one of these is dropped up to the next heading.
_DROP_HEADINGS = re.compile(
    r"^(benefits|perks|what we offer|our benefits|compensation (and|&) benefits|"
    r"equal (employment )?opportunity|eeo|diversity|accommodations?|"
    r"how to apply|application process|our commitment|privacy notice|"
    r"total rewards|why join us|life at)\b",
    re.IGNORECASE,
)
# Individual boilerplate sentences that appear anywhere.
_BOILERPLATE = re.compile(
    r"[^.\n]*\b(equal opportunity employer|without regard to (race|age|sex)|"
    r"reasonable accommodation|e-verify|affirmative action|protected veteran|"
    r"applicant privacy|pay transparency (nondiscrimination|policy)|"
    r"fair chance (ordinance|act)|criminal histor(y|ies))\b[^.\n]*\.?",
    re.IGNORECASE,
)


def _is_heading(line: str) -> bool:
    stripped = line.strip().rstrip(":")
    return 0 < len(stripped) <= 60 and not stripped.startswith("- ") and not stripped.endswith(".")


def strip_boilerplate(text: str) -> str:
    out: list[str] = []
    dropping = False
    for line in text.splitlines():
        if _is_heading(line):
            dropping = bool(_DROP_HEADINGS.match(line.strip().lstrip("#* ").strip()))
        if not dropping:
            out.append(line)
    cleaned = _BOILERPLATE.sub("", "\n".join(out))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_description(raw_html: str, max_chars: int = 12_000) -> str:
    text = strip_boilerplate(html_to_text(raw_html))
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text


# ---------------------------------------------------------------------------- numbers in code

_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?([kK])?"
_RANGE = re.compile(_MONEY + r"\s*(?:-|–|—|to)\s*" + _MONEY + r"(?:\s*(?:USD|per year|/\s?y(?:ea)?r|annually))?")
_HOURLY = re.compile(r"(per hour|/\s?h(ou)?r|hourly)", re.IGNORECASE)


def _money(number: str, k: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * 1000 if k else value


def parse_salary(text: str) -> tuple[float | None, float | None]:
    """Find an annual USD range like "$120,000 - $150,000" or "$120k–$150k". Hourly ranges are
    converted at 2,080 hours a year. Returns (None, None) when nothing plausible is found."""
    for m in _RANGE.finditer(text or ""):
        lo = _money(m.group(1), m.group(2))
        hi = _money(m.group(3), m.group(4))
        if lo > hi:
            lo, hi = hi, lo
        tail = text[m.end(): m.end() + 30]
        if _HOURLY.search(tail) and hi < 500:
            lo, hi = lo * 2080, hi * 2080
        if 15_000 <= lo <= 2_000_000 and hi <= 3_000_000:
            return lo, hi
    return None, None


_YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})\s*(?:\+|or more)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*"
    r"(?:years?|yrs?)\b(?:\s+of)?(?:\s+\w+){0,6}?\s+(?:experience|exp\b)",
    re.IGNORECASE,
)


def parse_years_required(text: str) -> int | None:
    """The smallest "N years of ... experience" requirement mentioned, or None if none found.

    Taking the smallest is deliberate: postings often list "3+ years of X" and "1+ years of Y";
    the binding requirement for a filter that only drops clear mismatches is the lowest."""
    found = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    found = [n for n in found if 0 < n <= 30]
    return min(found) if found else None


_CLEARANCE_LEVELS = {"public_trust": 1, "secret": 2, "top_secret": 3}
_CLEARANCE_PATTERNS = [
    ("top_secret", re.compile(r"\b(top\s+secret|TS\s*/\s*SCI|TS\s+clearance|SCI\s+eligib)", re.IGNORECASE)),
    ("secret", re.compile(r"\bsecret\s+(security\s+)?clearance\b|\bclearance\s+level:\s*secret\b", re.IGNORECASE)),
    ("public_trust", re.compile(r"\bpublic\s+trust\b", re.IGNORECASE)),
]


def clearance_required(text: str) -> str | None:
    """The highest security clearance the text mentions: public_trust, secret, top_secret."""
    for level, pattern in _CLEARANCE_PATTERNS:
        if pattern.search(text or ""):
            return level
    return None


def clearance_rank(level: str | None) -> int:
    return _CLEARANCE_LEVELS.get(level or "", 0)
