"""Extração de links, títulos e âncoras de textos Markdown."""
from __future__ import annotations

import bisect
import html
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .slug import Slugger, heading_text

_FENCE = re.compile(r"^[ \t]*(?:>[ \t]?)*[ \t]*(`{3,}|~{3,})(.*)$")
_FRONT_MATTER_END = re.compile(r"^(?:---|\.\.\.)[ \t]*$")
_BACKTICKS = re.compile(r"`+")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")

_DESTINATION = r"(?:<([^<>\n]*)>|((?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))+))"
_TITLE = r"(?:\s+(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?"
_INLINE = re.compile(
    r"(!?)\[((?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*)\]\(\s*"
    + _DESTINATION + "?" + _TITLE + r"\s*\)",
    re.S,
)
_REF_DEFINITION = re.compile(
    r"^[ \t]*(?:>[ \t]?)*[ \t]*\[((?:[^\[\]\\\n]|\\.)+)\]:[ \t]*(?:<([^<>\n]*)>|(\S+))"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$",
    re.M,
)
_REF_USE = re.compile(r"(?<![\w\]\\])!?\[((?:[^\[\]\\\n]|\\.)+)\]\[((?:[^\[\]\\\n]|\\.)*)\]")
_AUTOLINK = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.\-]{1,31}:[^\s<>]*)>")
_HTML_TAG = re.compile(r"<(a|img)(\s[^<>]*)?/?>", re.I)
_ATTRIBUTE = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
_BARE_URL = re.compile(r"(?<![\w/:.@\-])https?://[^\s<>\"'`]+", re.I)

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ATX_CLOSING = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_QUOTE_PREFIX = re.compile(r"^(?: {0,3}>[ ]?)+")
_NOT_PARAGRAPH = re.compile(
    r"^ {0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|<!--|</?[A-Za-z]|([-*_])(?:[ \t]*\1){2,}[ \t]*$)"
)


@dataclass
class Link:
    """Um link encontrado no Markdown.

    ``kind`` indica a sintaxe: "link", "imagem", "definição", "autolink",
    "html", "url" (URL solta) ou "referência" (uso de referência não definida,
    com o rótulo em ``label``).
    """

    target: str
    line: int
    kind: str
    label: Optional[str] = None


class _LineIndex:
    def __init__(self, text: str) -> None:
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _mask(text: str, spans: Sequence[Tuple[int, int]]) -> str:
    """Troca por espaços os trechos indicados, preservando quebras de linha."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _mask_blocks(text: str) -> str:
    """Esconde front matter YAML e blocos de código cercados (``` e ~~~)."""
    lines = text.split("\n")
    hidden = [False] * len(lines)
    first = 0
    if lines and lines[0].rstrip() == "---":
        for index in range(1, len(lines)):
            if _FRONT_MATTER_END.match(lines[index]):
                for k in range(index + 1):
                    hidden[k] = True
                first = index + 1
                break
    fence = None
    for index in range(first, len(lines)):
        match = _FENCE.match(lines[index])
        if fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
                hidden[index] = True
        else:
            hidden[index] = True
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
                and not match.group(2).strip()
            ):
                fence = None
    return "\n".join(" " * len(line) if hidden[i] else line for i, line in enumerate(lines))


def _code_span_spans(text: str) -> List[Tuple[int, int]]:
    spans = []
    position = 0
    while True:
        opening = _BACKTICKS.search(text, position)
        if opening is None:
            break
        start = opening.start()
        if start > 0 and text[start - 1] == "\\":
            position = start + 1
            continue
        closing = re.compile(r"(?<!`)" + opening.group(0) + r"(?!`)").search(text, opening.end())
        if closing is None or _BLANK_LINE.search(text, opening.end(), closing.start()):
            position = opening.end()
            continue
        spans.append((start, closing.end()))
        position = closing.end()
    return spans


def _prepare(text: str) -> Tuple[str, str]:
    """Devolve (texto para títulos, texto para links), com código e comentários escondidos.

    Para títulos, os comentários ficam só com os delimitadores, porque o
    conteúdo de um título ainda precisa das partes em código.
    """
    blocks = _mask_blocks(_normalize_newlines(text))
    without_code = _mask(blocks, _code_span_spans(blocks))
    comments = [m.span() for m in _COMMENT.finditer(without_code)]
    for_headings = _mask(blocks, [(start + 4, end - 3) for start, end in comments])
    for_links = _mask(without_code, comments)
    return for_headings, for_links


def _clean_destination(value: str) -> str:
    return html.unescape(_ESCAPED.sub(r"\1", value)).strip()


def normalize_label(label: str) -> str:
    """Normaliza o rótulo de uma referência ([Rótulo] e [rótulo] são iguais)."""
    return " ".join(label.split()).casefold()


def _trim_bare_url(url: str) -> str:
    while url:
        last = url[-1]
        if last in ".,:;!?*_~'\"":
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        elif last == "]" and url.count("[") < url.count("]"):
            url = url[:-1]
        else:
            break
    return url


def _first_value(match: "re.Match[str]") -> Tuple[Optional[str], int]:
    for group in (2, 3, 4):
        if match.group(group) is not None:
            return match.group(group), group
    return None, 0


def extract_links(text: str, bare_urls: bool = False) -> List[Link]:
    """Lista os links de um texto Markdown, em ordem, com o número da linha.

    Ignora blocos de código, trechos em código (`assim`), comentários HTML e
    front matter. Com ``bare_urls``, inclui também URLs soltas no texto.
    """
    _, masked = _prepare(text)
    lines = _LineIndex(masked)
    found: List[Tuple[int, Link]] = []
    taken: List[Tuple[int, int]] = []

    definitions: Dict[str, str] = {}
    for match in _REF_DEFINITION.finditer(masked):
        label = match.group(1)
        if label.startswith("^"):
            continue
        group = 2 if match.group(2) is not None else 3
        target = _clean_destination(match.group(group))
        definitions.setdefault(normalize_label(label), target)
        offset = match.start(group)
        found.append((offset, Link(target, lines.line(offset), "definição")))
        taken.append(match.span())

    def scan_inline(start: int, end: int) -> None:
        for match in _INLINE.finditer(masked, start, end):
            group = 3 if match.group(3) is not None else 4
            if match.group(group) is not None:
                offset = match.start(group)
                kind = "imagem" if match.group(1) else "link"
                found.append((offset, Link(_clean_destination(match.group(group)), lines.line(offset), kind)))
            taken.append(match.span())
            scan_inline(match.start(2), match.end(2))

    scan_inline(0, len(masked))

    for match in _REF_USE.finditer(masked):
        label = match.group(2) or match.group(1)
        if label.startswith("^") or normalize_label(label) in definitions:
            continue
        offset = match.start()
        found.append((offset, Link("", lines.line(offset), "referência", label=label)))

    for match in _AUTOLINK.finditer(masked):
        offset = match.start(1)
        found.append((offset, Link(match.group(1), lines.line(offset), "autolink")))
        taken.append(match.span())

    for match in _HTML_TAG.finditer(masked):
        wanted = "href" if match.group(1).lower() == "a" else "src"
        attributes = match.group(2) or ""
        for attribute in _ATTRIBUTE.finditer(attributes):
            if attribute.group(1).lower() != wanted:
                continue
            value, group = _first_value(attribute)
            if value is not None and value.strip():
                offset = match.start(2) + attribute.start(group)
                found.append((offset, Link(html.unescape(value).strip(), lines.line(offset), "html")))
            break
        taken.append(match.span())

    if bare_urls:
        rest = _mask(masked, taken)
        for match in _BARE_URL.finditer(rest):
            url = _trim_bare_url(match.group(0))
            found.append((match.start(), Link(url, lines.line(match.start()), "url")))

    found.sort(key=lambda item: item[0])
    return [link for _, link in found]


def _heading_lines(text: str) -> List[str]:
    for_headings, _ = _prepare(text)
    return for_headings.split("\n")


def extract_headings(text: str) -> List[Tuple[int, int, str]]:
    """Lista os títulos (ATX e Setext) como tuplas (linha, nível, conteúdo bruto)."""
    headings: List[Tuple[int, int, str]] = []
    paragraph: List[str] = []
    paragraph_line = 0
    for number, original in enumerate(_heading_lines(text), start=1):
        line = _QUOTE_PREFIX.sub("", original)
        if not line.strip():
            paragraph = []
            continue
        atx = _ATX.match(line)
        if atx:
            content = _ATX_CLOSING.sub("", atx.group(2) or "").strip()
            headings.append((number, len(atx.group(1)), content))
            paragraph = []
            continue
        setext = _SETEXT.match(line)
        if setext and paragraph:
            level = 1 if setext.group(1)[0] == "=" else 2
            headings.append((paragraph_line, level, "\n".join(p.strip() for p in paragraph)))
            paragraph = []
            continue
        if _NOT_PARAGRAPH.match(line):
            paragraph = []
            continue
        if not paragraph:
            if line.startswith("    ") or line.startswith("\t"):
                continue
            paragraph_line = number
        paragraph.append(line)
    return headings


def extract_anchors(text: str) -> Set[str]:
    """Conjunto de âncoras válidas: títulos (regras do GitHub) e <a id/name>."""
    slugger = Slugger()
    anchors = {slugger.slug(heading_text(raw)) for _, _, raw in extract_headings(text)}
    _, masked = _prepare(text)
    for match in _HTML_TAG.finditer(masked):
        if match.group(1).lower() != "a":
            continue
        for attribute in _ATTRIBUTE.finditer(match.group(2) or ""):
            if attribute.group(1).lower() in ("id", "name"):
                value, _ = _first_value(attribute)
                if value:
                    anchors.add(html.unescape(value))
    return anchors
