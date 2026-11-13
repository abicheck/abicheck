"""Known-gap canary: affected-symbol attribution matches type names as substrings.

Found by the closed layout fragment (``formal/FRAGMENT.md``): a change to
``struct s1`` listed ``use_s10 ... use_s199`` as affected, because
``_build_type_to_funcs`` relates a changed type to every function whose
signature *contains* its name, with no identifier boundary. See
``docs/contribute/known-gaps.md`` ("Affected-symbol attribution ignores
identifier boundaries").
"""

from __future__ import annotations

import pytest

from abicheck.diff_filtering import _build_type_to_funcs
from abicheck.model import Function, Param


@pytest.mark.known_gap_canary
def test_type_name_prefix_of_another_type_is_attributed_to_its_functions() -> None:
    funcs = {
        f"use_s{n}": Function(
            name=f"use_s{n}",
            mangled=f"use_s{n}",
            return_type="int",
            params=[Param(name="p", type=f"struct s{n} *")],
        )
        for n in (1, 10, 2)
    }
    to_funcs, _ = _build_type_to_funcs({"s1"}, funcs)
    # Gapped answer: use_s10 takes `struct s10 *`, unrelated to `s1`.
    # Correct answer once fixed: {"use_s1"}.
    assert to_funcs["s1"] == {"use_s1", "use_s10"}
