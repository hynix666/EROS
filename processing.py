"""Content processing — canonical §6.5.1.

* HTML → text: dependency-free extractor (script/style stripped, block
  elements become paragraph breaks). PDFs use pdfplumber when the optional
  extra is installed; otherwise the artifact is quarantined as an
  ArtifactError (taxonomy: quarantine, audit, continue run, log gap).
* Chunking: recursive splitter, 512-token target, 20% overlap (102 tokens).
  Token counting is whitespace-token approximation [judgment] — deterministic
  and dependency-free; the canonical numbers are targets, not exact BPE.
* Embeddings: ``bge-large-en-v1.5`` (1024-dim) through fastembed/ONNX when
  the ``embeddings`` extra is installed — CPU path, AVX-512 VNNI where the
  silicon has it. Without it, chunks store NULL embeddings and retrieval
  degrades to FTS-only; the degradation is recorded and disclosed in the
  report footer, never silent.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Sequence

from eros.errors import ArtifactError

logger = logging.getLogger(__name__)

CHUNK_TOKENS = 512
CHUNK_OVERLAP = 102  # 20% of 512 (canonical)

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2",
               "h3", "h4", "h5", "h6", "blockquote", "pre", "td", "th"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def extract_text(content: bytes, content_type: str = "text/html") -> str:
    ct = content_type.split(";")[0].strip().lower()
    if ct == "application/pdf" or content[:5] == b"%PDF-":
        try:
            import io

            import pdfplumber  # optional extra
        except ImportError as e:
            raise ArtifactError("PDF received but the 'pdf' extra (pdfplumber) is not installed",
                                content_type=ct) from e
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n\n".join((page.extract_text() or "") for page in pdf.pages)
    text = content.decode("utf-8", errors="replace")
    if ct.startswith("text/html") or "<html" in text[:2000].lower():
        p = _TextExtractor()
        p.feed(text)
        text = "".join(p.parts)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Chunking ────────────────────────────────────────────────────────────────
def _tokens(text: str) -> list[str]:
    return text.split()


def chunk_text(text: str, target: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Recursive splitter: paragraphs → sentences → hard token windows."""
    if not text.strip():
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    count = 0

    def flush():
        nonlocal current, count
        if current:
            chunks.append(" ".join(current).strip())
            if overlap > 0:
                tail = _tokens(chunks[-1])[-overlap:]
                current, count = [" ".join(tail)], len(tail)
            else:
                current, count = [], 0

    for para in paragraphs:
        ptoks = len(_tokens(para))
        if ptoks > target:
            for sent in re.split(r"(?<=[.!?])\s+", para):
                stoks = len(_tokens(sent))
                if count + stoks > target and count > overlap:
                    flush()
                if stoks > target:  # pathological sentence → hard windows
                    toks = _tokens(sent)
                    for i in range(0, len(toks), target - overlap):
                        chunks.append(" ".join(toks[i:i + target]))
                    current, count = [], 0
                else:
                    current.append(sent)
                    count += stoks
        else:
            if count + ptoks > target and count > overlap:
                flush()
            current.append(para)
            count += ptoks
    if current and " ".join(current).strip():
        chunks.append(" ".join(current).strip())
    # Dedup exact-duplicate trailing chunk the overlap re-seed can produce.
    return [c for i, c in enumerate(chunks) if i == 0 or c != chunks[i - 1]]


# ── Embeddings ──────────────────────────────────────────────────────────────
class Embedder:
    """bge-large-en-v1.5 via fastembed when available; honest None otherwise."""

    MODEL = "BAAI/bge-large-en-v1.5"

    def __init__(self) -> None:
        self._model = None
        self.available = False
        try:  # pragma: no cover - environment dependent
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.MODEL)
            self.available = True
        except Exception as e:  # ImportError or model-download failure
            logger.warning("embeddings unavailable (%s); retrieval degrades to FTS-only", e)

    def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        if not self.available or self._model is None:
            return [None] * len(texts)
        return [list(map(float, v)) for v in self._model.embed(list(texts), batch_size=256)]

    def embed_one(self, text: str) -> list[float] | None:
        return self.embed([text])[0]
