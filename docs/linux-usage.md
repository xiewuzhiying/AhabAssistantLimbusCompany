# Linux usage

Run from this checkout on NixOS. Keep Steam logged in. The native automation game uses an isolated Gamescope display and a systemd user service. A functioning user manager, Steam/Proton/runtime installation and graphics driver are required. Windows and simulator operation do not use this service. Frozen Linux packages are explicitly rejected for now; use this source entry point.

Create a private profile once, with all games using the source prefix closed:

```bash
scripts/linux/aalc create-profile --source /path/to/steamapps/compatdata/1973530
scripts/linux/aalc post-processing off
scripts/linux/aalc run
```

The default profile is `$XDG_DATA_HOME/aalc/compatdata/1973530` (normally `~/.local/share/aalc/compatdata/1973530`). Copying preserves Wine symlinks and uses reflinks where available; it can otherwise require the full prefix size. Existing destinations are refused. Account state and cached assets are copied locally and must not be published. Later Steam game updates still apply because the game installation is shared. Do not run two sessions for the same game account simultaneously.

`run` opens the normal AALC GUI and defaults only this launch to D3D11. For comparison use `run --graphics-api d3d12` or `run --graphics-api auto`. All profile commands accept `--profile /absolute/path`. This entry point explicitly selects its private profile; ordinary `python main.py` continues using configuration defaults. Configuration keys `compat_data_path` and `graphics_api` also work with normal startup; environment overrides are `AALC_COMPAT_DATA_PATH` and `AALC_GRAPHICS_API`. Prefix override works with automatically discovered games and creates missing directories. No Steam launch options are changed.

While the task is running, open a read-only window:

```bash
scripts/linux/aalc preview --fps 5 --width 960
```

Q or Escape closes only the viewer. Reopen after a game restart. Discovery currently requires exactly one Gamescope session visible through PipeWire; it does not select a host game as fallback. This is a local window, not a browser stream or interactive game controller. Logs: `~/.local/state/aalc/preview.log` (or `$XDG_STATE_HOME`).

With the game closed, `post-processing on|off` updates only the known `_ppOnBattle`, `_ppBloom`, `_ppBlur` booleans in the private profile's Limbus `LocalGameOptionData` registry record. It preserves other fields and keeps a first-write backup. Unknown schemas and unmarked profiles are refused. The `_ppSettingUsed` field is preserved because it is not a verified effect toggle. The mapping was observed locally; an update may change this schema. Cloud/account synchronization overriding these settings has not been ruled out.

Each launch creates a unique `aalc-game-*.service`. Normal stop terminates the whole service cgroup, including children that call setsid. A pidfd observes AALC death; when AALC disappears, the supervisor exits and systemd cleans the group. Processes deliberately moved into a separate cgroup by another manager are outside this guarantee. A missing user manager fails launch rather than silently weakening cleanup. `logs/gamescope.log` captures service output. Temporary environment payloads have mode 0600 and are deleted by the supervisor.

## Graphics investigation

```bash
nix develop --command python scripts/diagnostics/graphics_trial.py \
  --profile "$HOME/.local/share/aalc/compatdata/1973530" \
  --api d3d12 --post-processing off --seconds 120 \
  --output .audit/graphics-trials/d3d12-off-1
```

Run one trial at a time, keeping Proton, frame rate and resolution fixed. Compare D3D11/D3D12 × post processing on/off with repeated cold starts. This tool changes the selected private profile, captures frames and Player.log, and stops its own session. It does not click through login, consume game resources, or label a static menu as a freeze. A successful title-screen startup cannot resolve the previously observed post-login loading stall. To test that stage, enter the same scenario through normal AALC task execution and preserve frame history and logs. Results may contain account identifiers; keep `.audit` private.
