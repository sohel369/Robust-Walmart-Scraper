import os
import json
import csv
from typing import Dict, List, Any, Optional

class ConfigLoader:
    """Loads and manages configurations, store locations, proxies, and categories."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            # Default to the root of the walmart-scraper-azure project
            self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        else:
            self.base_dir = os.path.abspath(base_dir)

        self.config_dir = os.path.join(self.base_dir, "config")
        self.data_dir = os.path.join(self.base_dir, "data")

    def load_settings(self) -> Dict[str, Any]:
        settings_path = os.path.join(self.config_dir, "settings.json")
        if not os.path.exists(settings_path):
            raise FileNotFoundError(f"Settings file not found at: {settings_path}")
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_locations(self) -> List[Dict[str, Any]]:
        locations_path = os.path.join(self.config_dir, "locations.json")
        if not os.path.exists(locations_path):
            raise FileNotFoundError(f"Locations file not found at: {locations_path}")
        with open(locations_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_location_by_zip(self, zipcode: str) -> Optional[Dict[str, Any]]:
        locations = self.load_locations()
        for loc in locations:
            if str(loc.get("zipcode")).strip() == str(zipcode).strip():
                return loc
        return None

    def get_default_location(self) -> Dict[str, Any]:
        locations = self.load_locations()
        for loc in locations:
            if loc.get("is_default"):
                return loc
        if locations:
            return locations[0]
        raise ValueError("No locations configured in locations.json")

    def load_private_brands(self) -> List[str]:
        brands_path = os.path.join(self.config_dir, "private_brands.json")
        if not os.path.exists(brands_path):
            return ["Great Value", "Equate", "Marketside", "Sam's Choice"]
        with open(brands_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_proxies(self) -> Dict[str, Any]:
        proxy_path = os.path.join(self.config_dir, "proxies.json")
        proxies: Dict[str, Any] = {}
        if os.path.exists(proxy_path):
            with open(proxy_path, "r", encoding="utf-8") as f:
                proxies = json.load(f)
        
        # Allow environment variable override for production secrets
        env_override = proxies.get("env_var_override", "WALMART_PROXY_URL")
        env_val = os.getenv(env_override)
        if env_val:
            proxies["enabled"] = True
            proxies["http_proxy"] = env_val
            proxies["https_proxy"] = env_val
            
        return proxies

    def load_category_urls(self, csv_filename: str = "Walmart_Category_URLs.csv") -> List[Dict[str, str]]:
        csv_path = os.path.join(self.data_dir, "input", csv_filename)
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Category CSV not found at: {csv_path}")

        categories = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = row.get("category", "").strip()
                sub = row.get("sub_category", "").strip()
                url = row.get("url", "").strip()
                if url:
                    categories.append({
                        "category": cat,
                        "sub_category": sub,
                        "url": url
                    })
        return categories
