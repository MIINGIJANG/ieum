import ast
import tempfile
import unittest
from pathlib import Path

import run
from ksl_manager.interpolation import prepare_engine


class PortableStartTests(unittest.TestCase):
    def test_launcher_uses_project_path_and_explicit_local_host(self):
        args = run.command(8512)
        self.assertTrue(Path(args[4]).is_absolute())
        self.assertEqual(Path(args[4]).name, "app.py")
        self.assertIn("127.0.0.1", args)
        self.assertEqual(args[-1], "8512")

    def test_invalid_port_is_rejected(self):
        for port in [0, 65536, -1]:
            with self.assertRaises(ValueError):
                run.command(port)

    def test_adapter_accepts_formatted_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pipeline"
            (root / "src").mkdir(parents=True)
            (root / "run.py").write_text("pass\n", encoding="utf-8")
            source = (
                "def process(self):\n"
                "    cmd = []\n"
                '    if not self.settings["make_check_video"]:\n'
                '        cmd.append("--no-video")\n'
                "    return cmd\n"
            )
            (root / "src/pipeline.py").write_text(source, encoding="utf-8")
            job = Path(tmp) / "job"
            job.mkdir()
            engine, _ = prepare_engine(root, job, 0.4)
            tree = ast.parse((engine / "src/pipeline.py").read_text(encoding="utf-8"))
            self.assertIsInstance(tree.body[0].body[1], ast.Expr)
            self.assertIsInstance(tree.body[0].body[2], ast.If)
            self.assertEqual((root / "src/pipeline.py").read_text(encoding="utf-8"), source)
