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

"""A strict, scripted stand-in for external-process invocation.

The permissive shape this replaces, which recurs across the subprocess-facing
tests (Bazel queries, compiler discovery, castxml/clang probes)::

    def fake_run(cmd, **kw):
        if "aquery" in cmd:
            return _FakeProc(0, stdout=AQUERY_JSON)
        return _FakeProc(0, stdout=CQUERY_JSON)   # <- catch-all

A catch-all branch answers *any* command, so the test cannot detect a call that
should not have happened, a call that never happened, two calls in the wrong
order, a dropped argument, or a fallback path quietly reusing the previous
command's fixture. Each of those is a wiring bug the test was written to
prevent, and each passes.

This runner is scripted instead: every invocation must match the next
expectation, an unmatched invocation fails immediately, and
:meth:`StrictProcessRunner.assert_exhausted` fails if a scripted call never
happened. Failures carry the whole transcript, so the message says what the
code actually ran, not just that something did not match.

Usage::

    runner = StrictProcessRunner()
    runner.expect(argv_contains=["bazel", "aquery"], returns=proc(stdout=AQUERY))
    runner.expect(argv_contains=["bazel", "cquery"], returns=proc(stdout=CQUERY))
    runner.install(monkeypatch, build_query.deadline, "run_bounded")
    ...
    runner.assert_exhausted()
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def proc(
    stdout: str = "", stderr: str = "", returncode: int = 0, args: Sequence[str] = ()
) -> subprocess.CompletedProcess[str]:
    """A real :class:`subprocess.CompletedProcess`, so tests exercise the same
    attribute surface production code reads."""
    return subprocess.CompletedProcess(
        args=list(args), returncode=returncode, stdout=stdout, stderr=stderr
    )


@dataclass(frozen=True)
class Invocation:
    """One recorded call, for the transcript."""

    argv: tuple[str, ...]
    cwd: str | None
    kwargs: Mapping[str, Any]

    def render(self) -> str:
        cwd = f"  (cwd={self.cwd})" if self.cwd else ""
        return " ".join(self.argv) + cwd


@dataclass
class Expectation:
    """One scripted call.

    Exactly one argv matcher is used, in this precedence: ``argv`` (exact),
    ``argv_prefix``, ``argv_contains`` (every token present, order-free), then
    ``argv_matches`` (a predicate). Matching is deliberately explicit — an
    expectation that matches nothing in particular is the permissive mock this
    class exists to replace.
    """

    argv: Sequence[str] | None = None
    argv_prefix: Sequence[str] | None = None
    argv_contains: Sequence[str] | None = None
    argv_matches: Callable[[tuple[str, ...]], bool] | None = None
    cwd: str | Path | None = None
    env_contains: Mapping[str, str] | None = None
    returns: Any = None
    raises: BaseException | None = None
    label: str = ""
    _consumed: bool = field(default=False, repr=False)

    def describe(self) -> str:
        if self.label:
            return self.label
        for name in ("argv", "argv_prefix", "argv_contains"):
            value = getattr(self, name)
            if value is not None:
                return f"{name}={list(value)}"
        if self.argv_matches is not None:
            return f"argv_matches={getattr(self.argv_matches, '__name__', 'predicate')}"
        return "<no matcher>"

    def why_not(self, inv: Invocation, kwargs: Mapping[str, Any]) -> str | None:
        """``None`` if this expectation matches, else a human-readable reason."""
        if self.argv is not None:
            if tuple(self.argv) != inv.argv:
                return f"argv is not exactly {list(self.argv)}"
        elif self.argv_prefix is not None:
            n = len(self.argv_prefix)
            if inv.argv[:n] != tuple(self.argv_prefix):
                return f"argv does not start with {list(self.argv_prefix)}"
        elif self.argv_contains is not None:
            missing = [t for t in self.argv_contains if t not in inv.argv]
            if missing:
                return f"argv is missing token(s) {missing}"
        elif self.argv_matches is not None:
            if not self.argv_matches(inv.argv):
                return "argv predicate returned False"
        else:
            raise AssertionError(
                "Expectation has no argv matcher — a catch-all expectation is "
                "exactly the permissive mock StrictProcessRunner replaces."
            )

        if self.cwd is not None and str(self.cwd) != str(inv.cwd):
            return f"cwd is {inv.cwd!r}, expected {str(self.cwd)!r}"

        if self.env_contains:
            env = kwargs.get("env") or {}
            for key, value in self.env_contains.items():
                if key not in env:
                    return f"env is missing {key!r}"
                if env[key] != value:
                    return f"env[{key!r}] is {env[key]!r}, expected {value!r}"
        return None


class UnexpectedProcessCall(AssertionError):
    """Raised for a call the script did not allow."""


class StrictProcessRunner:
    """Scripted replacement for a subprocess-running callable.

    Ordered by default: the *n*-th call must match the *n*-th expectation.
    Pass ``ordered=False`` when the production code genuinely has no defined
    ordering, in which case a call matches the first still-unconsumed
    expectation — still strict about *which* calls happen and how many.
    """

    def __init__(self, *, ordered: bool = True) -> None:
        self.ordered = ordered
        self.expectations: list[Expectation] = []
        self.calls: list[Invocation] = []

    # -- scripting ---------------------------------------------------------

    def expect(self, **kwargs: Any) -> StrictProcessRunner:
        """Append one scripted call. Chainable."""
        self.expectations.append(Expectation(**kwargs))
        return self

    def install(self, monkeypatch: Any, target: Any, attr: str) -> StrictProcessRunner:
        """Patch ``target.attr`` with this runner."""
        monkeypatch.setattr(target, attr, self)
        return self

    # -- invocation --------------------------------------------------------

    def __call__(self, cmd: Sequence[str], *args: Any, **kwargs: Any) -> Any:
        argv = tuple(str(part) for part in cmd)
        cwd = kwargs.get("cwd")
        inv = Invocation(
            argv=argv, cwd=None if cwd is None else str(cwd), kwargs=kwargs
        )
        self.calls.append(inv)

        candidates = [e for e in self.expectations if not e._consumed]
        if not candidates:
            raise UnexpectedProcessCall(
                self._message(
                    f"unexpected call — the script had no calls left:\n  {inv.render()}"
                )
            )

        if self.ordered:
            head = candidates[0]
            reason = head.why_not(inv, kwargs)
            if reason is not None:
                raise UnexpectedProcessCall(
                    self._message(
                        f"call {len(self.calls)} does not match the next scripted "
                        f"call.\n  ran:      {inv.render()}\n  expected: "
                        f"{head.describe()}\n  reason:   {reason}"
                    )
                )
            matched = head
        else:
            matched = None
            reasons = []
            for candidate in candidates:
                reason = candidate.why_not(inv, kwargs)
                if reason is None:
                    matched = candidate
                    break
                reasons.append(f"    {candidate.describe()}: {reason}")
            if matched is None:
                joined = "\n".join(reasons)
                raise UnexpectedProcessCall(
                    self._message(
                        f"call {len(self.calls)} matched no remaining scripted "
                        f"call.\n  ran: {inv.render()}\n  candidates:\n{joined}"
                    )
                )

        matched._consumed = True
        if matched.raises is not None:
            raise matched.raises
        result = matched.returns
        if callable(result):
            return result(argv, kwargs)
        return result

    # -- verification ------------------------------------------------------

    def assert_exhausted(self) -> None:
        """Fail if any scripted call never happened.

        The half a permissive mock cannot check: a code path that silently
        stopped issuing a command still satisfies a mock that only answers
        questions it is asked.
        """
        pending = [e for e in self.expectations if not e._consumed]
        if pending:
            missing = "\n".join(f"    {e.describe()}" for e in pending)
            raise AssertionError(
                self._message(
                    f"{len(pending)} scripted call(s) never happened:\n{missing}"
                )
            )

    def assert_call_count(self, expected: int) -> None:
        if len(self.calls) != expected:
            raise AssertionError(
                self._message(f"expected {expected} call(s), saw {len(self.calls)}")
            )

    # -- diagnostics -------------------------------------------------------

    def transcript(self) -> str:
        if not self.calls:
            return "    (no commands were run)"
        return "\n".join(f"    {i + 1}. {c.render()}" for i, c in enumerate(self.calls))

    def _message(self, headline: str) -> str:
        return f"{headline}\n\n  transcript of all commands run:\n{self.transcript()}"
