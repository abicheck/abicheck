### Fixed

- **`inline_body_references_renamed_member` fires when the renamed member reads as a compatible addition** — the pimpl rename check paired a removed field only with `type_field_added`. A renamed member that lands at the end of the internal record reads `type_field_added_compatible`, so an inline public accessor reading it was never flagged. Every field-removal kind now pairs with every field-addition kind.
