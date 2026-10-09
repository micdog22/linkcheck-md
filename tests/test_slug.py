import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkcheck_md.slug import Slugger, heading_text, slugify  # noqa: E402


def anchor(raw: str) -> str:
    return slugify(heading_text(raw))


class SlugifyTest(unittest.TestCase):
    def test_keeps_accented_letters(self):
        self.assertEqual(anchor("Instalação rápida"), "instalação-rápida")
        self.assertEqual(anchor("Configuração Avançada"), "configuração-avançada")
        self.assertEqual(anchor("ÁGUA E ÓLEO"), "água-e-óleo")

    def test_removes_punctuation(self):
        self.assertEqual(anchor("O que é? (FAQ)"), "o-que-é-faq")
        self.assertEqual(anchor("C++ & C#"), "c--c")
        self.assertEqual(anchor("Versão 2.0: novidades!"), "versão-20-novidades")
        self.assertEqual(anchor("Preço: R$ 10,00"), "preço-r-1000")

    def test_keeps_hyphen_and_underscore(self):
        self.assertEqual(anchor("snake_case e kebab-case"), "snake_case-e-kebab-case")

    def test_emoji_is_removed_but_space_remains(self):
        self.assertEqual(anchor("🚀 Começando"), "-começando")
        self.assertEqual(anchor("Fim 🎉"), "fim-")

    def test_dashes_other_than_hyphen_are_removed(self):
        self.assertEqual(anchor("Antes \u2014 depois"), "antes--depois")

    def test_inline_code_links_and_emphasis(self):
        self.assertEqual(anchor("Usando `npm install`"), "usando-npm-install")
        self.assertEqual(anchor("Veja o [guia](docs/guia.md)"), "veja-o-guia")
        self.assertEqual(anchor("**Muito** *importante*"), "muito-importante")
        self.assertEqual(anchor("`__init__.py`"), "__init__py")

    def test_underscore_emphasis_follows_markdown_rules(self):
        self.assertEqual(anchor("__init__.py"), "initpy")
        self.assertEqual(anchor("nome_da_variavel"), "nome_da_variavel")
        self.assertEqual(anchor(r"\_privado\_"), "_privado_")

    def test_images_and_html_do_not_add_text(self):
        self.assertEqual(anchor("Projeto ![selo](selo.svg)"), "projeto-")
        self.assertEqual(anchor("Título <small>extra</small>"), "título-extra")


class SluggerTest(unittest.TestCase):
    def test_duplicates_get_numeric_suffix(self):
        slugger = Slugger()
        self.assertEqual([slugger.slug("Uso") for _ in range(3)], ["uso", "uso-1", "uso-2"])

    def test_suffix_collision_like_github_slugger(self):
        slugger = Slugger()
        result = [slugger.slug(text) for text in ("foo", "foo", "foo-1")]
        self.assertEqual(result, ["foo", "foo-1", "foo-1-1"])

    def test_duplicates_after_normalization(self):
        slugger = Slugger()
        self.assertEqual(slugger.slug("Instalação"), "instalação")
        self.assertEqual(slugger.slug("INSTALAÇÃO!"), "instalação-1")


if __name__ == "__main__":
    unittest.main()
