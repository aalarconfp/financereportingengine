# Working with Claude Code

## Cycle

```
User request → Claude inspects → Claude proposes plan → Claude modifies → tests → diff → user reviews → commit → push
```

- **Inspect:** read the affected modules, `CLAUDE.md` and the relevant `docs/` page. Do not open personal data unless the task needs it.
- **Plan:** list the files to change, the expected behaviour change (if any) and the risks. Wait for approval when the change touches gates, validation, methodology or security.
- **Modify:** smallest change that solves the request; keep `dax_queries.md` as the single DAX source and keep presentation free of financial logic.
- **Test:** `python pipeline/tests/run_tests.py` (no FAIL) and `python scripts/repo_safety_check.py` (clean).
- **Diff:** `git diff` / `git status`; the user reviews.
- **Commit:** only when the user says so, with explicit paths (`git add <file> …`, never `git add .`).
- **Push:** only with explicit user authorization.

## Claude Code must not

- modify Power BI (measures, tables, relationships, refresh, PBIX);
- publish or write to Notion;
- handle, print or store credentials or tokens, or run `az login` for the user;
- read or copy personal financial data that the task does not need;
- change the financial methodology (Cash Basis, Historical Cost, runway definition, reconciliation classes, gates);
- create or edit DAX without authorization;
- weaken a test or a gate to make it pass.

`.claude/settings.json` enforces part of this: it denies `git push`, `git remote add`, `gh repo create`, `az login` and reading `.env` / the private data folders, and asks before `git add`, `git commit`, REST runs and reading `regression/` or `work/`. Check the active rules with `/permissions`.

## Branching

```
main          stable, certified pipeline
feature/*     e.g. feature/rest-retry, feature/moneypro-integration
fix/*         e.g. fix/reconciliation-validation
chore/*       docs, tooling, housekeeping
```

One branch per change; merge to `main` after tests, safety check and review. No extra environments for now.

## Commit messages

Conventional style: `feat:`, `fix:`, `chore:`, `docs:`, `test:`. Never include amounts, account names, file names of real closes or identifiers in messages.
