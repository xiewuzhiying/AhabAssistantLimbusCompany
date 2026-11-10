# CONTEXT.md

## Domain Glossary

### Target System & Game
- **Limbus Company**: 2D gacha turn-based RPG developed by Project Moon, powered by the Unity engine.
- **Steam AppID**: `1973530`.
- **Gamescope**: Micro-compositor by Valve that creates an isolated nested Xwayland/Wayland sandbox. Used here to run the game in an isolated display environment with dedicated input routing.
- **Proton**: Valve's Wine-based compatibility layer running Windows games on Linux.

### Automation Architecture
- **AALC (AhabAssistantLimbusCompany)**: Python/PySide6 automation tool providing OCR, image matching, state machines, and task queues for Limbus Company.
- **Handle**: Window abstraction representing the game's display window (HWND on Windows, Window ID on X11).
- **AbstractInput**: Base interface declaring mouse and keyboard interaction primitives (`mouse_click`, `mouse_drag`, `key_press`, etc.).
- **XTest**: X11 protocol extension for synthetic input generation, implemented inside Gamescope via `libeis`.
- **Client Window Capture**: Capturing frame pixels directly from the game's top-level X11 Client Window (bypassing the composite-less Root Window).
- **Headless Backend**: Running Gamescope with `--backend headless` to completely isolate the game window from the host desktop compositor.
- **Audio Isolation**: Suppressing game audio output via `WINEDLLOVERRIDES="winepulse.drv=d;winealsa.drv=d"` and `-nosound` so no PipeWire streams appear in the host desktop mixer. See `docs/adr/0001-gamescope-headless-execution-and-audio-isolation.md`.
- **Freedesktop Notification**: Desktop notification standard specified by freedesktop.org over Session D-Bus (`org.freedesktop.Notifications`), supported across KDE, GNOME, XFCE, Dunst, Mako, and Wayland compositors.
- **Web Screen Streamer**: Lightweight HTTP MJPEG service broadcasting real-time visual frames from the game display handle to web browsers on demand.

### Native Linux Automation
- **Automation Profile**: The private game settings and local game state selected for AALC automation, separate from normal Steam play.
- **Automation Session**: One game run owned by AALC, including its input target, captured window and lifetime. An independently launched player game is not an automation session.
