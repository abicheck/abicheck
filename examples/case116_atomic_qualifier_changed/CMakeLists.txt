abicheck_add_case(case116_atomic_qualifier_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.c
    PLATFORMS linux
)

# _Atomic is C11; ensure at least C11 even if the toolchain default is older.
# Guard on TARGET: this case is linux-only, so on other platforms
# abicheck_add_case() returns early and the targets do not exist.
if(TARGET case116_atomic_qualifier_changed_v1)
    set_target_properties(case116_atomic_qualifier_changed_v1 case116_atomic_qualifier_changed_v2
        PROPERTIES C_STANDARD 11
    )
endif()
