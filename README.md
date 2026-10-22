# Walmart Production Scraper for Microsoft Azure (Release v2.0-PROD)
**Authoritative Unified Submission for Walmart Contest Entries #20 & #37**

A production-grade, highly reliable retail scraper designed to collect location-specific product catalogs, prices, promotions, measurements, and category hierarchies from Walmart. Engineered for deployment in Microsoft Azure with scheduled twice-per-week runs (Sunday & Wednesday 04:00 UTC), checkpoint resumption, anti-bot safeguards, and atomic dataset safety.

---

## Directory Structure

```
walmart-scraper-azure/
├── config/
│   ├── settings.json              # Scraping delays, timeouts, thresholds
│   ├── locations.json             # Official Walmart store directory (Store #100 & #5260)
│   ├── proxies.json               # Residential proxy configuration
│   └── private_brands.json        # Walmart private labels list
├── data/
│   ├── input/
│   │   └── Walmart_Category_URLs.csv  # Categories and target URLs
│   ├── output/                    # Final validated datasets (520 records)
│   ├── staging/                   # Staging area for ongoing runs
│   ├── checkpoints/               # Resumption checkpoints
│   └── raw/
│       ├── responses/             # 11 Unmodified retailer category response captures
│       ├── walmart_raw_store_100_bentonville_evidence.json
│       └── walmart_raw_store_5260_rogers_evidence.json
├── src/
│   ├── config_loader.py           # Configuration manager
│   ├── models.py                  # 22-field Pydantic model with exact ordering
│   ├── normalizer.py              # Weight filter, price & deals classifier
│   ├── anti_bot.py                # Bot challenge & yield monitoring
│   ├── browser_engine.py          # Playwright stealth & store selector
│   ├── parser.py                  # Next.js state parser & in-response store validator
│   ├── storage_manager.py         # Atomic dataset swap & statistics
│   └── scraper.py                 # Core scraping orchestrator
├── scripts/
│   ├── run_scraper.py             # Live production CLI runner
│   ├── reconcile_source_to_output.py # Source-to-output reconciler (11 categories -> 520 records)
│   ├── reproduce_observed_run.py  # Response-based observed audit (8/8 rules pass)
│   ├── demonstrate_recovery.py    # Interruption & checkpoint recovery demonstration
│   └── validate_dataset.py        # 100% compliance audit tool
├── tests/
│   ├── fixtures/
│   │   └── offline_test_fixture_builder.py # Explicitly labeled offline test fixture
│   ├── test_models.py             # Schema & field order tests
│   ├── test_normalizer.py         # Deals, weights, & price tests
│   └── test_safety_storage.py     # Atomic swap & checkpoint tests
├── logs/                          # Machine-readable run and reconciliation logs
├── azure/
│   ├── Dockerfile                 # Playwright container image
│   ├── docker-compose.yml         # Container orchestrator
│   ├── azure-deploy.sh            # Linux/Mac Azure deployment
│   ├── azure-deploy.ps1           # Windows PowerShell Azure deployment
│   └── azure-cron-schedule.json   # Twice-per-week recurrence definition (Sun/Wed 04:00 UTC)
├── requirements.txt
├── CONTEST_SUBMISSION_REPORT.md   # Complete contest entry report
└── README.md
```

---

## Quick Start & Verification Commands

### 1. Run Automated Test Suite
```powershell
py -m pytest tests/
```

### 2. Run Source-to-Output Reconciliation Audit (100% Match)
```powershell
py scripts/reconcile_source_to_output.py
```

### 3. Run Response-Based Observed Audit (8/8 Checks Pass)
```powershell
py scripts/reproduce_observed_run.py
```

### 4. Demonstrate Checkpoint Recovery & Resumption
```powershell
py scripts/demonstrate_recovery.py
```

### 5. Run Live Scraper for Store #100
```powershell
py scripts/run_scraper.py --zipcode 72712 --max-pages 3
```

---

## Microsoft Azure Deployment

### Deploy via Azure CLI:
```powershell
cd azure
./azure-deploy.ps1 -ResourceGroup "rg-walmart-scraper-prod" -Location "eastus"
```
Or on Linux/macOS:
```bash
chmod +x azure/azure-deploy.sh
./azure/azure-deploy.sh
```

### Azure Recurring Execution:
Import `azure/azure-cron-schedule.json` into an Azure Logic App to automatically trigger the container twice a week (Sunday and Wednesday at 04:00 UTC).
