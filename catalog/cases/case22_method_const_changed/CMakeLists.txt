abicheck_add_case(case22_method_const_changed
    V1_SOURCES old/lib.cpp
    V2_SOURCES new/lib.cpp
    V1_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    V2_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/new
    APP_SOURCES app.cpp
    APP_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    PLATFORMS linux macos windows
)
