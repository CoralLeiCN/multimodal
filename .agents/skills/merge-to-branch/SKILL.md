---
name: merge-to-branch
description: Squash every committed change from the current Codex task branch into an explicitly named local target branch as one commit while keeping the target history linear. Use when a user asks to merge, squash, land, or finalize the current branch into a branch they provide.
---

# Merge to Branch

Land the complete committed tree difference from the current source branch in a
user-provided target branch as one squash commit.

Success means:

- use the current branch as the source and never infer the target;
- include the complete committed difference between target and source;
- add exactly one non-merge commit to the target;
- leave the target and source with identical file trees; and
- do not push or remove branches or worktrees.

If the user asks only for an explanation, describe the workflow without changing
the repository.

## Handle Git Arguments Safely

Treat every resolved branch, ref, ref range, remote, and worktree path as opaque
data. Prefer a command runner that accepts an argument array and pass each value
as a separate argument. If only a shell command string is available, shell-escape
each complete value before substitution; never concatenate raw values into shell
syntax. Quoted placeholders below mark argument boundaries but do not replace
proper escaping of the resolved values.

## Workflow

### 1. Resolve the source and target

Require the user to provide `<target-branch>`. Run:

```bash
git status --short --branch
git branch --show-current
git worktree list --porcelain
git show-ref --verify --quiet "refs/heads/<target-branch>"
```

If the source is detached, create a focused branch and record it as
`<source-branch>`:

```bash
git switch -c "codex/<topic>"
```

Otherwise record the current branch as `<source-branch>`. Stop if the target does
not exist locally or equals the source.

From the worktree list, require exactly one entry with
`branch refs/heads/<target-branch>`. Record its path as `<target-worktree>` and
require it to differ from the current source worktree. Stop if the target has no
worktree or the worktree is ambiguous.

Require the target worktree to be clean:

```bash
git -C "<target-worktree>" status --short --branch
```

Do not alter or stash changes owned by another task.

### 2. Require a clean source worktree

Do not create source commits as part of this workflow. Inspect the source
worktree:

```bash
git status --short
```

If it reports staged, unstaged, or untracked changes, stop and report the paths.
Do not stage, commit, stash, discard, or otherwise alter them. Continue only
after the user explicitly requests any separate commit operation and the source
worktree is clean.

Treat the complete committed tree difference between `<target-branch>` and
`<source-branch>` as the landing scope. Do not filter individual commits, files,
or hunks from that difference.

### 3. Refresh the target

Because `<target-branch>` is checked out in `<target-worktree>`, resolve its
configured upstream through the worktree-local `@{upstream}` shorthand:

```bash
git -C "<target-worktree>" rev-parse --abbrev-ref "@{upstream}"
```

If it succeeds, record the result as `<target-upstream>`. With the target branch
still checked out, fetch without a repository argument so Git uses that branch's
configured remote, then fast-forward from the same upstream shorthand:

```bash
git -C "<target-worktree>" fetch
git -C "<target-worktree>" merge --ff-only "@{upstream}"
```

Stop if the fetch or fast-forward fails. Do not continue with a stale
remote-tracking ref or reset the target to resolve divergence. If the target has
no upstream, use the local target and report that no remote refresh was possible.

### 4. Update and validate the source

Run from the source worktree:

```bash
git merge-base --is-ancestor "<target-branch>" "<source-branch>"
```

If it fails, merge the refreshed target into the source. Resolve conflicts only
on the source branch, then rerun relevant checks:

```bash
git merge "<target-branch>"
```

Inspect the complete landing difference:

```bash
git log --oneline "<target-branch>..<source-branch>"
git diff --stat "<target-branch>...<source-branch>"
git diff --name-status "<target-branch>...<source-branch>"
git diff --check "<target-branch>...<source-branch>"
```

If there is no tree difference, stop without creating an empty commit.

### 5. Guard the landing point

Record the current target commit as `<target-before>`:

```bash
git -C "<target-worktree>" rev-parse "<target-branch>"
```

Immediately before landing, repeat the upstream fetch and fast-forward when an
upstream exists, then confirm the target still equals `<target-before>`. If it
moved, update and validate the source again, record the new `<target-before>`, and
repeat this guard.

### 6. Squash into the target

Run from the target worktree:

```bash
git -C "<target-worktree>" merge --squash "<source-branch>"
git -C "<target-worktree>" diff --cached --stat
git -C "<target-worktree>" diff --cached --check
```

Confirm the staged change represents the complete reviewed tree difference and
run relevant checks. If nothing is staged, do not create an empty commit.
Otherwise create one commit summarizing the landed changes:

```bash
git -C "<target-worktree>" commit -m "<change-summary>"
```

### 7. Verify the result

Run:

```bash
git -C "<target-worktree>" status --short --branch
git -C "<target-worktree>" rev-list --count "<target-before>..<target-branch>"
git -C "<target-worktree>" rev-list --merges "<target-before>..<target-branch>"
git -C "<target-worktree>" diff --exit-code "<target-branch>" "<source-branch>"
```

Require a clean target worktree, exactly one new commit, no new merge commit, and
identical target and source file trees. Do not expect their commit IDs to match
after a squash.

## Safeguards

- Do not check out the target in the source worktree.
- Do not use destructive Git commands, force-push, or delete branches or worktrees.
- Stop and report conflicts or product decisions that cannot be resolved safely.
- Do not push or perform cleanup unless the user asks.

Report the source branch, target branch, new target commit, upstream refresh
status, and checks run.
