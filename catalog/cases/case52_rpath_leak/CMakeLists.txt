# case52: RPATH leak — v1 has a hardcoded build-directory path baked into
# DT_RUNPATH. v2 uses an $ORIGIN-relative DT_RUNPATH.
# Both have identical ABI surface; the issue is a deployment bad practice.
#
# --enable-new-dtags is explicit: GCC distributions commonly default it on
# (emitting DT_RUNPATH) while a plain Clang driver leaves the linker default
# (DT_RPATH), which turned the same fixture into runpath_changed under GCC and
# rpath_changed under Clang. Pinning it keeps one transition per fixture.
abicheck_add_case(case52_rpath_leak
    V1_SOURCES old/lib.c
    V1_LINK_OPTIONS -Wl,--enable-new-dtags -Wl,-rpath,/home/build/myproject/lib
    V2_SOURCES new/lib.c
    V2_LINK_OPTIONS -Wl,--enable-new-dtags "-Wl,-rpath,$ORIGIN"
    APP_SOURCES app.c
    PLATFORMS linux
)
