### Fixed

- The Markdown headline's "Deployment risk changes" row no longer counts cross-source hygiene findings. These findings now have their own row, "Cross-source hygiene findings (not counted above)", which also gives the number introduced. Each finding is counted on exactly one headline row. A byte-identical MKL self-compare previously headlined 43,490 deployment risk changes.
- Review groups and `surface_changes` no longer include persistent (pre-existing) cross-source hygiene findings unless the gate counts them. The findings themselves are still listed in `changes`, in the hygiene section and in every count. On the MKL `libmkl_rt` self-compare, this removes 43,490 single-member groups labelled "modified" and cuts the JSON report from 195 MB to 163 MB.
