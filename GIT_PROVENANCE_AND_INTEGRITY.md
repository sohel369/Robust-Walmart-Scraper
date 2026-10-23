# GIT PROVENANCE & FILE INTEGRITY MANIFEST (WALMART ENTRY #20)

**Generated UTC:** 2026-10-23T03:45:40.202380+00:00  
**Repository URL:** [https://github.com/sohel369/Robust-Walmart-Scraper.git](https://github.com/sohel369/Robust-Walmart-Scraper.git)  
**Branch:** `master`  
**HEAD Commit:** `2c8aa554ceb7b18e7235986817fa34a41754d720`  
**Working Tree Clean:** `False`  

## 1. Recent Authoritative Commits
```text
2c8aa55 Synchronize Walmart baseline run dates to 2026-10-20 and update audit logs
09e8b9e Add dataset version, provenance and lineage manifest for Walmart
74ca669 Standardize scheduling to twice-per-week
67fddb2 Preserve genuine source UPC and eliminate item ID fallback for Walmart
643452c Enforce unbounded traversal, debug truncate guard, and persistent checkpoint row restoration
3142fc9 Remove 10-page limit, enforce 100% publication gate, and retain restored rows on resume
6422f3b Add .gitignore and remove pycache bytecode from tracking
f8f7125 Clean production tree: remove obsolete test generators, finalize v2.0-PROD
```

## 2. Deliverable File SHA-256 Checksums
Reviewers without access to Git CLI can independently verify that the deliverable files in this ZIP package exactly match the repository state using standard SHA-256 checksums:

| File Path | Size (Bytes) | SHA-256 Checksum |
| :--- | :---: | :--- |
| `data/output/walmart_contest_submission_500_records.json` | 527811 | `2fe187b6deefb45ff6c150d83ca1365ae5b74697c2c678ae511de860cf8c5aae` |
| `data/output/submission_audit_statistics.json` | 13772 | `e05b54324a22dbafe841d62ef7c320d11145ca5d8d3bf1b73be85df15df27a42` |
| `data/input/Walmart_Category_URLs.csv` | 1752 | `07d7e42688b58e835031ecba6450c16ead974502a7ed7c7ed6499dd5566edae1` |
| `logs/walmart_production_run.log` | 10471 | `f9859e1f564639b3634a24ffa207d5879edc8d85e5e0c1c201400f2dbb8246dd` |
| `logs/source_to_output_reconciliation_audit.json` | 4902 | `a2f1d431b81762a4d9a643e907118dee3a04aa39ba98e996dbfa4512460647a6` |

## 3. Remote Verification Link
The full commit history, diffs, and trees can be inspected directly on GitHub at:
`https://github.com/sohel369/Robust-Walmart-Scraper/tree/2c8aa554ceb7b18e7235986817fa34a41754d720`
