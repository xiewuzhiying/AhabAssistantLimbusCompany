# ADR 0001: Gamescope Headless Execution and Audio Isolation

## Status
Accepted

## Context
Running Limbus Company automation on Linux (specifically Wayland compositors like Niri) requires strict background isolation:
1. **Window Isolation**: The game must not spawn visible windows, steal focus, or interfere with host workspaces.
2. **Cursor Isolation**: Input injection must not move or hijack the host's physical pointer.
3. **Audio Isolation**: Game audio must not output to host speakers or register audio streams in the desktop audio mixer (DMS / WirePlumber / PipeWire) during automated runs, while retaining full audio when launched normally via Steam.
4. **Vulkan / DXVK Stability**: The legacy `gamescope --headless` flag crashes the NVIDIA Vulkan compute pipeline.

## Decisions

### 1. Headless Micro-compositor (`--backend headless`)
Use `gamescope --backend headless -W <width> -H <height> -r 60`.
- Creates a dedicated headless DRM/Vulkan backend without submitting Wayland surfaces to the host compositor.
- Spawns an isolated Xwayland session (typically display `:1`) with `libeis` synthetic input support.
- Never unlink shared X11 socket paths based on connection failure or timeout: neither proves session ownership. Try the abstract Unix socket when the disk socket is unavailable; leave shared socket cleanup to its owner.

### 2. Audio Elimination via Wine DLL Overrides and Unity Flag
To prevent audio streams from appearing in PipeWire / DMS:
- Set `WINEDLLOVERRIDES="winepulse.drv=d;winealsa.drv=d"` in the launcher environment. Wine disables the PulseAudio and ALSA backend drivers, reporting zero audio output devices to Windows APIs (WASAPI/DirectSound).
- FMOD gracefully falls back to `FMOD_OUTPUTTYPE_NOSOUND` without throwing exceptions or crashing.
- Pass `-nosound` to `LimbusCompany.exe` to suppress Unity audio subsystem initialization.
- Neither PipeWire nodes nor PulseAudio sink-inputs are created. Normal launches via Steam remain untouched.

### 3. Client Window ZPixmap Capture
Capture frame buffers from the game's Client Window drawable instead of the Root Window to prevent X11 `BadMatch` errors:
- Use `ScreenShot.take_screenshot_x11(gray=False)` for 24/32-bit RGB visual pipelines.
- Use `gray=True` (default) for RapidOCR and grayscale template matching.

### 4. Reliable UGUI Input Injection
- Use `GamescopeXTestInput` to send XTest synthetic inputs into the isolated Gamescope session.
- Host physical pointer remains untouched.
- For Unity UGUI interaction (buttons, tabs, nodes), hold mouse down for 100–150ms (`mouse_down` → `time.sleep(0.12)` → `mouse_up`) to ensure sampling across game frames.

## Consequences
- Full zero-window, zero-audio background automation.
- Complete separation of automation environment from host user desktop.

### Lifecycle clarification (2026-11-08 review)

Launch with `start_new_session=True`; terminate only the owned process group, including when its leader exits before its children. Do not run Python `preexec_fn` from the threaded Qt application. This removes the previous best-effort parent-death signal: abrupt AALC death is not guaranteed to reap descendants, and descendants that create another session remain outside process-group cleanup. Normal stop and close retain bounded group cleanup.
