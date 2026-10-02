### Fixed

- An enum member is classified as an end-of-list sentinel
  (`enum_last_member_value_changed`, a risk rather than a break) only when its
  name looks like an end marker **and** it holds the largest value among its
  peers on both sides. Previously the name alone decided, so a mid-list
  `E_MAX` whose value changed was demoted from a real break. Peers exclude
  other sentinel-named members and 32/64-bit width-forcing values
  (`FORCE_32BIT = 0x7FFFFFFF`, Vulkan's `*_MAX_ENUM`), so those idioms still
  classify as before. The serialization-tag detector applies the same rule.
