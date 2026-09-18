# Project instructions: Unison Snapshot Producer

## Start every task

- Read `START-HERE.md`, `docs/project/brief.md`, and the current stage gates.
- Restate the requested outcome and its acceptance criteria before changing behavior.
- Preserve the approved first-release boundary; propose scope changes explicitly.
- Explain decisions and results to the project owner in plain language.

## Engineering agreements

- Keep changes small, independently testable, and visible in Git.
- Run the project's verification command after relevant changes.
- Add or update tests when behavior changes or a defect is fixed.
- Do not add a dependency without explaining its purpose and maintenance cost.
- Never commit real secrets, tokens, customer data, production exports, or local credentials.
- Record architecture-significant or difficult-to-reverse decisions in `docs/project/decisions/`.
- Never test changes against production resources; preserve environment isolation.
- Update the changelog and follow the release process for behavior shipped to users.
- External actions such as repository changes, cloud provisioning, secret setting, deployment, or production access require an explicit user request.

## Stage changes

When real users, production data, payments, scheduled writes, new vendors, or business-critical use are introduced, reassess `.project-start/manifest.json` before proceeding.
