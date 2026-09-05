abicheck_add_case(case16_inline_to_non_inline
    V1_SOURCES old/lib.cpp
    V2_SOURCES new/lib.cpp
    V1_HEADERS old/lib.hpp
    V2_HEADERS new/lib.hpp
    V1_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/old/lib.hpp
    V2_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/new/lib.hpp
    APP_SOURCES app.cpp
    PLATFORMS linux macos windows
)
