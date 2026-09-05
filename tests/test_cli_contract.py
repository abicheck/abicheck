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

"""CLI interface-contract gate mirror + chokepoint parity (ADR-037 / G22 Phase 1).

This is the unit-test mirror of the ``cli-contract`` AI-readiness check
(``scripts/check_ai_readiness.py``), so the contract is enforced both as a fast
CI gate and in the regular test suite. It also pins the *behavioural* payoff of
the single chokepoint: ``compare-release`` and ``service.run_compare`` classify
a given pair identically (no ``scope_public`` default drift).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _cli_option_set_snapshot import OPTION_SET_SNAPSHOT as _OPTION_SET_SNAPSHOT

# Import the gate from scripts/ — the AI-readiness module is pure stdlib.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.serialization import save_snapshot  # noqa: E402
from scripts.check_ai_readiness import Findings, check_cli_contract  # noqa: E402

# ── D10.1: no front-end skips the Tier-2 service ─────────────────────────────


def test_no_tier_skip() -> None:
    """No ``abicheck/cli*.py`` module calls a Tier-1 core entry point
    (``checker.compare``, ``dumper.dump``, ``service.resolve_input``) directly
    outside the reviewed ``CLI_CONTRACT_ALLOWLIST``.

    Front-ends must route through the Tier-2 service layer (ADR-037 D1/D10.1;
    the latter two extend the identical rule per Phase 0 item 2 of
    docs/contribute/plans/duplication-and-convergence-assessment.md).
    """
    findings = Findings()
    check_cli_contract(findings)
    contract_errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert contract_errors == [], "Tier-1 call sites in front-ends:\n" + "\n".join(
        contract_errors
    )


# Each case plants one front-end module that reaches Tier-1 `checker.compare`
# a different way; the gate must flag exactly one violation naming that file.
# (filename, source) — covers: direct import, aliased lazy `compare` import,
# aliased `checker` *module* call, and the non-`cli*.py` `appcompat.py` scope.
_GATE_VIOLATION_CASES: list[pytest.ParameterSet] = [
    pytest.param(
        "cli_bad.py",
        "from .checker import compare\ndef go(a, b):\n    return compare(a, b)\n",
        id="direct-import",
    ),
    pytest.param(
        "cli_alias.py",
        "def go(a, b):\n"
        "    from .checker import compare as _compare\n"
        "    return _compare(a, b)\n",
        id="aliased-lazy-import",
    ),
    pytest.param(
        "cli_modalias.py",
        "from . import checker as core\ndef go(a, b):\n    return core.compare(a, b)\n",
        id="aliased-module-call",
    ),
    pytest.param(
        "appcompat.py",
        "from .checker import compare\ndef check(a, b):\n    return compare(a, b)\n",
        id="appcompat-in-scope",
    ),
]


@pytest.mark.parametrize("filename, source", _GATE_VIOLATION_CASES)
def test_gate_flags_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
) -> None:
    """The gate is not a no-op: each way of reaching Tier-1 is caught once."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert filename in errors[0]


def test_service_compare_call_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing through ``service.compare_snapshots`` must NOT be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_ok.py").write_text(
        "from .service import compare_snapshots\n"
        "def go(a, b):\n"
        "    return compare_snapshots(a, b)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


# ── Phase 0 item 2 of the convergence plan: `dumper.dump` / `service.resolve_input`
#    extend the identical Tier-1 rule ─────────────────────────────────────────

_EXTRA_TIER1_VIOLATION_CASES: list[pytest.ParameterSet] = [
    pytest.param(
        "cli_dumps_directly.py",
        "from .dumper import dump\ndef go(a):\n    return dump(a)\n",
        id="dumper-dump-direct-import",
    ),
    pytest.param(
        "cli_dumps_aliased.py",
        "from . import dumper as core\ndef go(a):\n    return core.dump(a)\n",
        id="dumper-dump-aliased-module-call",
    ),
    pytest.param(
        "cli_resolves_directly.py",
        "from .service import resolve_input\ndef go(a):\n    return resolve_input(a)\n",
        id="service-resolve-input-direct-import",
    ),
    pytest.param(
        "cli_resolves_aliased.py",
        "from . import service as svc\ndef go(a):\n    return svc.resolve_input(a)\n",
        id="service-resolve-input-aliased-module-call",
    ),
    pytest.param(
        "cli_dumps_qualified.py",
        "import abicheck.dumper\ndef go(a):\n    return abicheck.dumper.dump(a)\n",
        id="dumper-dump-unaliased-qualified-call",
    ),
    pytest.param(
        "cli_resolves_qualified.py",
        "import abicheck.service\ndef go(a):\n    return abicheck.service.resolve_input(a)\n",
        id="service-resolve-input-unaliased-qualified-call",
    ),
    pytest.param(
        "cli_resolves_package_alias.py",
        "import abicheck as abi\nimport abicheck.service\n"
        "def go(a):\n    return abi.service.resolve_input(a)\n",
        id="service-resolve-input-package-alias-qualified-call",
    ),
    pytest.param(
        "cli_resolves_package_alias_from_import.py",
        "import abicheck as abi\nfrom abicheck import service\n"
        "def go(a):\n    return abi.service.resolve_input(a)\n",
        id="service-resolve-input-package-alias-from-import-qualified-call",
    ),
]


@pytest.mark.parametrize("filename, source", _EXTRA_TIER1_VIOLATION_CASES)
def test_gate_flags_extra_tier1_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
) -> None:
    """`dumper.dump`/`service.resolve_input` are caught the same way
    `checker.compare` already is, by direct import and by aliased module call."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert filename in errors[0]


def test_nested_frontend_single_dot_relative_import_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`compat/cli.py` lives one package below the top level, so its own
    single-dot relative import (`from .service import ...`) names a
    *sibling* within `abicheck.compat`, not abicheck's own top-level
    `service` module — it must not be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    compat = pkg / "compat"
    compat.mkdir()
    (compat / "cli.py").write_text(
        "from .service import resolve_input\ndef go(a):\n    return resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


def test_relative_import_of_a_module_named_abicheck_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from .abicheck import service` is a *relative* import (level 1) that
    resolves to `abicheck.abicheck.service`, not the package root — it must
    not be mistaken for the absolute `from abicheck import service` form."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_weird.py").write_text(
        "from .abicheck import service\ndef go(a):\n    return service.resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


def test_nested_frontend_correctly_leveled_relative_import_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converse of the case above: `compat/cli.py` reaching abicheck's
    own top-level `service` module needs *two* dots (`from ..service import
    ...`), and that correctly-leveled import must still be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    compat = pkg / "compat"
    compat.mkdir()
    (compat / "cli.py").write_text(
        "from ..service import resolve_input\ndef go(a):\n    return resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "compat/cli.py" in errors[0]


def test_service_resolve_input_call_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing through `service_input_resolution.resolve_side_snapshot` must
    NOT be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_ok.py").write_text(
        "from .service_input_resolution import resolve_side_snapshot\n"
        "def go(a):\n"
        "    return resolve_side_snapshot(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


def test_cli_resolve_own_wrapper_is_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli_resolve.py` is the sanctioned CLI-side wrapper over
    `service.resolve_input` (see its module docstring) — its own call must not
    be flagged, even though every other `cli*.py` module is checked."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    return service.resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


@pytest.mark.parametrize(
    "filename, source",
    [
        pytest.param(
            "cli_unrelated_importfrom.py",
            "from vendor.service import resolve_input\n"
            "def go(a):\n"
            "    return resolve_input(a)\n",
            id="unrelated-importfrom-suffix-collision",
        ),
        pytest.param(
            "cli_unrelated_import.py",
            "import vendor.service\n"
            "def go(a):\n"
            "    return vendor.service.resolve_input(a)\n",
            id="unrelated-import-suffix-collision",
        ),
        pytest.param(
            "cli_bare_importfrom.py",
            "from service import resolve_input\ndef go(a):\n    return resolve_input(a)\n",
            id="bare-absolute-importfrom-collision",
        ),
        pytest.param(
            "cli_bare_import.py",
            "import service\ndef go(a):\n    return service.resolve_input(a)\n",
            id="bare-absolute-import-collision",
        ),
    ],
)
def test_unrelated_module_with_matching_suffix_is_not_flagged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
) -> None:
    """A third-party module that merely *ends* in a target module's name
    (``vendor.service``) must not be mistaken for abicheck's own module."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


def test_cli_resolve_exemption_is_scoped_to_the_wrapper_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `cli_resolve.py` exemption covers only calls inside
    `_resolve_input()` itself — a *different* function in the same module
    bypassing `service.resolve_input()` directly must still be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    return service.resolve_input(a)\n"
        "\n"
        "def _other_bypass(a):\n"
        "    from . import service\n"
        "    return service.resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:7" in errors[0]


def test_cli_resolve_exemption_ignores_same_named_class_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-named `_resolve_input` *method* on a class is not the
    documented module-level wrapper — a bypass inside it must still be
    flagged, not silently inherit the module-level exemption."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    return service.resolve_input(a)\n"
        "\n"
        "class Foo:\n"
        "    def _resolve_input(self, a):\n"
        "        from . import service\n"
        "        return service.resolve_input(a)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:8" in errors[0]


def test_cli_resolve_exemption_ignores_nested_function_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bypass placed inside a *nested* function defined within the
    module-level `_resolve_input()` wrapper is not part of the wrapper's
    own scope — it must still be flagged, not silently inherit the
    exemption via a full `ast.walk` into the nested `def`."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    def bypass():\n"
        "        return service.resolve_input(a)\n"
        "    return bypass()\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:4" in errors[0]


def test_cli_resolve_exemption_ignores_nested_lambda_bypass_sharing_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bypass inside a nested *lambda*'s body, invoked immediately on the
    same physical line as its own IIFE call, must still be flagged — a
    line-only membership check would wrongly exempt it because the outer
    lambda-invocation `Call` (correctly recorded by
    `_iter_calls_in_own_scope`, since it belongs to the wrapper's own scope)
    shares a `lineno` with the inner, pruned bypass call (Codex review)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    return (lambda: service.resolve_input(a))()\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:3" in errors[0]


def test_cli_resolve_exemption_ignores_default_argument_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bypass inside a default-argument *expression* on the wrapper's own
    signature executes once, in the *enclosing* (module) scope at
    def-time — not inside the wrapper's own runtime scope — so it must
    still be flagged, not silently inherit the exemption merely because it
    lexically sits inside the `FunctionDef` node (Codex review)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "from . import service\n"
        "\n"
        "\n"
        'def _resolve_input(a=service.resolve_input("bypass")):\n'
        "    return a\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:4" in errors[0]


def test_cli_resolve_exemption_covers_nested_definitions_own_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opposite of the default-argument case above: a call inside a
    default-argument expression on a function *nested* inside the wrapper
    executes immediately in the *wrapper's own* scope (at the point the
    nested `def` statement itself runs), not inside the nested function's
    own body — so it must be exempt, not flagged, even though the nested
    definition's *body* is still correctly pruned (Codex review: pruning
    a nested definition wholesale wrongly flagged this as a violation)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    def inner(x=service.resolve_input(a)):\n"
        "        return x\n"
        "    return inner()\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert errors == []


def test_cli_resolve_exemption_covers_nested_definitions_return_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same principle as the default-argument case above, for the other
    definition-head expression that runs eagerly in the enclosing scope: a
    nested function's own `-> T` return annotation. It executes in the
    *wrapper's* own scope at the point the nested `def` statement runs, not
    inside the nested function's own body, so it must be exempt too (Codex
    review: `_definition_head_parts` included `node.args` but not
    `node.returns`, so this shape was wrongly flagged)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    def inner() -> service.resolve_input(a):\n"
        "        return None\n"
        "    return inner()\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert errors == []


def test_cli_resolve_exemption_ignores_comprehension_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comprehension/generator expression introduces its own implicit
    scope in CPython — a call in its result expression (here, a generator
    expression's `elt`) executes inside *that* scope, not the wrapper's
    own, so it must still be flagged (Codex review: only explicit
    def/class/lambda were treated as nested scopes, so
    `next(service.resolve_input(x) for x in paths)` nested inside the
    wrapper was wrongly exempted)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(paths):\n"
        "    from . import service\n"
        "    return next(service.resolve_input(x) for x in paths)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:3" in errors[0]


def test_cli_resolve_exemption_covers_comprehensions_outermost_iterable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opposite of the comprehension case above: only a comprehension's
    *outermost* `for` clause's iterable is evaluated eagerly in the
    enclosing scope (documented CPython behavior) — a call there is a
    genuine wrapper-scope call and must stay exempt."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(a):\n"
        "    from . import service\n"
        "    return [x for x in service.resolve_input(a)]\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert errors == []


def test_cli_resolve_exemption_ignores_bypass_in_nested_comprehension_iterable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A head part can itself be scope-defining: an outer comprehension's
    outermost iterable is itself another comprehension
    (`[x for x in [service.resolve_input(y) for y in xs]]`), and the
    *inner* comprehension's own result expression is not the outer
    comprehension's enclosing-scope iterable — it's the inner
    comprehension's own scope — so it must still be flagged (Codex
    review: recursing into a head part via the top-level entry point
    bypassed the pruning check that only fires on an ordinary child,
    letting a scope-defining head part's own body inherit the
    exemption)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_resolve.py").write_text(
        "def _resolve_input(xs):\n"
        "    from . import service\n"
        "    return [x for x in [service.resolve_input(y) for y in xs]]\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_resolve.py:3" in errors[0]


def test_allowlist_does_not_cover_a_second_call_to_the_same_target_on_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two calls to the same Tier-1 target on one line (`(dump(a), dump(b))`)
    must each need their own reviewed allowlist entry — allowlisting the
    first must not silently exempt the second."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_dup.py").write_text(
        "from .dumper import dump\ndef go(a, b):\n    return (dump(a), dump(b))\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    # Only the first call's exact site is allowlisted.
    monkeypatch.setattr(
        gate,
        "CLI_CONTRACT_ALLOWLIST",
        frozenset({"abicheck/cli_dup.py:3:12:dumper.dump"}),
    )

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_dup.py:3:21" in errors[0]


def test_cli_contract_allowlist_entries_are_real_violations() -> None:
    """Every `CLI_CONTRACT_ALLOWLIST` entry must still name a genuine Tier-1
    call site *for its own recorded target* in the real tree — otherwise the
    allowlist rots into a rubber stamp for a call that was already fixed or
    removed, silently starts covering a *different* Tier-1 violation that
    happens to land on the same line, or silently starts covering a
    *second* call to the same target on that line (the key includes the
    column offset specifically so this can be verified, not just that some
    finding exists at that `path:lineno`)."""
    import re

    import scripts.check_ai_readiness as gate

    findings = gate.Findings()
    original = gate.CLI_CONTRACT_ALLOWLIST
    try:
        gate.CLI_CONTRACT_ALLOWLIST = frozenset()
        gate.check_cli_contract(findings)
    finally:
        gate.CLI_CONTRACT_ALLOWLIST = original
    pattern = re.compile(r"^([^:]+:\d+:\d+): front-end calls Tier-1 `([^`]+)` ")
    flagged_sites: set[str] = set()
    for c, m in findings.errors:
        if c != "cli-contract":
            continue
        match = pattern.match(m)
        if match is not None:
            flagged_sites.add(f"{match.group(1)}:{match.group(2)}")
    stale = original - flagged_sites
    assert not stale, (
        f"allowlist entries no longer correspond to a real violation for "
        f"their own recorded target: {stale}"
    )


# ── D10.2: shared-decorator coverage (ADR-037 D3 / G22 Phase 2) ──────────────


def _registered_commands() -> dict:
    """Return the registered top-level commands (dump/compare/scan/deps/compat)."""
    from abicheck.cli import main

    return main.commands


def _command_flags(cmd: object) -> set[str]:
    flags: set[str] = set()
    for p in cmd.params:  # type: ignore[attr-defined]
        if getattr(p, "param_type_name", None) != "option":
            continue
        flags.update(p.opts)
        flags.update(p.secondary_opts)
    return flags


def test_decorator_coverage() -> None:
    """Every verdict-emitting command carries each required shared option family
    (in full), or is on the ``INTENTIONAL_SUBSET`` allowlist (ADR-037 D10.2).

    This introspects the *live Click params* — stronger than the gate's AST
    decorator scan, so a family applied but secretly stripped would still fail.
    """
    from abicheck import cli_options as co

    commands = _registered_commands()
    for cmd_name in co.VERDICT_EMITTING_COMMANDS:
        flags = _command_flags(commands[cmd_name])
        for family in co.REQUIRED_FAMILIES:
            if (cmd_name, family) in co.INTENTIONAL_SUBSET:
                continue
            missing = co.FAMILY_FLAGS[family] - flags
            assert not missing, (
                f"{cmd_name} is missing {family} flags {sorted(missing)} — "
                "compose the shared decorator or add an INTENTIONAL_SUBSET entry"
            )


def test_intentional_subset_entries_are_real_gaps() -> None:
    """An allowlisted (command, family) must be a *genuine* omission — otherwise
    the allowlist rots into a rubber stamp for families that are actually present."""
    from abicheck import cli_options as co

    commands = _registered_commands()
    for (cmd_name, family), reason in co.INTENTIONAL_SUBSET.items():
        assert reason.strip(), f"{cmd_name}/{family} needs a non-empty reason"
        flags = _command_flags(commands[cmd_name])
        assert co.FAMILY_FLAGS[family] - flags, (
            f"{cmd_name} actually carries the whole {family} family — drop the "
            "INTENTIONAL_SUBSET entry"
        )


def test_gate_flags_missing_decorator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.2 is not a no-op: a verdict command lacking a required family is caught."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    cmds = pkg / "frontends/cli/commands"
    cmds.mkdir(parents=True)
    # A `compare` command composing only some required families.
    (cmds / "compare.py").write_text(
        "import click\n"
        '@main.command("compare")\n'
        "@two_sided_input_options\n"
        "@policy_options\n"
        "def compare_cmd():\n"
        "    pass\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    # severity/scope/output are missing → three coverage errors naming `compare`.
    missing = {
        fam
        for fam in ("severity_options", "scope_options", "output_options")
        if any(fam in m and "compare" in m for m in msgs)
    }
    assert missing == {"severity_options", "scope_options", "output_options"}, msgs


def test_gate_flags_missing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mapped command whose module exists but no longer declares it is flagged
    (D10.2 must not silently pass when coverage can't be verified)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    cmds = pkg / "frontends/cli/commands"
    cmds.mkdir(parents=True)
    (cmds / "compare.py").write_text("def helper():\n    return 1\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert any("`compare` was not found" in m for _c, m in findings.errors), findings.errors


def test_intentional_subset_decorator_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command listed in the intentional-subset allowlist may omit a required
    family without being flagged. The real allowlist is empty today, so this
    drives the mechanism with a synthetic command + allowlist entry."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_synth.py").write_text(
        "import click\n"
        '@main.command("synth")\n'
        "@two_sided_input_options\n"
        "@policy_options\n"
        "@scope_options\n"
        "@output_options(['json'])\n"
        "def synth_cmd():\n"
        "    pass\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_VERDICT_CMD_MODULES", {"cli_synth.py": "synth"})
    monkeypatch.setattr(
        gate,
        "_INTENTIONAL_SUBSET_DECORATORS",
        frozenset({("synth", "severity_options")}),
    )

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert not any("synth" in m and "severity_options" in m for m in msgs), msgs


# ── D10.4: one default per flag (ADR-037 D3 / G22 Phase 2) ───────────────────


def test_one_default_per_flag() -> None:
    """The real ``cli_options.py`` has no un-deferred conflicting flag default."""
    import scripts.check_ai_readiness as gate

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    assert [m for c, m in findings.errors if c == "cli-contract"] == []


def test_gate_flags_conflicting_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.4 catches the same ``--flag`` declared with two different defaults."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--mode", default="off")(func)\n'
        "def b(func):\n"
        '    return click.option("--mode", default="on")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(msgs) == 1 and "--mode" in msgs[0], msgs


def test_conflicting_defaults_always_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the deprecation-era allowlist gone, any flag declared with two
    different defaults across shared decorators is flagged (ADR-037 D10.4)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--collect-mode", default="off")(func)\n'
        "def b(func):\n"
        '    return click.option("--collect-mode", default="source-target")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(msgs) == 1 and "--collect-mode" in msgs[0], msgs


# ── Gate tables mirror the cli_options source of truth ───────────────────────


def test_gate_tables_mirror_cli_options() -> None:
    """The pure-stdlib gate duplicates ``cli_options`` contract tables (it cannot
    import the package). Assert the two never drift (ADR-037 D10)."""
    import scripts.check_ai_readiness as gate
    from abicheck import cli_options as co

    # command ↔ module map (inverted between the two).
    assert gate._VERDICT_CMD_MODULES == {
        mod: cmd for cmd, mod in co.VERDICT_EMITTING_COMMANDS.items()
    }
    # required decorators = the decorator for each required family.
    assert gate._REQUIRED_FAMILY_DECORATORS == frozenset(
        co.FAMILY_DECORATOR[f] for f in co.REQUIRED_FAMILIES
    )
    # allowlist, mapped from (cmd, family) to (cmd, decorator).
    assert gate._INTENTIONAL_SUBSET_DECORATORS == frozenset(
        (cmd, co.FAMILY_DECORATOR[fam]) for (cmd, fam) in co.INTENTIONAL_SUBSET
    )


# ── D8: --ast-frontend (legacy --header-backend aliases removed) ─────────────


@pytest.mark.parametrize("cmd_name", ["compare", "dump"])
def test_ast_frontend_is_the_only_frontend_spelling(cmd_name: str) -> None:
    """``--ast-frontend`` is the frontend flag; the removed ``--header-backend``
    alias is gone (clean removal, ADR-037 D7/D8)."""
    cmd = _registered_commands()[cmd_name]
    by_dest = {p.name: p for p in cmd.params}  # type: ignore[attr-defined]
    param = by_dest["header_backend"]
    assert "--ast-frontend" in param.opts
    assert "--header-backend" not in param.opts


def test_per_side_ast_frontend_is_spelled_on_ast_frontend_itself() -> None:
    """The per-side frontend override is ``--ast-frontend old=``/``new=``.

    ADR-040 Lever 1's side-prefix convention, not a third and fourth flag:
    the separate ``--ast-frontend old=``/``--ast-frontend new=`` pair (and the
    ``--*-header-backend`` aliases before it) are gone, and ``compare``'s
    ``--ast-frontend`` is repeatable so each side can name its own.
    """
    from click.testing import CliRunner

    from abicheck.cli import main

    cmd = _registered_commands()["compare"]
    dests = {p.name for p in cmd.params}  # type: ignore[attr-defined]
    assert "old_header_backend" not in dests
    assert "new_header_backend" not in dests

    param = {p.name: p for p in cmd.params}["header_backend"]  # type: ignore[attr-defined]
    assert param.multiple
    assert param.opts == ["--ast-frontend"]

    out = CliRunner().invoke(main, ["compare", "--help-all"]).output
    assert "--ast-frontend old=" not in out
    assert "--ast-frontend new=" not in out


def test_legacy_header_backend_flag_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The removed ``--header-backend`` spelling is now a hard usage error."""
    from click.testing import CliRunner

    from abicheck.cli import main

    old_p = _make_snap_file(tmp_path, "libdn", "1.0", [_func("a")])
    new_p = _make_snap_file(tmp_path, "libdn", "2.0", [_func("a")])
    res = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--header-backend", "castxml"]
    )
    assert res.exit_code != 0
    assert "no such option" in res.output.lower() or "No such option" in res.output


# ── D8: --ast-frontend unifies L2 header AST + L4 source-ABI extractor ────────


def test_ast_frontend_threads_to_l4_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--ast-frontend` selects the L4 source-ABI replay extractor too, not just
    the L2 header AST — one frontend choice across both stages (ADR-037 D8)."""
    import abicheck.buildsource.inline as inline
    import abicheck.cli_buildsource as cb
    from abicheck.model import AbiSnapshot

    captured: dict[str, object] = {}

    def _fake_collect(**kwargs: object) -> None:
        captured.update(kwargs)
        return None

    # embed_build_source imports collect_inline_pack from the inline module at
    # call time, so patch it at the source.
    monkeypatch.setattr(inline, "collect_inline_pack", _fake_collect)
    src = tmp_path / "src"
    src.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    cb.embed_build_source(
        snap, None, src, collect_mode="source-target", extractor="clang"
    )
    assert captured.get("extractor") == "clang"


@pytest.mark.parametrize("name", ["dump", "scan"])
def test_project_config_flag_is_config_not_build_config(name: str) -> None:
    """`--build-config` was renamed to `--config` (ADR-037 D4) to match `compare`
    and reflect that it loads the whole project .abicheck.yml. No back-compat
    window is kept, so the old spelling must be gone on dump/scan."""
    commands = _registered_commands()
    flags = _command_flags(commands[name])
    assert "--config" in flags, name
    assert "--build-config" not in flags, name  # old spelling fully removed


# ── Resolved option-set snapshot (catches an accidental flag drop in review) ──
#
# Frozen sets of every option spelling each verdict-emitting command exposes
# (_OPTION_SET_SNAPSHOT, imported above from _cli_option_set_snapshot.py — a
# plain data fixture, not a test module, split out purely to keep this
# debt.yaml no_growth-tracked file under its recorded baseline). A diff
# there in review means a flag was added or dropped — update deliberately.


@pytest.mark.parametrize("cmd_name", sorted(_OPTION_SET_SNAPSHOT))
def test_option_set_snapshot(cmd_name: str) -> None:
    """Each command's full option surface matches the frozen snapshot.

    ``--header-graph``/``--header-graph-includes`` stay in this snapshot
    (G29 Phase A) because they are still real, registered Click options —
    just hidden (``hidden=True``, deprecated no-op shims; see
    test_header_graph_flags_are_hidden_but_still_parse below for the
    ``--help``-visibility/inert-behavior half of this contract, which this
    raw-registration snapshot doesn't check)."""
    commands = _registered_commands()
    flags = _command_flags(commands[cmd_name])
    assert sorted(flags) == sorted(_OPTION_SET_SNAPSHOT[cmd_name])


@pytest.mark.parametrize("cmd_name", ["compare", "dump"])
def test_header_graph_flags_are_hidden_but_still_parse(cmd_name: str) -> None:
    """G29 Phase A: --header-graph/--header-graph-includes are deprecated,
    hidden no-op shims — absent from --help, but still accepted (and
    otherwise behaviorally inert; see test_compare_dispatch.py /
    test_cli_coverage_extra.py for end-to-end no-op coverage) rather than
    erroring as an unrecognized option, so an existing script/CI invocation
    that still passes them doesn't hard-break."""
    from click.testing import CliRunner

    from abicheck.cli import main

    help_result = CliRunner().invoke(main, [cmd_name, "--help"])
    assert "--header-graph" not in help_result.output

    commands = _registered_commands()
    cmd = commands[cmd_name]
    hidden_flags = {
        p.opts[0]
        for p in cmd.params  # type: ignore[attr-defined]
        if getattr(p, "hidden", False) and "--header-graph" in p.opts[0]
    }
    assert hidden_flags == {"--header-graph", "--header-graph-includes"}


@pytest.mark.parametrize("cmd_name", ["compare", "dump", "scan"])
def test_removed_gcc_spellings_are_gone_entirely(cmd_name: str) -> None:
    """--gcc-path/--gcc-prefix/--gcc-option are removed, not hidden.

    They were briefly kept as hidden-but-functional aliases for
    --compiler/--compiler-prefix/--compiler-option. Carrying two spellings
    meant a per-invocation conflict resolver whose only correct answer for
    the repeatable option pair was to reject mixing them, so the legacy
    names were dropped outright (pre-1.0) rather than deprecated in place.
    A caller still passing one now gets a hard usage error naming the flag,
    which is strictly better than a silently-ignored value."""
    from click.testing import CliRunner

    from abicheck.cli import main

    commands = _registered_commands()
    cmd = commands[cmd_name]
    dests = {p.name for p in cmd.params}  # type: ignore[attr-defined]
    spellings = {opt for p in cmd.params for opt in p.opts}  # type: ignore[attr-defined]
    assert not spellings & {"--gcc-path", "--gcc-prefix", "--gcc-option"}, cmd_name
    assert not dests & {"gcc_path", "gcc_prefix", "gcc_option_tokens"}, cmd_name

    for help_flag in ("--help", "--help-all"):
        result = CliRunner().invoke(main, [cmd_name, help_flag])
        assert "--gcc-path" not in result.output, (cmd_name, help_flag)
        assert "--gcc-prefix" not in result.output, (cmd_name, help_flag)
        assert "--gcc-option" not in result.output, (cmd_name, help_flag)
    # --compiler is an advanced/toolchain-tier flag (the same disclosure tier
    # the old --gcc-path occupied), so it's only guaranteed on --help-all.
    help_all_output = CliRunner().invoke(main, [cmd_name, "--help-all"]).output
    assert "--compiler" in help_all_output, cmd_name


def _all_leaf_commands() -> list[tuple[str, object]]:
    """Every leaf command in the live tree, as (dotted-path, command)."""
    import click

    from abicheck.cli import main

    out: list[tuple[str, object]] = []

    def walk(cmd: object, path: list[str]) -> None:
        if isinstance(cmd, click.Group):
            for name, sub in cmd.commands.items():
                walk(sub, path + [name])
        elif path:
            out.append((" ".join(path), cmd))

    walk(main, [])
    return out


def test_no_option_has_empty_help() -> None:
    """Every *visible* option on every command carries help text.

    A blank ``--help`` line is a UX defect (the flag shows with no description).
    Hidden options are exempt — they are deliberately off the help surface. This
    guards the cleanup that routed `-v/--verbose` through `@verbose_option` and
    filled the stray blank `-o`/`--format`/`--policy` strings.
    """
    import click

    blank: list[str] = []
    for path, cmd in _all_leaf_commands():
        for p in cmd.params:  # type: ignore[attr-defined]
            if isinstance(p, click.Option) and not p.hidden and not p.help:
                blank.append(f"{path} {p.opts[-1]}")
    assert blank == [], f"options with empty --help: {blank}"


def test_shared_concept_canonical_spelling() -> None:
    """A shared concept shows one canonical long flag across every command.

    The CLI carries the same idea on many commands (public headers, the output
    path). They had drifted in which spelling renders *first* in ``--help``
    (`collect` led with ``--headers``; `probe run` used ``--out``). The aliases
    still resolve, but the displayed primary must be uniform so the surface
    reads as one tool. ABICC-dialect commands use their own single-dash dests
    and are naturally excluded (they never bind ``headers``/``output``/``out``).
    """
    import click

    header_offenders: list[str] = []
    output_offenders: list[str] = []
    for path, cmd in _all_leaf_commands():
        for p in cmd.params:  # type: ignore[attr-defined]
            if not isinstance(p, click.Option):
                continue
            longs = [o for o in p.opts if o.startswith("--")]
            if p.name == "headers" and longs and longs[0] != "--header":
                header_offenders.append(f"{path}: {longs}")
            if p.name in {"output", "out"} and "--output" not in p.opts:
                output_offenders.append(f"{path}: {list(p.opts)}")
    assert header_offenders == [], (
        f"header option must lead with --header: {header_offenders}"
    )
    assert output_offenders == [], (
        f"output-path option must offer --output: {output_offenders}"
    )


# ── Chokepoint parity: one classifier, no scope_public drift ─────────────────


def _make_snap_file(
    tmp_path: Path, name: str, version: str, funcs: list[Function]
) -> Path:
    snap = AbiSnapshot(library=name, version=version, functions=funcs)
    p = tmp_path / f"{name}_{version}.json"
    save_snapshot(snap, p)
    return p


def _func(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
    )


def test_compare_release_matches_service_run_compare(tmp_path: Path) -> None:
    """``compare-release``'s per-pair runner classifies identically to
    ``service.run_compare`` — they share the one chokepoint (ADR-037 D1)."""
    from abicheck import service
    from abicheck.cli_compare_release import _run_compare_pair

    old_p = _make_snap_file(tmp_path, "libfoo", "1.0", [_func("foo"), _func("bar")])
    new_p = _make_snap_file(tmp_path, "libfoo", "2.0", [_func("foo")])

    svc_result, _, _ = service.run_compare(
        old_p, new_p, scope_to_public_surface=True
    ).as_tuple()
    rel_result, _, _ = _run_compare_pair(
        old_p,
        new_p,
        old_headers=[],
        new_headers=[],
        old_includes=[],
        new_includes=[],
        old_version="",
        new_version="",
        lang="c++",
        suppress=None,
        policy="strict_abi",
        policy_file_path=None,
        old_pdb_path=None,
        new_pdb_path=None,
        scope_to_public_surface=True,
    ).as_tuple()

    assert svc_result.verdict == rel_result.verdict
    assert sorted(c.kind for c in svc_result.breaking) == sorted(
        c.kind for c in rel_result.breaking
    )
    assert sorted(c.kind for c in svc_result.source_breaks) == sorted(
        c.kind for c in rel_result.source_breaks
    )
    assert sorted(c.kind for c in svc_result.compatible) == sorted(
        c.kind for c in rel_result.compatible
    )


def test_run_compare_request_equivalent_to_kwargs_shim(tmp_path: Path) -> None:
    """The kwargs ``run_compare`` shim and a hand-built ``CompareRequest`` agree."""
    from abicheck.api_types import CompareRequest, InputSpec
    from abicheck.service import run_compare, run_compare_request

    old_p = _make_snap_file(tmp_path, "libbar", "1.0", [_func("a"), _func("b")])
    new_p = _make_snap_file(tmp_path, "libbar", "2.0", [_func("a")])

    shim_result, _, _ = run_compare(old_p, new_p).as_tuple()
    req = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
    req_result, _, _ = run_compare_request(req).as_tuple()

    assert shim_result.verdict == req_result.verdict
    assert sorted(c.kind for c in shim_result.breaking) == sorted(
        c.kind for c in req_result.breaking
    )


def test_run_compare_request_normalizes_lang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted upper-case ``lang`` is lowered before snapshot resolution.

    ``validate()`` accepts ``"C"`` case-insensitively, but the ELF dump path
    does case-sensitive ``lang == "c"`` checks — ``run_compare_request`` must
    normalise so ``"C"`` is not silently treated as C++.
    """
    from abicheck import service
    from abicheck.api_types import CompareRequest, InputSpec

    old_p = _make_snap_file(tmp_path, "liblang", "1.0", [_func("a")])
    new_p = _make_snap_file(tmp_path, "liblang", "2.0", [_func("a")])

    seen_langs: list[str] = []

    def _spy_resolve_input(path, headers, includes, version, lang, **kwargs):  # type: ignore[no-untyped-def]
        seen_langs.append(lang)
        return AbiSnapshot(library="liblang", version=version)

    monkeypatch.setattr(service, "resolve_input", _spy_resolve_input)

    req = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p), lang="C")
    service.run_compare_request(req)

    assert seen_langs == ["c", "c"]


# ── D1: service_scan must not depend on the CLI frontend ────────────────────
#
# service_scan.run_scan historically imported its shared scan-engine core
# (run_scan_core / _BudgetOverflow / _EvidenceContractError) from cli_scan.py —
# a Click command module — the reverse of the intended frontend → service →
# engine dependency direction (ADR-037 D1). That engine core now lives in
# scan_engine.py (no @click.option decorators, not registered as a command);
# cli_scan.py (the CLI) and service_scan.py (the typed service API) both
# import from it instead of service_scan reaching into the CLI module.


def _imported_modules(path: Path) -> set[str]:
    """Return every module name imported anywhere in *path* (module-level,
    function-local, or under ``TYPE_CHECKING`` — all are real coupling, just
    with different init-time consequences)."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                out.add(module)
            for alias in node.names:
                out.add(f"{module}.{alias.name}" if module else alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


def test_service_scan_does_not_import_cli_scan() -> None:
    """service_scan.py must never import from cli_scan.py (the Click ``scan``
    command module) — the shared engine core lives in scan_engine.py, which
    both cli_scan.py and service_scan.py depend on independently."""
    import abicheck.service_scan as service_scan_mod

    path = Path(service_scan_mod.__file__)
    imported = _imported_modules(path)
    assert not {"cli_scan", "abicheck.cli_scan"} & imported, (
        "service_scan.py imports from cli_scan.py — this reintroduces the "
        "service→CLI dependency inversion ADR-037 D1 / the scan_engine split "
        "fixed. Import the needed symbols from abicheck.scan_engine instead."
    )


def test_cli_scan_reexports_the_real_scan_engine_functions() -> None:
    """cli_scan.py's re-exported run_scan_core (etc.) are the *same objects*
    as scan_engine's, not divergent copies — the CLI and the typed service API
    both call one engine (ADR-037 D1)."""
    from abicheck import cli_scan, scan_engine

    assert cli_scan.run_scan_core is scan_engine.run_scan_core
    assert cli_scan._BudgetOverflow is scan_engine._BudgetOverflow
    assert cli_scan._EvidenceContractError is scan_engine._EvidenceContractError


def test_contract_alone_implies_contract_evaluation(tmp_path: Path) -> None:
    """``--contract`` alone now implies ``--contract`` (ADR-049
    Phase 6, CLI audit PR 3/5: abicheck.cli_options.resolve_contract_evaluation).

    It selects the domain the shadow evaluator judges against, and naming a
    domain is by itself enough to ask for a decision against it -- this used
    to be a `UsageError` (a no-op flag rejected before any input was parsed);
    it now runs the real evaluator, identically to passing both flags
    explicitly. The typed Python API (`api_types.CompareRequest.
    validation_errors`, `service._validate_contract_mode`) is unaffected and
    still requires both explicitly -- see test_compatibility_evaluation_frontend.py
    / test_service_unit.py for that half of the contract.
    """
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import snapshot_to_json

    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    snap_json = snapshot_to_json(AbiSnapshot(library="x", version="1"))
    old_path.write_text(snap_json, encoding="utf-8")
    new_path.write_text(snap_json, encoding="utf-8")

    result_implicit = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_path),
            str(new_path),
            "--contract",
            "exports",
            "--format",
            "json",
        ],
    )
    result_explicit = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_path),
            str(new_path),
            "--contract",
            "exports",
            "--format",
            "json",
        ],
    )
    assert result_implicit.exit_code == result_explicit.exit_code
    assert "Usage:" not in result_implicit.output
    assert "contract_context" in result_implicit.output


def test_contract_evaluation_no_longer_rejected_for_directory_comparisons() -> None:
    """``--contract``/``--contract`` now DO have per-library
    fan-out wiring (CLI-audit P1, release/package contract parity) --
    ``_reject_set_input_flags`` no longer even accepts these two kwargs.
    See ``test_cli_compare_contract_evaluation.py::
    TestReleaseFanOutContractParity`` for the positive CLI-level coverage
    (directory `compare` with `--contract` now applies per
    library, same as a single-pair `compare`). ``--pack`` stays rejected
    for directory inputs -- see the same test class's
    ``test_pack_still_rejected_on_directory_inputs``.
    """
    import inspect

    from abicheck.cli_compare_helpers import _reject_set_input_flags

    params = inspect.signature(_reject_set_input_flags).parameters
    assert "contract_evaluation" not in params
    assert "contract_mode" not in params
    # exit_code_scheme also no longer exists on this function at all (CLI
    # cleanup phase two PR G2 deleted the flag entirely, so there is
    # nothing left to reject it against here either) -- the signature now
    # starts at reconcile_build_context.
    assert "exit_code_scheme" not in params
    # Passes through untouched -- none of these kwargs exist on this
    # function anymore, so there is nothing left here to reject.
    _reject_set_input_flags(False, None)
