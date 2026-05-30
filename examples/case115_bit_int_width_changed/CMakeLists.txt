abicheck_add_case(case115_bit_int_width_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.c
    PLATFORMS linux
)

# _BitInt(N) is a C23 feature; request the latest C standard the toolchain has.
# Guard on TARGET: this case is linux-only, so on other platforms
# abicheck_add_case() returns early and the targets do not exist.
if(TARGET case115_bit_int_width_changed_v1)
    set_target_properties(case115_bit_int_width_changed_v1 case115_bit_int_width_changed_v2
        PROPERTIES C_STANDARD 23
    )
endif()
