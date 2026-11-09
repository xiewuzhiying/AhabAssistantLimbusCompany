# Linux readiness delivery — 2026-11-10

The nine readiness packages are implemented locally. The upstream baseline was refreshed from 8ea9dd3 to 2fcb1b4; the upstream Windows zombie-process fix was preserved during integration. Nothing has been pushed, and no remote scheduled workflow is active.

## Branch contract

- `main`: upstream product tree plus the fork synchronization workflow; upstream publishing workflows are excluded. This is the intended fork default branch.
- `pr/linux-native`: Linux implementation, portable CI and user documentation. Fork automation and historical machine audits are excluded.
- `linux-next`: the PR product tree plus fork synchronization policy, with publication workflows excluded. Daily automation prepares candidates for both `main` and `linux-next`, tests the Linux candidate on Windows/Linux and the upstream-only main candidate on Windows, then advances both atomically. Conflicts, test failures or concurrent branch changes leave both remote branches unchanged. It never advances the PR branch.

Original audit/rebuild branches and `archive/main-before-linux-readiness` retain provenance. The final cleanup also preserves `archive/linux-readiness-before-pr-cleanup`. Historical findings and the first reconstruction map remain under `docs/audits/2026-11-08-full-branch-review/` on the archive/rebuild branches. Do not use historical findings as claims about the final version.

## Implemented behavior

Persistent private-profile and graphics-API selection is shared by GUI, CLI and scheduling. First-run setup offers staged copying or importing a prepared private prefix; cancellation reaps the copy process and removes staging output. Steam's prefix is never silently substituted. New profiles retain copied graphics settings and default to D3D11 for automation.

Environment guidance covers Debian-family, Arch-family and optional NixOS source execution. Read-only doctor/status JSON includes stable codes, actions, phase and retryability, including custom YAML runtime paths. Existing-instance expert requests are acknowledged; timed-out queued requests are cancelled, while an operation already applying is reported as unconfirmed rather than rejected.

Input, capture and preview bind to the owned session. Linux-native-only settings expose a read-only preview window. Its capture process imports no application configuration, has a five-second timeout, and cannot hold the automation thread's Xlib lock. Native worker cancellation unwinds cooperative checkpoints; startup/restart waits for cleanup. Inhibitor ownership and process cleanup use the existing managed-session boundaries.

## Validation

- Ordinary suite with DISPLAY/WAYLAND_DISPLAY unset: 175 passed, 1 hardware timing benchmark skipped. The subsequently added preview import regression passed in the focused suite (26 passed).
- Targeted new-module typecheck: zero errors; changed new modules pass Ruff. UI construction smoke passed.
- Real systemd stop and owner-SIGKILL descendant cleanup: 2 passed.
- Actual game startup captured changing frames. Final preview acceptance launched, captured, closed and restarted the real game twice; both cycles displayed 960×540 frames and preview workers closed in about 0.2 seconds. No gameplay input was sent.
- Preview import in a fresh process creates no files and does not import application configuration. Diagnostics likewise passed the read-only temporary-directory probe.
- Fork workflow rehearsal in temporary local repositories passed candidate merges, main workflow-conflict policy, bundle cloning and atomic branch advancement. This is not a remote Actions run.
- Debian/Arch rootless container attempts were blocked by host setgroups/chown restrictions. Windows and distribution CI have not run remotely; do not claim those platforms passed real-game acceptance.

## Independent review closure

Standards review identified missing validation of the main candidate, late execution of timed-out requests and IPC disconnect handling. These were corrected and the targeted follow-up closed all three findings.

Spec review identified the same IPC gap and unbounded preview calls. Capture moved to a bounded process; a subsequent review caught configuration writes from its imports. The final helper only imports Xlib/PIL and connection support, and the dedicated regression plus reviewer follow-up closed that issue.

## Remaining limits

Native task cancellation remains cooperative: an in-flight external call in the automation worker can still delay shutdown. Preview and profile-copy cancellation now have their own bounds, but the application does not promise forceful termination of arbitrary Python or kernel I/O.

Setup reports busy/completion state rather than byte progress and conservatively checks full-copy disk space even when reflinks are available. Full interactive acceptance for every scheduler/first-run scenario and distribution remains pending. D3D12 causality and prolonged soak remain intentionally outside this work.

Start with `scripts/linux/aalc run`; Linux controls are in Settings. See [Linux usage](../linux-usage.md). Remote fork creation, pushing, workflow activation and PR publication are the next publication step.
