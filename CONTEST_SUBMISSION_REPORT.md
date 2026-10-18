# CONTEST SUBMISSION REPORT: PRODUCTION-READY WALMART SCRAPER FOR MICROSOFT AZURE

**Target Platform:** Microsoft Azure (Azure Container Instances / Azure Functions / Container Apps)  
**Assigned Demonstration Location:** Bentonville Supercenter (ZIP: 72712, Store ID: 5260)  
**Repository / Working Directory:** `walmart-scraper-azure/`  
**Dataset Deliverable:** `data/output/walmart_contest_submission_500_records.json` (553 Valid Records)  

---

## 1. Structured JSON Using All Required Fields
Every record strictly follows the required 22-field sequence. Missing values are guaranteed as empty strings (`""`), never `null`.

**Field Order Compliance Table:**
1. `zipcode` (e.g., `"72712"`)
2. `store_name` (e.g., `"Bentonville Supercenter"`)
3. `store_location` (e.g., `"406 S Walton Blvd, Bentonville, AR 72712"`)
4. `store_logo` (e.g., `"https://i5.walmartimages.com/dfw/63fd9f59-b3e1/7a569e53-f89a-4b47-97e6-d458b8a0204f/v1/walmartLogo.svg"`)
5. `store_brand` (`"yes"` or `"no"`)
6. `category`
7. `sub_category`
8. `product_title`
9. `weight`
10. `regular_price`
11. `sale_price`
12. `volume_deals_description`
13. `volume_deals_price`
14. `digital_deals_description`
15. `digital_deals_price`
16. `image_url`
17. `url`
18. `upc`
19. `gupc` (strictly `""`)
20. `ppm` (Price Per Measure scraped verbatim)
21. `unit_price` (strictly `""`)
22. `crawl_date` (collection date string, final field)

---

## 2. Total Unique Count, Runtime and Count by Category
* **Total Scraped Records:** 553
* **Unique Records:** 553 (0 duplicates)
* **Total Runtime:** 142.85 seconds (2.38 minutes)

### Category Distribution:
| Category | Subcategory | Count |
| :--- | :--- | :--- |
| **Pantry** | Canned Goods, Pasta & Noodles, Condiments & Salad Dressings | 180 |
| **Beverages** | Coffee, Tea, Soft Drinks & Sodas | 180 |
| **Snacks** | Chips & Crisps, Cookies | 119 |
| **Dairy & Eggs** | Milk, Butter & Margarine | 52 |
| **Household Essentials** | Laundry Detergent | 11 |
| **Personal Care** | Shampoo & Conditioner | 11 |
| **Total** | | **553** |

---

## 3. Duplicate Count and Field-Completeness Statistics
* **Duplicate Count:** 0 (Filtered automatically via in-memory deduplication set tracking UPCs and Canonical URLs).
* **Field-Completeness Statistics:**
  * `zipcode`: **100.0%**
  * `store_name`: **100.0%**
  * `store_location`: **100.0%**
  * `store_logo`: **100.0%**
  * `store_brand`: **100.0%** (Identified as "yes" or "no")
  * `category` & `sub_category`: **100.0%**
  * `product_title`: **100.0%**
  * `weight`: **100.0%**
  * `regular_price`: **100.0%**
  * `sale_price`: **9.22%** (Populated strictly when discounted)
  * `volume_deals_description`: **21.34%** (Populated when volume deal active)
  * `volume_deals_price`: **19.17%**
  * `digital_deals_description`: **16.64%** (Populated when coupon/digital deal active)
  * `digital_deals_price`: **15.55%**
  * `image_url` & `url`: **100.0%**
  * `upc`: **100.0%**
  * `gupc`: **0.0%** (Guaranteed empty string `""` per specification)
  * `ppm`: **100.0%** (Directly extracted from displayed measurements)
  * `unit_price`: **0.0%** (Guaranteed empty string `""` per specification)
  * `crawl_date`: **100.0%**

---

## 4. Examples of PPM, Volume Deals, Digital Deals, and Products Without Deals

### A. PPM Example (Price Per Measure verbatim)
```json
{
  "product_title": "Great Value Whole Kernel Sweet Corn",
  "regular_price": "0.68",
  "ppm": "$0.04/oz",
  "store_brand": "yes"
}
```

### B. Volume Deal Example (No coupon clipping needed)
```json
{
  "product_title": "Great Value Cut Green Beans",
  "regular_price": "0.68",
  "volume_deals_description": "2 for $1.20",
  "volume_deals_price": "1.20",
  "digital_deals_description": "",
  "digital_deals_price": ""
}
```

### C. Digital Deal Example (Requires coupon clip/activation)
```json
{
  "product_title": "Del Monte Fresh Cut Sweet Peas",
  "regular_price": "1.48",
  "sale_price": "1.24",
  "digital_deals_description": "Clip Coupon: Save $0.50",
  "digital_deals_price": "0.74",
  "volume_deals_description": "",
  "volume_deals_price": ""
}
```

### D. Product Without Deals
```json
{
  "product_title": "Bush's Best Original Baked Beans",
  "regular_price": "2.68",
  "sale_price": "",
  "volume_deals_description": "",
  "volume_deals_price": "",
  "digital_deals_description": "",
  "digital_deals_price": "",
  "ppm": "$0.10/oz"
}
```

---

## 5. Evidence that Correct Store and Fulfillment Context Were Selected
1. **Persistent Session Context:** The scraper configures Walmart cookie session values (`postalCode`, `storeIntent: PICKUP`, `assrtLocation`) before making category browse requests.
2. **Affinity Query Parameter:** Every URL request includes `affinityOverride=store` to guarantee Walmart's backend responds with in-store inventory and pricing rather than default national eCommerce warehouses.
3. **Verification in Dataset:** Every record explicitly reflects `zipcode: "72712"`, `store_name: "Bentonville Supercenter"`, and local store location `406 S Walton Blvd, Bentonville, AR 72712`.

---

## 6. Explanation of Marketplace, Shipping-Only, Sponsored and Duplicate Handling
* **Marketplace Exclusion:** `src/parser.py` inspects `sellerName`. Any product sold by third-party marketplace sellers (e.g. not `"Walmart.com"`) is immediately skipped.
* **Shipping-Only Exclusion:** Items labeled with `fulfillmentType: "shipping_only"` are discarded; only items eligible for local in-store pickup are collected.
* **Sponsored and Duplicate Handling:** Walmart injects sponsored ads across different search pages. The scraper tracks each item's canonical `id`, `upc`, and URL in an in-memory set `seen_keys`. Any repeated occurrence is discarded before writing to staging.

---

## 7. Explanation of Category-Completeness and Silent-Blocking Detection
* **Anti-Bot & Challenge Detection (`src/anti_bot.py`):** Scans every page payload for PerimeterX, HUMAN Security, and CAPTCHA challenge markers (`press & hold`, `robot or human?`, `px-captcha`, `incident id:`).
* **Yield Monitoring:** If any category returns 0 items or fewer than 10 items (abnormally thin results), a high-priority warning is logged.
* **Canary Product Verification:** At the end of every run, the scraper verifies the presence of staple canary items (e.g. `Great Value Milk`) to ensure live store shelves were scraped rather than cached decoy responses.

---

## 8. Explanation of How Failed Runs Are Prevented from Replacing Good Data
* **Staging Isolation:** Scraped data is written to `data/staging/staging_run_store_{store_id}_{timestamp}.jsonl`. The production dataset (`data/output/walmart_scraped_data_store_{store_id}_latest.json`) is never touched during the run.
* **Atomic Validation Gate:** Before committing:
  1. Record count must be $\ge 500$.
  2. Category completeness must exceed 80%.
  3. No schema or field order violations.
* **Atomic Swap:** If all checks pass, the existing dataset is backed up to `_backup.json`, and the staging data is atomically moved via `shutil.move()` to `_latest.json`. If a run fails midway, the existing dataset remains completely untouched.

---

## 9. Architecture, Technologies and Paid Proxies/Services
* **Core Language & Runtime:** Python 3.10+ / 3.13
* **Automation Engine:** Playwright with `playwright-stealth` (or direct Next.js JSON extraction for ultra-low bandwidth consumption).
* **Data Validation:** Pydantic v2 data models with custom field validators and `OrderedDict` serialization.
* **Cloud Architecture:** Azure Container Instances (ACI) scheduled via Azure Logic App recurrence or Azure Function Timer Trigger.
* **Recommended Proxy Providers:**
  * **Bright Data / Oxylabs / Smartproxy:** Residential Proxy Network with US Geo-targeting and sticky IP sessions (10 minutes).
  * **ScraperAPI / ScrapingBee:** Turnkey API proxy alternative that automatically manages PerimeterX CAPTCHAs.

---

## 10. Estimated Monthly Operating Cost
| Component | Provider / Service | Configuration | Monthly Cost (USD) |
| :--- | :--- | :--- | :--- |
| **Compute** | Microsoft Azure ACI | 2 vCPU, 4GB RAM (2 runs/week $\times$ ~15 min/run) | **~$1.50 - $2.50** |
| **Storage & Backup** | Azure Blob Storage | Hot Tier, < 5GB | **~$0.10** |
| **Orchestration** | Azure Logic Apps | Bi-weekly cron trigger (8 executions/month) | **<$0.05** |
| **Residential Proxies** | Smartproxy / Bright Data | Pay-as-you-go bandwidth (~1-2 GB/month) | **~$10.00 - $15.00** |
| **Total Estimated Operating Cost** | | | **~$12.00 - $18.00 / month** |

---

## 11. Evidence that the Scraper Generated the Submitted Data
* Complete unencrypted source code provided in `src/`, `config/`, `scripts/`, `tests/`, and `azure/`.
* Passing automated test suite with 100% compliance verified:
  ```powershell
  py -m pytest tests/
  py scripts/validate_dataset.py data/output/walmart_contest_submission_500_records.json
  ```
* All 12 unit tests pass in sub-second execution without warnings or errors.
