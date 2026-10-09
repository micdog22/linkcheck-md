import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkcheck_md.checker import (  # noqa: E402
    BROKEN,
    IGNORED,
    OK,
    WARNING,
    LocalChecker,
    Options,
    check_files,
    find_markdown_files,
)

GUIDE = """# Guia

## Configuração

## Uso

## Uso

<a name="extra"></a>
"""


class LocalLinksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs" / "sub").mkdir(parents=True)
        (self.root / "docs" / "guia.md").write_text(GUIDE, encoding="utf-8")
        (self.root / "docs" / "imagem.png").write_bytes(b"\x89PNG")
        (self.root / "meu arquivo.md").write_text("# Arquivo com espaço\n", encoding="utf-8")
        self.readme = self.root / "README.md"
        self.readme.write_text("# Projeto\n\n## Seção única\n", encoding="utf-8")
        self.checker = LocalChecker(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, target):
        return self.checker.check(self.readme, target)

    def test_existing_and_missing_files(self):
        self.assertEqual(self.check("docs/guia.md"), (OK, ""))
        self.assertEqual(self.check("./docs/../docs/guia.md"), (OK, ""))
        self.assertEqual(self.check("docs/nada.md"), (BROKEN, "arquivo não existe"))

    def test_anchor_in_other_file(self):
        self.assertEqual(self.check("docs/guia.md#configuração")[0], OK)
        self.assertEqual(self.check("docs/guia.md#uso-1")[0], OK)
        self.assertEqual(self.check("docs/guia.md#extra")[0], OK)
        status, reason = self.check("docs/guia.md#instalacao")
        self.assertEqual(status, BROKEN)
        self.assertIn("âncora #instalacao não encontrada", reason)
        self.assertIn("guia.md", reason)

    def test_anchor_in_same_file(self):
        self.assertEqual(self.check("#seção-única")[0], OK)
        self.assertEqual(self.check("#top")[0], OK)
        status, reason = self.check("#secao-unica")
        self.assertEqual(status, BROKEN)
        self.assertIn("neste arquivo", reason)

    def test_percent_encoding(self):
        self.assertEqual(self.check("docs/guia.md#configura%C3%A7%C3%A3o")[0], OK)
        self.assertEqual(self.check("meu%20arquivo.md")[0], OK)

    def test_case_only_difference_in_anchor_is_a_warning(self):
        self.assertEqual(self.check("docs/guia.md#Configuração")[0], WARNING)

    def test_case_difference_in_file_name_is_broken(self):
        self.assertEqual(self.check("docs/Guia.md")[0], BROKEN)

    def test_root_relative_query_directory_and_non_markdown(self):
        self.assertEqual(self.check("/docs/guia.md#uso")[0], OK)
        self.assertEqual(self.check("docs/imagem.png?raw=true")[0], OK)
        self.assertEqual(self.check("docs/imagem.png#qualquer")[0], OK)
        self.assertEqual(self.check("docs/sub/")[0], OK)


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_find_markdown_files_skips_vendor_folders(self):
        self.write("README.md", "# A\n")
        self.write("docs/guia.markdown", "# B\n")
        self.write("docs/notas.txt", "nada\n")
        self.write("node_modules/pacote/README.md", "# C\n")
        self.write("vendor/lib/README.md", "# D\n")
        self.write(".git/info.md", "# E\n")
        self.write("rascunhos/x.md", "# F\n")
        names = [p.relative_to(self.root).as_posix() for p in find_markdown_files([str(self.root)], ["rascunhos"])]
        self.assertEqual(names, ["README.md", "docs/guia.markdown"])

    def test_find_markdown_files_rejects_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            find_markdown_files([str(self.root / "nao-existe")])

    def test_report_with_local_only(self):
        readme = self.write(
            "README.md",
            "# Projeto\n"
            "[ok](docs/a.md) [quebrado](docs/b.md)\n"
            "[externo](https://example.com/x) [e-mail](mailto:maria@example.com) [tel](tel:+5511999999999)\n"
            "[ignorado](rascunho.md) [indefinido][nada]\n",
        )
        self.write("docs/a.md", "# A\n")
        options = Options(local_only=True, ignore=[re.compile("rascunho")], root=self.root)
        report = check_files([readme], options)
        summary = [(r.line, r.target, r.status) for r in report.results]
        self.assertEqual(
            summary,
            [
                (2, "docs/a.md", OK),
                (2, "docs/b.md", BROKEN),
                (3, "https://example.com/x", IGNORED),
                (3, "mailto:maria@example.com", IGNORED),
                (3, "tel:+5511999999999", IGNORED),
                (4, "rascunho.md", IGNORED),
                (4, "[nada]", BROKEN),
            ],
        )
        self.assertEqual(report.broken, 2)
        self.assertEqual(report.checked, 3)

    def test_relative_to_each_file(self):
        guide = self.write("docs/guia.md", "[voltar](../README.md#projeto)\n[irmão](outro.md)\n")
        self.write("README.md", "# Projeto\n")
        report = check_files([guide], Options(local_only=True, root=self.root))
        self.assertEqual([r.status for r in report.results], [OK, BROKEN])


if __name__ == "__main__":
    unittest.main()
