"""Verificação de links locais (arquivos e âncoras) e remotos (HTTP/HTTPS)."""
from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Pattern, Sequence, Set, Tuple

from . import __version__
from .extract import extract_anchors, extract_links

OK = "ok"
BROKEN = "quebrado"
WARNING = "aviso"
IGNORED = "ignorado"

MARKDOWN_SUFFIXES = (".md", ".markdown")
SKIPPED_DIRS = ("node_modules", ".git", "vendor")
DEFAULT_USER_AGENT = f"linkcheck-md/{__version__} (+https://github.com/micdog22/linkcheck-md)"
REDIRECT_CODES = (301, 302, 303, 307, 308)
PERMANENT_CODES = (301, 308)
MAX_RETRY_AFTER = 10.0

HTTP_REASONS = {
    400: "requisição inválida",
    401: "exige autenticação",
    403: "acesso negado",
    404: "não encontrado",
    410: "removido",
    429: "requisições demais",
    500: "erro interno do servidor",
    502: "gateway inválido",
    503: "serviço indisponível",
    504: "tempo esgotado no gateway",
}

_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


@dataclass
class UrlStatus:
    """Resultado da verificação de uma URL."""

    status: str
    reason: str = ""
    code: Optional[int] = None
    new_url: Optional[str] = None


@dataclass
class Result:
    """Resultado de um link em um arquivo."""

    file: Path
    line: int
    target: str
    kind: str
    status: str
    reason: str = ""
    code: Optional[int] = None
    new_url: Optional[str] = None


@dataclass
class Options:
    bare_urls: bool = False
    local_only: bool = False
    ignore: Sequence[Pattern[str]] = ()
    workers: int = 8
    timeout: float = 10.0
    retries: int = 2
    backoff: float = 1.0
    user_agent: str = DEFAULT_USER_AGENT
    root: Optional[Path] = None


@dataclass
class Report:
    files: List[Path]
    results: List[Result] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def broken(self) -> int:
        return self.count(BROKEN)

    @property
    def checked(self) -> int:
        return sum(1 for result in self.results if result.status != IGNORED)


def find_markdown_files(paths: Iterable[str], exclude: Sequence[str] = ()) -> List[Path]:
    """Expande arquivos e pastas (recursivamente) em uma lista de arquivos Markdown."""
    skipped = set(SKIPPED_DIRS) | set(exclude)
    files: List[Path] = []
    seen: Set[Path] = set()

    def add(path: Path) -> None:
        absolute = Path(os.path.abspath(path))
        if absolute not in seen:
            seen.add(absolute)
            files.append(absolute)

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d not in skipped)
                for name in sorted(filenames):
                    if name.lower().endswith(MARKDOWN_SUFFIXES):
                        add(Path(dirpath) / name)
        elif path.is_file():
            add(path)
        else:
            raise FileNotFoundError(str(raw))
    return files


def display_path(path: Path) -> str:
    """Caminho relativo à pasta atual, quando possível."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _TooManyRedirects(Exception):
    pass


def _to_ascii_url(url: str) -> str:
    """Converte domínios e caminhos com acentos para uma URL só com ASCII."""
    parts = urllib.parse.urlsplit(url)
    netloc = parts.netloc
    if not netloc.isascii():
        userinfo, at, hostport = netloc.rpartition("@")
        host, colon, port = hostport.partition(":")
        netloc = userinfo + at + host.encode("idna").decode("ascii") + colon + port
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~")
    query = urllib.parse.quote(parts.query, safe="/%:@!$&'()*+,;=~?")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


def _describe_error(error: BaseException, timeout: float) -> str:
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        error = error.reason
    if isinstance(error, (socket.timeout, TimeoutError)):
        return f"tempo esgotado ({timeout:g} s)"
    if isinstance(error, ssl.SSLError):
        return "erro de certificado/SSL"
    if isinstance(error, socket.gaierror):
        return "domínio não encontrado"
    if isinstance(error, ConnectionRefusedError):
        return "conexão recusada"
    if isinstance(error, (ConnectionResetError, http.client.RemoteDisconnected)):
        return "conexão encerrada pelo servidor"
    if isinstance(error, (ValueError, UnicodeError, http.client.InvalidURL)):
        return "URL inválida"
    return f"falha de conexão ({error})"


def _http_reason(code: int) -> str:
    text = HTTP_REASONS.get(code)
    return f"HTTP {code} ({text})" if text else f"HTTP {code}"


class HttpChecker:
    """Verifica URLs com HEAD (e GET quando o servidor não aceita HEAD).

    Segue redirecionamentos, tenta de novo com espera crescente em 429/5xx e
    guarda o resultado de cada URL para não repetir requisições.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        retries: int = 2,
        backoff: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_redirects: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.user_agent = user_agent
        self.max_redirects = max_redirects
        self._sleep = sleep
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._cache: Dict[str, UrlStatus] = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> UrlStatus:
        key = urllib.parse.urldefrag(url)[0]
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        status = self._check(key)
        with self._lock:
            self._cache[key] = status
        return status

    def _request(self, url: str, method: str):
        request = urllib.request.Request(
            _to_ascii_url(url),
            method=method,
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, response.headers
        except urllib.error.HTTPError as error:
            headers = error.headers
            error.close()
            return error.code, headers

    def _follow(self, url: str, method: str):
        hops: List[Tuple[int, str]] = []
        current = url
        for _ in range(self.max_redirects + 1):
            code, headers = self._request(current, method)
            location = headers.get("Location") if headers is not None else None
            if code in REDIRECT_CODES and location:
                current = urllib.parse.urljoin(current, location.strip())
                hops.append((code, current))
                continue
            return code, headers, hops
        raise _TooManyRedirects()

    def _retry_delay(self, headers, attempt: int) -> float:
        value = headers.get("Retry-After") if headers is not None else None
        if value and value.strip().isdigit():
            return min(float(value.strip()), MAX_RETRY_AFTER)
        return self.backoff * (2 ** attempt)

    def _check(self, url: str) -> UrlStatus:
        method = "HEAD"
        attempt = 0
        while True:
            try:
                code, headers, hops = self._follow(url, method)
            except _TooManyRedirects:
                return UrlStatus(BROKEN, "redirecionamentos demais")
            except Exception as error:  # rede, DNS, SSL, tempo esgotado, URL inválida
                return UrlStatus(BROKEN, _describe_error(error, self.timeout))
            if method == "HEAD" and code in (403, 405, 501):
                method = "GET"
                continue
            if (code == 429 or 500 <= code <= 599) and attempt < self.retries:
                self._sleep(self._retry_delay(headers, attempt))
                attempt += 1
                continue
            break
        if code < 400:
            new_url = None
            first_code = None
            for hop_code, location in hops:
                if hop_code not in PERMANENT_CODES:
                    break
                first_code = first_code or hop_code
                new_url = location
            if new_url:
                reason = f"redirecionamento permanente ({first_code}) para {new_url}"
                return UrlStatus(WARNING, reason, first_code, new_url)
            return UrlStatus(OK, code=code)
        if code == 429:
            return UrlStatus(WARNING, "o servidor limitou as requisições (HTTP 429); confira mais tarde", 429)
        return UrlStatus(BROKEN, _http_reason(code), code)


class LocalChecker:
    """Verifica arquivos relativos e âncoras (#secao) em arquivos Markdown."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._anchors: Dict[Path, Set[str]] = {}
        self._listings: Dict[Path, Optional[Set[str]]] = {}

    def anchors(self, path: Path) -> Set[str]:
        if path not in self._anchors:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                text = ""
            self._anchors[path] = extract_anchors(text)
        return self._anchors[path]

    def _listing(self, directory: Path) -> Optional[Set[str]]:
        if directory not in self._listings:
            try:
                self._listings[directory] = set(os.listdir(directory))
            except OSError:
                self._listings[directory] = None
        return self._listings[directory]

    def _case_mismatch(self, base: Path, relative: str) -> Optional[str]:
        """Em sistemas que ignoram maiúsculas, detecta grafias que quebrariam no GitHub."""
        current = base
        real_parts: List[str] = []
        mismatch = False
        for part in Path(relative).parts:
            if part in ("", "."):
                continue
            if part == "..":
                current = current.parent
                real_parts.append(part)
                continue
            names = self._listing(current)
            if names is None:
                return None
            if part not in names:
                match = next((name for name in sorted(names) if name.lower() == part.lower()), None)
                if match is None:
                    return None
                part = match
                mismatch = True
            real_parts.append(part)
            current = current / part
        return "/".join(real_parts) if mismatch else None

    def check(self, source: Path, target: str) -> Tuple[str, str]:
        path_part, _, fragment = target.partition("#")
        path_part = path_part.split("?", 1)[0]
        if path_part:
            decoded = urllib.parse.unquote(path_part)
            if decoded.startswith("/"):
                base, relative = self.root, decoded.lstrip("/")
            else:
                base, relative = source.parent, decoded
            target_path = Path(os.path.normpath(os.path.join(str(base), relative)))
            if not target_path.exists():
                return BROKEN, "arquivo não existe"
            real = self._case_mismatch(base, relative)
            if real:
                return BROKEN, f"maiúsculas/minúsculas diferentes do arquivo real ({real})"
        else:
            target_path = source
        if not fragment or not target_path.is_file():
            return OK, ""
        if target_path.suffix.lower() not in MARKDOWN_SUFFIXES:
            return OK, ""
        anchor = urllib.parse.unquote(fragment)
        anchors = self.anchors(target_path)
        if anchor in anchors or anchor.lower() == "top":
            return OK, ""
        similar = next((a for a in sorted(anchors) if a.lower() == anchor.lower()), None)
        if similar:
            return WARNING, f"âncora #{anchor} só existe como #{similar} (maiúsculas/minúsculas)"
        where = "neste arquivo" if target_path == source else f"em {display_path(target_path)}"
        return BROKEN, f"âncora #{anchor} não encontrada {where}"


def check_files(
    files: Sequence[Path],
    options: Optional[Options] = None,
    http_checker: Optional[HttpChecker] = None,
) -> Report:
    """Extrai e verifica todos os links dos arquivos informados."""
    options = options or Options()
    local = LocalChecker(Path(os.path.abspath(options.root or os.getcwd())))
    http = http_checker or HttpChecker(
        timeout=options.timeout,
        retries=options.retries,
        backoff=options.backoff,
        user_agent=options.user_agent,
    )
    report = Report(files=list(files))
    pending: List[Tuple[Result, str]] = []

    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as error:
            report.results.append(
                Result(path, 0, "", "arquivo", WARNING, f"não foi possível ler o arquivo ({error.strerror})")
            )
            continue
        for link in extract_links(text, bare_urls=options.bare_urls):
            if link.kind == "referência":
                report.results.append(
                    Result(path, link.line, f"[{link.label}]", "referência", BROKEN, "referência não definida")
                )
                continue
            target = link.target
            if not target or target == "#":
                continue
            if any(pattern.search(target) for pattern in options.ignore):
                report.results.append(Result(path, link.line, target, "ignorado", IGNORED, "--ignorar"))
                continue
            if target.startswith("//"):
                target = "https:" + target
            scheme_match = _SCHEME.match(target)
            scheme = scheme_match.group(1).lower() if scheme_match else ""
            if scheme in ("http", "https"):
                if options.local_only:
                    report.results.append(Result(path, link.line, target, "http", IGNORED, "--somente-locais"))
                    continue
                result = Result(path, link.line, target, "http", OK)
                report.results.append(result)
                pending.append((result, urllib.parse.urldefrag(target)[0]))
            elif scheme:
                report.results.append(Result(path, link.line, target, scheme, IGNORED, f"esquema {scheme}:"))
            else:
                status, reason = local.check(path, target)
                report.results.append(Result(path, link.line, target, "local", status, reason))

    unique = sorted({url for _, url in pending})
    if unique:
        with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
            statuses = dict(zip(unique, pool.map(http.check, unique)))
        for result, url in pending:
            status = statuses[url]
            result.status = status.status
            result.reason = status.reason
            result.code = status.code
            result.new_url = status.new_url
    return report
