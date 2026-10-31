# GIT PROVENANCE & FILE INTEGRITY MANIFEST (WALMART ENTRY #20)

**Generated UTC:** 2026-10-31T15:27:21.877224+00:00  
**Repository URL:** [https://github.com/sohel369/Robust-Walmart-Scraper.git](https://github.com/sohel369/Robust-Walmart-Scraper.git)  
**Branch:** `master`  
**HEAD Commit:** `d3fb6925905440a42f21f5f54907cbaf99eda0c4`  
**Working Tree Clean:** `False`  

## 1. Recent Authoritative Commits
```text
d3fb692 fix(pipeline): resolve reviewer edge cases for store validation, retention identity, deals, and bounded probe
2ed5ef7 docs(lineage,audit): document code review blocker resolutions and update audit lineage manifests
1211ff4 feat(offers,probe): support simultaneous volume and digital promotions and implement bounded probe diagnostic
eadd683 fix(retention): preserve records with missing UPCs via unified identity and abort on unreadable catalog
566002b fix(pagination): decouple raw yield from deduplication and gate publication on canary check
45cbdbd fix(store-verification): enforce fail-closed store validation and tie prices to verified local context
e6b1927 feat: default to unbounded full production traversal in run_scraper and Dockerfile
d4d47ae Update git provenance and audit timestamps for Walmart submission
```

## 2. Deliverable File SHA-256 Checksums
Reviewers without access to Git CLI can independently verify that the deliverable files in this ZIP package exactly match the repository state using standard SHA-256 checksums:

| File Path | Size (Bytes) | SHA-256 Checksum |
| :--- | :---: | :--- |
| `data/output/walmart_contest_submission_500_records.json` | 527811 | `f7fd7501825aec6656247b760c2bf6a5697e9e99b5b846756f59eccc5831b747` |
| `data/output/submission_audit_statistics.json` | 13772 | `939d550b309cffe8e80ac40bb22454cdcf7645881ebb7df88569c56142613975` |
| `data/input/Walmart_Category_URLs.csv` | 1752 | `07d7e42688b58e835031ecba6450c16ead974502a7ed7c7ed6499dd5566edae1` |
| `logs/walmart_production_run.log` | 16685 | `59c7a6ea3cd7b12b44248ed3ad121bec42a94317d78890927f959bb6ae498485` |
| `logs/source_to_output_reconciliation_audit.json` | 4902 | `75d315ab74e39d6d6155320433d20403898f48ad72d16fed02da98c31e2fb74d` |

## 3. Remote Verification Link
The full commit history, diffs, and trees can be inspected directly on GitHub at:
`https://github.com/sohel369/Robust-Walmart-Scraper/tree/d3fb6925905440a42f21f5f54907cbaf99eda0c4`
