# __attribute__((ms_abi)) is only supported on x86-64 with GCC/Clang.
if(NOT (CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$"
        AND CMAKE_C_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"))
  message(STATUS "Skipping case64_calling_convention_changed (requires x86-64 + GCC/Clang)")
  return()
endif()

abicheck_add_case(case64_calling_convention_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.c
    PLATFORMS linux
)
