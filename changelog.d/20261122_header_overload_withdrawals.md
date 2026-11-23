### Fixed

- Compare public header overloads that share a placeholder linker name by their scoped declaration identities. Removing a oneDAL SYCL queue overload or shortening the largest fixed-arity overload now reports the affected source signature.
- Detect a default argument removed from a surviving public header overload, including the oneDAL queue overload of `homogen_table::wrap`.
- Detect lost argument counts when a header overload gains required parameters. Verify the declaring header across SDK roots and skip conflicting default-argument redeclarations.
- Preserve SYCL plugin changes supported by a partial scan and report the missing inventory evidence. Account for older extractors when comparing UR adapter presence.
- Report an existing public, unexported template overload marked `= delete` as a source break, including when another overload shares its placeholder linker name.
