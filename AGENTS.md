## Agent skills

### Issue tracker

Issues live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Pull requests

When completed work is on a non-POC feature branch and is intended to merge into `main`, create a pull request with the `gh` CLI. Before creating it, inspect the branch status, recent commits, remote tracking, and the full diff from `main`; include any prerequisite commits in the review. Do not create a PR for throwaway prototype/POC branches unless explicitly requested.

### Local library development

Run the backend from the repository root (not Docker). Local library development uses builtin authentication and persistent local paths configured in the untracked `shelfmark/.env`:

```bash
uv run --env-file shelfmark/.env python -m shelfmark
```

Run the frontend in a second terminal:

```bash
cd src/frontend && npm run dev
```

Vite serves on `http://localhost:5173` and proxies `/api` and `/socket.io` to the backend on `http://localhost:8084`.

Seed the local library database with Books 1-3 (available formats, no files, and in-flight) before exercising library UI. This resets the disposable local library DB and seed files, and creates local admin credentials `demo` / `demo`:

```bash
CONFIG_DIR="$PWD/.local/config" uv run python scripts/seed_library_demo.py
```

The script writes only under `.local/`; remove `.local/config/users.db` and `.local/seed-files/` to reset the seed data.
