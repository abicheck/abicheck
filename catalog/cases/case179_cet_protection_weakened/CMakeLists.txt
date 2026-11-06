# Intel CET (-fcf-protection, .note.gnu.property IBT/SHSTK bits) is x86-64
# only; guard the build itself so a non-x86-64 host (or a compiler without
# -fcf-protection support) never attempts it, matching
# case64_calling_convention_changed's pattern.
if(NOT (CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$"
        AND CMAKE_C_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"))
  message(STATUS "Skipping case179_cet_protection_weakened (requires x86-64 + GCC/Clang)")
  return()
endif()

# Force the linker to mark v1 IBT+SHSTK: crt objects without the GNU property
# note (e.g. a non-CET GCC build) otherwise AND the bits away, leaving v1
# indistinguishable from v2. Guarded for linkers without -z ibt/shstk; the
# ground-truth fixture_preconditions then SKIP the case instead of FAILing.
include(CheckLinkerFlag)
check_linker_flag(C "LINKER:-z,ibt,-z,shstk" ABICHECK_LINKER_HAS_Z_IBT_SHSTK)
set(_case179_v1_link "")
if(ABICHECK_LINKER_HAS_Z_IBT_SHSTK)
  set(_case179_v1_link -Wl,-z,ibt -Wl,-z,shstk)
endif()

abicheck_add_case(case179_cet_protection_weakened
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_COMPILE_OPTIONS -fcf-protection=full
    V1_LINK_OPTIONS ${_case179_v1_link}
    V2_COMPILE_OPTIONS -fcf-protection=none
    APP_SOURCES app.c
    PLATFORMS linux
)
