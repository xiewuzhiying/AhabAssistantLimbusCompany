# Domain Docs

How engineering skills consume this repository's domain documentation.

## Before exploring, read these

- **`CONTEXT.md`** at the repository root.
- **`docs/adr/`**: read architectural decision records touching the domain you are working on.

If any of these files do not exist yet, proceed silently without raising warnings. The `/domain-modeling` skill creates them as domain concepts are settled.

## File structure

Single-context repository:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-gamescope-nested-x11-sandbox.md
└── module/
```

## Use the glossary's vocabulary

Always use the terms defined in `CONTEXT.md` (e.g. `Gamescope`, `Nested Display`, `Client Window`, `AbstractInput`, `XTest`, `Zero-copy Capture`). Avoid inventing competing synonyms.

## Flag ADR conflicts

If an implementation or proposal contradicts an existing ADR in `docs/adr/`, flag it explicitly before overriding.
