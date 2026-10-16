---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The agent-eval `base_commit` test checks that the SHA exists in the local object store, not that it is reachable from the default branch

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): OPEN.** `tests/test_agent_evals.py::test_manifest_base_commit_exists_in_history` runs `git cat-file -e <base_commit>` and nothing more. The name says "in history", but no ancestry check against the default branch is made.

`add-change-kind-small`'s `base_commit` was `83972cb8`, a commit on an abandoned sibling branch of PR #604. It is not an ancestor of `main`, and no remote ref contains it. It was reachable only by exact SHA, so a normal checkout did not have it and the test failed on `main` too. The manifest was repointed at `f5a40556`, the `main` commit that introduced the task.

The gap: a manifest pinned to a fetchable but unmerged commit still passes this test in any clone that has that object. Scoring against it would then start from a tree that `main` never contained.

Why it is not closed here: an ancestry check (`git merge-base --is-ancestor <base> origin/main`) needs `origin/main` to exist in every checkout that runs the test. CI's `actions/checkout` default is shallow and may not fetch `main`, so a naive check would fail or skip differently per lane. Getting this right is a CI-wiring change, not a one-line test edit.

Fix direction: make the check reachability from the default branch, using the same shallow-checkout skip the test already has, and fetch the default branch explicitly in the job that runs it.
