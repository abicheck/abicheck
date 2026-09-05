# case50: SONAME present but inconsistent — v1 has SONAME=libfoo.so.0
# (wrong major), v2 has SONAME=libfoo.so.1 (correct).
# Both libraries have the same ABI surface; the issue is metadata policy.
abicheck_add_case(case50_soname_inconsistent
    V1_SOURCES bad.c
    V1_LINK_OPTIONS $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wl,-soname,libfoo.so.0>
    V2_SOURCES good.c
    V2_LINK_OPTIONS $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wl,-soname,libfoo.so.1>
    APP_SOURCES app.c
    PLATFORMS linux
)
