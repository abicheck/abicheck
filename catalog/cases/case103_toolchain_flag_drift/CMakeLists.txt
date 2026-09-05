# Identical source for v1 and v2; the only difference is an ABI-affecting
# compiler flag (-fshort-enums) applied to the v2 build. The toolchain-flag
# drift detector reads the flag set from DWARF DW_AT_producer and reports the
# mismatch, even though no symbol or layout changed in this minimal example.
abicheck_add_case(case103_toolchain_flag_drift
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    V2_COMPILE_OPTIONS -fshort-enums
    APP_SOURCES app.c
    PLATFORMS linux
)
