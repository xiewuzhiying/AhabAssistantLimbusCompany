# Separate PR review from daily upstream integration

Accepted during the 2026-11-10 design interview. Keep a stable `pr/linux-native` branch for upstream review and a `linux-next` branch for daily use. Daily synchronization tests a candidate merge before advancing the integration branch; conflicts or failed checks leave it unchanged, and integration changes are never automatically merged back into the review branch.

This keeps upstream review reproducible while allowing the fork to track ongoing upstream work. Audit branches remain available for provenance. GitHub fork `main` is the default branch tracking upstream plus fork-only synchronization workflow. Failed candidates retain their Actions evidence; no issues or conflict resolutions are generated automatically. Synchronization must not trigger release or MirrorChyan publication. Remote activation is a separate publication step.
