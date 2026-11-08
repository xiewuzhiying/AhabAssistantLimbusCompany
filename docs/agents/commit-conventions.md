# Commit Conventions

This repository strictly adheres to [Conventional Commits](https://www.conventionalcommits.org).

## Format
```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

## Language & Style
- **English only**: All commit messages must be written in English.
- **Imperative mood**: Use "add", "fix", "refactor", not "added", "fixes", "refactoring".
- **Case**: Lowercase subject line; no trailing period (`.`).

## Atomicity (Atomic Commits)
- Every commit must represent a single, self-contained, logical unit of change.
- A commit should be compileable/runnable and independently revertible.
- Do not mix refactoring, formatting, and new features in the same commit.

## Types
- `feat`: A new feature or capability (e.g. Linux Gamescope driver).
- `fix`: A bug fix.
- `docs`: Documentation only changes.
- `refactor`: Code change that neither fixes a bug nor adds a feature (e.g. extracting platform abstractions).
- `perf`: A code change that improves performance.
- `test`: Adding or correcting tests.
- `chore`: Build process, dependency updates, tooling or nix environment changes.

## Scopes
- `input`: Input injection (`module/automation/input_handlers/`)
- `screen`: Screen / window handle management (`module/game_and_screen/`)
- `screenshot`: Frame grabbing / capture (`module/automation/screenshot.py`)
- `ocr`: OCR engine configuration and inference (`module/ocr/`)
- `nix`: Nix expressions, flakes, dev shells
- `agents`: AGENTS.md, docs/agents/, agent skill configurations
