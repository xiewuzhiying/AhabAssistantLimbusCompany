# Issue tracker: Forgejo (Gitea)

Issues and work items for this repository are tracked on Forgejo at `git.lan.foxrain.qzz.io`
(repo `xiewuzhiying/AhabAssistantLimbusCompany`). Use the `tea` CLI for all operations.

## Conventions

- **Create an issue**: `tea issue create --repo xiewuzhiying/AhabAssistantLimbusCompany --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `tea issue --comments <number> --repo xiewuzhiying/AhabAssistantLimbusCompany`.
- **List issues**: `tea issues list --repo xiewuzhiying/AhabAssistantLimbusCompany --state open`.
- **Comment on an issue**: `tea comment <number> --repo xiewuzhiying/AhabAssistantLimbusCompany --content "..."`.
- **Apply / remove labels**: `tea issue edit <number> --repo xiewuzhiying/AhabAssistantLimbusCompany --labels "..."`.
- **Close**: `tea issue close <number> --repo xiewuzhiying/AhabAssistantLimbusCompany`.

Infer repo identity from `git remote -v` (`forgejo` or `origin`).

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Create a Forgejo issue via `tea`.

## When a skill says "fetch the relevant ticket"

Run `tea issue --comments <number> --repo xiewuzhiying/AhabAssistantLimbusCompany`.
