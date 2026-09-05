# case49: Executable stack — v1 is linked with -z execstack, giving it
# a GNU_STACK segment with RWE (executable). v2 has RW (non-executable).
# Both have identical ABI surface; the issue is a security bad practice.
abicheck_add_case(case49_executable_stack
    V1_SOURCES old/lib.c
    V1_LINK_OPTIONS -Wl,-z,execstack
    V2_SOURCES new/lib.c
    V2_LINK_OPTIONS -Wl,-z,noexecstack
    APP_SOURCES app.c
    PLATFORMS linux
)
