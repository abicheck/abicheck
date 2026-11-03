# An exported instantiation of a public class template is not in the public contract

**Status (2026-11-03): OPEN.** Needs a contract-semantics ruling (ADR), not a
detector fix.

Trigger: catalog `case180_symbol_binding_lost_unique` compared under the
default `--contract public` (live or stored): `NO_CHANGE`, with
`symbol_binding_lost_unique` recorded and excluded as out of contract. The
same snapshots under `--contract all` give `COMPATIBLE_WITH_RISK`. The catalog
runner passes the case only because it compares every case unscoped
(`scope.public: false`, `tests/_legacy_scope.py`) unless a case opts in.

The chain, link by link:

1. **Fixed (2026-11-03).** The finding's symbol is a function-local static
   (`_ZZN8RegistryI6WidgetE8instanceEvE5value`). It is not a declaration, so
   it is never in a header-derived public symbol set, and the public-surface
   filter excluded it with the reason `not-exported` although it is in the
   dynamic symbol table. It is now judged by its enclosing function
   (`model/owner_recovery.local_entity_owner`): for a public **inline
   function** the local static's binding loss is reported
   (`tests/test_local_static_unique_binding_scope.py`; a private-header
   control stays out).
2. **Open.** The enclosing function here is `Registry<Widget>::instance()`,
   an implicit instantiation made in the library's `.cpp`. Neither header
   frontend emits an uninstantiated class template, so the public surface
   holds no `Registry<Widget>::instance` and the owner is itself classified
   `exported_not_public`, described as an instantiation of an *external*
   template although `Registry` is declared in the library's own public
   header.

What a fix has to decide: whether an exported instantiation of a class
template declared in a public header, over template arguments that are
themselves public, belongs to the public contract — and with what evidence
(the v55 `public_header_identifiers` index spells both names). That changes
contract membership for every such instantiation, so it needs an ADR-049
amendment and a measurement against the catalog's deliberately internal cases
(case119/case184 must stay out). Globally switching to `--contract all` is not
a fix: on the catalog it also moves 118, 119 and 184.

Applicability: Clang never emits `STB_GNU_UNIQUE`, so the transition does not
exist there (`not_applicable_toolchains` in ground truth).
