abicheck_add_case(case16_inline_to_non_inline
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.hpp
    V2_HEADERS v2.hpp
    V1_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/v1.hpp
    V2_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/v2.hpp
    APP_SOURCES app.cpp
    PLATFORMS linux macos windows
)
