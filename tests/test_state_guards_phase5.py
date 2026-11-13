"""Phase 5 of docs/contribute/plans/implicit-state-machines.md: small state
guards, each checked over its whole (small) state space or several sibling
inputs, against expectations written here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# -- BudgetedExecutor after shutdown (process_resources.py) ---------------------


@pytest.mark.parametrize("granted", [0, 2])
def test_no_work_is_accepted_after_shutdown_whatever_was_granted(
    monkeypatch: pytest.MonkeyPatch, granted: int
) -> None:
    from abicheck import process_resources as pr

    monkeypatch.setattr(pr.THREAD_BUDGET, "grant", lambda requested: granted)
    monkeypatch.setattr(pr.THREAD_BUDGET, "release", lambda n: None)
    ex = pr.BudgetedExecutor(2)
    assert ex.submit(lambda: 7).result() == 7
    ex.shutdown()
    with pytest.raises(RuntimeError, match="after shutdown"):
        ex.submit(lambda: 8)


# -- registration window depth (deadline.py) ------------------------------------


def test_registration_window_balances_and_rejects_an_extra_exit() -> None:
    from abicheck import deadline as dl

    assert dl._registration_window_depth() == 0
    for n in range(1, 4):
        for _ in range(n):
            dl._enter_registration_window()
        for _ in range(n):
            dl._exit_registration_window()
        assert dl._registration_window_depth() == 0
    with pytest.raises(RuntimeError, match="more times than entered"):
        dl._exit_registration_window()
    assert dl._registration_window_depth() == 0


# -- stored member assurance status (workflows/release_assurance_members.py) ----

KNOWN = ("complete", "not_requested", "partial", "failed", "not_comparable")


@pytest.mark.parametrize("status", [*KNOWN, "bogus", "Complete", "", None, 3])
def test_only_a_known_member_status_is_reported_as_itself(status: object) -> None:
    from abicheck.workflows.release_assurance_members import member_assurance_fields

    got, notes = member_assurance_fields(
        SimpleNamespace(analysis_assurance=SimpleNamespace(status=status, notes=("n",)))
    )
    if status in KNOWN:
        assert (got, notes) == (status, ("n",))
    else:
        assert got == "unknown"
        assert notes == ("n", f"unrecognized assurance status {status!r}")


# -- braces inside literals (header_conditionals.py) ----------------------------


@pytest.mark.parametrize(
    "member",
    [
        'const char* p = "}";',
        'const char* p = "{";',
        "char c = '}';",
        "char c = '{';",
        'const char* p = "\\"}";',  # escaped quote before the brace
        'const char* p = "}}{";',
        "int n = 0;",  # control: no literal at all
    ],
)
def test_a_brace_inside_a_literal_neither_opens_nor_closes_a_scope(member: str) -> None:
    from abicheck.header_conditionals import scan_conditional_fields

    src = f"struct A {{\n  {member}\n#ifdef X\n  int x;\n#endif\n}};\n"
    fields = scan_conditional_fields(src)
    assert fields.get("A", {}).get("x", {}).get("guard") == "X", fields
