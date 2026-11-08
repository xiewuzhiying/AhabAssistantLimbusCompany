# Mirror soak recording

Run `run_mirror_soak.py` from the repository root with the project's Python environment, `PYTHONPATH=$PWD`, and `QT_QPA_PLATFORM=offscreen`. It runs the production `my_script_task` in a Qt thread, including `init_game()`, the retry monitor and `Mirror_task()`.

Configure the local `config.yaml` for exactly five normal mirror runs and an enabled team queue first. The runner validates five runs and disables neither teams nor task behavior silently. Do not run alongside another Limbus session. Example:

```bash
python scripts/diagnostics/run_mirror_soak.py --output .audit/runs/mirror-five --hours 8
```

The output directory must not exist. Save shell stdout/stderr as well. Touch `<output>/STOP` for a recorded stop, or send SIGTERM. SIGUSR1 writes all Python thread stacks immediately.

Recorded artifacts:

- `metadata.json`, `config.json`: code revision, interpreter, launch environment and effective configuration.
- `aalc.log`, `events.jsonl`: complete task logs, completion/attempt counters, input arguments, screenshot timing/identity, periodic hashes and process metrics.
- `status.json`, `summary.json`: current progress and final verdict. Only five reported completions is success; a returned task with zero completions is not success.
- `ring/`: a bounded approximately four-minute screenshot history at two-second intervals; `frames/`: archived frame every 30 seconds; `incidents/`: lossless latest frames on failures and dungeon transitions.
- `stacks.log`: all Python thread stacks every three minutes and on incidents, even if application locks stall. Error-triggered images and stack dumps are limited to once every ten seconds, while every error message is retained.
- `unity-Player.log`, `unity-Player-prev.log`: game logs copied at shutdown when available, before a later launch can overwrite crash evidence.

Frame hashes are diagnostic signals, not freeze verdicts. Recording reuses real application captures, does not capture the host desktop, and moves image encoding to a bounded queue. The guard refuses non-Gamescope input and host screenshot fallback. It stops after three consecutive failed dungeon attempts, 180 seconds without a successful capture after the first frame, or the configured time limit. Normal application retry remains enabled within these boundaries.

An observed Gamescope exit ends the run with `gamescope-exited` and records its return code. Shutdown requests a cooperative stop and waits three seconds for the worker; if it cannot finish, the standalone recorder exits with failure after owned-process cleanup and saving its summary. It does not use asynchronous Qt thread termination, which can deadlock Python. A recovery of an already-started dungeon counts as a reported completion, so record that separately from fresh, uninterrupted runs when interpreting the result.

Cleanup targets the runner-owned Gamescope process group and its recorded descendants, avoiding the launcher's known global process-name cleanup. The worker remains alive until cleanup so Linux's thread-scoped parent-death signal does not terminate Gamescope prematurely. The test configuration and game progress are retained. Artifacts may contain account identifiers and configuration secrets; keep them local and review before sharing.
