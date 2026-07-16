#ifndef CASE182_H
#define CASE182_H

/* ---- Public API (declared here, exported) ---- */
int public_api(int x);

/* Note: internal_helper() is intentionally NOT declared here. It is a
 * default-visibility symbol that the linker exports anyway (no
 * -fvisibility=hidden, no version script hiding it) — an accidental export,
 * the same pattern audited by case143_audit_accidental_export.
 */

#endif
