"""Conservative single-active managed LLM lifecycle, independent of adapters."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

from .chat import BackendBusy
from .model_launchers import MODEL_LAUNCHERS, LaunchSpec

LOG = logging.getLogger(__name__)


class LifecycleError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ModelManager:
    """One worker, one owned process group, one atomic dispatch/transition gate.

    External/manual endpoints are never adopted as owned processes. The launcher
    registry and environment are trusted server-side configuration, not HTTP input.
    """
    def __init__(self, config, adapters, dispatcher, log_dir, launchers=None, spawn=None):
        self.config, self.adapters, self.dispatcher = config, adapters, dispatcher
        self.launchers = dict(MODEL_LAUNCHERS if launchers is None else launchers)
        self.log_dir = Path(log_dir)
        self._spawn = spawn or subprocess.Popen
        self._lock = threading.RLock()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-lifecycle")
        self._future = None
        self._closing = threading.Event()
        self._listener = None
        self._selector = None
        self._process = None
        self._owned_id = None
        self._pgid = None
        self._leader_start = None
        self.selected_id = config.default_backend_id
        self.target_id = None
        self.state = "offline"
        self.error_code = None
        self.revision = 0
        self.epoch = uuid.uuid4().hex
        dispatcher.set_admission(self.can_chat)

    def set_listener(self, listener, selector=None):
        self._listener, self._selector = listener, selector

    def snapshot(self):
        with self._lock:
            return {"state": self.state, "selected_backend_id": self.selected_id,
                    "target_backend_id": self.target_id, "owned": self._owned_id is not None,
                    "error_code": self.error_code,
                    "revision": self.revision, "epoch": self.epoch,
                    "transitioning": self.dispatcher.gate.blocked}

    def _publish(self, state, error=None):
        with self._lock:
            self.state, self.error_code = state, error
            self.revision += 1
        if self._listener:
            self._listener(self.snapshot())

    def can_chat(self, backend_id):
        profile = self.config.backends[backend_id]
        if not profile.runtime.managed:
            return True  # External availability is reported by its adapter.
        with self._lock:
            return self.selected_id == backend_id and self.state == "ready"

    def refresh(self):
        """Read-only health reconciliation; no launch/kill/adoption."""
        if self.dispatcher.gate.blocked:
            return self.snapshot()
        with self._lock:
            selected, process = self.selected_id, self._process
        ready = self.adapters[selected].health().state == "ready"
        with self._lock:
            if self.dispatcher.gate.blocked or selected != self.selected_id:
                return self.snapshot()
            old = (self.state, self.error_code)
            if process is not None and process.poll() is not None:
                self.state, self.error_code = "error", "process_exited"
            elif self.state != "error":
                self.state = "ready" if ready else "offline"
            if old != (self.state, self.error_code):
                self.revision += 1
        if process is not None and process.poll() is not None:
            self._retire_departed_group()
        return self.snapshot()

    def activate(self, backend_id):
        profile = self.config.backends.get(backend_id)
        if profile is None:
            raise LifecycleError("unknown_backend", "所选模型未配置。")
        if profile.runtime.managed and profile.runtime.launcher not in self.launchers:
            raise LifecycleError("unknown_launcher", "所选模型没有可用的启动方式。")
        self.dispatcher.gate.begin_transition()
        with self._lock:
            self.target_id = backend_id
        self._publish("switching" if self._owned_id and self._owned_id != backend_id else "starting")
        self._future = self._worker.submit(self._run, backend_id)
        return self.snapshot()

    def stop(self):
        self.dispatcher.gate.begin_transition()
        self._publish("stopping")
        self._future = self._worker.submit(self._run, None)
        return self.snapshot()

    def _unowned(self, target=None):
        ready = []
        for item in self.config.backends.values():
            if not item.runtime.managed or item.id == self._owned_id:
                continue
            occupancy = self.adapters[item.id].endpoint_occupancy()
            if occupancy not in ("occupied", "absent"):
                raise LifecycleError("endpoint_uncertain", "无法确认已有模型是否停止，已取消启动。")
            if occupancy == "occupied":
                ready.append(item.id)
        if ready and (target is None or ready != [target]):
            raise LifecycleError("unowned_model", "已有独立启动的模型正在运行，请先在原终端停止它。")
        return ready

    def _run(self, backend_id):
        error = None
        final = "offline"
        try:
            if backend_id is None:
                if not self._owned_id:
                    self._unowned()
                self._stop_owned()
            else:
                profile = self.config.backends[backend_id]
                # Never spawn while ANY declared unowned local managed model is ready.
                if profile.runtime.managed:
                    self._unowned(backend_id)
                if self._owned_id and self._owned_id != backend_id:
                    self._publish("switching")
                    self._stop_owned()
                if profile.runtime.managed:
                    if self._owned_id == backend_id:
                        if self._process.poll() is not None:
                            self._stop_owned()
                        elif self.adapters[backend_id].health().state == "ready":
                            final = "ready"
                        else:
                            raise LifecycleError("failed_health", "模型连接状态异常，请停止后重试。")
                    if not self._owned_id:
                        occupancy = self.adapters[backend_id].endpoint_occupancy()
                        if occupancy == "occupied":
                            if self.adapters[backend_id].health().state != "ready":
                                raise LifecycleError("failed_health", "已有模型暂时无法响应，请稍后重试。")
                            final = "ready"  # Manual service, connection only; no ownership.
                        elif occupancy == "absent":
                            self._publish("starting")
                            self._start_owned(profile)
                            final = "ready"
                        else:
                            raise LifecycleError("endpoint_uncertain", "无法确认模型服务状态，已取消启动。")
                else:
                    final = "ready" if self.adapters[backend_id].health().state == "ready" else "offline"
                with self._lock:
                    self.selected_id = backend_id
                if self._selector:
                    self._selector(backend_id)
        except LifecycleError as exc:
            error, final = exc.code, "error"
            LOG.warning("Model lifecycle %s: %s", exc.code, exc)
        except Exception:
            error, final = "lifecycle_failed", "error"
            LOG.exception("Model lifecycle failed")
        finally:
            with self._lock:
                self.target_id = None
                self.state, self.error_code = final, error
                self.revision += 1
            self.dispatcher.gate.end_transition()
            if self._listener:
                self._listener(self.snapshot())

    def _start_owned(self, profile):
        attempt_id, started_at = uuid.uuid4().hex, time.monotonic()
        def observe(outcome):
            elapsed = time.monotonic() - started_at
            LOG.info("attempt_id=%s backend_id=%s elapsed_seconds=%.3f outcome=%s",
                     attempt_id, profile.id, elapsed, outcome,
                     extra={"attempt_id": attempt_id, "backend_id": profile.id,
                            "elapsed_seconds": elapsed, "outcome": outcome})
        observe("starting")
        spec = self.launchers[profile.runtime.launcher](profile)
        if (not isinstance(spec, LaunchSpec) or not spec.argv or
                any(not isinstance(item, str) or not item for item in spec.argv)):
            raise LifecycleError("invalid_launcher", "模型启动配置不可用。")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / f"model-{profile.id}.log").open("ab") as output:
            output.write((f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} attempt_id={attempt_id} "
                          f"backend_id={profile.id} starting ---\n").encode())
            output.flush()
            process = self._spawn(spec.argv, cwd=str(spec.cwd), shell=False, start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        with self._lock:
            self._process, self._owned_id, self._pgid = process, profile.id, process.pid
            self._leader_start = self._leader_birth()
        LOG.info("Started owned backend=%s pid=%s", profile.id, process.pid)
        deadline = time.monotonic() + profile.runtime.startup_timeout_seconds
        try:
            while True:
                if self._closing.is_set():
                    raise LifecycleError("server_closing", "界面服务正在关闭。")
                if process.poll() is not None:
                    raise LifecycleError("process_exited", "模型启动失败，请查看开发日志。")
                if self.adapters[profile.id].health().state == "ready" and process.poll() is None:
                    observe("ready")
                    return
                if time.monotonic() >= deadline:
                    raise LifecycleError("startup_timeout", "模型启动超时，请重试。")
                self._closing.wait(.05)
        except Exception as exc:
            observe(getattr(exc, "code", "startup_failed"))
            try:
                self._stop_owned()
            finally:
                observe("cleanup_complete" if self._owned_id is None else "cleanup_incomplete")
            raise

    def _group_alive(self):
        """Linux non-zombie group members. Do not confuse reaped parent with group exit."""
        if self._pgid is None:
            return False
        try:
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    fields = path.read_text().rsplit(")", 1)[1].split()
                    if fields[0] != "Z" and int(fields[2]) == self._pgid and int(fields[3]) == self._pgid:
                        return True
                except (OSError, ValueError, IndexError):
                    continue
            return False
        except OSError as exc:
            raise LifecycleError("process_check_failed", "无法确认模型退出，已取消切换。") from exc

    def _signal(self, sig):
        self._retire_departed_group()
        if self._pgid is not None:
            LOG.info("Signal owned backend=%s pgid=%s signal=%s", self._owned_id, self._pgid, sig)
            try:
                os.killpg(self._pgid, sig)
            except ProcessLookupError:
                pass

    def _leader_birth(self):
        if self._pgid is None:
            return None
        try:
            fields = (Path("/proc") / str(self._pgid) / "stat").read_text().rsplit(")", 1)[1].split()
            return fields[19]  # Linux stat starttime, not a reused numeric PID.
        except FileNotFoundError:
            return None

    def _retire_departed_group(self):
        if self._pgid is None or self._process is None or self._process.poll() is None:
            return
        birth = self._leader_birth()
        if (birth is not None and birth != self._leader_start) or not self._group_alive():
            with self._lock:
                self._pgid = None

    def _stop_owned(self):
        if self._owned_id is None:
            return
        backend_id, process = self._owned_id, self._process
        timeout = self.config.backends[backend_id].runtime.shutdown_timeout_seconds
        self._publish("stopping")
        self._signal(signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()
            if process.poll() is not None and not self._group_alive():
                break
            time.sleep(.02)
        if process.poll() is None or self._group_alive():
            LOG.warning("Graceful shutdown timed out for %s; SIGKILL fallback", backend_id)
            self._signal(signal.SIGKILL)
        try:
            process.wait(timeout=max(timeout, .5))
        except subprocess.TimeoutExpired as exc:
            raise LifecycleError("shutdown_timeout", "旧模型尚未退出，已取消切换。") from exc
        deadline = time.monotonic() + max(timeout, .5)
        while self._group_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if self._group_alive():
            raise LifecycleError("shutdown_timeout", "旧模型尚未退出，已取消切换。")
        # The numeric process group may be reused once empty. Retain only the
        # endpoint blocker, not authority to signal a departed process group.
        with self._lock:
            self._pgid = None
        # Keep ownership reference until endpoint release is confirmed; no next launch.
        deadline = time.monotonic() + timeout
        while self.adapters[backend_id].endpoint_occupancy() != "absent":
            if time.monotonic() >= deadline:
                raise LifecycleError("endpoint_in_use", "旧模型连接仍被占用，已取消切换。")
            time.sleep(.02)
        with self._lock:
            self._process, self._owned_id, self._pgid = None, None, None
        LOG.info("Owned backend exited and endpoint released: %s", backend_id)

    def wait(self, timeout=None):
        if self._future:
            self._future.result(timeout=timeout)

    def close(self):
        self._closing.set()
        self.dispatcher.gate.close()
        self._worker.shutdown(wait=True, cancel_futures=True)
        try:
            self._stop_owned()  # No unowned/external model is ever signalled here.
        except Exception:
            LOG.exception("Could not fully close owned model; manual inspection required")
