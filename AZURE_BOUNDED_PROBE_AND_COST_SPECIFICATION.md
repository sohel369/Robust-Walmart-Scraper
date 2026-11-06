# AZURE BOUNDED PROBE & DEPENDENCY COST SPECIFICATION (WALMART ENTRY #20)

**Package:** Walmart Scraper Entry #20  
**Authoritative Response to Review Target:** October 22 Evening Review — Item 3 & Bounded Probe Requirement  
**Next Inspectable Delivery Target:** **Proposed: October 26, 2026 (Subject to Buyer Authorization)**  

---

## 1. Precise Bounded Probe Specification

In accordance with reviewer instructions:

| Parameter | Bound / Limit | Description |
| :--- | :--- | :--- |
| **Execution Environment** | Azure Container Instances (ACI) | US East (Virginia) or US South Central (Texas) |
| **Target Store Context** | Store ID `100` | Walmart Supercenter, 406 S Walton Blvd, Bentonville, AR 72712 |
| **Target Probe Route** | 1 Department Only | Food (`/browse/food/976759`) |
| **Maximum Page Depth** | 1 Page (`page=1`) | No deep pagination during probe |
| **Maximum Record Limit** | 10 to 20 Products | Strict hard ceiling (`max_records_override=20`) |
| **Timeout Ceiling** | 15.0 seconds whole-run outer deadline | Fast fail-safe with isolated process-tree termination |
| **Total Probe Requests Limit** | Max 15 requests | Enforced per-request admission boundary |
| **Total Probe Egress Budget** | < 2.0 MB (2,097,152 bytes) | Strict byte boundary enforced on each response body |
| **Traffic Ledger** | `logs/azure_bounded_probe_live_traffic.json` | Real-time progressive ledger persisted as events occur |
| **Raw Capture Evidence** | `logs/probe_response_raw_capture.html` | Exact raw response bytes covered by SHA-256 hash |
| **Output Artifact** | `logs/azure_bounded_probe_result.json` | Stores HTTP status, store context, latency, traffic accounting |

---

## 2. Cloud Dependency & Cost Requirements

| Component | Technical Specification | Execution Unit | Unit Price | Estimated Monthly Cost (8 Runs/Mo) |
| :--- | :--- | :--- | :--- | :--- |
| **Azure Container Instances** | 1 vCPU, 2.0 GB Memory, Linux Jammy | ~15 minutes per production run | $0.0000135/vCPU-s + $0.0000015/GB-s | **$0.18 – $0.35 / month** |
| **Azure Logic Apps** | Standard Consumption (Recurrence Trigger) | 8 triggers per month (Sun & Wed 04:00 UTC) | $0.000025 per execution | **< $0.01 / month** |
| **Azure Blob Storage** | Hot LRS (Container: `walmart-scraped-data`) | ~1 MB JSON storage + 8 PUT ops | $0.018 per GB/month | **< $0.01 / month** |
| **Optional Proxy Egress** | US Residential Proxy Pool (if Datacenter ASN challenged) | ~250 MB data transfer per run | $3.50 per GB bandwidth | **$0.70 – $1.75 / month** (Optional) |
| **TOTAL ESTIMATED BUDGET** | **Pre-Configured Architecture** | **Twice-Weekly Production Sweep** | — | **$0.20 – $2.10 / month** |

> [!IMPORTANT]
> **Strict Governance Statement:**  
> Zero paid cloud services or recurring instances will be created without explicit buyer authorization.

---

## 3. Next Inspectable Delivery Date

* **Proposed Delivery Target:** **Monday, October 26, 2026 (Subject to Buyer Access Approval)**
