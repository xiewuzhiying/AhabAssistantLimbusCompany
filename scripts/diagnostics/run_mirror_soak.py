"""Run the real AALC task thread with bounded local diagnostic recording.

Invoke from the repository root with the project Python environment. Requires a
configured, enabled mirror team queue and no unrelated Limbus session. Touch
<output>/STOP to stop after recording the current state. Artifacts are local and
may contain account identifiers; do not publish them without review.
"""

from __future__ import annotations

import argparse
import faulthandler
import functools
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path


class IncidentLog(logging.Handler):
    """Record errors separately from the task log and capture failure evidence."""

    def __init__(self, record_event, snapshot, clock=time.monotonic):
        super().__init__(level=logging.ERROR)
        self.record_event = record_event
        self.snapshot = snapshot
        self.clock = clock
        self.last_snapshot = float("-inf")

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.record_event("error_log", message=record.getMessage())
            now = self.clock()
            if now - self.last_snapshot >= 10:
                self.last_snapshot = now
                self.snapshot("error-log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=8)
    parser.add_argument("--capture-timeout", type=int, default=180)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ("ring", "frames", "incidents"):
        (out / name).mkdir()
    (out / "runner.pid").write_text(str(os.getpid()))
    events = (out / "events.jsonl").open("a", buffering=1)
    stacks = (out / "stacks.log").open("a", buffering=1)
    faulthandler.enable(file=stacks, all_threads=True)
    faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
    faulthandler.dump_traceback_later(180, repeat=True, file=stacks)
    lock = threading.RLock()
    started = time.monotonic()
    state = {
        "completed": 0,
        "remaining": 5,
        "attempts": 0,
        "frames": 0,
        "last_frame": None,
        "last_action": None,
        "stage": "initializing",
    }
    last_frame = [None]
    images = queue.Queue(maxsize=8)
    stop_recording = threading.Event()
    stop_request = threading.Event()
    task_done = threading.Event()
    release_thread = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_request.set())
    signal.signal(signal.SIGINT, lambda *_: stop_request.set())

    def event(kind, **values):
        with lock:
            events.write(
                json.dumps(
                    {"time": time.time(), "elapsed": round(time.monotonic() - started, 3), "kind": kind, **values},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

    def snapshot(reason):
        frame = last_frame[0]
        if frame is not None:
            name = f"{time.time_ns()}-{re.sub(r'[^a-zA-Z0-9_-]', '_', reason)[:50]}.png"
            try:
                images.put_nowait((out / "incidents" / name, frame.copy(), True))
            except queue.Full:
                event("snapshot_dropped", reason=reason)
        event("incident", reason=reason)
        faulthandler.dump_traceback(file=stacks, all_threads=True)

    def save_images():
        while not stop_recording.is_set() or not images.empty():
            try:
                path, frame, lossless = images.get(timeout=1)
            except queue.Empty:
                continue
            try:
                frame.save(path, **({} if lossless else {"quality": 82}))
            except Exception as exc:
                event("image_write_error", error=repr(exc))

    image_thread = threading.Thread(target=save_images, name="SoakImageWriter", daemon=True)
    image_thread.start()

    import psutil
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from module.config import cfg
    from module.logger import log

    # Separate unrotated run log: retain the complete chronology.
    log.setLevel(logging.INFO)
    file_handler = logging.FileHandler(out / "aalc.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(pathname)s:%(lineno)d %(message)s")
    )
    log.addHandler(file_handler)

    log.addHandler(IncidentLog(event, snapshot))
    from app import mediator
    from module.automation import auto
    from module.automation.screenshot import ScreenShot
    from module.game_and_screen import screen
    from module.game_and_screen.gamescope_launcher import GamescopeLauncher
    from module.my_error.my_error import userStopError
    from tasks.base import script_task_scheme as scheme
    from tasks.base.retry_monitor import retry_monitor

    cfg.normalize_and_sync_team_state(persist=False)
    if not cfg.teams_active_queue or not cfg.mirror or cfg.set_mirror_count != 5:
        raise ValueError("Expected enabled mirror queue and exactly five runs")
    if cfg.daily_task or cfg.get_reward or cfg.buy_enkephalin or cfg.infinite_dungeons:
        raise ValueError("This recorder expects only the five-run mirror task")
    for proc in psutil.process_iter(["name"]):
        if "limbuscompany" in (proc.info["name"] or "").lower():
            raise RuntimeError("An existing Limbus session must be stopped before this controlled launch")
    (out / "config.json").write_text(json.dumps(cfg.config.model_dump(), ensure_ascii=False, indent=2))
    metadata = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version,
        "argv": sys.argv,
        "teams": cfg.teams_active_queue,
        "gamescope": subprocess.run(["gamescope", "--version"], capture_output=True, text=True).stderr,
        "guards": [
            "reject host screenshot fallback",
            "reject non-Gamescope input",
            "stop after three failed dungeon attempts",
            "owned-process cleanup",
        ],
    }
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

    def progress(completed, remaining):
        state.update(completed=completed, remaining=remaining)
        event("progress", completed=completed, remaining=remaining)
        snapshot("progress")

    mediator.mirror_signal.connect(progress, Qt.ConnectionType.DirectConnection)

    original_capture = ScreenShot.take_screenshot
    last_saved = [0.0, 0.0]

    def capture(gray=True):
        if stop_request.is_set():
            raise userStopError("Soak recording stopped")
        begin = time.monotonic()
        try:
            frame = original_capture(gray)
        except Exception as exc:
            event("capture_exception", error=repr(exc))
            raise
        if frame is None:
            event("capture_none", duration=time.monotonic() - begin)
            return None
        state["frames"] += 1
        state["last_frame"] = time.monotonic()
        last_frame[0] = frame
        seq = state["frames"]
        event(
            "frame",
            sequence=seq,
            display=getattr(screen.handle, "_display_name", None),
            window=getattr(screen.handle, "_hwnd", None),
            mode=frame.mode,
            size=frame.size,
            duration=round(time.monotonic() - begin, 4),
        )
        now = time.monotonic()
        if now - last_saved[0] >= 2:
            last_saved[0] = now
            event("frame_hash", sequence=seq, sha256=hashlib.sha256(frame.tobytes()).hexdigest())
            path = out / "ring" / f"{int(now - started) // 2 % 120:03d}.jpg"
            try:
                images.put_nowait((path, frame.copy(), False))
                if now - last_saved[1] >= 30:
                    last_saved[1] = now
                    images.put_nowait((out / "frames" / f"{seq:07d}.jpg", frame.copy(), False))
            except queue.Full:
                event("image_queue_full")
        return frame

    ScreenShot.take_screenshot = staticmethod(capture)

    def blocked_fallback(*_args, **_kwargs):
        event("host_capture_blocked")
        raise RuntimeError("Diagnostic guard: isolated capture cannot fall back to host desktop")

    ScreenShot.take_screenshot_mss = staticmethod(blocked_fallback)
    ScreenShot.take_screenshot_pyautogui = staticmethod(blocked_fallback)

    original_init = auto.init_input

    def init_input():
        result = original_init()
        handler = auto.input_handler
        for name in ("mouse_click", "mouse_drag", "mouse_scroll", "key_press", "mouse_down", "mouse_up"):
            if not hasattr(handler, name):
                continue
            method = getattr(handler, name)

            @functools.wraps(method)
            def recorded(*a, _method=method, _name=name, **kw):
                if stop_request.is_set():
                    raise userStopError("Soak recording stopped")
                if not screen.handle.is_gamescope:
                    snapshot("non-isolated-input")
                    raise userStopError("Diagnostic guard: refusing host input")
                state["last_action"] = time.monotonic()
                event("input", operation=_name, args=a, kwargs=kw, display=screen.handle.display_name)
                return _method(*a, **kw)

            setattr(handler, name, recorded)
        event("input_driver", driver=type(handler).__name__)
        return result

    auto.init_input = init_input
    init_input()

    original_mirror = scheme.onetime_mir_process
    failures = [0]

    def mirror_attempt(team_setting, team_num):
        state["attempts"] += 1
        state["stage"] = f"mirror-attempt-{state['attempts']}"
        event("dungeon_start", attempt=state["attempts"], team=team_num)
        snapshot("dungeon-start")
        result = original_mirror(team_setting, team_num)
        event("dungeon_result", result=bool(result), attempt=state["attempts"])
        snapshot("dungeon-result")
        failures[0] = 0 if result else failures[0] + 1
        if failures[0] >= 3:
            raise userStopError("Three failed dungeon attempts; preserve failure evidence")
        return result

    scheme.onetime_mir_process = mirror_attempt

    class Worker(scheme.my_script_task):
        def run(self):
            try:
                super().run()
            finally:
                task_done.set()
                # Keep the worker available until cooperative shutdown completes.
                release_thread.wait(30)

    worker = Worker()
    launcher = GamescopeLauncher()
    ending = [False]

    def owned_cleanup():
        launcher.terminate()

    def finish(reason):
        if ending[0]:
            return
        ending[0] = True
        stop_request.set()
        snapshot(reason)
        retry_monitor.stop()
        exception = getattr(worker, "exception", None)
        summary = {
            **state,
            "reason": reason,
            "exception": repr(exception) if exception else None,
            "elapsed": time.monotonic() - started,
            "success": state["completed"] == 5 and exception is None,
            "gamescope_returncode": launcher.process.poll() if launcher.process else None,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        event("finish", **summary)
        owned_cleanup()
        # Preserve the game's own crash evidence before another launch rotates it.
        if launcher._compat_data:
            game_logs = launcher._compat_data / "pfx/drive_c/users/steamuser/AppData/LocalLow/ProjectMoon/LimbusCompany"
            for name in ("Player.log", "Player-prev.log"):
                source = game_logs / name
                if source.is_file():
                    try:
                        shutil.copy2(source, out / f"unity-{name}")
                    except OSError as exc:
                        event("game_log_copy_error", error=repr(exc))
        release_thread.set()
        stopped = worker.wait(3000)
        stop_recording.set()
        image_thread.join(3)
        if not stopped:
            # QThread.terminate() can strand the interpreter while a Python
            # frame owns locks. The owned game is already cleaned up; finish
            # this standalone recorder process instead of cancelling a thread.
            summary.update(success=False, worker_shutdown_timeout=True)
            (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            event("worker_shutdown_timeout")
            faulthandler.cancel_dump_traceback_later()
            events.flush()
            stacks.flush()
            os._exit(1)
        app.quit()

    def tick():
        try:
            pid = launcher.process.pid if launcher.process else None
            procs = []
            for process_id in set([os.getpid(), pid, *launcher.owned_pids()]):
                if not process_id:
                    continue
                try:
                    p = psutil.Process(process_id)
                    for item in [p]:
                        with item.oneshot():
                            procs.append(
                                {
                                    "pid": item.pid,
                                    "name": item.name(),
                                    "rss": item.memory_info().rss,
                                    "threads": item.num_threads(),
                                    "fds": item.num_fds(),
                                    "status": item.status(),
                                }
                            )
                except psutil.Error:
                    pass
            event("heartbeat", **state, processes=procs)
            (out / "status.json.tmp").write_text(
                json.dumps(
                    {**state, "elapsed": time.monotonic() - started, "pid": os.getpid()}, ensure_ascii=False, indent=2
                )
            )
            (out / "status.json.tmp").replace(out / "status.json")
            if task_done.is_set():
                finish("task-returned")
            elif stop_request.is_set() or (out / "STOP").exists():
                finish("requested-stop")
            elif launcher.process is not None and launcher.process.poll() is not None:
                finish("gamescope-exited")
            elif time.monotonic() - started > args.hours * 3600:
                finish("time-limit")
            elif state["last_frame"] and time.monotonic() - state["last_frame"] > args.capture_timeout:
                finish("capture-timeout")
        except Exception:
            event("supervisor_exception", traceback=traceback.format_exc())
            finish("supervisor-error")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(5000)
    event("start", metadata=metadata)
    worker.start()
    app.exec()
    faulthandler.cancel_dump_traceback_later()
    events.close()
    stacks.close()
    return 0 if state["completed"] == 5 else 1


if __name__ == "__main__":
    sys.exit(main())
