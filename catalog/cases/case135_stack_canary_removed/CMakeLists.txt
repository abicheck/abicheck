# case135: Stack canary removed — v1 is compiled with -fstack-protector-all
# (every function gets a stack-smashing guard), v2 with -fno-stack-protector.
# Identical ABI surface; the __stack_chk_fail guard references disappear.
abicheck_add_case(case135_stack_canary_removed
    V1_SOURCES v1.c
    V1_COMPILE_OPTIONS -fstack-protector-all
    V2_SOURCES v2.c
    V2_COMPILE_OPTIONS -fno-stack-protector
    APP_SOURCES app.c
    PLATFORMS linux
)
