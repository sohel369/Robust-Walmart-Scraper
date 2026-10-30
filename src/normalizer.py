import re
from typing import Tuple, List, Optional

class DataNormalizer:
    """Handles field sanitization, price normalization, weight validation, and deals parsing."""

    def __init__(self, private_brands: Optional[List[str]] = None):
        self.private_brands = [b.lower() for b in (private_brands or [
            "great value", "equate", "marketside", "sam's choice", "parent's choice",
            "mainstays", "pen+gear", "no boundaries", "time and tru", "george",
            "wonder nation", "ozark trail", "hyper tough", "onn", "onn."
        ])]

        # Regex pattern for approximate weight detection
        self.approx_weight_pattern = re.compile(
            r'\b(?:approx|approximately|approx\.|estimated|est\.)\b',
            re.IGNORECASE
        )

        # Regex patterns for price matching
        self.price_pattern = re.compile(r'(\d+(?:\.\d{1,2})?)')

        # Regex for volume deals (no clipping required)
        self.volume_deal_pattern = re.compile(
            r'(?:\d+\s+for\s+\$?\d+(?:\.\d{2})?|'
            r'buy\s+\d+,\s*get\s+\d+\s+free|'
            r'buy\s+\d+\s+get\s+\d+\s+free|'
            r'bogo|'
            r'mix\s+(?:or|and)\s+match\s+\d+\s+for\s+\$?\d+(?:\.\d{2})?|'
            r'save\s+\$?\d+(?:\.\d{2})?\s+when\s+you\s+buy\s+\d+|'
            r'must\s+buy\s+\d+)',
            re.IGNORECASE
        )

        # Regex for digital deals (clip, coupon, activate required)
        self.digital_deal_pattern = re.compile(
            r'(?:clip\s+(?:and\s+save\s+)?coupon|'
            r'clipped\s+coupon|'
            r'with\s+digital\s+coupon|'
            r'digital\s+coupon|'
            r'activate\s+coupon|'
            r'save\s+\$?\d+(?:\.\d{2})?\s+with\s+(?:digital\s+)?coupon)',
            re.IGNORECASE
        )

        # Exclusions for cashback/walmart cash
        self.cashback_pattern = re.compile(
            r'(?:walmart\s+cash|cashback|cash\s+back|manufacturer\s+offer)',
            re.IGNORECASE
        )

    def is_approximate_weight(self, weight_str: str) -> bool:
        """
        Returns True if the weight contains 'approx', 'approximately', or equivalent.
        Products with approximate weights MUST be excluded per data rules.
        """
        if not weight_str:
            return False
        return bool(self.approx_weight_pattern.search(weight_str))

    def clean_price(self, price_raw: str) -> str:
        """
        Formats prices to consistent numeric format, e.g. '12.98'.
        Returns empty string if invalid or absent.
        """
        if not price_raw:
            return ""
        
        # Remove commas, clean text
        cleaned = str(price_raw).replace(",", "").strip()
        match = self.price_pattern.search(cleaned)
        if match:
            val = float(match.group(1))
            return f"{val:.2f}"
        return ""

    def is_walmart_private_brand(self, title: str, brand: str) -> str:
        """
        Returns 'yes' if identified as a Walmart private-label product, else 'no'.
        """
        brand_clean = (brand or "").lower().strip()
        title_clean = (title or "").lower().strip()

        for pb in self.private_brands:
            if pb == brand_clean:
                return "yes"
            # Title starts with or contains the exact private label name
            if re.search(rf'\b{re.escape(pb)}\b', title_clean):
                return "yes"

        return "no"

    def extract_ppm(self, raw_ppm: str) -> str:
        """
        Extracts displayed Price Per Measure (PPM) directly without modifying or calculating.
        Example: '$0.25/oz', '$3.99/lb', '$0.50/ct'. Returns empty string if not displayed.
        """
        if not raw_ppm:
            return ""
        ppm_str = str(raw_ppm).strip()
        # Ensure it looks like a measurement unit
        if re.search(r'\$?\d+(?:\.\d{1,2})?\s*/\s*[a-zA-Z]+', ppm_str):
            return ppm_str
        return ppm_str

    def parse_deals(
        self,
        promo_badge: str,
        deal_description: str,
        displayed_promo_price: str
    ) -> Tuple[str, str, str, str]:
        """
        Classifies promotions into Volume Deals vs Digital Deals independently,
        allowing a combined volume promotion plus digital coupon to populate both fields.
        Preserves each offer and its quantity/clipping/eligibility/limit/validity terms
        without treating savings/cashback/coupon-discounts as payable prices.
        Returns:
            (volume_deals_description, volume_deals_price, digital_deals_description, digital_deals_price)
        """
        badge = (promo_badge or "").strip()
        desc = (deal_description or "").strip()
        combined_text = f"{badge} {desc}".strip()
        if not combined_text:
            return ("", "", "", "")

        vol_desc = ""
        vol_price = ""
        dig_desc = ""
        dig_price = ""

        # 1. Volume Deals Extraction
        is_vol = bool(
            self.volume_deal_pattern.search(combined_text) or
            ("for $" in combined_text.lower()) or
            ("bogo" in combined_text.lower())
        )
        if is_vol:
            vol_match = self.volume_deal_pattern.search(combined_text)
            if vol_match:
                vol_desc = vol_match.group(0).strip()
                if desc and desc not in vol_desc and not self.digital_deal_pattern.search(desc):
                    vol_desc = f"{vol_desc} ({desc})"
            elif "for $" in combined_text.lower() or "bogo" in combined_text.lower():
                vol_desc = badge if ("for $" in badge.lower() or "bogo" in badge.lower()) else combined_text

            # Extract payable bundle price if explicitly specified (e.g. '2 for $5' -> '5.00')
            price_in_vol = re.search(r'for\s+\$?(\d+(?:\.\d{2})?)', vol_desc, re.I)
            if price_in_vol:
                vol_price = self.clean_price(price_in_vol.group(1))
            elif displayed_promo_price and not self.digital_deal_pattern.search(combined_text):
                vol_price = self.clean_price(displayed_promo_price)

        # 2. Digital Deals Extraction
        is_dig = bool(self.digital_deal_pattern.search(combined_text))
        if is_dig:
            dig_match = self.digital_deal_pattern.search(combined_text)
            if dig_match:
                if self.digital_deal_pattern.search(desc):
                    dig_desc = desc
                elif self.digital_deal_pattern.search(badge):
                    dig_desc = badge
                else:
                    dig_desc = dig_match.group(0).strip()

            # Preserve clipping/quantity/limit/validity terms
            clean_promo = self.clean_price(displayed_promo_price)
            savings_match = re.search(r'save\s+\$?(\d+(?:\.\d{2})?)', dig_desc, re.I)
            off_match = re.search(r'\$?(\d+(?:\.\d{2})?)\s+off', dig_desc, re.I)
            with_coupon_price = re.search(r'\$?(\d+(?:\.\d{2})?)\s+with\s+(?:digital\s+)?coupon', dig_desc, re.I)

            if with_coupon_price:
                dig_price = self.clean_price(with_coupon_price.group(1))
            elif clean_promo:
                # Disallow treating pure dollar-off savings as payable price
                is_pure_savings = False
                if savings_match and self.clean_price(savings_match.group(1)) == clean_promo:
                    is_pure_savings = True
                if off_match and self.clean_price(off_match.group(1)) == clean_promo:
                    is_pure_savings = True

                # If promo price is already used by volume deal, do not duplicate into digital price
                if not is_pure_savings and not (is_vol and clean_promo == vol_price):
                    if not self.cashback_pattern.search(dig_desc):
                        dig_price = clean_promo

        # 3. Cash Back / Walmart Cash Check
        if self.cashback_pattern.search(combined_text) and not self.digital_deal_pattern.search(combined_text):
            dig_desc = ""
            dig_price = ""

        return (vol_desc, vol_price, dig_desc, dig_price)
