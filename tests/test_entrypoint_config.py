"""Explicit deployment paths must not fall back to the shared sample identity."""
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
import ui_server


class EntrypointConfigTests(unittest.TestCase):
    def test_explicit_paths_are_passed_to_server(self):
        with TemporaryDirectory() as directory:
            deploy = Path(directory)
            with patch.object(ui_server, "create_server") as factory, patch.object(ui_server.signal, "signal"), contextlib.redirect_stdout(io.StringIO()):
                ui_server.main(["--config", str(deploy / "config/backends.json"),
                                "--assistant-config", str(deploy / "config/assistant.json"),
                                "--model-log-dir", str(deploy / "logs"), "--port", "0"])
            self.assertEqual(factory.call_args.args[0], deploy / "config/backends.json")
            self.assertEqual(factory.call_args.kwargs["assistant_path"], deploy / "config/assistant.json")
            self.assertEqual(factory.call_args.kwargs["model_log_dir"], deploy / "logs")

    def test_defaults_remain_standalone(self):
        with patch.object(ui_server, "create_server") as factory, patch.object(ui_server.signal, "signal"), contextlib.redirect_stdout(io.StringIO()):
            ui_server.main([])
        self.assertEqual(factory.call_args.args[0], ui_server.DEFAULT_CONFIG)
        self.assertIsNone(factory.call_args.kwargs["assistant_path"])

    def test_unknown_argument_does_not_start_server(self):
        with patch.object(ui_server, "create_server") as factory, patch.object(ui_server.signal, "signal"), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ui_server.main(["--command", "arbitrary"])
        factory.assert_not_called()
