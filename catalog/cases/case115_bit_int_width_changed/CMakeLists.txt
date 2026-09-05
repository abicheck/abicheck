abicheck_add_case(case115_bit_int_width_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.c
    PLATFORMS linux
)

# _BitInt(N) is a C23 feature; request C23 so it is available.
# Guard on TARGET: this case is linux-only, so on other platforms
# abicheck_add_case() returns early and the targets do not exist.
if(TARGET case115_bit_int_width_changed_v1)
    # C_STANDARD value 23 (C23) is only recognised by CMake >= 3.21; the
    # examples project floor is 3.15. Use C_STANDARD_REQUIRED so an unsupported
    # standard fails loudly instead of silently decaying. On older CMake fall
    # back to C17 — the feature probe (requires_feature: _BitInt) already skips
    # this case when the compiler lacks _BitInt regardless of the std flag.
    if(CMAKE_VERSION VERSION_GREATER_EQUAL "3.21")
        set(_case115_c_std 23)
    else()
        set(_case115_c_std 17)
    endif()
    set_target_properties(case115_bit_int_width_changed_v1 case115_bit_int_width_changed_v2
        PROPERTIES C_STANDARD ${_case115_c_std} C_STANDARD_REQUIRED ON
    )
endif()
