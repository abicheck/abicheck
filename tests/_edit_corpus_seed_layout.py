"""Record- and base-layout probe roles for the edit corpus's C++ seed.

Split from ``_edit_corpus_seeds`` to keep that module under the test-file cap.
"""

from __future__ import annotations

RoleText = tuple[str, str]

# Record- and base-layout probes, one per ``_edit_corpus_edit_model`` class the
# layout operators (``_edit_corpus_operators_layout``) generate. Members are
# char/short/int/unsigned only, so the layout is the same at -m32 and LP64.
LAYOUT_ROLES: dict[str, RoleText] = {
    "lay.ihead": (
        "struct LIH { int a; int b; };\nint lih_use(LIH* p);",
        "int lih_use(LIH* p) { return p->b; }",
    ),
    "lay.imid": (
        "struct LIM { int a; int b; };\nint lim_use(LIM* p);",
        "int lim_use(LIM* p) { return p->b; }",
    ),
    "lay.rhead": (
        "struct LRH { int z; int a; int b; };\nint lrh_use(LRH* p);",
        "int lrh_use(LRH* p) { return p->b; }",
    ),
    "lay.rmid": (
        "struct LRM { int a; int z; int b; };\nint lrm_use(LRM* p);",
        "int lrm_use(LRM* p) { return p->b; }",
    ),
    "lay.rtail": (
        "struct LRT { int a; char b; char c; };\nint lrt_use(LRT* p);",
        "int lrt_use(LRT* p) { return p->a; }",
    ),
    "lay.tsame": (
        "struct LTS { int a; int b; };\nint lts_use(LTS* p);",
        "int lts_use(LTS* p) { return p->a; }",
    ),
    "lay.tshrink": (
        "struct LTK { int a; char c; };\nint ltk_use(LTK* p);",
        "int ltk_use(LTK* p) { return p->c; }",
    ),
    "lay.talign": (
        "struct LTA { char a; char b[4]; };\nint lta_use(LTA* p);",
        "int lta_use(LTA* p) { return p->a; }",
    ),
    "lay.ashrink": (
        "struct LAS { char a[8]; int b; };\nint las_use(LAS* p);",
        "int las_use(LAS* p) { return p->b; }",
    ),
    "lay.agrow": (
        "struct LAG { char a[1]; int b; };\nint lag_use(LAG* p);",
        "int lag_use(LAG* p) { return p->b; }",
    ),
    "lay.ralign": (
        "struct LRA { int a; };\nint lra_use(LRA* p);",
        "int lra_use(LRA* p) { return p->a; }",
    ),
    "lay.radd": ("", ""),
    "lay.bwabs": (
        "struct LBW { unsigned a : 3; unsigned b : 4; };\nint lbw_use(LBW* p);",
        "int lbw_use(LBW* p) { return (int)p->a; }",
    ),
    "lay.bwshift": (
        "struct LBS { unsigned a : 16; unsigned b : 16; int c; };\nint lbs_use(LBS* p);",
        "int lbs_use(LBS* p) { return p->c; }",
    ),
    "lay.bftof": (
        "struct LBF { unsigned a : 8; unsigned b : 8; int c; };\nint lbf_use(LBF* p);",
        "int lbf_use(LBF* p) { return p->c; }",
    ),
    "layb.remove": (
        "struct LB0 { int k; };\nstruct LD0 : LB0 { int d; };\nint ld0_use(LD0* p);",
        "int ld0_use(LD0* p) { return p->d; }",
    ),
    "layb.add": (
        "struct LB1 { int k; };\nstruct LD1 { int d; };\nint ld1_use(LD1* p);",
        "int ld1_use(LD1* p) { return p->d; }",
    ),
    "layb.nonvirt": (
        "struct LB2 { int k; };\nstruct LD2 : virtual LB2 { int d; };\nint ld2_use(LD2* p);\nint ld2_make();",
        "int ld2_use(LD2* p) { return p->d; }\n"
        "// Constructing LD2 makes the compiler emit its full DWARF definition (a\n"
        "// class with a virtual base and no key function is otherwise declaration-only).\n"
        "int ld2_make() { LD2 v; v.d = 1; return v.d; }",
    ),
    "layb.access": (
        "struct LB3 { int k; };\nstruct LD3 : public LB3 { int d; };\nint ld3_use(LD3* p);",
        "int ld3_use(LD3* p) { return p->d; }",
    ),
    "lay.tcopy": (
        "struct LTC { int a; };\nint ltc_get(LTC v);",
        "int ltc_get(LTC v) { return v.a; }",
    ),
    "lay.stdlay": (
        "struct LSL { int a; int b; };\nint lsl_use(LSL* p);",
        "int lsl_use(LSL* p) { return p->a; }",
    ),
}
