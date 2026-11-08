# ADR 0003: Standalone Proton Launcher over Steam Wrapper

## Status
Accepted

## Context
AALC needs the ability to autonomously launch Limbus Company (Steam AppID `1973530`) inside a Gamescope headless sandbox satisfying ADR 0001 (isolated display, no audio, no cursor interference).

Currently, AALC starts the game via `open_uri("steam://rungameid/1973530")`, delegating the entire launch chain to the running Steam client. If the user has not configured Gamescope inside Steam's Launch Options, the game launches into the host XWayland desktop with full audio, interrupting host workflows.

We require a solution where:
1. AALC can launch the game directly into an isolated Gamescope session on demand.
2. The user's normal gaming experience (launching directly via Steam GUI with window, display, and audio) remains untouched.

## Considered Options

### Option A: Steam Launch Options Wrapper (Rejected)
Configure Steam's per-game Launch Options to run a shim script (e.g. `aalc-wrapper %command%`) that inspects an external trigger (state file, socket, or environment) to decide whether to launch normally (`exec "$@"`) or wrap inside Gamescope headless.

**Why Rejected:**
1. **Host-Side Invasive Setup**: Requires manual user intervention in the Steam client GUI, or brittle mutation of Steam's binary/key-value storage (`localconfig.vdf`). Modifying VDF files while Steam is active is prone to race conditions and cache overwriting upon Steam shutdown.
2. **State Pollution & Fragility**: If AALC crashes or terminates abnormally without cleaning up the trigger token/file, subsequent manual game launches from Steam could mistakenly trigger the headless, muted sandbox.
3. **Black-box Process Lifecycle**: Processes launched via Steam are managed under Steam's internal reaper/pressure-vessel trees. AALC cannot reliably capture process PIDs, streams (stdout/stderr), or crash return codes directly.

### Option B: Standalone Proton Runner (Accepted, Lutris-style)
AALC directly spawns `gamescope` and invokes the Steam Proton execution chain (pressure-vessel / Proton runtime) targeting `LimbusCompany.exe`, using the existing Steam library and `compatdata/1973530` prefix.

## Decisions

### 1. Adopt Lutris-Style Direct Proton Execution
AALC acts as the direct parent process of the Gamescope container:
- Gamescope is invoked directly by AALC with headless parameters (`--backend headless -W 1920 -H 1080 -r 60`).
- Inside Gamescope, AALC executes the Proton launcher targeting `LimbusCompany.exe` with the existing `WINEPREFIX` (`steamapps/compatdata/1973530/pfx`).
- Audio isolation from ADR 0001 is injected directly (`WINEDLLOVERRIDES="winepulse.drv=d;winealsa.drv=d"` and `-nosound`).
- Steam DRM authentication (`steam_api64.dll`) communicates with the running background Steam client via the standard Steam local IPC socket (`/tmp/dumps/<uid>_dump_*` or DBus/Pipe), ensuring project Moon server login tickets remain valid without routing execution through the Steam client UI.

### 2. Complete Separation of Play Modes
- **Player Play**: User clicks "Play" in Steam. Default un-sandboxed desktop game with full audio and host window.
- **AALC Automation**: AALC launches the standalone Proton runner under Gamescope. Zero user intervention required in Steam settings.

## Consequences
- AALC must implement a Steam environment resolver capable of locating Steam libraries, Proton runtime paths, and prefix directories.
- NixOS Steam runtime compatibility (FHS environment or Steam Linux Runtime pressure-vessel) must be accounted for by the runner.
- Prototyping must verify that direct Proton invocation under a running Steam background client successfully passes Project Moon's server ticket authentication.
