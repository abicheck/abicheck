"""A pointer-level change of a never-exported declaration is an API break.

Bug class: ``regrade_unexported_params`` relabelled ``func_params_changed`` of
a function no binary exports (a ``static inline`` header function) as
``header_only_function_params_changed``, but the sibling
``param_pointer_level_changed``/``return_pointer_level_changed`` kept their
BREAKING default for the same function. Oracle, over each side's export fact
(absent / present / unknown): the findings are always kept, and graded
API_BREAK iff both sides confirm the export absent.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare import export_transition as et
from abicheck.model import Function
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict

_STATES = ("absent", "present", "unknown")


@pytest.mark.parametrize(
    ("old_state", "new_state"), list(itertools.product(_STATES, _STATES))
)
def test_grade_follows_both_export_facts(
    monkeypatch: pytest.MonkeyPatch, old_state: str, new_state: str
) -> None:
    f_old = Function(name="hscale", mangled="hscale", return_type="int")
    f_new = Function(name="hscale", mangled="hscale", return_type="int")
    state = {id(f_old): old_state, id(f_new): new_state}
    monkeypatch.setattr(
        et, "is_export_confirmed_absent", lambda f: state[id(f)] == "absent"
    )
    found = [
        Change(
            kind=ChangeKind.PARAM_POINTER_LEVEL_CHANGED, symbol="hscale", description=""
        ),
        Change(
            kind=ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
            symbol="hscale",
            description="",
        ),
    ]
    monkeypatch.setattr(et, "pointer_level_changes", lambda *a, **k: list(found))
    out = et.unexported_aware_pointer_levels(
        "hscale",
        f_old,
        f_new,
        None,
        None,
        params_unconfirmed=False,
    )
    assert [c.kind for c in out] == [c.kind for c in found]
    lowered = old_state == "absent" and new_state == "absent"
    assert {c.effective_verdict for c in out} == {
        Verdict.API_BREAK if lowered else None
    }
