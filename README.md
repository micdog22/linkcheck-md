# linkcheck-md — encontre links quebrados na sua documentação Markdown (Python)

Documentação envelhece: páginas saem do ar, arquivos mudam de nome, títulos são reescritos e aquela âncora `#instalação` deixa de existir. O **linkcheck-md** lê seus arquivos Markdown, encontra todos os links e confere cada um: URLs na internet, arquivos do próprio repositório e âncoras de seções, calculadas com as mesmas regras do GitHub (acentos incluídos).

Serve para quem mantém README, wiki ou pasta `docs/` e quer pegar link quebrado antes do leitor. Roda no terminal ou no CI e não tem dependências: só Python 3.9+.

## Recursos

- Encontra links em todas as sintaxes comuns: `[texto](url "título")`, imagens `![alt](src)`, referências `[texto][ref]` com `[ref]: url`, autolinks `<https://...>`, HTML `<a href>`/`<img src>` dentro do Markdown e, com `--urls-soltas`, URLs soltas no texto.
- Ignora blocos de código (```` ``` ```` e `~~~`), trechos em `código`, comentários HTML e front matter.
- **URLs:** HEAD com GET de reserva quando o servidor responde 403, 405 ou 501; segue redirecionamentos e avisa sobre os permanentes (301/308) mostrando a nova URL; tempo limite; novas tentativas com espera crescente em 429 e 5xx; requisições em paralelo e cache por URL.
- **Arquivos locais:** confere se o arquivo existe, inclusive a grafia exata de maiúsculas e minúsculas (no macOS e no Windows `Guia.md` e `guia.md` são o mesmo arquivo; no GitHub, não).
- **Âncoras:** `#secao` é conferida nos títulos do arquivo de destino com as regras do GitHub (`Instalação rápida` vira `#instalação-rápida`, títulos repetidos ganham `-1`, `-2`) e também em `<a id>`/`<a name>`.
- Aponta referências usadas e nunca definidas (`[texto][ref]` sem `[ref]: ...`).
- Saída agrupada por arquivo, com linha e motivo; resumo final; `--json`; código de saída 1 quando há link quebrado.

## Instalação

```bash
pipx install git+https://github.com/micdog22/linkcheck-md
# ou
pip install git+https://github.com/micdog22/linkcheck-md
```

## Como usar

```bash
linkcheck-md                          # todos os .md e .markdown da pasta atual (recursivo)
linkcheck-md README.md docs/          # arquivos e pastas específicos
linkcheck-md --somente-locais         # sem internet: só arquivos e âncoras
linkcheck-md --ignorar 'localhost' --ignorar 'linkedin\.com'
linkcheck-md --json > relatorio.json
```

Exemplo de saída:

```
README.md
  12: ERRO   https://example.com/pagina-antiga — HTTP 404 (não encontrado)
  30: ERRO   docs/instalacao.md — arquivo não existe
  41: AVISO  http://example.org — redirecionamento permanente (301) para https://example.org/

docs/guia.md
   7: ERRO   #configuracao — âncora #configuracao não encontrada neste arquivo

Resumo: 4 arquivos, 57 links verificados, 3 quebrados, 1 aviso, 2 ignorados.
```

Nas pastas, `node_modules`, `.git` e `vendor` são puladas. Avisos (redirecionamento permanente, âncora que só difere em maiúsculas/minúsculas, servidor que limitou as requisições) aparecem na saída, mas não mudam o código de saída.

### Opções

| Opção | O que faz |
| --- | --- |
| `--somente-locais` | Não acessa a rede; verifica só arquivos e âncoras. |
| `--urls-soltas` | Verifica também URLs soltas no texto, fora de links. |
| `--ignorar REGEX` | Ignora links que casam com a expressão regular (pode repetir). |
| `--excluir PASTA` | Pula outras pastas pelo nome, além das padrão (pode repetir). |
| `--paralelo N` | Requisições simultâneas (padrão: 8). |
| `--tempo-limite SEG` | Tempo máximo de cada requisição (padrão: 10 s). |
| `--tentativas N` | Novas tentativas em 429 e 5xx (padrão: 2). |
| `--user-agent TEXTO` | User-Agent enviado nas requisições. |
| `--raiz PASTA` | Base dos links que começam com `/` (padrão: pasta atual). |
| `--json` | Saída em JSON, para usar em outras ferramentas. |

Códigos de saída: `0` nenhum link quebrado, `1` há links quebrados, `2` erro de uso (caminho inexistente, opção inválida).

### No GitHub Actions

```yaml
name: Links
on: [push, pull_request]
jobs:
  links:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install git+https://github.com/micdog22/linkcheck-md
      - run: linkcheck-md
```

Alguns sites bloqueiam acessos automatizados e respondem 403 mesmo com a página no ar. Nesses casos, use `--ignorar`.

## Como rodar localmente

```bash
git clone https://github.com/micdog22/linkcheck-md
cd linkcheck-md
PYTHONPATH=src python3 -m linkcheck_md --help
```

## Testes

```bash
python3 -m unittest discover -s tests -v
```

Os testes de HTTP sobem um servidor local com `http.server` (respostas 200, 404, 301, 405 no HEAD, endpoint lento etc.). Nenhum teste acessa a internet.

## Como funciona

- **Âncoras como no GitHub:** o texto do título é reduzido ao que aparece na página (links viram só o texto, `código` mantém o conteúdo, ênfase e imagens somem), passa para minúsculas, perde tudo o que não é letra, número, espaço, `-` ou `_` (pontuação e emoji saem, acentos ficam) e os espaços viram `-`. Exemplos: `O que é? (FAQ)` → `#o-que-é-faq`, `C++ & C#` → `#c--c`, `🚀 Começando` → `#-começando`.
- **Links relativos** partem da pasta do arquivo Markdown; links que começam com `/` partem da raiz do repositório (`--raiz`), como no GitHub. Partes `?consulta` são ignoradas e `%20`/`%C3%A7` são decodificados.
- `#top` sempre vale (o navegador leva ao topo da página).
- `mailto:`, `tel:` e outros esquemas que não são http/https não são verificados.
- **Rede:** a ferramenta só acessa a internet para conferir as URLs http/https dos seus arquivos. Com `--somente-locais`, nada sai da sua máquina.

## Contribuindo

Issues e pull requests são bem-vindos.

## Licença

MIT — veja [LICENSE](LICENSE).
