# case136: Executable stack removed (the FIX direction; complements case49).
# v1 has an executable GNU_STACK (-z execstack, RWE); v2 corrects it to RW
# (-z noexecstack). Identical ABI surface.
abicheck_add_case(case136_executable_stack_removed
    V1_SOURCES v1.c
    V1_LINK_OPTIONS -Wl,-z,execstack
    V2_SOURCES v2.c
    V2_LINK_OPTIONS -Wl,-z,noexecstack
    APP_SOURCES app.c
    PLATFORMS linux
)
