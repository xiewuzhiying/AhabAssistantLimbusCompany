# Linux readiness delivery — 2026-11-10

Upstream baseline refreshed to 2fcb1b4. The original audit branch and archive/main-before-linux-readiness retain provenance. Local branches main, pr/linux-native and linux-next are prepared; nothing was pushed and no scheduled workflow activated.

Implemented persistent private-profile policy, guarded single-instance selection, cooperative native cancellation, startup failure bounds, owned-cgroup display selection, source-only setup and UI preview, staged prefix copying, machine-readable diagnostics, portable test collection and Windows/Debian/Arch CI definitions. Main and daily integration carry fork-only synchronization; PR tree excludes historical machine audits. Upstream Windows zombie-process behavior was retained during conflict resolution.

Validation:
- Full ordinary suite: 171 passed. Later focused checks: 30 passed after final lifecycle corrections.
- New module typecheck: zero errors. UI construction smoke passed after correcting ExpandLayout API usage.
- Real systemd stop/owner-death checks: 2 passed. Cooperative cancellation regression confirms held locks unwind and cleanup runs.
- Actual D3D11 game startup captured changing frames using owned Xwayland discovery and cleaned its service. First attempt without that fallback captured no frames; fixed and rerun.
- doctor read-only temporary-directory probe created no files. Malformed configuration lists now produce a failed structured report.
- Debian and Arch rootless container dependency checks were attempted but blocked by host UID/setgroups/chown restrictions. Their CI workflows have not yet executed remotely; Windows execution also awaits CI. Do not claim these distributions passed real-game acceptance.

Independent Standards and Spec reviews identified lifecycle/UI/diagnostic issues; implemented fixes include private preview handle on a worker, waiting for tools/readers before deletion, close-confirmation latch, central failed-cleanup gate, skip-setup modal removal, and side-effect-free diagnostic discovery.

Remaining limitations for review:
- Legacy single-instance protocol has no response acknowledgement: failed expert selection is logged by the existing GUI, while the forwarding client can exit successfully. This remains short of a fully machine-readable command response.
- Cancellation is cooperative; in-flight Xlib/subprocess calls can delay shutdown. No forceful Python thread termination is used for native workers, but a permanently stuck external call is not a bounded shutdown guarantee.
- doctor recognizes standard Steam layout and environment path overrides; application YAML custom path overrides are not comprehensively mirrored by the lightweight detector.
- Setup copy displays busy/completion status, not byte progress. Copies use conservative free-space checks even on reflink filesystems.
- First-run, scheduling and GUI closure paths have source/unit/smoke coverage; full user-interaction acceptance across all listed scenarios is not complete.
- D3D12 causality and prolonged soak are intentionally outside this work.

Use `scripts/linux/aalc run`; Linux controls are in Settings. Diagnostics: `scripts/linux/aalc doctor --json` and `status --json`. Follow docs/linux-usage.md. This report records implementation and pending evidence; it is not an unconditional merge-ready certification.
