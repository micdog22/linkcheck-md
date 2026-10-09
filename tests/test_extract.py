import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkcheck_md.extract import extract_anchors, extract_headings, extract_links  # noqa: E402


def targets(text: str, **kwargs):
    return [(link.target, link.line, link.kind) for link in extract_links(textwrap.dedent(text), **kwargs)]


class InlineLinksTest(unittest.TestCase):
    def test_link_with_title_and_image(self):
        found = targets("""\
            Veja o [guia](docs/guia.md "Guia completo").
            ![Logo](img/logo.png)
            """)
        self.assertEqual(found, [("docs/guia.md", 1, "link"), ("img/logo.png", 2, "imagem")])

    def test_badge_inside_link(self):
        found = targets("[![CI](https://img.example.com/ci.svg)](https://ci.example.com/run)\n")
        self.assertEqual(
            found,
            [("https://img.example.com/ci.svg", 1, "imagem"), ("https://ci.example.com/run", 1, "link")],
        )

    def test_parentheses_angle_brackets_and_escapes(self):
        found = targets(r"""
            [Wiki](https://pt.wikipedia.org/wiki/Java_(linguagem_de_programação))
            [Com espaço](<docs/meu arquivo.md>)
            [Escapado](docs/a\_b.md) e [entidade](busca?a=1&amp;b=2)
            """)
        self.assertEqual(
            [t for t, _, _ in found],
            [
                "https://pt.wikipedia.org/wiki/Java_(linguagem_de_programação)",
                "docs/meu arquivo.md",
                "docs/a_b.md",
                "busca?a=1&b=2",
            ],
        )


class ReferenceLinksTest(unittest.TestCase):
    def test_definitions_and_undefined_uses(self):
        found = targets("""\
            Leia [o manual][Manual] e [este][] e [aquele][sumiu].
            Matriz arr[0][1] não é link.

            [manual]: https://example.com/manual "Manual"
            [este]: <docs/este arquivo.md>
            [^1]: Nota de rodapé não é link.
            """)
        self.assertEqual(
            found,
            [
                ("", 1, "referência"),
                ("https://example.com/manual", 4, "definição"),
                ("docs/este arquivo.md", 5, "definição"),
            ],
        )
        undefined = [link for link in extract_links("Ver [x][nada].\n") if link.kind == "referência"]
        self.assertEqual(undefined[0].label, "nada")

    def test_text_after_destination_is_not_a_definition(self):
        self.assertEqual(targets("[Nota]: isto é só texto\n"), [])


class OtherSyntaxesTest(unittest.TestCase):
    def test_autolinks_and_html(self):
        found = targets("""\
            Acesse <https://example.com/a> ou <mailto:maria@example.com>.
            <a href="pagina.html#topo">topo</a> <img alt="x" src='img/foto.png'>
            <a
              href="multi.md">quebra de linha</a>
            """)
        self.assertEqual(
            found,
            [
                ("https://example.com/a", 1, "autolink"),
                ("mailto:maria@example.com", 1, "autolink"),
                ("pagina.html#topo", 2, "html"),
                ("img/foto.png", 2, "html"),
                ("multi.md", 4, "html"),
            ],
        )

    def test_bare_urls_only_with_option(self):
        text = "Veja https://example.com/a. E (https://example.com/b_(c)), [x](https://example.com/d).\n"
        self.assertEqual(targets(text), [("https://example.com/d", 1, "link")])
        self.assertEqual(
            [t for t, _, _ in targets(text, bare_urls=True)],
            ["https://example.com/a", "https://example.com/b_(c)", "https://example.com/d"],
        )


class IgnoredRegionsTest(unittest.TestCase):
    def test_code_comments_and_front_matter_are_ignored(self):
        found = targets("""\
            ---
            site: https://front.example.com
            ---
            Texto com `[falso](nao.md)` e ``[duplo `crase`](nao2.md)``.

            ```markdown
            [cercado](cerca.md)
            ```

            ~~~~
            <https://til.example.com>
            ```
            [ainda dentro](dentro.md)
            ~~~~

            <!-- [comentado](comentado.md)
            <a href="tambem.md"> -->
            [real](real.md)
            """)
        self.assertEqual(found, [("real.md", 18, "link")])

    def test_inline_backticks_on_one_line_are_not_a_fence(self):
        self.assertEqual(targets("```js``` e [ok](ok.md)\n"), [("ok.md", 1, "link")])

    def test_unclosed_fence_hides_the_rest(self):
        self.assertEqual(targets("[antes](a.md)\n```\n[depois](b.md)\n"), [("a.md", 1, "link")])


class HeadingsTest(unittest.TestCase):
    def test_atx_setext_closing_hashes_and_quotes(self):
        text = textwrap.dedent("""\
            # Título principal #
            Subtítulo
            ---------
            ## Sem fechamento ##
            > ### Dentro de citação
            ####### Não é título
            #Também não

            - item
            ---
            Outro
            =====
            """)
        self.assertEqual(
            extract_headings(text),
            [
                (1, 1, "Título principal"),
                (2, 2, "Subtítulo"),
                (4, 2, "Sem fechamento"),
                (5, 3, "Dentro de citação"),
                (11, 1, "Outro"),
            ],
        )

    def test_headings_in_code_are_ignored(self):
        text = "```\n# Não é título\n```\n    # Código indentado\n# Real\n"
        self.assertEqual(extract_headings(text), [(5, 1, "Real")])

    def test_anchors_include_duplicates_and_explicit_ids(self):
        text = '# Uso\n## Uso\n<a id="manual"></a>\n<a name="antigo"></a>\n```\n<a id="falso"></a>\n```\n'
        self.assertEqual(extract_anchors(text), {"uso", "uso-1", "manual", "antigo"})


if __name__ == "__main__":
    unittest.main()
