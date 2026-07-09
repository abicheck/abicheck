# v2: the keyword-only `encoding` argument was renamed to `codec` and lost its
# default. The compiled .so is otherwise byte-identical (same PyInit_mymod,
# same imported Py* surface, same abi3 tag) — the break lives only here.
def transform(data, codec: str) -> bytes: ...
