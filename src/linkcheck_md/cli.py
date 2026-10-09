"""Interface de linha de comando do linkcheck-md."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import __version__
from .checker import (
    BROKEN,
    DEFAULT_USER_AGENT,
    IGNORED,
    WARNING,
    Options,
    Report,
    Result,
    check_files,
    display_path,
    find_markdown_files,
)

_TRANSLATIONS = (
    ("the following arguments are required", "os seguintes argumentos são obrigatórios"),
    ("unrecognized arguments", "argumentos não reconhecidos"),
    ("expected one argument", "esperava um valor"),
    ("invalid choice", "opção inválida"),
    ("choose from", "escolha entre"),
    ("ambiguous option", "opção ambígua"),
    ("could match", "pode ser"),
    ("argument ", "argumento "),
)


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, "uso: " if prefix is None else prefix)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        for english, portuguese in _TRANSLATIONS:
            message = message.replace(english, portuguese)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"número inteiro inválido: {value!r}")
    if number < 1:
        raise argparse.ArgumentTypeError("use um número maior ou igual a 1")
    return number


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"número inteiro inválido: {value!r}")
    if number < 0:
        raise argparse.ArgumentTypeError("use um número maior ou igual a 0")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError(f"número inválido: {value!r}")
    if not number > 0:
        raise argparse.ArgumentTypeError("use um número maior que 0")
    return number


def _regex(value: str) -> "re.Pattern[str]":
    try:
        return re.compile(value)
    except re.error as error:
        raise argparse.ArgumentTypeError(f"expressão regular inválida {value!r}: {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="linkcheck-md",
        description="Encontra links quebrados em arquivos Markdown: URLs, arquivos locais e âncoras (#secao).",
        epilog=(
            "exemplos:\n"
            "  linkcheck-md                      verifica todos os .md da pasta atual\n"
            "  linkcheck-md README.md docs/      arquivos e pastas específicos\n"
            "  linkcheck-md --somente-locais     sem acessar a internet\n"
            "  linkcheck-md --ignorar localhost --json\n\n"
            "códigos de saída: 0 = nenhum link quebrado, 1 = há links quebrados, 2 = erro de uso"
        ),
        formatter_class=_Formatter,
        add_help=False,
    )
    parser._positionals.title = "argumentos"
    parser._optionals.title = "opções"
    parser.add_argument(
        "caminhos", nargs="*", default=["."], metavar="CAMINHO",
        help="arquivos ou pastas a verificar (padrão: pasta atual, recursivamente)",
    )
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e sai")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}", help="mostra a versão e sai")
    parser.add_argument("--somente-locais", action="store_true", help="verifica só arquivos e âncoras, sem acessar a rede")
    parser.add_argument("--urls-soltas", action="store_true", help="verifica também URLs soltas no texto (fora de links)")
    parser.add_argument(
        "--ignorar", action="append", default=[], type=_regex, metavar="REGEX",
        help="ignora links que casam com a expressão regular (pode repetir)",
    )
    parser.add_argument(
        "--excluir", action="append", default=[], metavar="PASTA",
        help="nome de pasta a pular, além de node_modules, .git e vendor (pode repetir)",
    )
    parser.add_argument("--paralelo", type=_positive_int, default=8, metavar="N", help="requisições simultâneas (padrão: 8)")
    parser.add_argument(
        "--tempo-limite", type=_positive_float, default=10.0, metavar="SEG",
        help="tempo máximo de cada requisição, em segundos (padrão: 10)",
    )
    parser.add_argument(
        "--tentativas", type=_non_negative_int, default=2, metavar="N",
        help="novas tentativas em respostas 429 e 5xx (padrão: 2)",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, metavar="TEXTO", help="User-Agent das requisições")
    parser.add_argument(
        "--raiz", type=Path, default=None, metavar="PASTA",
        help="pasta usada para links que começam com / (padrão: pasta atual)",
    )
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    return parser


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _summary(report: Report) -> str:
    parts = [
        _plural(len(report.files), "arquivo", "arquivos"),
        _plural(report.checked, "link verificado", "links verificados"),
        _plural(report.broken, "quebrado", "quebrados"),
        _plural(report.count(WARNING), "aviso", "avisos"),
    ]
    ignored = report.count(IGNORED)
    if ignored:
        parts.append(_plural(ignored, "ignorado", "ignorados"))
    return "Resumo: " + ", ".join(parts) + "."


def _problems(report: Report) -> List[Result]:
    return [r for r in report.results if r.status in (BROKEN, WARNING)]


def format_text(report: Report, color: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    lines: List[str] = []
    grouped: Dict[Path, List[Result]] = {}
    for result in _problems(report):
        grouped.setdefault(result.file, []).append(result)
    for path in report.files:
        results = grouped.get(path)
        if not results:
            continue
        lines.append(paint(display_path(path), "1"))
        width = len(str(max(r.line for r in results)))
        for result in sorted(results, key=lambda r: r.line):
            label = paint("ERRO ", "31;1") if result.status == BROKEN else paint("AVISO", "33;1")
            lines.append(f"  {str(result.line).rjust(width)}: {label}  {result.target} — {result.reason}")
        lines.append("")
    if not lines:
        lines.append(paint("Nenhum link quebrado.", "32;1"))
    lines.append(_summary(report))
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    return {
        "versao": __version__,
        "arquivos": len(report.files),
        "verificados": report.checked,
        "quebrados": report.broken,
        "avisos": report.count(WARNING),
        "ignorados": report.count(IGNORED),
        "problemas": [
            {
                "arquivo": display_path(r.file),
                "linha": r.line,
                "link": r.target,
                "tipo": r.kind,
                "situacao": r.status,
                "motivo": r.reason,
                "codigo_http": r.code,
                "nova_url": r.new_url,
            }
            for r in sorted(_problems(report), key=lambda r: (report.files.index(r.file), r.line))
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        files = find_markdown_files(args.caminhos, args.excluir)
    except FileNotFoundError as error:
        print(f"linkcheck-md: caminho não encontrado: {error}", file=sys.stderr)
        return 2
    if args.raiz is not None and not args.raiz.is_dir():
        print(f"linkcheck-md: a pasta de --raiz não existe: {args.raiz}", file=sys.stderr)
        return 2
    options = Options(
        bare_urls=args.urls_soltas,
        local_only=args.somente_locais,
        ignore=args.ignorar,
        workers=args.paralelo,
        timeout=args.tempo_limite,
        retries=args.tentativas,
        user_agent=args.user_agent,
        root=args.raiz,
    )
    report = check_files(files, options)
    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        if not files:
            print("Nenhum arquivo Markdown encontrado.")
            return 0
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        print(format_text(report, color))
    return 1 if report.broken else 0
