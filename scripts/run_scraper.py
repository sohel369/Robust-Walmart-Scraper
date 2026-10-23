import os
import sys
import argparse
import json

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.scraper import WalmartProductionScraper

def main():
    parser = argparse.ArgumentParser(description="Run Walmart Production Scraper")
    parser.add_argument("--zipcode", type=str, default="72712", help="Target Walmart store ZIP code")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional max pages override per subcategory (default: None for complete unbounded traversal)")
    args = parser.parse_args()

    scraper = WalmartProductionScraper()
    stats = scraper.execute_run(zipcode=args.zipcode, max_pages_override=args.max_pages)
    
    print("\n" + "="*50)
    print("RUN SUMMARY STATISTICS:")
    print("="*50)
    print(json.dumps(stats, indent=2))

if __name__ == "__main__":
    main()
