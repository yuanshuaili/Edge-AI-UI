"""Lifecycle tests use tiny CPU-only processes, never inference libraries."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace

from test_chat_dispatch import mock_profile
from ui_backend.adapter_base import HealthStatus
from ui_backend.adapters import MockAdapter
from ui_backend.chat import BackendBusy, ChatDispatcher
from ui_backend.config import AppConfig, load_config, ConfigError


def wait_until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("condition not reached")


class LifecycleTests(unittest.TestCase):
    def test_attempt_logs_distinguish_ready_timeout_and_exit(self):
        for outcome, health, command in (
                ("ready", True, None), ("startup_timeout", False, None),
                ("process_exited", False, [sys.executable, "-c", "raise SystemExit(7)"])):
            with self.subTest(outcome=outcome):
                manager, _, processes, _ = self.make_manager(health=health, command=command)
                with self.assertLogs("ui_backend.model_manager", level="INFO") as captured:
                    manager.activate("a")
                    manager.wait(2)
                records = [r for r in captured.records if getattr(r, "outcome", None) == outcome]
                self.assertTrue(records, captured.output)
                self.assertEqual(records[-1].backend_id, "a")
                self.assertEqual(len(records[-1].attempt_id), 32)
                self.assertGreaterEqual(records[-1].elapsed_seconds, 0)
                manager.close()

    def make_manager(self, health=True, command=None, shutdown=.15, startup=.3):
        from ui_backend.config import RuntimeConfig
        from ui_backend.model_manager import ModelManager
        from ui_backend.model_launchers import LaunchSpec
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        runtime = RuntimeConfig(True, "cpu-fixture", startup, shutdown, False)
        profiles = {key: replace(mock_profile(key), runtime=runtime) for key in ("a", "b")}
        profiles["mock"] = mock_profile("mock")
        processes = []
        process_by_id = {}
        starts = []
        class CpuHealth(MockAdapter):
            def health(self):
                process = process_by_id.get(self.id)
                return HealthStatus("ready" if health and process is not None and process.poll() is None else "offline")
        adapters = {key: CpuHealth(value) if key != "mock" else MockAdapter(value) for key, value in profiles.items()}
        config = AppConfig("", "a", profiles)
        dispatcher = ChatDispatcher(config, adapters)
        def launcher(profile):
            starts.append(profile.id)
            if processes:
                self.assertIsNotNone(processes[-1].poll(), "new launch before old child exited")
            return LaunchSpec(tuple(command or [sys.executable, "-c", "import time; time.sleep(30)"]) + (profile.id,), Path(temp.name))
        def spawn(*args, **kwargs):
            process = subprocess.Popen(*args, **kwargs)
            processes.append(process)
            process_by_id[args[0][-1]] = process
            return process
        manager = ModelManager(config, adapters, dispatcher, Path(temp.name),
                               launchers={"cpu-fixture": launcher}, spawn=spawn)
        self.addCleanup(manager.close)
        return manager, dispatcher, processes, starts

    def test_mock_activation_and_offline_stop_need_no_process(self):
        manager, _, processes, _ = self.make_manager()
        manager.stop()
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "offline")
        manager.activate("mock")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "ready")
        self.assertEqual(manager.snapshot()["selected_backend_id"], "mock")
        self.assertEqual(processes, [])

    def test_duplicate_start_and_single_active_switch_order(self):
        manager, _, processes, starts = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        manager.activate("a")
        manager.wait(2)
        self.assertEqual(starts, ["a"])
        manager.activate("b")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "ready")
        self.assertEqual(starts, ["a", "b"])
        self.assertIsNotNone(processes[0].poll())
        self.assertIsNone(processes[1].poll())
        manager.activate("mock")
        manager.wait(2)
        self.assertIsNotNone(processes[1].poll())

    def test_transition_and_generation_cannot_overlap_with_reset(self):
        manager, dispatcher, _, _ = self.make_manager(health=False)
        manager.activate("a")
        for action in (lambda: dispatcher.dispatch_text_chat("mock", "no"),
                       lambda: dispatcher.clear_session("mock"), lambda: manager.activate("b")):
            with self.assertRaises(BackendBusy):
                action()
        manager.wait(2)
        dispatcher.gate.enter_operation()
        try:
            with self.assertRaises(BackendBusy):
                manager.activate("b")
        finally:
            dispatcher.gate.leave_operation()

    def test_startup_timeout_stops_owned_process_and_reports_error(self):
        manager, _, processes, _ = self.make_manager(health=False)
        manager.activate("a")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "error")
        self.assertEqual(manager.snapshot()["error_code"], "startup_timeout")
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(manager.snapshot()["owned"])

    def test_early_process_exit_never_becomes_ready(self):
        manager, _, processes, _ = self.make_manager(health=False, command=[sys.executable, "-c", "raise SystemExit(7)"])
        manager.activate("a")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["error_code"], "process_exited")
        self.assertEqual(processes[0].returncode, 7)

    def test_shutdown_timeout_uses_kill_and_reaps_child(self):
        command = [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"]
        manager, _, processes, _ = self.make_manager(command=command)
        manager.activate("a")
        manager.wait(2)
        time.sleep(.1)  # Fixture has installed its handler before stop.
        manager.stop()
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "offline")
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)

    def test_unowned_endpoint_is_never_killed_or_duplicated(self):
        manager, _, processes, _ = self.make_manager()
        manager.adapters["a"] = MockAdapter(manager.config.backends["a"])
        manager.activate("a")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "ready")
        self.assertFalse(manager.snapshot()["owned"])
        manager.stop()
        manager.wait(2)
        self.assertEqual(manager.snapshot()["error_code"], "unowned_model")
        manager.activate("b")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["error_code"], "unowned_model")
        manager.close()
        self.assertEqual(processes, [])

    def test_port_still_ready_prevents_next_launch(self):
        manager, _, processes, starts = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        manager.adapters["a"] = MockAdapter(manager.config.backends["a"])
        manager.activate("b")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["error_code"], "endpoint_in_use")
        self.assertEqual(starts, ["a"])
        self.assertIsNotNone(processes[0].poll())

    def test_retry_does_not_signal_already_departed_process_group(self):
        from unittest.mock import patch
        manager, _, processes, _ = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        manager.adapters["a"] = MockAdapter(manager.config.backends["a"])
        manager.stop()
        manager.wait(2)
        self.assertIsNotNone(processes[0].poll())
        # Endpoint is still occupied but the original group has gone. Never send
        # a second signal to that numeric ID: it may now belong to another owner.
        with patch("ui_backend.model_manager.os.killpg") as signal_group:
            manager.stop()
            manager.wait(2)
            signal_group.assert_not_called()

    def test_unknown_launcher_fails_before_spawn(self):
        manager, _, processes, _ = self.make_manager()
        from ui_backend.model_manager import LifecycleError
        with self.assertRaises(LifecycleError):
            manager.activate("unknown")
        manager.launchers.clear()
        with self.assertRaises(LifecycleError):
            manager.activate("a")
        self.assertEqual(processes, [])

    def test_normal_close_stops_only_owned_model(self):
        manager, _, processes, _ = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        manager.close()
        self.assertIsNotNone(processes[0].poll())

    def test_unexpected_exit_retires_signal_authority_before_shutdown(self):
        from unittest.mock import patch
        manager, _, processes, _ = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        processes[0].terminate()
        processes[0].wait(2)
        manager.refresh()
        with patch("ui_backend.model_manager.os.killpg") as signals:
            manager.close()
            signals.assert_not_called()

    def test_reused_group_leader_identity_is_never_signalled(self):
        from unittest.mock import patch
        manager, _, processes, _ = self.make_manager()
        manager.activate("a")
        manager.wait(2)
        processes[0].terminate()
        processes[0].wait(2)
        manager._leader_start = "original"
        with patch.object(manager, "_leader_birth", return_value="different-owner", create=True), \
             patch.object(manager, "_group_alive", return_value=True), \
             patch("ui_backend.model_manager.os.killpg") as signals:
            manager._signal(signal.SIGTERM)
            signals.assert_not_called()

    def test_manual_managed_service_can_select_external_mock_and_return(self):
        manager, dispatcher, processes, _ = self.make_manager()
        manager.adapters["a"] = MockAdapter(manager.config.backends["a"])
        for target in ("a", "mock", "a"):
            manager.activate(target)
            manager.wait(2)
            self.assertEqual(manager.snapshot()["state"], "ready")
            self.assertEqual(dispatcher.dispatch_text_chat(target, "你好").backend_id, target)
        self.assertEqual(processes, [])

    def test_indeterminate_endpoint_probe_forbids_new_model_spawn(self):
        from ui_backend.adapters import TinyChatAdapter
        from unittest.mock import patch
        import socket
        manager, _, processes, _ = self.make_manager()
        profile = replace(mock_profile("a"), adapter="tinychat", endpoint="tcp://127.0.0.1:11111")
        manager.adapters["a"] = TinyChatAdapter(profile)
        with patch("ui_backend.adapters.socket.create_connection", side_effect=socket.timeout):
            manager.activate("b")
            manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "error")
        self.assertEqual(processes, [])

    def test_saturated_live_listener_does_not_authorize_second_model(self):
        import socket
        from ui_backend.adapters import TinyChatAdapter
        manager, _, processes, _ = self.make_manager()
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)  # Deliberately do not accept: still an occupied endpoint.
        address = listener.getsockname()
        saturated = False
        for _ in range(8):
            client = socket.socket()
            self.addCleanup(client.close)
            client.settimeout(.05)
            try:
                client.connect(address)
            except socket.timeout:
                saturated = True
                break
        self.assertTrue(saturated)
        profile = replace(mock_profile("a"), adapter="tinychat", endpoint=f"tcp://127.0.0.1:{address[1]}")
        manager.adapters["a"] = TinyChatAdapter(profile, connect_timeout=.05)
        manager.activate("b")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["error_code"], "endpoint_uncertain")
        self.assertEqual(processes, [])

    def test_shutdown_waits_for_descendants_before_next_launch(self):
        # Parent and child both ignore SIGTERM; group fallback must remove both.
        command = [sys.executable, "-c", "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.fork(); time.sleep(30)"]
        manager, _, processes, starts = self.make_manager(command=command)
        manager.activate("a")
        manager.wait(2)
        time.sleep(.1)
        old_group = processes[0].pid
        manager.activate("b")
        manager.wait(2)
        self.assertEqual(starts, ["a", "b"])
        self.assertIsNotNone(processes[0].poll())
        living = []
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                if fields[0] != "Z" and int(fields[2]) == old_group:
                    living.append(path)
            except (OSError, IndexError, ValueError):
                pass
        self.assertEqual(living, [])

    def test_external_offline_does_not_spawn_and_reports_offline(self):
        manager, _, processes, _ = self.make_manager()
        manager.adapters["mock"].health = lambda: HealthStatus("offline")
        manager.activate("mock")
        manager.wait(2)
        self.assertEqual(manager.snapshot()["state"], "offline")
        self.assertEqual(manager.snapshot()["selected_backend_id"], "mock")
        self.assertEqual(processes, [])


class RuntimeTests(unittest.TestCase):
    def test_runtime_rejects_commands_module_paths_and_invalid_timeouts(self):
        source = Path(__file__).parents[1] / "config" / "backends.json"
        data = json.loads(source.read_text())
        for runtime in ({"managed": True, "launcher": "os.system"},
                        {"managed": True, "launcher": "tinychat_qwen25", "command": "anything"},
                        {"managed": False, "auto_start": True},
                        {"managed": True, "launcher": "tinychat_qwen25", "startup_timeout_seconds": True}):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as directory:
                data["backends"][0]["runtime"] = runtime
                path = Path(directory) / "backends.json"
                path.write_text(json.dumps(data))
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_nano_launcher_fixed_quantization_and_no_shell(self):
        from unittest.mock import patch
        from ui_backend.model_launchers import tinychat_qwen25
        profile = replace(mock_profile("nano-qwen25"), adapter="tinychat", runtime_model_name="Qwen2.5-3B-Instruct", endpoint="tcp://127.0.0.1:8765")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "awq_env/bin").mkdir(parents=True)
            (root / "awq_env/bin/python").touch()
            (root / "models/Qwen2.5-3B-Instruct").mkdir(parents=True)
            (root / "llm-awq/quant_cache").mkdir(parents=True)
            (root / "llm-awq/quant_cache/Qwen2.5-3B-Instruct-w4-g128-awq-v2.pt").touch()
            with patch.dict(os.environ, {"EDGE_AI_TINYCHAT_ROOT": directory}):
                spec = tinychat_qwen25(profile)
        self.assertEqual(spec.argv[spec.argv.index("--precision") + 1], "W4A16")
        self.assertEqual(spec.argv[spec.argv.index("--q_group_size") + 1], "128")
        self.assertEqual(spec.argv[spec.argv.index("--backend-id") + 1], "nano-qwen25")
        self.assertEqual(spec.cwd.name, "llm-awq")
        self.assertIn("-u", spec.argv)
