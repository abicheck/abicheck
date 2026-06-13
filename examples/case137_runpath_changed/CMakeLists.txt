# case137: DT_RUNPATH added/changed — v2 bakes in a -rpath (emitted as
# DT_RUNPATH with default new dtags). Identical ABI surface; the dynamic
# library search path the loader uses changes.
abicheck_add_case(case137_runpath_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V2_LINK_OPTIONS -Wl,-rpath,/opt/vendor/lib -Wl,--enable-new-dtags
    APP_SOURCES app.c
    PLATFORMS linux
)
