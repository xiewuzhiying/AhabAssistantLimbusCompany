# PROJECT AGENTS.md

## Project Context
- **Name**: AhabAssistantLimbusCompany (AALC)
- **Branch**: `xwzy/dev` (Linux / Gamescope native adaptation)
- **Runtime**: Python 3.12+, PySide6, OpenCV, ONNX Runtime (RapidOCR)
- **Host System**: NixOS 26.05 (declarative, non-FHS, no `/nix/store` hardcoding)

## Git Commit Conventions
- **Specification**: [Conventional Commits](https://www.conventionalcommits.org)
- **Language**: English only, imperative mood, lowercase subject, no trailing period (e.g., `feat(input): implement gamescope x11 driver`).
- **Atomicity**: Strictly atomic commits. Each commit must be an isolated, logically coherent, self-contained, and revertible unit of change.
- **Reference**: See `docs/agents/commit-conventions.md`.

## Agent skills

### Issue tracker
Tracked on local Forgejo via the `tea` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels
Default five-role triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs
Single-context repository layout (`CONTEXT.md` at root, architecture decision records in `docs/adr/`). See `docs/agents/domain.md`.
