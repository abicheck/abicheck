# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`scripts/gen_harbor_tasks.py` -- the generated Harbor task battery.

Real end-to-end grading (does `solve.sh` -> the shim -> `verify_run.py`
actually produce reward=1 for a correct answer) is exercised directly, not
mocked -- see `TestSolveScriptsEndToEnd`. Full trial execution (an actual
Harbor `harbor run`, which needs Docker) is out of scope for the fast unit
lane; see `agent-evals/skills/harbor/CLAUDE.md` for what that still leaves
unverified.

`TestHarborSchemaValidation` is the one class in this file that needs the
real `harbor` package (`>=3.12`, not a repository dependency) -- it
self-skips via `pytest.importorskip` rather than a new pytest marker
(tests/CLAUDE.md: "don't change the marker scheme"), and CI installs
`harbor` and runs it as a dedicated, best-effort step precisely because it
is not otherwise reachable from any existing marker lane.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib
from _workflow_exec import bash_executable

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
TASKS_DIR = EVAL_DIR / "harbor" / "tasks"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(EVAL_DIR))

import gen_harbor_tasks as gen  # noqa: E402

pytestmark = pytest.mark.skipif(
    not TASKS_DIR.is_dir(), reason="agent-evals/skills/harbor/tasks/ not generated"
)

#: Shared skip for the three test classes below that actually execute a
#: generated script's shell logic (not just parse/validate its text) --
#: TestPythonInterposer, TestArchitectureGuard, TestSolveScriptsEndToEnd.
#: Real Windows CI evidence (PR #816) found this goes deeper than the
#: `_bash_path()`/`.as_posix()` fix already applied: even with a correctly
#: POSIX-rendered substituted path, `readlink -f`'s own *output* under Git
#: Bash's POSIX emulation layer didn't bytewise match the expected
#: comparison string (TestPythonInterposer), and the `uname -m`
#: architecture-mismatch guard didn't short-circuit as expected either
#: (TestArchitectureGuard) -- both genuine Git-Bash-emulation differences
#: from real Linux bash this sandbox has no Windows host to debug against.
#: These scripts are written to run inside the harbor task's own Linux
#: container (`python:3.13-slim`) in the first place -- exercising them
#: directly via the CI *host's* bash on Windows was never really testing
#: their actual target environment, only Git Bash's POSIX emulation of it.
_SKIP_ON_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "these scripts run inside a Linux container at real trial time; "
        "executing them directly via Git Bash's POSIX emulation on the "
        "Windows CI host has real, unresolved semantic differences from "
        "actual Linux bash (readlink -f output shape, uname -m guard "
        "short-circuiting) -- see this constant's own comment"
    ),
)


def _task_dirs() -> list[Path]:
    return sorted(p for p in TASKS_DIR.iterdir() if p.is_dir())


def _bash_path(path: Path) -> str:
    """*path* rendered for substitution into bash script *text* (not an
    argv element -- those go through subprocess directly and need no
    rewriting).

    ``str(path)`` on Windows renders backslashes (``C:\\Users\\...``), and
    bash's own unquoted-word parser treats a backslash as an escape
    character: it silently strips it and keeps the following letter,
    turning the substituted path into `C:UsersrunneradminAppDataLocal...`
    with no separators at all -- a real Windows CI failure (`cd`/`python3
    -c`/`readlink -f` all corrupted the same way), not a hypothetical.
    Git Bash's own path translation understands a forward-slash Windows
    path natively, so `.as_posix()` (a no-op on POSIX) is both correct and
    doesn't require actually POSIX-translating the drive letter."""
    return path.as_posix()


def _write_lf_text(path: Path, content: str) -> None:
    """``path.write_text(content, encoding="utf-8", newline="\\n")`` -- the
    same LF-forcing shape as the generator's own ``gen._write_lf`` helper,
    reused here for tests that mutate-then-restore a *real, git-tracked*
    file under ``TASKS_DIR`` (not a throwaway ``tmp_path`` copy).

    Real Windows CI evidence (PR #816): plain ``write_text(content,
    encoding="utf-8")`` defaults to ``newline=None``, which on Windows
    translates every ``\\n`` in *content* to ``\\r\\n`` on write -- not just
    for the test's own mutation, but for its ``finally:`` "restore" step
    too, since that restore is exactly as unguarded. The very first such
    test to run therefore leaves the real working tree's Dockerfile/
    task.toml permanently CRLF-corrupted for the rest of the test session
    (each restore re-corrupts what it just "fixed"), which every later
    test that reads those same on-disk files -- generator output is always
    LF, via ``gen._write_lf`` -- then reads as "content differs", even
    though nothing about the generator or the committed tree was ever
    actually wrong. Confirmed via real `windows-latest` CI logs: *every*
    generated Dockerfile failed `--check` uniformly, and `removed-export/
    task.toml` (the one file `test_check_fails_on_drift` mutates) failed
    too -- exactly the set of files these unguarded-write tests touch, not
    a symptom any `.gitattributes`/digest-computation bug could produce."""
    path.write_text(content, encoding="utf-8", newline="\n")


class TestGeneratorCheck:
    def test_check_passes_against_the_committed_tree(self):
        assert gen.generate(check=True) is True

    def test_check_fails_on_drift(self, tmp_path, monkeypatch):
        stray = TASKS_DIR / "removed-export" / "task.toml"
        original = stray.read_text(encoding="utf-8")
        try:
            _write_lf_text(stray, original + "\n# hand-edited\n")
            assert gen.generate(check=True) is False
        finally:
            _write_lf_text(stray, original)

    def test_check_survives_the_pinned_ref_moving(self, monkeypatch):
        """A commit boundary always moves `git rev-parse HEAD` past whatever
        was baked into the Dockerfile at generation time (the SHA is
        necessarily computed *before* the commit that carries it exists) --
        `--check` must not treat that expected drift as real drift. Real
        regression: this failed on every commit after the tree was first
        committed, caught by literally committing and re-running `--check`,
        not by reasoning about it."""
        monkeypatch.setattr(gen, "_abicheck_ref", lambda: "f" * 40)
        assert gen.generate(check=True) is True

    def test_runtime_relevant_digest_is_deterministic_and_content_sensitive(
        self, tmp_path
    ):
        """`_runtime_relevant_digest` must answer from real file content, not
        a commit reference resolved through git history -- that's the whole
        point of replacing the old `git rev-parse --verify`/`git diff
        --name-only`-based check, which stopped working the instant the
        pinned commit became unreachable (this repo's PRs squash-merge, so
        a source branch's own commits do not outlive its merge). Built
        against a synthetic tree mirroring `_RUNTIME_RELEVANT_PATHS`, not
        this repo's real `graders/`, so the test doesn't depend on their
        current content."""
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        for root in (root_a, root_b):
            (root / "agent-evals" / "skills" / "graders").mkdir(parents=True)
            (root / "agent-evals" / "skills" / "shim").mkdir(parents=True)
            (root / "agent-evals" / "skills" / "harbor").mkdir(parents=True)
            (root / "agent-evals" / "skills" / "graders" / "dimensions.py").write_text(
                "x = 1\n", encoding="utf-8"
            )
            (root / "agent-evals" / "skills" / "shim" / "abicheck").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            (root / "agent-evals" / "skills" / "harbor" / "verify_run.py").write_text(
                "pass\n", encoding="utf-8"
            )
        # Identical content, two different roots -- the digest must agree.
        assert gen._runtime_relevant_digest(root_a) == gen._runtime_relevant_digest(
            root_b
        )
        # A real change to a runtime-relevant file must move the digest --
        # this is the signal that replaces "is the pinned ref reachable and
        # does `git diff` against it show anything under these paths".
        (root_b / "agent-evals" / "skills" / "graders" / "dimensions.py").write_text(
            "x = 2\n", encoding="utf-8"
        )
        assert gen._runtime_relevant_digest(root_a) != gen._runtime_relevant_digest(
            root_b
        )

    def test_runtime_relevant_digest_ignores_pycache_and_bytecode(self, tmp_path):
        """`__pycache__`/`.pyc`/`.pyo` are generated, environment-dependent
        noise, not source -- confirmed reproducible against this repo's real
        `graders/` directory: running the test suite before regenerating
        leaves `graders/__pycache__/*.pyc` on disk, and computing the digest
        with vs. without those files present produced two different hex
        strings for the identical source tree, which would fail `--check`
        on a perfectly fresh checkout purely because CI happened to import
        a graders module in an earlier step of the same job (Codex review,
        fresh evidence). Built against a synthetic tree so this doesn't
        depend on whether this repo's own `graders/` happens to have a
        pycache directory at test time."""
        root = tmp_path / "src"
        graders = root / "agent-evals" / "skills" / "graders"
        graders.mkdir(parents=True)
        (root / "agent-evals" / "skills" / "shim").mkdir(parents=True)
        (root / "agent-evals" / "skills" / "harbor").mkdir(parents=True)
        (graders / "dimensions.py").write_text("x = 1\n", encoding="utf-8")
        (root / "agent-evals" / "skills" / "shim" / "abicheck").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        (root / "agent-evals" / "skills" / "harbor" / "verify_run.py").write_text(
            "pass\n", encoding="utf-8"
        )
        before = gen._runtime_relevant_digest(root)

        pycache = graders / "__pycache__"
        pycache.mkdir()
        (pycache / "dimensions.cpython-313.pyc").write_bytes(b"\x00\x01fake bytecode")
        (graders / "dimensions.pyc").write_bytes(b"\x00\x01another fake")
        after = gen._runtime_relevant_digest(root)

        assert before == after

    def test_runtime_relevant_digest_detects_a_genuine_crlf_edit(self, tmp_path):
        """`_runtime_relevant_digest` must NOT normalize line endings away --
        an earlier revision collapsed CRLF to LF before hashing to paper
        over a real platform-dependence bug (a Windows checkout of these
        paths, absent .gitattributes, CRLF-translated them), and that
        normalization also silently hid a genuine CRLF-introducing edit to
        a runtime-relevant file, which could break a shebang in the real
        built image (Codex review, fresh evidence). The platform-dependence
        is now closed at its source instead (.gitattributes pins these
        paths to `text eol=lf`, and `_write_lf` forces LF when this
        generator itself runs on Windows) -- see that helper's own
        docstring -- so this digest is free to stay sensitive to a real
        byte-level change of any kind, line endings included."""
        root = tmp_path / "src"
        for sub in ("graders", "shim", "harbor"):
            (root / "agent-evals" / "skills" / sub).mkdir(parents=True)
        target = root / "agent-evals" / "skills" / "harbor" / "verify_run.py"
        target.write_bytes(b"import sys\nprint(sys.argv)\n")
        (root / "agent-evals" / "skills" / "graders" / "dimensions.py").write_text(
            "x = 1\n", encoding="utf-8"
        )
        (root / "agent-evals" / "skills" / "shim" / "abicheck").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        before = gen._runtime_relevant_digest(root)

        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        after = gen._runtime_relevant_digest(root)

        assert before != after

    def test_write_lf_forces_lf_regardless_of_content(self, tmp_path):
        """`_write_lf` is what keeps this generator's own output
        byte-identical across platforms -- plain `write_text()` uses
        `newline=None`, which on Windows would translate every `\\n` in
        the content to `\\r\\n` on write, independent of git entirely.
        Exercised directly since none of `generate()`'s own callers run
        on an actual Windows host in this suite."""
        target = tmp_path / "out.txt"
        gen._write_lf(target, "line one\nline two\n")
        assert target.read_bytes() == b"line one\nline two\n"

    def test_runtime_relevant_digest_hashes_posix_style_relative_paths(self):
        """CodeRabbit review, fresh evidence: `_runtime_relevant_digest`
        must key each file by `path.relative_to(root).as_posix()`, not
        `str(...)` -- `str()` on a `PureWindowsPath` renders backslashes
        (`agent-evals\\skills\\graders\\dimensions.py`), which would make
        the digest differ from a POSIX checkout's forward-slash rendering
        of the identical relative path even once the content bytes
        themselves are LF-stable, so a genuinely unchanged tree would still
        read as stale on Windows.

        `_runtime_relevant_digest` itself always resolves real `Path`
        objects for the host it runs on, so it can't be driven with a
        `PureWindowsPath` directly -- this instead replays the function's
        own hashing loop (same byte layout: relative-path bytes, a NUL,
        content bytes, a NUL) against `PurePosixPath`'s and
        `PureWindowsPath`'s renderings of the identical relative path and
        content, keyed by `.as_posix()` exactly as the real function does,
        and asserts the two produce the identical digest -- which fails
        immediately if `.as_posix()` is swapped back for a bare `str()`."""
        from hashlib import sha256
        from pathlib import PurePosixPath, PureWindowsPath

        relative = "agent-evals/skills/graders/dimensions.py"
        content = b"x = 1\n"

        def _digest(path_cls):
            h = sha256()
            h.update(path_cls(relative).as_posix().encode())
            h.update(b"\0")
            h.update(content)
            h.update(b"\0")
            return h.hexdigest()

        assert _digest(PurePosixPath) == _digest(PureWindowsPath)
        # And the two renderings must genuinely differ under `str()` --
        # otherwise this test would pass even against the pre-fix code.
        assert str(PureWindowsPath(relative)) != str(PurePosixPath(relative))

    def test_runtime_relevant_digest_does_not_track_the_skill_tree(self, tmp_path):
        """`.claude/skills/check-abi-compatibility` is deliberately absent
        from `_RUNTIME_RELEVANT_PATHS` -- an earlier round added it because
        `_dockerfile()` used to `cp -r` this tree into every image's
        `/opt/skills/` for a skill-arm trial, but that bake-in step was
        itself later removed (Codex review, fresh evidence: baking the
        skill into *every* image regardless of arm let a baseline trial's
        own agent discover and manually follow it via ordinary shell
        access, contaminating the baseline arm -- the skill is now
        injected per-trial by Harbor's own `--skill` mechanism, entirely
        outside the Docker build). No generated task file depends on the
        skill's content any more, so a skill-only edit must not move this
        digest -- the inverse of what this test used to assert. Built
        against a synthetic tree, like the sibling determinism test above,
        so this doesn't depend on the real skill's current content."""
        root = tmp_path / "src"
        (root / "agent-evals" / "skills" / "graders").mkdir(parents=True)
        (root / "agent-evals" / "skills" / "shim").mkdir(parents=True)
        (root / "agent-evals" / "skills" / "harbor").mkdir(parents=True)
        skill_dir = root / ".claude" / "skills" / "check-abi-compatibility"
        skill_dir.mkdir(parents=True)
        (root / "agent-evals" / "skills" / "graders" / "dimensions.py").write_text(
            "x = 1\n", encoding="utf-8"
        )
        (root / "agent-evals" / "skills" / "shim" / "abicheck").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        (root / "agent-evals" / "skills" / "harbor" / "verify_run.py").write_text(
            "pass\n", encoding="utf-8"
        )
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("original workflow content\n", encoding="utf-8")
        before = gen._runtime_relevant_digest(root)

        skill_md.write_text(
            "original workflow content\nplus a real edit\n", encoding="utf-8"
        )
        after = gen._runtime_relevant_digest(root)

        assert before == after

    def test_dockerfile_reduces_the_agent_visible_clone_to_an_allowlist(self):
        """The agent has ordinary shell access to its own container's
        filesystem for the whole agent phase -- a full `git clone` of this
        repository (what `_dockerfile()` does to obtain `abicheck` itself)
        would hand the agent being evaluated ordinary shell access to every
        other task's own ground truth. Two earlier fixes narrowed what
        survives this clone (a denylist, then an allowlist keeping
        `graders/`/`verify_run.py` alongside `abicheck/`) but neither closed
        the actual problem: `graders/dimensions.py`'s own scenario-
        identifying comments mean *any* in-container copy of it is
        answer-bearing, so no naming scheme applied to this same container
        can hide it from a process with shell access to that container
        (Codex review, fresh evidence). The real fix is architectural, not
        this allowlist: the graders now live only in the separate verifier
        container built from `tests/Dockerfile`
        (`test_verifier_dockerfile_keeps_only_the_grader_allowlist` below),
        which the agent never has filesystem access to at all -- so this
        allowlist only needs to keep what the *agent's* image itself still
        needs, which is `abicheck/` alone. Pinned as a content assertion on
        the generated Dockerfile rather than an actual Docker build (no
        Docker in this sandbox -- see `agent-evals/skills/harbor/
        CLAUDE.md`)."""
        dockerfile = (
            TASKS_DIR / "removed-export" / "environment" / "Dockerfile"
        ).read_text(encoding="utf-8")
        # The `RUN set -eux; cd /opt/abicheck-src; ...` statement is a
        # single semicolon-joined shell command -- isolate exactly its body
        # (there is an earlier, unrelated `RUN set -eux;` step for the
        # version-metadata patch), not the whole file, so this test can
        # tell "the real step" from "merely mentioned in a comment
        # elsewhere".
        start = dockerfile.index("RUN set -eux; \\\n    cd /opt/abicheck-src;")
        end = dockerfile.index("\n\n", start)
        cleanup_block = dockerfile[start:end]
        # The whole original clone -- answer-bearing paths and everything
        # else nobody has named yet -- must be discarded wholesale, not
        # selectively pruned.
        assert "rm -rf /opt/abicheck-src" in cleanup_block
        assert "mv /tmp/rt-keep /opt/abicheck-src" in cleanup_block
        # And exactly the allowlist -- nothing more -- must be staged
        # before that wholesale discard: `abicheck/` (the editable install
        # needs its source to persist at this exact path) alone.
        assert "mv abicheck /tmp/rt-keep/abicheck" in cleanup_block
        # The graders and `verify_run.py` must NOT be staged into the
        # agent's own image at all any more -- they now live only in the
        # separate verifier container.
        assert "graders" not in cleanup_block
        assert "verify_run.py" not in cleanup_block
        # No other `mv ... /tmp/rt-keep/...` line may exist -- a second
        # staged path would silently widen the allowlist without this test
        # noticing unless it's counted explicitly.
        assert cleanup_block.count("/tmp/rt-keep/") == 1  # the one mv target
        # And no `RUN`/`COPY`/`ENV` instruction anywhere in the agent
        # Dockerfile may reference either path -- only this file's own
        # explanatory comments (checked separately, above) are allowed to
        # name them. Isolate the instruction lines (anything not starting
        # with `#` after stripping leading whitespace) rather than banning
        # the words outright, since the surrounding prose legitimately
        # explains *why* they were removed.
        instruction_lines = "\n".join(
            line
            for line in dockerfile.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        assert "graders" not in instruction_lines
        assert "verify_run.py" not in instruction_lines

    def test_verifier_dockerfile_keeps_only_the_grader_allowlist(self):
        """`tests/Dockerfile` -- the separate verifier's own image -- must
        clone the same pinned ref and keep exactly `agent-evals/skills/
        graders/`, `verify_run.py`, and `abicheck/` itself.

        `abicheck/` is deliberately *not* excluded here, unlike an earlier
        version of this test: `graders/evidence.py`'s own `_option_tables()`
        has a lazy `import click; from abicheck.cli import main` used to
        read the real Click command tree (which options are bare flags vs.
        value-taking), and neither being importable degrades grading to a
        conservative fallback that can hide a real self-comparison from
        zero-tolerance dimensions (Codex review, fresh evidence) -- so the
        editable install this container's agent-image sibling also carries
        is needed here too, for a different consumer."""
        dockerfile = (TASKS_DIR / "removed-export" / "tests" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        start = dockerfile.index("RUN set -eux; \\\n    cd /opt/abicheck-src;")
        end = dockerfile.rindex("mv /tmp/rt-keep /opt/abicheck-src") + len(
            "mv /tmp/rt-keep /opt/abicheck-src"
        )
        cleanup_block = dockerfile[start:end]
        assert "rm -rf /opt/abicheck-src" in cleanup_block
        assert "mv /tmp/rt-keep /opt/abicheck-src" in cleanup_block
        assert "mv abicheck /tmp/rt-keep/abicheck" in cleanup_block
        assert (
            "mv agent-evals/skills/graders /tmp/rt-keep/agent-evals/skills/graders"
            in cleanup_block
        )
        assert (
            "mv agent-evals/skills/harbor/verify_run.py "
            "/tmp/rt-keep/agent-evals/skills/harbor/verify_run.py" in cleanup_block
        )
        assert cleanup_block.count("/tmp/rt-keep/") == 4  # mkdir -p + 3 mv targets
        # No compiler toolchain needed -- click/rich-click/pyelftools are all
        # pure-Python wheels -- but the editable install itself is required.
        assert "build-essential" not in dockerfile
        assert 'pip install --no-cache-dir -e "."' in dockerfile

    def test_old_reachability_design_crashed_on_a_genuinely_unresolvable_ref(self):
        """Pins the actual failure the digest redesign replaced -- not
        merely a wrong answer, an unhandled crash. Replays the old
        function's exact body (removed from the module) against a
        fabricated 40-hex-char string that is not any real object in this
        repository: `git rev-parse --verify -q` does not reliably fail for
        a syntactically valid SHA-shaped string (confirmed directly --
        it can report success without the object existing), so the
        subsequent `git diff --name-only <ref>..HEAD` call, run with
        `check=True`, raised `CalledProcessError` instead of the intended
        graceful "not tolerable" answer (Codex review, third round)."""
        import subprocess as _subprocess

        def _old_ref_drift_is_tolerable(committed_ref):
            if committed_ref is None:
                return True
            verify = _subprocess.run(
                ["git", "rev-parse", "--verify", "-q", committed_ref],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if verify.returncode != 0:
                return False
            diff = _subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    f"{committed_ref}..HEAD",
                    "--",
                    *gen._RUNTIME_RELEVANT_PATHS,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            return not diff.stdout.strip()

        fabricated_ref = "e" * 40
        with pytest.raises(_subprocess.CalledProcessError):
            _old_ref_drift_is_tolerable(fabricated_ref)

    def test_check_survives_an_unreachable_pinned_ref_with_unchanged_content(self):
        """The exact scenario the digest redesign exists for: after a
        squash-merge, the commit pinned in an already-committed Dockerfile
        is not reachable from this checkout's history at all (its source
        branch's own commits don't survive the merge) -- but nothing under
        `_RUNTIME_RELEVANT_PATHS` actually changed, so `--check` must still
        pass without ever asking git to resolve the pinned ref (the
        previous design crashed outright on exactly this input -- see
        `test_old_reachability_design_crashed_on_a_genuinely_unresolvable_ref`
        immediately above)."""
        import re as _re

        dockerfiles = sorted(TASKS_DIR.rglob("Dockerfile"))
        originals = {p: p.read_text(encoding="utf-8") for p in dockerfiles}
        try:
            for p, text in originals.items():
                _write_lf_text(
                    p,
                    _re.sub(
                        r"ARG ABICHECK_REF=[0-9a-f]{40}",
                        # A ref this checkout's local git history has never
                        # heard of -- not a real commit anywhere.
                        "ARG ABICHECK_REF=" + "e" * 40,
                        text,
                    ),
                )
            assert gen.generate(check=True) is True
        finally:
            for p, text in originals.items():
                _write_lf_text(p, text)

    def test_check_catches_a_stale_digest_with_real_grader_drift_behind_it(self):
        """End-to-end proof that a real, un-regenerated change under
        `_RUNTIME_RELEVANT_PATHS` still fails `--check`, now signaled via the
        embedded digest rather than git history. Every Dockerfile, not just
        one -- a stale digest on any single one must still be caught."""
        import re as _re

        dockerfiles = sorted(TASKS_DIR.rglob("Dockerfile"))
        originals = {p: p.read_text(encoding="utf-8") for p in dockerfiles}
        try:
            for p, text in originals.items():
                _write_lf_text(
                    p,
                    _re.sub(
                        r"# ABICHECK_RUNTIME_DIGEST=[0-9a-f]{64}",
                        "# ABICHECK_RUNTIME_DIGEST=" + "0" * 64,
                        text,
                    ),
                )
            assert gen.generate(check=True) is False
        finally:
            for p, text in originals.items():
                _write_lf_text(p, text)

    def test_check_still_catches_a_real_dockerfile_edit(self):
        """The normalization above must not swallow a genuine content
        change -- only the one line it's scoped to."""
        stray = TASKS_DIR / "removed-export" / "environment" / "Dockerfile"
        original = stray.read_text(encoding="utf-8")
        try:
            _write_lf_text(stray, original.replace("castxml", "castxml-evil"))
            assert gen.generate(check=True) is False
        finally:
            _write_lf_text(stray, original)

    @_SKIP_ON_WINDOWS
    def test_check_catches_a_lost_executable_bit(self):
        """A byte-identical `tests/test.sh` that lost its executable bit
        (e.g. a manual re-save) is unusable to Harbor, which executes it
        directly -- a bytes-only comparison would silently pass this
        (Codex review).

        Skipped on Windows (real CI evidence, PR #816): NTFS has no POSIX
        executable-bit concept, so `os.chmod`/`Path.stat().st_mode` there
        cannot actually clear or observe `0o111` -- `stray.chmod(original_
        mode & ~0o111)` is a silent no-op, `_diff_trees`'s executable-bit
        comparison then correctly finds nothing changed, and `--check`
        reports `True` (matching reality on that filesystem) instead of
        the `False` this test expects. Not a bug in `_diff_trees` or the
        generator -- there is no executable bit to lose on this platform in
        the first place."""
        stray = TASKS_DIR / "removed-export" / "tests" / "test.sh"
        original_mode = stray.stat().st_mode
        try:
            stray.chmod(original_mode & ~0o111)
            assert gen.generate(check=True) is False
        finally:
            stray.chmod(original_mode)

    def test_regeneration_is_idempotent(self):
        """Same normalization `--check` applies, and for the identical
        reason: the tree on disk (a checkout of the *last* commit that ran
        this generator) necessarily pins the SHA of *that commit's own
        parent* -- the SHA is computed before the commit that carries it
        exists -- while a live `generate()` call right now pins the
        *current* `HEAD`, which is that later commit itself. Comparing raw
        bytes therefore fails on every fresh checkout whose tip commit
        touched the generator or its output, which is every commit that
        ever regenerates this tree -- caught by actually running this test
        immediately after a real commit, not by re-reading the assertion.

        `generate(check=False)` writes straight into the real, tracked
        `TASKS_DIR` (this test's whole point is exercising that exact call,
        not a copy of it) -- a `finally` restores every file's original
        bytes afterward, so running this test locally doesn't leave the
        real pinned-ref rewrite (correct, but noise) sitting in the
        working tree for a human to notice and have to discard (Codex
        review)."""
        raw_before = {
            p.relative_to(TASKS_DIR): p.read_bytes()
            for p in TASKS_DIR.rglob("*")
            if p.is_file()
        }
        try:
            before = {
                rel: gen._normalize_pinned_ref(data) for rel, data in raw_before.items()
            }
            gen.generate(check=False)
            after = {
                p.relative_to(TASKS_DIR): gen._normalize_pinned_ref(p.read_bytes())
                for p in TASKS_DIR.rglob("*")
                if p.is_file()
            }
            assert before == after
        finally:
            for rel, data in raw_before.items():
                (TASKS_DIR / rel).write_bytes(data)


class TestTaskStructure:
    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_every_task_has_the_five_required_entries(self, task_dir):
        assert (task_dir / "task.toml").is_file()
        assert (task_dir / "instruction.md").is_file()
        assert (task_dir / "environment" / "Dockerfile").is_file()
        assert (task_dir / "tests" / "test.sh").is_file()
        assert (task_dir / "solution" / "solve.sh").is_file()

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_every_task_has_a_separate_verifier_dockerfile(self, task_dir):
        """The verifier's own image -- built from `tests/`, not
        `environment/` (confirmed against the real `harbor` package's
        `Trial._verifier_env_build_context()`) -- must exist alongside
        `task.toml`'s `[verifier].environment_mode = "separate"` declaration,
        or Harbor has nothing to build the separate container from."""
        assert (task_dir / "tests" / "Dockerfile").is_file()
        parsed = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        assert parsed["verifier"]["environment_mode"] == "separate"

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_task_toml_parses_and_names_the_task(self, task_dir):
        parsed = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        assert parsed["task"]["name"] == f"abicheck/{task_dir.name}"
        assert parsed["schema_version"] == "1.4"
        # An agent-arm run must be able to opt out of runtime network --
        # confirms the generator's own no-network default landed, not a
        # public-network fallback nobody would notice went missing.
        assert parsed["environment"]["network_mode"] == "no-network"

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_generated_files_all_carry_the_ownership_marker(self, task_dir):
        # instruction.md is deliberately excluded: it is the agent's own
        # prompt text, and a "do not hand-edit" comment as its first line
        # would be part of what the agent reads, not a marker for a human
        # editor.
        for rel in ("task.toml", "README.md"):
            text = (task_dir / rel).read_text(encoding="utf-8")
            assert "GENERATED FILE" in text.splitlines()[0], rel
        assert (
            "GENERATED FILE"
            in (task_dir / "environment" / "Dockerfile")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        assert (
            "GENERATED FILE"
            in (task_dir / "tests" / "Dockerfile")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_scenario_json_is_scoped_to_tests_not_environment(self, task_dir):
        """The ground truth must never land where the agent can read it."""
        assert (task_dir / "tests" / "scenario.json").is_file()
        assert not (task_dir / "environment" / "scenario.json").exists()
        assert not (task_dir / "environment" / "workspace" / "scenario.json").exists()

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_agent_image_never_bakes_in_the_skill(self, task_dir):
        """Every agent image used to `cp -r` the skill into `/opt/skills/`
        unconditionally, regardless of which arm the trial actually
        requested -- so a baseline agent, with the same ordinary shell
        access to its own container every other finding in `_dockerfile()`
        already documents, could discover and manually follow the treatment,
        contaminating the one arm this corpus exists to keep clean of it
        (Codex review, fresh evidence). Fixed by dropping the bake-in step
        entirely: the skill is now injected per-trial by Harbor's own
        `--skill` mechanism (`Trial._upload_injected_skills()`, confirmed
        against the real, installed `harbor` package's source to run
        entirely outside the Docker build, after the image has already
        started), so no *instruction* in either arm's built image may
        reference `/opt/skills` or the skill's own name at all -- only this
        step's own explanatory comment, checked separately below, is
        allowed to name it as history."""
        dockerfile = (task_dir / "environment" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        instruction_lines = "\n".join(
            line
            for line in dockerfile.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        assert "/opt/skills" not in instruction_lines
        assert "check-abi-compatibility" not in instruction_lines

    def test_no_task_leaks_the_tool_name_or_a_verdict_word(self):
        """The exact `workspace_leaks` scan the existing harness runs before
        any model call -- reused, not re-implemented, against every
        generated task's agent-visible workspace."""
        from runners.claude_code import workspace_leaks

        leaky = {
            task_dir.name: workspace_leaks(task_dir / "environment" / "workspace")
            for task_dir in _task_dirs()
        }
        leaky = {k: v for k, v in leaky.items() if v}
        assert leaky == {}


class TestReadmeCommandParsing:
    def test_extracts_the_documented_compare_operands(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\n## abicheck command\n\n```bash\n"
            "gcc -shared -fPIC -g v1.c -o libfoo_v1.so\n"
            "gcc -shared -fPIC -g v2.c -o libfoo_v2.so\n"
            "abicheck compare libfoo_v1.so libfoo_v2.so\n"
            "```\n",
            encoding="utf-8",
        )
        result = gen._readme_abicheck_command(case)
        assert result is not None
        block, run_line = result
        assert run_line == "abicheck compare libfoo_v1.so libfoo_v2.so"
        assert "abicheck compare" in block

    def test_none_when_the_section_is_absent(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\nNo section here.\n", encoding="utf-8"
        )
        assert gen._readme_abicheck_command(case) is None

    def test_none_when_the_block_has_no_bare_compare_line(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\n## abicheck command\n\n```bash\nabicheck scan --against x.so\n```\n",
            encoding="utf-8",
        )
        assert gen._readme_abicheck_command(case) is None

    def test_a_line_continued_command_with_extra_flags_and_an_env_prefix(
        self, tmp_path
    ):
        """case124's own shape: an env-var prefix, and trailing flags the
        verdict actually depends on (--header), wrapped across a
        backslash-continued line -- the exact shape the bare-form-only
        parser this test class used to pin could not read at all (regressed
        to the Category-B "no reference solution" stub for a real Category A
        scenario)."""
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\n## abicheck command\n\n```bash\n"
            "g++ -shared -fPIC -g v1.cpp -o libaudio_v1.so\n"
            "g++ -shared -fPIC -g v2.cpp -o libaudio_v2.so\n"
            "ABICHECK_AST_FRONTEND=clang abicheck compare libaudio_v1.so libaudio_v2.so \\\n"
            "    --header old=v1.h --header new=v2.h\n"
            "```\n",
            encoding="utf-8",
        )
        result = gen._readme_abicheck_command(case)
        assert result is not None
        block, run_line = result
        assert run_line == (
            "ABICHECK_AST_FRONTEND=clang abicheck compare libaudio_v1.so "
            "libaudio_v2.so --header old=v1.h --header new=v2.h"
        )
        assert "g++ -shared" in block


@_SKIP_ON_WINDOWS
class TestPythonInterposer:
    """The agent Dockerfile's Python-interposer install step must intercept
    every name the real interpreter is reachable under, not just `python`/
    `python3` -- `python:3.13-slim` makes both of those symlinks through to
    the one real binary, `python3.13`, and a `python3.13 -m abicheck`
    invocation is a real, unmodified CPython spelling an agent can use.
    Exercised by actually running the Dockerfile's own extracted RUN body
    (only the literal `/usr/local/bin` prefix substituted for a synthetic
    directory -- the shell logic itself is untouched) against a synthetic
    directory reproducing the base image's exact symlink layout, not by
    reading the Dockerfile text."""

    def test_python_dot_version_invocation_is_also_intercepted(self, tmp_path):
        import os

        dockerfile = (
            TASKS_DIR / "removed-export" / "environment" / "Dockerfile"
        ).read_text(encoding="utf-8")
        start = dockerfile.index('RUN resolved="$(readlink -f')
        end = dockerfile.index("\nENV SKILL_EVAL_REAL_PYTHON", start)
        run_block = dockerfile[start:end]
        assert run_block.startswith("RUN ")
        # Backslash-newline is valid bash line continuation as-is -- no
        # rewriting needed, only the `RUN ` prefix stripped.
        shell_body = run_block[len("RUN ") :]

        fakebin = tmp_path / "usr" / "local" / "bin"
        fakebin.mkdir(parents=True)
        real = fakebin / "python3.13"
        real.write_text("#!/bin/sh\necho REAL_PYTHON_RAN\n", encoding="utf-8")
        real.chmod(0o755)
        (fakebin / "python3").symlink_to("python3.13")
        (fakebin / "python").symlink_to("python3")

        script = shell_body.replace("/usr/local/bin", _bash_path(fakebin))
        env = {**os.environ, "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}"}
        proc = subprocess.run(  # noqa: S603
            [bash_executable(), "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

        # Every name the real interpreter used to be reachable under --
        # including the versioned one a previous version of this fix left
        # unintercepted -- must now run the interposer instead.
        for name in ("python3.13", "python3", "python"):
            out = subprocess.run(  # noqa: S603
                [str(fakebin / name)], capture_output=True, text=True, timeout=10
            )
            assert "REAL_PYTHON_RAN" not in out.stdout, name

        # And the renamed original binary must still be the real interpreter
        # -- the interposer's own `exec "$SKILL_EVAL_REAL_PYTHON"` fallback
        # depends on it.
        real_renamed = fakebin / "python3.13-real"
        assert real_renamed.is_file()
        out = subprocess.run(  # noqa: S603
            [str(real_renamed)], capture_output=True, text=True, timeout=10
        )
        assert "REAL_PYTHON_RAN" in out.stdout


@_SKIP_ON_WINDOWS
class TestArchitectureGuard:
    """`_test_sh`'s runtime `uname -m` guard for a scenario declaring
    `architectures` (e.g. `evidence-too-shallow`, which embeds an x86_64
    prebuilt artifact) -- exercised for real, not mocked, since a first
    version of this guard built its `python3 -c` payload with
    `json.dumps()` (double-quoted strings) inside an outer bash
    double-quoted string, which silently truncated the argument at the
    first unescaped `"` and fed the shell interpreter's own bareword back
    in as literal (unquoted) Python source -- `NameError: name 'x86_64' is
    not defined`, reproducible only by actually running the generated
    script, never by reading it.
    """

    def _run_with_fake_uname(self, tmp_path, task_id, reported_arch):
        task_dir = TASKS_DIR / task_id
        test_sh = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
        logs = tmp_path / "logs"
        script = tmp_path / "test.sh"
        script.write_text(
            test_sh.replace("/logs/verifier", _bash_path(logs)), encoding="utf-8"
        )
        script.chmod(0o755)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_uname = bin_dir / "uname"
        fake_uname.write_text(f"#!/bin/sh\necho {reported_arch}\n", encoding="utf-8")
        fake_uname.chmod(0o755)

        import os

        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        proc = subprocess.run(  # noqa: S603
            [bash_executable(), str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc, logs

    def test_mismatched_architecture_exits_nonzero_without_writing_a_reward(
        self, tmp_path
    ):
        """No reward file at all -- verified against the real `harbor`
        package's own `Verifier.verify()` (not guessed): with neither
        `reward.txt` nor `reward.json` present, it raises
        `RewardFileNotFoundError`, which `TrialResult` records as
        `exception_info` on a trial whose `verifier_result` stays `None` --
        structurally distinct from a real, scored 0. A written `reward=0`
        (the guard's first version) would count an environment mismatch as
        a failed agent trial in every arm on a non-x86_64 host, depressing
        aggregate scores (Codex review, fresh evidence, second round)."""
        proc, logs = self._run_with_fake_uname(
            tmp_path, "evidence-too-shallow", "aarch64"
        )
        assert proc.returncode == 1
        assert not logs.exists() or not any(logs.iterdir())
        assert "architecture_mismatch" in proc.stderr
        assert "arm64" in proc.stderr
        assert "x86_64" in proc.stderr

    def test_matching_architecture_falls_through_to_the_real_verifier(self, tmp_path):
        # Falls through past the guard and fails only because
        # /opt/abicheck-src doesn't exist on this bare host -- confirming
        # the guard did NOT short-circuit, not that the full verifier ran.
        proc, logs = self._run_with_fake_uname(
            tmp_path, "evidence-too-shallow", "x86_64"
        )
        assert not (logs / "reward.json").is_file()
        assert "verify_run.py" in proc.stderr

    def test_a_scenario_with_no_declared_architectures_has_no_guard(self):
        test_sh = (TASKS_DIR / "removed-export" / "tests" / "test.sh").read_text(
            encoding="utf-8"
        )
        assert "architecture_mismatch" not in test_sh


@pytest.mark.integration
class TestSolveScriptsEndToEnd:
    """Real execution: `solve.sh` -> the recording shim -> `verify_run.py`.

    Marked `integration` (CodeRabbit review, PR #816): this class shells out
    to bash/gcc/abicheck via `subprocess` for real, same as any other
    `integration`-marked test in this repo (tests/CLAUDE.md: "Mark tests
    that shell out ... with the matching marker so default runs stay
    fast"). It previously had only a `skipif`, which does gate a *missing*
    toolchain but does not exclude the class from the default fast lane on
    a host that happens to have gcc/abicheck on PATH already (e.g. plain
    ubuntu-latest CI runners, or a contributor's own dev machine) --
    `.github/workflows/ci.yml`'s `integration-tests` job already runs
    `pytest -m integration` with castxml/gcc installed explicitly, so this
    class now runs there instead of silently riding along in the
    unit-tests job's `-m "not integration and ..."` fast lane.

    Skipped without gcc/abicheck on PATH -- the same precondition the
    existing harness's own `missing_toolchain()` gates on, not a new one.

    Also skipped on macOS specifically: every generated `solve.sh` runs the
    scenario's documented command verbatim, and every one of these six
    scenarios' documented commands is a plain, headerless
    ``abicheck compare a.so b.so`` -- no ``-H``. `docs/reference/platforms.md`
    ("What 'No Headers' Actually Means") documents this as a real,
    deliberate, pre-existing platform gap, not something introduced here:
    unlike ELF (DWARF read directly from the binary) or PE (via a `.pdb`),
    `_dump_macho` has no Mach-O debug-map/dSYM reader at all, so a headerless
    Mach-O comparison is **always L0 (exports/load-commands) only**,
    regardless of whether the `.dylib` carries `-g` debug info -- and on
    macOS the linker doesn't even embed DWARF in the linked binary by
    default (it leaves `N_OSO` stabs pointing at the now-discarded `.o`
    files; `dsymutil` would be needed to consolidate a `.dSYM`, which none
    of these reference solutions run). A real CI run confirmed the
    consequence directly (macos-latest `unit-tests`, PR #816): the
    `changed-signature`/`enum-value-change`/`struct-layout-drift`/
    `vtable-change` scenarios all need type/layout facts an L0-only compare
    structurally cannot see, and reported false-negative verdicts one this
    codebase's own docs already say to expect on this platform+input
    combination. Closing it for real needs a genuine Mach-O debug-map/dSYM
    reader -- its own scoped feature, not a same-PR patch -- so this class
    is skipped on Darwin entirely rather than guessing which subset of the
    six might coincidentally still pass.

    Also skipped on Windows, for the identical structural reason: a
    MinGW-built DLL's ``-g`` debug info is DWARF embedded in the PE, and
    abicheck's PE debug reader (`pdb_metadata.py`) only understands PDB
    (`/Zi`, MSVC), so a headerless MinGW-built compare is L0-only too --
    confirmed against real Windows CI (`windows-latest` `unit-tests`,
    PR #816), which still failed the same four scenarios after the
    unrelated Git-Bash path-mangling bug this same test module also fixes
    was resolved.
    """

    @pytest.fixture(autouse=True)
    def _require_toolchain(self):
        if sys.platform == "darwin":
            pytest.skip(
                "headerless Mach-O compare is L0-only, always (no debug-map/"
                "dSYM reader) -- these reference solutions run no -H, so "
                "the DWARF/layout-dependent scenarios structurally can't "
                "grade correctly here; see docs/reference/platforms.md"
            )
        if sys.platform == "win32":
            # Real Windows CI evidence (PR #816, after the Git-Bash path
            # fix landed): 4 of 6 scenarios (changed-signature/
            # enum-value-change/struct-layout-drift/vtable-change) still
            # fail with the identical false-negative verdict shape as the
            # macOS Mach-O gap above -- a MinGW-built DLL's -g debug info
            # is DWARF embedded in the PE, but abicheck's PE debug reader
            # (pdb_metadata.py) only understands PDB, so a headerless
            # compare of these MinGW-built binaries is L0-only too, same
            # root cause as Mach-O, different container format. The other
            # two (removed-export/compatible-addition) do pass now, but
            # skipping the whole class rather than guessing which subset
            # keeps passing, matching the macOS skip's own discipline.
            pytest.skip(
                "headerless PE compare only reads PDB debug info, never "
                "MinGW's DWARF-in-PE -- same L0-only gap as macOS Mach-O "
                "above; see docs/reference/platforms.md"
            )
        if shutil.which("gcc") is None or shutil.which("abicheck") is None:
            pytest.skip("gcc and/or abicheck not on PATH")

    @pytest.mark.parametrize(
        "scenario_id",
        [
            "removed-export",
            "compatible-addition",
            "changed-signature",
            "enum-value-change",
            "struct-layout-drift",
            "vtable-change",
            "constant-value-changed",
            "runtime-floor-raised",
        ],
    )
    def test_reference_solution_grades_correct(self, tmp_path, scenario_id):
        task_dir = TASKS_DIR / scenario_id
        if not (task_dir / "solution" / "solve.sh").is_file():
            pytest.skip(f"{scenario_id} has no generated solve.sh")
        solve_text = (task_dir / "solution" / "solve.sh").read_text(encoding="utf-8")
        if "exit 1" in solve_text and "abicheck compare" not in solve_text:
            pytest.skip(f"{scenario_id}'s solve.sh is an intentional stub")

        workspace = tmp_path / "workspace"
        shutil.copytree(
            task_dir / "environment" / "workspace" / "library", workspace / "library"
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        real_abicheck = shutil.which("abicheck")
        shim = bin_dir / "abicheck"
        shutil.copy2(EVAL_DIR / "shim" / "abicheck", shim)
        shim.chmod(0o755)

        local_solve = tmp_path / "solve_local.sh"
        local_solve.write_text(
            solve_text.replace(
                "/workspace/library", _bash_path(workspace / "library")
            ).replace("/workspace/final.md", _bash_path(workspace / "final.md")),
            encoding="utf-8",
        )

        import os

        env = {
            **os.environ,
            "SKILL_EVAL_CALLS": str(workspace / "calls.jsonl"),
            "SKILL_EVAL_REAL_ABICHECK": real_abicheck,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        }
        proc = subprocess.run(  # noqa: S603
            [bash_executable(), str(local_solve)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert (workspace / "final.md").is_file()

        pack = json.loads(
            (EVAL_DIR / "skill-eval-pack.json").read_text(encoding="utf-8")
        )
        scenario_path = tmp_path / "scenario.json"
        scenario_path.write_text(
            json.dumps(pack["scenarios"][scenario_id]), encoding="utf-8"
        )

        reward_txt = tmp_path / "reward.txt"
        reward_json = tmp_path / "reward.json"
        verify_env = {**env, "HARBOR_GRADERS_ROOT": str(EVAL_DIR)}
        verify_proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(EVAL_DIR / "harbor" / "verify_run.py"),
                "--workspace",
                str(workspace),
                "--scenario",
                str(scenario_path),
                "--reward-txt",
                str(reward_txt),
                "--reward-json",
                str(reward_json),
            ],
            env=verify_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert verify_proc.returncode == 0, verify_proc.stderr
        assert reward_txt.read_text(encoding="utf-8").strip() == "1", json.loads(
            reward_json.read_text(encoding="utf-8")
        )


class TestHarborSchemaValidation:
    """Every generated task validates against the real `harbor` package's
    own `Task`/`TaskConfig` Pydantic models -- not a hand-guessed schema,
    and not merely `task.toml` parsing as plain TOML (`TestTaskStructure`
    above already covers that): `Task.__init__` additionally runs Harbor's
    own `_validate_tests` (verifier/agent/solution shape, OS selection)
    against `TaskConfig.model_validate_toml`'s parsed result.

    `harbor` needs Python >=3.12 and is not a repository dependency (it
    exists to validate output *for* Harbor, not to be one), so this
    self-skips via `pytest.importorskip` when it isn't installed --
    deliberately not a new pytest marker, per tests/CLAUDE.md's "don't
    change the marker scheme". CI installs it and runs this class as one
    dedicated, best-effort step (`.github/workflows/ci.yml`'s
    `harbor-schema` step) rather than folding it into an existing marker
    lane that assumes a different toolchain (castxml/gcc, not a PyPI
    package) and a different Python floor (this repo's own canonical 3.13
    happens to satisfy `harbor`'s floor, but nothing pins that relationship
    -- an explicit, separate install keeps the two floors independent).
    """

    @pytest.fixture(autouse=True)
    def _harbor(self):
        # `agent-evals/skills/harbor/` (this repo's own generated-task tree)
        # is itself an implicit PEP 420 namespace package named `harbor`,
        # and this module's own `sys.path.insert(0, str(EVAL_DIR))` above
        # puts it ahead of site-packages -- `pytest.importorskip("harbor")`
        # alone resolves to *that* directory when the real package isn't
        # installed (bare `import harbor` succeeds either way) and only
        # fails later, as a genuine error rather than a clean skip, the
        # first time something reaches for a real submodule. Importing the
        # actual submodule this class needs sidesteps the ambiguity
        # entirely: a namespace package has no `models` submodule, so this
        # raises (and `importorskip` turns into a skip) exactly when the
        # real package is absent, and resolves correctly to the real,
        # installed `harbor` when it's present (PEP 420: a regular package
        # -- one with `__init__.py`, which the real one has and the local
        # shadow doesn't -- wins over a namespace package regardless of
        # which comes first on `sys.path`, confirmed directly against a
        # real installed `harbor`).
        return pytest.importorskip("harbor.models.task.task")

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_task_validates_against_the_real_harbor_schema(self, task_dir, _harbor):
        task = _harbor.Task(task_dir)
        assert task.name == f"abicheck/{task_dir.name}"
