# case52: RPATH leak — v1 has a hardcoded build-directory path baked into
# DT_RPATH/DT_RUNPATH. v2 uses $ORIGIN-relative RUNPATH.
# Both have identical ABI surface; the issue is a deployment bad practice.
abicheck_add_case(case52_rpath_leak
    V1_SOURCES old/lib.c
    V1_LINK_OPTIONS -Wl,-rpath,/home/build/myproject/lib
    V2_SOURCES new/lib.c
    V2_LINK_OPTIONS "-Wl,-rpath,$ORIGIN"
    APP_SOURCES app.c
    PLATFORMS linux
)
