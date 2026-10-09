"""Âncoras de títulos no mesmo formato usado pelo GitHub."""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Dict

_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_AUTOLINK = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.\-]{1,31}:[^\s<>]*)>")
_EMAIL_AUTOLINK = re.compile(r"<([^\s<>@]+@[^\s<>@]+\.[^\s<>@]+)>")
_IMAGE = re.compile(r"!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])")
_LINK = re.compile(r"\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])")
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_EMPHASIS = (
    re.compile(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1"),
    re.compile(r"(?<![\w\\])(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)"),
    re.compile(r"(~~)(?=\S)(.+?)(?<=\S)\1"),
)
_PRIVATE_BASE = 0xE000


def slugify(text: str) -> str:
    """Converte o texto de um título na âncora do GitHub (sem tratar repetições).

    Regras: tudo em minúsculas; mantém letras (inclusive acentuadas), números,
    marcas combinantes, "_" e "-"; remove pontuação, símbolos e emoji; troca
    cada espaço por "-".
    """
    kept = []
    for char in text.lower():
        if char == " ":
            kept.append("-")
            continue
        category = unicodedata.category(char)
        if char == "-" or category[0] in "LMN" or category == "Pc":
            kept.append(char)
    return "".join(kept)


class Slugger:
    """Gera âncoras únicas: títulos repetidos recebem -1, -2, ..."""

    def __init__(self) -> None:
        self._occurrences: Dict[str, int] = {}

    def slug(self, text: str) -> str:
        base = slugify(text)
        result = base
        while result in self._occurrences:
            self._occurrences[base] += 1
            result = f"{base}-{self._occurrences[base]}"
        self._occurrences[result] = 0
        return result


def heading_text(raw: str) -> str:
    """Reduz o conteúdo Markdown de um título ao texto que aparece na página."""
    protected = _ESCAPED.sub(lambda m: chr(_PRIVATE_BASE + ord(m.group(1))), raw)
    parts = []
    position = 0
    for match in _CODE_SPAN.finditer(protected):
        parts.append(_inline_to_text(protected[position:match.start()]))
        code = match.group(2).replace("\n", " ")
        if len(code) >= 2 and code[0] == " " and code[-1] == " " and code.strip(" "):
            code = code[1:-1]
        parts.append(_restore(code, keep_backslash=True))
        position = match.end()
    parts.append(_inline_to_text(protected[position:]))
    return "".join(parts)


def _inline_to_text(text: str) -> str:
    text = _COMMENT.sub("", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _EMAIL_AUTOLINK.sub(r"\1", text)
    text = _IMAGE.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _LINK.sub(r"\1", text)
    text = _TAG.sub("", text)
    previous = None
    while previous != text:
        previous = text
        for pattern in _EMPHASIS:
            text = pattern.sub(r"\2", text)
    return _restore(html.unescape(text))


def _restore(text: str, keep_backslash: bool = False) -> str:
    chars = []
    for char in text:
        code = ord(char)
        if _PRIVATE_BASE <= code < _PRIVATE_BASE + 128:
            original = chr(code - _PRIVATE_BASE)
            chars.append("\\" + original if keep_backslash else original)
        else:
            chars.append(char)
    return "".join(chars)
