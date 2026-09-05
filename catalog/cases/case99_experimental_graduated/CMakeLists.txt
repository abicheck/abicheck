abicheck_add_case(case99_experimental_graduated
    V1_SOURCES old/lib.cpp
    V2_SOURCES new/lib.cpp
    V1_HEADERS old/lib.h
    V2_HEADERS new/lib.h
    APP_SOURCES app.cpp
    PLATFORMS linux macos windows
)
