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

"""ABICC ``<skip_headers>``/``<skip_including>`` rules, compiled.

The previous implementation tested one thing: ``h.name in skip or str(h)
in skip``. A descriptor's tree-relative rule (``fftw/fftw.h``) therefore
matched nothing at all, because the resolved header path is absolute and
its basename is ``fftw.h`` -- so MKL's sixteen load-bearing skips excluded
zero headers while the run still produced a confident verdict.

Rewriting the rules to bare basenames would fix MKL and break the next
tree: two headers named ``version.h`` under different subdirectories are
two different headers, and a rule naming one of them must not take the
other.

abi-compliance-checker distinguishes **three rule classes**
(``Internals/Path.pm``'s ``classifyPath`` and ``Internals/Filter.pm``'s
``skipHeader_I``), and this module is the same classification made
explicit:

``name``
    No separator and no metacharacter -- matched against the header's
    *basename* (``mkl_direct_types.h``).
``path``
    Contains ``/`` or ``\\`` -- matched against the header's path at
    **component boundaries**, anywhere in it, including descendants. So
    ``fftw/fftw.h`` takes ``/opt/mkl/include/fftw/fftw.h`` and
    ``fftw/offload/`` takes everything beneath it, while ``foo/bar``
    never takes ``myfoo/bar``. A trailing separator makes the rule a
    directory: it matches descendants only, never a *file* of that name.
``pattern``
    Contains a metacharacter -- compiled as a regular expression (with
    shell ``*``/``?`` spelled the way a descriptor author writes them),
    matched against the basename, and additionally against the whole
    path when the pattern itself carries a separator.

The two elements are **not** the same rule and must not be merged:

``skip_headers``
    Do not include, and do not analyze. Really narrows the surface, so
    it is what gets recorded as this snapshot's achieved exclusion.
``skip_including``
    Do not *directly* include; a declaration still counts if another
    header reaches it. Changes how the surface is reached, not what it
    is, so it is not recorded as a narrowing.

See ``docs/contribute/known-gaps.md`` for the one part of
``skip_headers`` this cannot yet enforce (a header reached transitively
through another header's ``#include``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

#: ``<skip_headers>``: not included, not analyzed.
ACTION_EXCLUDE = "exclude"
#: ``<skip_including>``: not directly included, still analyzed when reached.
ACTION_DO_NOT_INCLUDE = "do_not_directly_include"

KIND_NAME = "name"
KIND_PATH = "path"
KIND_PATTERN = "pattern"

#: What makes a rule a *pattern* rather than a literal name or path. ``*``
#: and ``?`` are the shell spellings a descriptor author writes; the rest
#: are the regex constructs ABICC passes through to the Perl matcher.
_PATTERN_METACHARACTERS = frozenset("*?[]()|+^$")

_SEPARATORS = ("/", "\\")


def _normalize(text: str) -> str:
    """Windows separators folded onto ``/``; repeated separators collapsed.

    A descriptor written on Windows and one written on Linux name the same
    header, and the resolved path this is matched against is always in the
    host's own spelling -- so both sides are normalized rather than one.
    """
    out = text.replace("\\", "/")
    while "//" in out:
        out = out.replace("//", "/")
    return out


def _is_pattern(value: str) -> bool:
    return bool(_PATTERN_METACHARACTERS & set(value))


def _compile_pattern(value: str) -> re.Pattern[str]:
    """Compile a descriptor pattern, rejecting one that cannot compile.

    ``*`` and ``?`` are translated to their regex equivalents first (a
    descriptor author writes ``mkl_*_types.h``, not ``mkl_.*_types\\.h``);
    every other construct is passed through, which is what makes a real
    character class or alternation work. A bare ``*`` at the start would
    otherwise be an invalid quantifier, so the translation happens before
    compilation rather than after a failed attempt.
    """
    out: list[str] = []
    for ch in _normalize(value):
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(ch)
    source = "".join(out)
    try:
        return re.compile(source)
    except re.error as exc:
        raise ValidationError(
            f"descriptor skip rule {value!r} is not a valid pattern: {exc}"
        ) from None


@dataclass(frozen=True)
class HeaderSkipRule:
    """One compiled ``<skip_headers>``/``<skip_including>`` entry."""

    kind: str
    #: The rule as the descriptor spelled it, kept verbatim for reporting
    #: and for the snapshot's own exclusion record.
    value: str
    action: str
    #: ``path`` rules only: the normalized, separator-trimmed spelling.
    _normalized: str = ""
    #: ``path`` rules only: the rule named a directory (trailing separator).
    _directory: bool = False
    _regex: re.Pattern[str] | None = None

    def matches(self, header: Path | str) -> bool:
        """Whether *header* is taken by this rule."""
        path = _normalize(str(header))
        name = path.rsplit("/", 1)[-1]
        if self.kind == KIND_NAME:
            return name == self.value
        if self.kind == KIND_PATH:
            return _path_rule_matches(path, self._normalized, self._directory)
        assert self._regex is not None
        if self._regex.search(name):
            return True
        return bool(
            any(sep in self.value for sep in _SEPARATORS) and self._regex.search(path)
        )


def _path_rule_matches(path: str, rule: str, directory: bool) -> bool:
    """*rule*'s components occur consecutively in *path*, on boundaries.

    Not ``in``: a substring test takes ``myfoo/bar`` for ``foo/bar``, which
    is a different header in a different tree. Not a suffix test either: a
    descriptor names a tree-relative path (``fftw/fftw.h``) while the
    resolved operand is absolute, and a directory rule must additionally
    take everything beneath it.
    """
    if not rule:
        return False
    if not directory and path == rule:
        return True
    if not directory and path.endswith("/" + rule):
        return True
    # Descendants: the rule's last component must be followed by a
    # separator, so `fftw/offload` never takes a *file* named `offload`.
    return f"/{rule}/" in path or path.startswith(rule + "/")


def compile_skip_rule(value: str, action: str) -> HeaderSkipRule:
    """Compile one descriptor entry, classified the way ABICC classifies it."""
    text = value.strip()
    if not text:
        raise ValidationError("descriptor skip rule is empty")
    if _is_pattern(text):
        return HeaderSkipRule(
            kind=KIND_PATTERN, value=text, action=action, _regex=_compile_pattern(text)
        )
    if any(sep in text for sep in _SEPARATORS):
        normalized = _normalize(text)
        directory = normalized.endswith("/")
        return HeaderSkipRule(
            kind=KIND_PATH,
            value=text,
            action=action,
            _normalized=normalized.rstrip("/"),
            _directory=directory,
        )
    return HeaderSkipRule(kind=KIND_NAME, value=text, action=action)


def compile_skip_rules(
    skip_headers: Iterable[str] = (), skip_including: Iterable[str] = ()
) -> tuple[HeaderSkipRule, ...]:
    """Compile both descriptor elements into one ordered rule list.

    Order is ``skip_headers`` then ``skip_including`` so that when one
    header is taken by both, the stronger action (a real exclusion) is the
    one reported -- see :func:`match_skip_rule`.
    """
    rules = [compile_skip_rule(v, ACTION_EXCLUDE) for v in skip_headers]
    rules += [compile_skip_rule(v, ACTION_DO_NOT_INCLUDE) for v in skip_including]
    return tuple(rules)


def match_skip_rule(
    header: Path | str, rules: Sequence[HeaderSkipRule]
) -> HeaderSkipRule | None:
    """The first rule taking *header*, or ``None``.

    First rather than "any", because the caller needs the *action*: a
    header named by ``<skip_headers>`` is gone from the surface, and one
    named only by ``<skip_including>`` is not. :func:`compile_skip_rules`
    orders the two elements so the stronger action wins a tie.
    """
    for rule in rules:
        if rule.matches(header):
            return rule
    return None


def apply_skip_rules(
    headers: Sequence[Path], rules: Sequence[HeaderSkipRule]
) -> list[Path]:
    """*headers* minus every entry any rule takes.

    Both actions drop the header from the *direct* ``-H`` operand list --
    that is what ``skip_including`` means and what ``skip_headers``
    implies. They differ in what is recorded afterwards; see
    :func:`achieved_exclusion_patterns`.
    """
    return [h for h in headers if match_skip_rule(h, rules) is None]


def achieved_exclusion_patterns(
    rules: Sequence[HeaderSkipRule], headers: Sequence[Path] = ()
) -> tuple[str, ...]:
    """The rule spellings that really narrowed the analyzed surface.

    Two filters, and both are about the same invariant -- a snapshot
    records what a run *achieved*, never what it *requested*:

    * Only ``<skip_headers>``. A ``<skip_including>`` rule changes how a
      declaration is reached, not whether it is part of the contract, so
      recording it as an exclusion would make the snapshot claim a
      narrowing it did not perform -- and the comparability gate would then
      refuse an otherwise identical operand.
    * Only rules that took at least one of *headers*. A rule matching
      nothing narrowed nothing; recording it would make the coverage
      warning report headers omitted that are all still present, and would
      refuse an identical unexcluded snapshot. This generalizes the
      metacharacter filter it replaces (a pattern rule used to be unable
      to match *anything*, so dropping every metacharacter-bearing rule
      was the same test by a proxy that no longer holds).

    *headers* is the resolved header list the rules were applied to. It
    defaults to empty for callers that have none, in which case nothing is
    recorded -- the conservative direction: claiming no narrowing on a run
    that made one costs a refused comparison, claiming one it did not make
    manufactures findings.

    Sorted and de-duplicated, so the record does not depend on the order
    the descriptor happened to list its rules in.
    """
    return tuple(
        sorted(
            {
                r.value
                for r in rules
                if r.action == ACTION_EXCLUDE and any(r.matches(h) for h in headers)
            }
        )
    )
