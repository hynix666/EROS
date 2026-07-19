"""Deterministic Groundedness Kernel (DGK) — EROS v3.2 §6.8.1 / ADR-015.

The DGK proves UNGROUNDED. No model participates. It extracts numbers,
dates, entities, and quotations from a claim and proves whether each
occurs in the cited evidence chunks under the canonical tolerances:

  * Numbers:     scale unification ("1.5 billion" -> 1_500_000_000),
                 percent/decimal alternates, 0.5% relative tolerance.
  * Dates:       normalised to ISO 8601; partial dates ("June 2024")
                 normalised to the first of the month; strict parsing.
  * Entities:    NER (spaCy en_core_web_sm when available, deterministic
                 rule-based fallback otherwise) + 0.25 normalised
                 Levenshtein tolerance for OCR/parsing noise.
  * Quotations:  exact substring match after NFKC normalisation,
                 whitespace collapse, and quote-mark unification.

Verdict semantics (canonical): the kernel returns 'UNGROUNDED' when it
can *prove* an anchor is absent from all cited evidence, otherwise
'INDETERMINATE'. It never returns "grounded" — proving groundedness is
not its job, and Tier-2 NLI (advisory) sits above it.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from rapidfuzz.distance import Levenshtein

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Canonical tolerances (§6.8.1). Overridable per-call so M7 tuning can loosen
# entity_tolerance and record the value used (groundedness_kernel_results).
# ─────────────────────────────────────────────────────────────────────────────
NUMBER_TOLERANCE = 0.005   # 0.5% relative
ENTITY_TOLERANCE = 0.25    # normalised Levenshtein distance

_SCALE_WORDS = {
    "thousand": 1_000, "k": 1_000,
    "million": 1_000_000, "m": 1_000_000, "mm": 1_000_000,
    "billion": 1_000_000_000, "b": 1_000_000_000, "bn": 1_000_000_000,
    "trillion": 1_000_000_000_000, "t": 1_000_000_000_000, "tn": 1_000_000_000_000,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Date patterns, most specific first. Deterministic; ambiguous all-numeric
# non-ISO forms (05/06/2024) are deliberately NOT extracted — a kernel that
# guesses date order manufactures false UNGROUNDED verdicts.
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.I), "dmy"),
    (re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "mdy"),
    (re.compile(rf"\b({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.I), "my"),
]

_NUMBER_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"       # 1,500,000 | 1.5 | 42
    r"\s*(%|(?:percent|" + "|".join(_SCALE_WORDS) + r")\b)?",
    re.I,
)

_QUOTE_RE = re.compile(r"[\"\u201c\u2018']([^\"\u201c\u201d\u2018\u2019']{4,})[\"\u201d\u2019']")

_ENTITY_STOPWORDS = {
    "the", "a", "an", "in", "on", "of", "for", "and", "or", "but", "with",
    "however", "meanwhile", "according", "it", "its", "this", "that", "these",
    "those", "he", "she", "they", "we", "i", "as", "at", "by", "from", "to",
}
_MONTH_TOKENS = set(_MONTHS)

# spaCy is the spec-named NER path; the rule-based extractor is the
# deterministic portable fallback. Resolution happens once at import.
try:  # pragma: no cover - environment dependent
    import spacy

    try:
        _NLP = spacy.load("en_core_web_sm", disable=["parser", "tagger", "lemmatizer"])
        NER_BACKEND = "spacy"
    except OSError:
        _NLP = None
        NER_BACKEND = "rulebased"
        logger.warning("spaCy installed but en_core_web_sm missing; DGK using rule-based NER fallback")
except ImportError:  # pragma: no cover
    _NLP = None
    NER_BACKEND = "rulebased"


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class KernelResult:
    verdict: str  # 'UNGROUNDED' | 'INDETERMINATE'
    missing_numbers: list[str] = field(default_factory=list)
    missing_dates: list[str] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)
    missing_quotations: list[str] = field(default_factory=list)
    entity_tolerance: float = ENTITY_TOLERANCE
    number_tolerance: float = NUMBER_TOLERANCE
    ner_backend: str = NER_BACKEND


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation primitives
# ─────────────────────────────────────────────────────────────────────────────
def _nfkc(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip()


def _norm_quote(text: str) -> str:
    t = _nfkc(text).lower()
    t = re.sub(r"[^\w\s]", "", t)          # punctuation-insensitive per §6.8.1
    return re.sub(r"\s+", " ", t).strip()


def extract_numbers(text: str) -> list[tuple[float, bool, str]]:
    """Return (value, is_percent, surface) triples with scale unification."""
    out: list[tuple[float, bool, str]] = []
    for m in _NUMBER_RE.finditer(text):
        raw, suffix = m.group(1), (m.group(2) or "").lower().rstrip(".")
        value = float(raw.replace(",", ""))
        is_percent = suffix in ("%", "percent")
        if suffix in _SCALE_WORDS:
            value *= _SCALE_WORDS[suffix]
        out.append((value, is_percent, m.group(0).strip()))
    return out


def extract_dates(text: str) -> tuple[set[str], str]:
    """Return (ISO dates, text with date spans masked).

    Masking prevents a date's components from being re-extracted as bare
    numbers ("June 2024" must not also demand a standalone 2024).
    """
    found: set[str] = set()
    masked = text
    for pattern, kind in _DATE_PATTERNS:
        def repl(m: re.Match[str]) -> str:
            try:
                if kind == "iso":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "dmy":
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
                elif kind == "mdy":
                    mo, d, y = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
                else:  # my — partial date, first of month (canonical)
                    mo, d, y = _MONTHS[m.group(1).lower()], 1, int(m.group(2))
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    found.add(f"{y:04d}-{mo:02d}-{d:02d}")
                    return " \u0000DATE\u0000 "
            except (KeyError, ValueError):
                pass
            return m.group(0)

        masked = pattern.sub(repl, masked)
    return found, masked


def extract_quotations(text: str) -> list[str]:
    return [m.group(1) for m in _QUOTE_RE.finditer(_nfkc(text))]


def _rule_based_entities(text: str) -> set[str]:
    """Deterministic fallback NER: runs of Capitalised/alnum-capitalised tokens."""
    ents: set[str] = set()
    token_re = re.compile(r"\b([A-Z][\w&.'-]*(?:\s+[A-Z0-9][\w&.'-]*)*)")
    for m in token_re.finditer(text):
        candidate = m.group(1).strip(".,'\"")
        first = candidate.split()[0].lower()
        if first in _ENTITY_STOPWORDS or first in _MONTH_TOKENS:
            parts = candidate.split()[1:]
            if not parts:
                continue
            candidate = " ".join(parts)
            if candidate and candidate.split()[0].lower() in _MONTH_TOKENS:
                continue
        if len(candidate) >= 3 and not candidate.replace(" ", "").isdigit():
            ents.add(candidate)
    return ents


def extract_entities(text: str, extra_dictionary: Iterable[str] = ()) -> set[str]:
    ents: set[str] = set()
    if _NLP is not None:
        doc = _NLP(text)
        ents |= {e.text.strip(".,'\"") for e in doc.ents
                 if e.label_ in ("ORG", "PERSON", "GPE", "PRODUCT", "FAC", "LOC", "NORP", "EVENT", "WORK_OF_ART")}
    ents |= _rule_based_entities(text)  # union: fallback also catches OCR-noised caps like "Micros0ft"
    for term in extra_dictionary:       # custom domain dictionaries (§6.8.1)
        if re.search(re.escape(term), text, re.I):
            ents.add(term)
    return {e for e in ents if len(e) >= 3}


def _lemma_entity(e: str) -> str:
    """Light lemmatisation for fuzzy entity matching only (§6.8.1 step 7)."""
    e = e.lower().strip()
    e = re.sub(r"'s\b", "", e)
    return re.sub(r"\s+", " ", e)


# ─────────────────────────────────────────────────────────────────────────────
# Matching logic
# ─────────────────────────────────────────────────────────────────────────────
def _number_matches(claim_val: float, claim_pct: bool,
                    evidence: Sequence[tuple[float, bool, str]],
                    tol: float) -> bool:
    candidates = [claim_val]
    if claim_pct:
        candidates.append(claim_val / 100.0)  # "10%" ↔ "0.1"
    for ev_val, ev_pct, _ in evidence:
        ev_candidates = [ev_val] + ([ev_val / 100.0] if ev_pct else [])
        for cv in candidates:
            for ev in ev_candidates:
                if cv == ev:
                    return True
                denom = max(abs(cv), abs(ev))
                if denom > 0 and abs(cv - ev) / denom <= tol:
                    return True
    return False


def _entity_matches(entity: str, evidence_text: str,
                    evidence_entities: set[str], tol: float) -> bool:
    target = _lemma_entity(entity)
    if target in evidence_text.lower():
        return True
    for ev in evidence_entities:
        if Levenshtein.normalized_distance(target, _lemma_entity(ev)) <= tol:
            return True
    # Sliding-window fuzzy substring for OCR noise inside longer evidence text
    ev_lower = evidence_text.lower()
    n = len(target)
    if n >= 4:
        step = max(1, n // 4)
        for i in range(0, max(1, len(ev_lower) - n + 1), step):
            window = ev_lower[i:i + n + 2]
            if Levenshtein.normalized_distance(target, window[:n]) <= tol:
                return True
    return False


def check_claim(claim_text: str,
                evidence_texts: Sequence[str],
                *,
                number_tolerance: float = NUMBER_TOLERANCE,
                entity_tolerance: float = ENTITY_TOLERANCE,
                domain_dictionary: Iterable[str] = ()) -> KernelResult:
    """Run the kernel on one claim against its cited evidence chunks."""
    claim_norm = _nfkc(claim_text)
    evidence_norm = _nfkc(" \n ".join(evidence_texts))

    # 1) Quotations first (their internal anchors ride on the quote match)
    missing_quotes: list[str] = []
    ev_quote_haystack = _norm_quote(evidence_norm)
    claim_quotes = extract_quotations(claim_norm)
    for q in claim_quotes:
        if _norm_quote(q) not in ev_quote_haystack:
            missing_quotes.append(q)
    claim_wo_quotes = _QUOTE_RE.sub(" ", claim_norm)

    # 2) Dates (mask them so their digits don't re-extract as numbers)
    claim_dates, claim_masked = extract_dates(claim_wo_quotes)
    ev_dates, _ = extract_dates(evidence_norm)
    missing_dates = sorted(d for d in claim_dates if d not in ev_dates)

    # 3) Numbers on the date-masked claim
    claim_numbers = extract_numbers(claim_masked)
    _, ev_masked = extract_dates(evidence_norm)
    ev_numbers = extract_numbers(ev_masked)
    missing_numbers = [
        surface for (val, pct, surface) in claim_numbers
        if not _number_matches(val, pct, ev_numbers, number_tolerance)
    ]

    # 4) Entities on the original (unmasked) claim text
    claim_entities = extract_entities(claim_wo_quotes, domain_dictionary)
    ev_entities = extract_entities(evidence_norm, domain_dictionary)
    missing_entities = sorted(
        e for e in claim_entities
        if not _entity_matches(e, evidence_norm, ev_entities, entity_tolerance)
    )

    ungrounded = bool(missing_numbers or missing_dates or missing_entities or missing_quotes)
    return KernelResult(
        verdict="UNGROUNDED" if ungrounded else "INDETERMINATE",
        missing_numbers=missing_numbers,
        missing_dates=missing_dates,
        missing_entities=missing_entities,
        missing_quotations=missing_quotes,
        entity_tolerance=entity_tolerance,
        number_tolerance=number_tolerance,
    )
