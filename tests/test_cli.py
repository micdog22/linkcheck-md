import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from linkcheck_md import __version__  # noqa: E402
from linkcheck_md.cli import main  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guia.md").write_text("# Guia\n\n## Configuração\n", encoding="utf-8")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_all_links_ok(self):
        Path("README.md").write_text("[Guia](docs/guia.md#configuração)\n", encoding="utf-8")
        code, out, _ = self.run_cli("--somente-locais")
        self.assertEqual(code, 0)
        self.assertIn("Nenhum link quebrado.", out)
        self.assertIn("2 arquivos, 1 link verificado, 0 quebrados, 0 avisos", out)

    def test_broken_links_grouped_by_file(self):
        Path("README.md").write_text(
            "# Projeto\n\nVeja [isto](docs/nada.md).\n[Seção](#sumiu)\n[Site](https://example.com)\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("--somente-locais")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(lines[0], "README.md")
        self.assertEqual(lines[1], "  3: ERRO   docs/nada.md: arquivo não existe")
        self.assertEqual(lines[2], "  4: ERRO   #sumiu: âncora #sumiu não encontrada neste arquivo")
        self.assertIn("2 quebrados", out)
        self.assertIn("1 ignorado", out)

    def test_json_output(self):
        Path("README.md").write_text("[a](docs/nada.md)\n[b](docs/guia.md)\n", encoding="utf-8")
        code, out, _ = self.run_cli("--somente-locais", "--json", "README.md")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data["arquivos"], 1)
        self.assertEqual(data["verificados"], 2)
        self.assertEqual(data["quebrados"], 1)
        self.assertEqual(data["problemas"][0]["linha"], 1)
        self.assertEqual(data["problemas"][0]["motivo"], "arquivo não existe")

    def test_ignore_pattern_and_exclude_folder(self):
        Path("README.md").write_text("[a](rascunho/nada.md)\n", encoding="utf-8")
        (self.root / "rascunhos").mkdir()
        (self.root / "rascunhos" / "x.md").write_text("[b](quebrado.md)\n", encoding="utf-8")
        code, out, _ = self.run_cli("--somente-locais", "--ignorar", "^rascunho/", "--excluir", "rascunhos")
        self.assertEqual(code, 0, out)

    def test_usage_errors(self):
        code, _, err = self.run_cli("nao-existe")
        self.assertEqual(code, 2)
        self.assertIn("caminho não encontrado", err)
        code, _, err = self.run_cli("--ignorar", "(")
        self.assertEqual(code, 2)
        self.assertIn("expressão regular inválida", err)
        code, _, err = self.run_cli("--tempo-limite", "zero")
        self.assertEqual(code, 2)
        self.assertIn("erro:", err)

    def test_module_entry_point(self):
        env = dict(os.environ, PYTHONPATH=str(SRC))
        result = subprocess.run(
            [sys.executable, "-m", "linkcheck_md", "--version"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), f"linkcheck-md {__version__}")


if __name__ == "__main__":
    unittest.main()
