### Changed

- **Typed Python API names follow ADR-091.** `run_compare` and `CompareRequest` take `old_label`/`new_label` (was `old_version`/`new_version`), `acknowledgments` (was `acknowledgments_path`), `public_symbols` (was `force_public_symbols`), `debuginfod` (was `enable_debuginfod`), `follow_deps` (was `follow_dependencies`), and `include_system_declarations` (was `include_dependencies`, on `run_compare`, `run_dump` and `InputSpec`). `run_dump` takes `frontend` (was `header_backend`). `DumpRequest` takes `debuginfod` and `follow_deps`.
- **`policy` takes a profile name or a policy document.** `CompareRequest.policy` and `run_compare(policy=)` accept `str | Path` and resolve exactly as the CLI's `--policy NAME|PATH`: a built-in profile name selects the profile, anything else is a document (a packaged built-in such as `security`, or an existing path). `CompareRequest.policy_profile` and `CompareRequest.policy_document` expose the resolved pair.
- **`contract` is one typed field with the CLI's value set.** `CompareRequest.contract` and `run_compare(contract=)` accept `public`, `exports`, `all` and `auto`, mapped as `--contract` maps them (`auto` means no domain stated).

### Removed

- **The old typed spellings are removed outright.** Passing `policy_file_path`, `contract_evaluation`, `contract_mode`, `acknowledgments_path`, `force_public_symbols`, `enable_debuginfod`, `follow_dependencies`, `include_dependencies`, `header_backend` (on `run_dump`), `old_version` or `new_version` (on `run_compare`) raises `TypeError`. The migration table is in `docs/start/upgrading-to-0.7.md`.
- **`contract_evaluation=False` is refused.** Contract relevance is always evaluated, as the CLI does; the release layer raises `ValidationError` rather than dropping the request.
