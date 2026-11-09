# Linux source installation

Supported initial scope: x86_64 Linux, a systemd user session, Gamescope and native Steam/Proton. Arch-family and Debian-family distributions are primary source-install targets; NixOS retains its development shell. Flatpak Steam, non-systemd systems and frozen Linux bundles are outside this release scope. Windows and simulator flows remain separate.

Install Steam, select Proton, successfully launch Limbus once and exit it. Keep Steam logged in while using AALC. Install uv using its official installation instructions, then install desktop dependencies (package availability depends on distribution release):

```bash
# Debian/Ubuntu: enable your distribution's Steam repositories as appropriate
sudo apt install gamescope libegl1 libgl1 libxkbcommon0 libdbus-1-3
# Arch: Steam requires the distribution's multilib repository
sudo pacman -S gamescope libglvnd libxkbcommon dbus
```

From the checkout, `uv sync --frozen` installs the pinned Python environment. Start with `scripts/linux/aalc run`, or `uv run python main.py`. On NixOS the wrapper uses `nix develop` automatically. Neither path needs a hardcoded Nix store location.

On first interactive launch, Linux setup explains the required Steam launch and offers copying its inactive `steamapps/compatdata/1973530` directory into a private location (normally `~/.local/share/aalc/compatdata/1973530`). It also accepts an existing private prefix. Copying is staged, checks available space, and cleans partial output on failure or cancellation so retry is possible. Closing setup cancels a copy and waits for cleanup. Original settings are preserved; changes to post processing are optional. The default AALC API is D3D11, a compatibility choice rather than a diagnosed stall fix.

The settings page provides Linux environment/profile controls and a read-only preview button in native Linux game mode. Preview uses the owned automation session; closing it does not stop the task. It waits for a restarted session. Frames are captured in a separate process with a five-second timeout; preview refresh speed depends on the machine. No host game or unrelated Gamescope is selected. Windows does not show these controls.

Configuration is persisted under `$XDG_CONFIG_HOME/aalc/native-linux.json` (normally `~/.config/aalc/`). GUI, CLI, timers and login startup read it. Explicit profile/API requests are forwarded to the existing application and cannot switch an active session. Expert import bypasses the wizard but validates the prepared private prefix. Rejected or unconfirmed requests return exit 2. An unconfirmed request may already be applying: inspect the existing instance settings before retrying. Requests still queued at timeout are cancelled.

```bash
scripts/linux/aalc run --skip-setup --profile /absolute/private/compatdata
scripts/linux/aalc run --graphics-api auto
scripts/linux/aalc doctor --json
scripts/linux/aalc status --json
scripts/linux/aalc preview
```

Diagnostics are versioned JSON with stable check codes, phase, suggested action, retryability, state and log location. stdout contains JSON; failures return nonzero. They do not export account data, registry contents, screenshots or full process environments. Setup includes copyable diagnostics. Missing prerequisites terminate unattended startup instead of opening a wizard or retrying indefinitely. Fix the reported condition before retrying.

Session cleanup uses a unique systemd user service, pidfd owner monitoring and cgroup termination. Native task cancellation is cooperative and another task cannot start while its worker is cleaning up. Processes deliberately migrated into another cgroup are outside this guarantee. Stop/close can wait for an in-flight external call; they do not forcibly kill a Python thread holding application locks.

Ordinary CI needs no installed game or graphics session. Hardware timing benchmarks are opt-in with `AALC_BENCHMARKS=1`. Real tests are opt-in with `AALC_LIVE_TESTS=1`, `PYTHONPATH=$PWD`, and an available systemd/graphics session. Distribution container checks validate installation/imports/tests, not real-game operation. Windows, Debian-family and Arch-family CI definitions are included; local NixOS validation is recorded separately.
