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

"""``scripts/perf_antipatterns.py``: each rule fires on its shape, stays
quiet on the near-miss that is *not* that shape, and the baseline compare
behaves as a ratchet.

The near-misses are the point: a rule that cannot tell ``x in a_set`` from
``x in a_list``, or a once-evaluated iterable from a per-element one, would
bury the real sites in noise and get its baseline re-recorded blindly.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from perf_antipatterns import (  # noqa: E402
    Site,
    compare_to_baseline,
    load_baseline,
    scan_source,
    scan_tree,
    site_counts,
)


def _rules(src: str) -> list[str]:
    return [s.rule for s in scan_source(textwrap.dedent(src), "abicheck/x.py")]


@pytest.mark.parametrize(
    ("src", "rule"),
    [
        (
            "def f(xs):\n    seen = []\n    for x in xs:\n        if x not in seen:\n            seen.append(x)\n",
            "list-membership-in-loop",
        ),
        (
            "def f(xs):\n    keep = sorted(xs)\n    return [x for x in xs if x in keep]\n",
            "list-membership-in-loop",
        ),
        (
            "def f(xs):\n    order = list(xs)\n    for x in xs:\n        order.index(x)\n",
            "list-membership-in-loop",
        ),
        (
            "import re\ndef f(ps, s):\n    for p in ps:\n        re.compile(p).search(s)\n",
            "regex-compile-in-loop",
        ),
        (
            "import json\ndef f(lines):\n    return [json.loads(l) for l in lines]\n",
            "parse-or-copy-in-loop",
        ),
        (
            "import copy\ndef f(xs):\n    while xs:\n        copy.deepcopy(xs.pop())\n",
            "parse-or-copy-in-loop",
        ),
        (
            "def f(xs):\n    acc = []\n    for x in xs:\n        acc = acc + [x]\n",
            "quadratic-accumulation",
        ),
        (
            "def f(xs):\n    acc = []\n    for x in xs:\n        acc = [*acc, x]\n",
            "quadratic-accumulation",
        ),
        (
            "def f(xs):\n    acc = {}\n    for k in xs:\n        acc = {**acc, k: 1}\n",
            "quadratic-accumulation",
        ),
        (
            "import subprocess\ndef f(xs):\n    for x in xs:\n        subprocess.run(['echo', x])\n",
            "subprocess-in-loop",
        ),
        (
            "def f(xs, pool):\n    for x in xs:\n        y = sorted(pool)\n",
            "sort-in-loop",
        ),
        ("def f(xs, pool):\n    for x in xs:\n        pool.sort()\n", "sort-in-loop"),
        # a while condition's walrus does not make an unrelated name vary
        (
            "def f(read, pool):\n    while (chunk := read()):\n        y = sorted(pool)\n",
            "sort-in-loop",
        ),
        (
            "def f(xs):\n    out = ''\n    for x in xs:\n        out += x\n",
            "str-concat-in-loop",
        ),
        (
            "import copy\ndef f(xs, base):\n    for x in xs:\n        copy.copy(base)\n",
            "parse-or-copy-in-loop",
        ),
        (
            "import dataclasses\ndef f(xs, base):\n    for x in xs:\n        dataclasses.replace(base, v=x)\n",
            "parse-or-copy-in-loop",
        ),
        # quadratic-dedup: each element rescans the sequence it came from,
        # whatever that sequence is bound to (a parameter included)
        (
            "def f(seq):\n    return [x for i, x in enumerate(seq) if seq.index(x) == i]\n",
            "quadratic-dedup",
        ),
        (
            "def f(seq):\n    return [x for x in seq if seq.count(x) == 1]\n",
            "quadratic-dedup",
        ),
        (
            "def f(seq):\n    return [x for i, x in enumerate(seq) if x not in seq[:i]]\n",
            "quadratic-dedup",
        ),
        (
            "def f(seq):\n    return {x for i, x in enumerate(seq) if x not in seq[i + 1 :]}\n",
            "quadratic-dedup",
        ),
        (
            "class C:\n    def m(self):\n        return [x for i, x in enumerate(self.seq) if self.seq.index(x) == i]\n",
            "quadratic-dedup",
        ),
        (
            "def f(obj):\n    return [x for i, x in enumerate(obj.seq) if x not in obj.seq[:i]]\n",
            "quadratic-dedup",
        ),
        (
            "import pickle\ndef f(blobs):\n    return [pickle.loads(b) for b in blobs]\n",
            "parse-or-copy-in-loop",
        ),
        (
            "def f(xs, model):\n    for x in xs:\n        model.model_copy(deep=True)\n",
            "parse-or-copy-in-loop",
        ),
        (
            "import json\ndef f(xs):\n    return [json.loads(json.dumps(x)) for x in xs]\n",
            "parse-or-copy-in-loop",
        ),
        (
            "import urllib.request\ndef f(urls):\n    for u in urls:\n        urllib.request.urlopen(u)\n",
            "blocking-io-in-loop",
        ),
        (
            "import requests\ndef f(urls):\n    return [requests.get(u) for u in urls]\n",
            "blocking-io-in-loop",
        ),
        (
            "import time\ndef f(xs):\n    for x in xs:\n        time.sleep(0.1)\n",
            "blocking-io-in-loop",
        ),
    ],
)
def test_rule_fires_on_its_shape(src: str, rule: str) -> None:
    assert _rules(src) == [rule]


@pytest.mark.parametrize(
    "src",
    [
        # a set, not a list
        "def f(xs):\n    seen = set()\n    for x in xs:\n        if x not in seen:\n            seen.add(x)\n",
        # bound to a list *and* to something else: not provably a list
        "def f(xs, flag):\n    pool = []\n    if flag:\n        pool = set(xs)\n    for x in xs:\n        x in pool\n",
        # a parameter: unknown container
        "def f(xs, pool):\n    for x in xs:\n        x in pool\n",
        # membership outside any loop
        "def f(xs):\n    keep = list(xs)\n    return 3 in keep\n",
        # the outermost comprehension iterable is evaluated once
        "import json\ndef f(blob):\n    return [x for x in json.loads(blob)]\n",
        # a loop's own iterable is evaluated once
        "import re\ndef f(s):\n    for m in re.compile('a').finditer(s):\n        pass\n",
        # a nested def runs when called, not per iteration
        "import re\ndef f(xs):\n    for x in xs:\n        def g():\n            return re.compile(x)\n",
        # rebuilt each iteration, not accumulated
        "def f(cs):\n    for c in cs:\n        key = (c,)\n        key = key + (1,)\n",
        # in-place growth is amortized O(1)
        "def f(xs):\n    acc = []\n    for x in xs:\n        acc += [x]\n        acc.append(x)\n",
        # sorting each item's own small value, for deterministic output
        "def f(items):\n    for it in items:\n        ', '.join(sorted(it.names))\n",
        # the sorted collection changes every iteration
        "def f(xs):\n    acc = []\n    for x in xs:\n        acc.append(x)\n        top = sorted(acc)\n",
        # a while condition's walrus rebinds its target every iteration
        "def f(read):\n    while (chunk := read()):\n        sorted(chunk)\n",
        "import copy\ndef f(read):\n    while (chunk := read()):\n        copy.copy(chunk)\n",
        # sorted in a comprehension element is per-element by construction
        "def f(groups):\n    return [sorted(g) for g in groups]\n",
        # an error path runs at most once
        "def f(xs, known):\n    for x in xs:\n        if x not in known:\n            raise ValueError(sorted(known))\n",
        # += on a number is not string concatenation
        "def f(xs):\n    total = 0\n    for x in xs:\n        total += x\n",
        # each item copied once is linear
        "import dataclasses\ndef f(xs):\n    return [dataclasses.replace(x, v=1) for x in xs]\n",
        "import copy\ndef f(xs):\n    for x in xs:\n        copy.copy(x)\n",
        # dedup through a set / dict.fromkeys is linear
        "def f(seq):\n    return list(dict.fromkeys(seq))\n",
        # membership against a *different* parameter is not self-dedup
        "def f(seq, other):\n    return [x for x in seq if x in other]\n",
        # a different attribute (or the same attribute of another object) is not self-dedup
        "def f(obj):\n    return [x for x in obj.seq if x in obj.other]\n",
        "def f(a, b):\n    return [x for x in a.seq if b.seq.count(x) == 1]\n",
        # a shallow model_copy per item is linear
        "def f(xs):\n    return [x.model_copy() for x in xs]\n",
        # reading each local file once is the work, not blocking network I/O
        "def f(paths):\n    return [p.read_text() for p in paths]\n",
        # module-level hoisted compile
        "import re\nPAT = re.compile('x')\ndef f(xs):\n    return [PAT.match(x) for x in xs]\n",
    ],
)
def test_rule_stays_quiet_on_near_misses(src: str) -> None:
    assert _rules(src) == []


def test_sites_name_their_enclosing_function() -> None:
    (site,) = scan_source(
        "class C:\n    def m(self, xs):\n        out = []\n        for x in xs:\n            out = out + [x]\n",
        "abicheck/x.py",
    )
    assert site.function == "C.m"
    assert site.key == "abicheck/x.py::C.m::quadratic-accumulation"


def _site(n: int) -> list[Site]:
    return [Site("abicheck/x.py", "f", "regex-compile-in-loop", i) for i in range(n)]


def test_baseline_is_a_ratchet() -> None:
    key = "abicheck/x.py::f::regex-compile-in-loop"
    assert compare_to_baseline(_site(2), {key: 2}) == ([], [])
    errors, warnings = compare_to_baseline(_site(3), {key: 2})
    assert len(errors) == 1 and "baseline 2" in errors[0] and not warnings
    errors, warnings = compare_to_baseline(_site(1), {key: 2})
    assert not errors and len(warnings) == 1 and "shrink" in warnings[0]
    errors, _ = compare_to_baseline(_site(1), {})
    assert errors, "a site in a function with no baseline entry is new"


@pytest.mark.repo_scan
def test_committed_baseline_matches_the_tree() -> None:
    """The live tree has no site beyond the baseline and no stale entry."""
    sites = list(scan_tree())
    assert compare_to_baseline(sites, load_baseline()) == ([], [])
    assert site_counts(sites) == load_baseline()


# -- exemptions --------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        # trailing pragma with a reason
        """
        def f(lines):
            for line in lines:
                json.loads(line)  # perf-ok: JSON-lines input, one parse per record
        """,
        # own-line pragma covers the next statement, however it is wrapped
        """
        def f(lines):
            for line in lines:
                # perf-ok: JSON-lines input, one parse per record
                out = json.loads(
                    line
                )
        """,
        # trailing pragma a formatter moved to a wrapped statement's last line
        """
        def f(lines):
            for line in lines:
                out = json.loads(
                    line
                )  # perf-ok: JSON-lines input, one parse per record
        """,
        # a string the loop body rebinds unconditionally each iteration
        """
        def f(items):
            out = []
            for item in items:
                line = f"{item}"
                if item:
                    line += "!"
                out.append(line)
        """,
        # module-scope comprehension: runs once, at import
        """
        PATTERNS = tuple(re.compile(k) for k in ("a", "b"))
        """,
    ],
)
def test_exempted_shapes_are_quiet(src: str) -> None:
    assert _rules(src) == []


@pytest.mark.parametrize(
    ("src", "rule"),
    [
        # a pragma with no reason exempts nothing
        (
            """
            def f(lines):
                for line in lines:
                    json.loads(line)  # perf-ok:
            """,
            "parse-or-copy-in-loop",
        ),
        # an own-line pragma covers only the next statement
        (
            """
            def f(lines):
                for line in lines:
                    # perf-ok: JSON-lines input, one parse per record
                    a = json.loads(line)
                    b = json.loads(line)
            """,
            "parse-or-copy-in-loop",
        ),
        # rebinding only on some iterations: the string still accumulates
        (
            """
            def f(text):
                current = ""
                for ch in text:
                    if ch == ":":
                        current = ""
                    current += ch
            """,
            "str-concat-in-loop",
        ),
        # rebinding in an *outer* loop: the inner loop accumulates
        (
            """
            def f(rows):
                for row in rows:
                    line = ""
                    for cell in row:
                        line += cell
            """,
            "str-concat-in-loop",
        ),
        # pragma-like text inside a string literal is data, not a pragma
        (
            """
            def f(lines):
                for line in lines:
                    json.loads(line + "# perf-ok: expected parsing work")
            """,
            "parse-or-copy-in-loop",
        ),
        # an own-line pragma does not reach past a blank line
        (
            """
            def f(lines):
                for line in lines:
                    # perf-ok: JSON-lines input, one parse per record

                    json.loads(line)
            """,
            "parse-or-copy-in-loop",
        ),
        # a reset a `continue` can bypass does not make the string fresh
        (
            """
            def f(items):
                s = ""
                for x in items:
                    if x:
                        s += x
                        continue
                    s = ""
            """,
            "str-concat-in-loop",
        ),
        # a comprehension inside a function runs per call
        (
            """
            def f(names):
                return [re.compile(n) for n in names]
            """,
            "regex-compile-in-loop",
        ),
    ],
)
def test_exemptions_are_narrow(src: str, rule: str) -> None:
    assert _rules(src) == [rule]
