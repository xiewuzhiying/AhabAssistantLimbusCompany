# ADR 0002: Freedesktop Desktop Notifications and Lightweight MJPEG Web Stream

## Status
Accepted

## Context
When running AALC in headless or background modes on Linux (such as nested inside Gamescope or minimized across various desktop environments like KDE, GNOME, XFCE, Hyprland, and Niri):
1. **Desktop Notifications**: AALC needs to deliver interactive status notifications (e.g., task completion, alerts) seamlessly regardless of desktop environment or compositor. Relying on external binaries like `notify-send` introduces unneeded subprocess overhead and may fail in minimal or declarative environments (e.g., NixOS) where `libnotify` is not in PATH.
2. **Visual Observability**: Background or headless game instances provide no direct visual window. Users need a reliable, lightweight way to observe the running game's live state from desktop browsers, secondary monitors, or mobile devices without resorting to static snapshot dumping or heavyweight video streaming stacks (FFmpeg/WebRTC).

## Decisions

### 1. D-Bus Freedesktop Notification Standard via QtDBus
- Implement Linux notifications by directly connecting to `org.freedesktop.Notifications` over Session D-Bus using `PySide6.QtDBus` (`QDBusInterface`).
- Query server capabilities via `GetCapabilities` to adaptively support actions (`actions` hint) and avoid crashes on minimal notification daemons.
- Listen to `ActionInvoked` D-Bus signals for interactive button clicks (such as "知道了" and "关闭 AALC").
- Provide graceful degradation: if D-Bus is unavailable, fall back to `notify-send` command and debug logging.
- Unify cross-platform notification dispatch under `app/notification.py`.

### 2. Zero-Dependency HTTP MJPEG Web Streamer
- Implement an on-demand HTTP streaming server using Python's standard library `http.server.ThreadingHTTPServer`.
- Deliver live game frames via `multipart/x-mixed-replace; boundary=frame` MJPEG stream at `/stream`.
- Provide a responsive, dark-themed dashboard at `/` that embeds the live video stream with zero external frontend dependencies.
- Provide a static JPEG snapshot endpoint at `/frame.jpg`.

### 3. Demand-Driven Frame Grabber
- Maintain an active viewer reference count: capture frames from `GameHandle` only when at least one web client is connected (`active_viewers > 0`).
- When zero clients are connected, the background capture thread sleeps, incurring **0% additional CPU/GPU overhead**.
- Target 15–20 FPS with lightweight JPEG compression (quality 75), keeping latency under 30ms and local network bandwidth under 1 MB/s.

### 4. Config-Driven Zero-Trust Network Exposure
- Default host binding to `127.0.0.1` and port `18080`, configurable in `config.yaml` / UI settings.
- Network security and zero-trust authentication (VPN, Tailscale, Authentik, WireGuard) are left to user infrastructure choice without artificial proxy layers.

## Consequences
- Native, robust notifications across all Linux desktop environments without external binary prerequisites.
- Instant, zero-latency visual observability for headless and background game sessions accessible from any browser.
