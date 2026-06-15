---
file: CONTRIBUTING.md
purpose: Developer workflow guide — task-manifest guard and commit hygiene
created-by: dev-team
sprint: sprint-2
story: US-4 AC-4.3
---

# Contributing to solanaTrilly

## Celery Task-Manifest Guard

solanaTrilly maintains a committed task manifest at `core/task_manifest.json`. This file enumerates every Celery task the application is expected to register. A pytest test (AC-4.2) enforces strict bidirectional equality between the manifest and the live task registry — the CI merge gate will reject any PR where these two sets diverge.

### Why this matters

The S6 incident: a task was renamed during development. The developer updated `core/tasks.py` and `core/task_manifest.json`, staged both files with `git add`, then ran `git stash` to temporarily shelve unrelated work-in-progress. After `git stash`, only the unstaged files were correctly stashed — but the staged manifest update was dropped silently. The resulting commit contained the renamed task in `tasks.py` but the old name in `task_manifest.json`. The test suite caught the divergence in CI, but not before wasted review cycles and a broken branch.

The guard below prevents this class of failure.

---

## Dev Workflow Guard: Before Every Push

Follow these steps on every branch before pushing:

### Step 1 — Verify your commit content

After `git commit`, run:

```
git show --stat HEAD
```

This prints the list of files changed in the most recent commit. Confirm that every file you intended to commit appears in the output. If a file is missing, it was not included in the commit — do not push until you have fixed it.

### Step 2 — Never `git stash` between `git add` and `git commit`

**Warning: never run `git stash` between `git add` and `git commit`.**

Running `git stash` after staging files but before committing can silently drop staged changes from your working state. The files remain staged in the index, but if you later do work on other files, apply the stash, and commit — the sequence can produce a commit that omits the staged changes entirely. This is the root cause of the S6 scar: `git stash` between add and commit caused the committed manifest to be out of sync with the task registry.

**Safe sequence:**

```
# 1. Edit your files
# 2. Stage them
git add core/tasks.py core/task_manifest.json

# 3. Commit immediately — do NOT stash between add and commit
git commit -m "[US-N] Description"

# 4. Verify the commit contains what you intended
git show --stat HEAD

# 5. Push
git push origin feature/US-N-AC-N
```

If you need to shelve work-in-progress, commit it as a WIP commit BEFORE staging the files you intend to commit cleanly:

```
# Save WIP first
git add -p  # selectively stage only WIP
git commit -m "WIP: temporary save"

# Now stage and commit your real change
git add core/tasks.py core/task_manifest.json
git commit -m "[US-N] Update task manifest"
git show --stat HEAD
```

---

## Updating the Task Manifest

When you add, rename, or remove a Celery task:

1. Edit `core/tasks.py` (the task definition).
2. Edit `core/task_manifest.json` to reflect the new task name(s).
3. Stage both files together: `git add core/tasks.py core/task_manifest.json`
4. Commit immediately (no `git stash` between steps 3 and 4).
5. Run `git show --stat HEAD` to confirm both files appear in the commit.
6. Run tests locally: `docker compose run --rm web pytest core/tests/test_task_manifest_ac41.py core/tests/test_task_manifest_ac42.py -v`

The CI merge gate runs the full manifest test suite automatically via pytest auto-discovery. A PR with a mismatched manifest will be blocked.

---

## General Commit Hygiene

- Commit format: `[US-X] Description of change`
- Branch format: `feature/US-X-AC-Y`
- All new code files must include structured metadata front matter (see `CLAUDE.md` for format).
- Run `docker compose run --rm web ruff check .` before pushing to catch lint errors early.
- Run `docker compose run --rm web pytest --cov=. --cov-config=pyproject.toml --cov-fail-under=80 -q` before pushing to verify coverage.
