### Fixed

- Compare public header overloads that share a placeholder linker name by their scoped declaration identities. Removing a oneDAL SYCL queue overload or shortening the largest fixed-arity overload now reports the affected source signature.
- Detect a default argument removed from a surviving public header overload, including the oneDAL queue overload of `homogen_table::wrap`.
