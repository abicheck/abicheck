abicheck_add_case(case23_pure_virtual_added
    V1_SOURCES old/lib.cpp
    V2_SOURCES new/lib.cpp
    V1_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    V2_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/new
    APP_SOURCES app.cpp
    PLATFORMS linux macos windows
)
