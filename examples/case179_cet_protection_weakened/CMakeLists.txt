# Intel CET (-fcf-protection, .note.gnu.property IBT/SHSTK bits) is x86-64
# only; guard the build itself so a non-x86-64 host (or a compiler without
# -fcf-protection support) never attempts it, matching
# case64_calling_convention_changed's pattern.
if(NOT (CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$"
        AND CMAKE_C_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"))
  message(STATUS "Skipping case179_cet_protection_weakened (requires x86-64 + GCC/Clang)")
  return()
endif()

abicheck_add_case(case179_cet_protection_weakened
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_COMPILE_OPTIONS -fcf-protection=full
    V2_COMPILE_OPTIONS -fcf-protection=none
    APP_SOURCES app.c
    PLATFORMS linux
)
