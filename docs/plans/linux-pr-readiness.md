# Linux PR readiness

Status: implemented locally; branch preparation and validation are recorded in linux-readiness-delivery.md.
Review point: 23681704664973b60f857f7d5bd355bfa7ca9024.

## Confirmed scope

- Make Linux source execution usable beyond NixOS; binary distribution is not a prerequisite for this work.
- Stop investigating the D3D12 stall root cause. Preserve existing workaround options without claiming a diagnosis.
- Prepare the implementation for an upstream PR while preserving a usable fork.
- Add environment guidance and in-app preview; do not show Linux-native preview controls on Windows.

## Nine work packages proposed for discussion

1. Portable tests and Windows/Linux CI, with explicit real-environment test selection.
2. Cooperative cancellation and bounded cleanup during start, run, stop and restart.
3. Keep-awake ownership and release across cancellation and failure.
4. Consistent source-install support, entry points and public documentation.
5. Explicit single-instance handling of profile/API selection.
6. Owned-session binding shared by input, capture and preview.
7. Persistent private-profile selection across GUI, CLI, scheduling and autostart.
8. Environment preflight, first-run guidance and bounded startup failure/retry.
9. Private-profile creation recovery, disk-space handling and settings lifecycle.

UI preview integration belongs to package 6. Environment guidance belongs to package 8.

## Accepted decisions (round 1)

- Maintain pr/linux-native as a stable review branch and linux-next as daily integration. Test a candidate upstream merge before advancing linux-next; on conflicts/failures leave the branch unchanged. Never automatically merge linux-next back into the PR branch. Preserve audit history separately.
- Support x86_64 Linux with a systemd user manager, Gamescope and native Steam/Proton. Prioritize Arch-family and Debian-family source-install instructions; retain optional NixOS environment. Non-systemd and Flatpak Steam support are outside this iteration.
- Persist private-profile and graphics API selection in the application. GUI, CLI, timers and autostart use the same policy; no silent fallback to Steam prefix. Explicitly handle different selections in an existing instance; do not switch while running.
- Default first-run flow requires a successful ordinary Steam launch and exit before copying its prefix. No fresh-prefix initialization implementation in this iteration. Expert import semantics are accepted below.

## Discovered hosting facts

Read-only gh inspection found no accessible GitHub fork for xiewuzhiying/AhabAssistantLimbusCompany. Existing remotes are GitHub upstream and local Forgejo. No scheduled merge workflow exists. Current main CI also includes release actions, so fork integration checks must be separated from publication. The locally pinned upstream is not a fresh remote verification.

## Accepted decisions (round 2)

- `--skip-setup --profile <path>` bypasses the wizard for an already prepared private prefix. Validate and register it without requiring an AALC-created marker. Dependency, isolation and in-use checks remain mandatory; do not initialize empty prefixes.
- GitHub fork main is the default branch tracking upstream with fork-only synchronization workflow. pr/linux-native and linux-next are the two working branches. Keep CI failure/conflict evidence in Actions without automated issue creation or conflict resolution. Do not run upstream release/MirrorChyan publication as part of synchronization. Remote setup/activation is a later publication step.
- Preview is a Linux-native-only UI button opening a separate read-only window bound to the current automation session. Disable before session start; reconnect after restart; closing preview does not stop the task. Remote web streaming, interaction and recording are excluded.
- Invalid environment/profile ends the current startup with an actionable error. Interactive users can retry after fixing it; unattended launches record/notify failure without interactive prompts. Startup is bounded and cancellable; another launch is disallowed until cleanup finishes.
- Diagnostics use the machine-readable contract accepted in round 3 below.
- New profiles retain copied graphics preferences; default automation rendering API is D3D11. Post-processing changes are opt-in. Preserve the user's existing private settings. UI offers auto/d3d11/d3d12 without claiming a diagnosed fix.

## Accepted decisions (round 3)

- Provide read-only `doctor --json` and `status --json` interfaces with versioned JSON, stable error codes, failure phase, current state, check results, suggested actions and log locations. Distinguish retryable failures, configuration changes and human actions. Failed commands return nonzero; JSON is the only stdout output and operational logs go to stderr. UI offers copyable diagnostics. Unattended execution never prompts. Do not export complete environments, accounts, registry contents or screenshots by default; diagnostics do not install software or mutate configuration.
- Verify dependency installation, imports and ordinary tests in Windows, Debian-family and Arch-family environments; retain NixOS checks. Ordinary CI does not require an account, installed game or GPU. Local real-session acceptance covers first setup, preview, cancellation during startup, restart after stop, abnormal owner exit cleanup and consistent scheduled profile selection. Container/package tests do not constitute real-game validation on those distributions. D3D12 attribution, prolonged soak and Linux binary distribution are excluded.

## Implementation order and acceptance

1. Establish portable tests and CI boundaries. Preserve Windows test/build behavior and make real-session tests explicit.
2. Unify persisted profile selection, expert import, single-instance requests and scheduled/autostart entry points. Reject silent Steam-prefix fallback and runtime profile switching.
3. Bind window discovery, input, capture and preview to the owned automation session. Other player sessions must not be selected.
4. Implement cooperative cancellation, bounded startup/stop and keep-awake cleanup. Starting again is gated on cleanup completion.
5. Add common source entry points, environment checks, resumable first-run guidance and private-profile creation failure recovery. Focus installation instructions on Arch/Debian families; keep Nix optional.
6. Add Linux-native UI preview and structured diagnostics using the same session/profile interfaces.
7. Run targeted tests throughout, cross-platform checks and local real-session acceptance; review the result independently and fix confirmed findings. Record limits honestly.
8. Prepare the stable PR branch and fork-only daily candidate-merge workflow, preserving original audit provenance. Separate upstream deliverable code/docs from fork-only automation and bulky historical evidence. Remote fork creation, push, workflow activation and PR publication remain a later concrete publication step.

## Completion boundary

The nine work packages above are the implementation scope. Routine implementation details and exact timeout values may be chosen from measured behavior; no additional feature-design interview is needed unless new evidence changes a settled requirement. Record checks and outstanding platform validation in the delivery report. No remote branches or scheduled automation have been created by this interview.
