"""Boundary PII Anonymization and Token Masking Middleware for MiniSashimi.

Provides bidirectional privacy preservation for LLM prompts and completions:
- Regex detectors for emails, IPv4/IPv6 addresses, API tokens, credit cards, phones, and physical addresses.
- Contextual name recognition (honorifics and identity phrases).
- Session placeholder mapping with 5,000 entry memory protection ceiling (CWE-400).
- Length-descending unmasking to prevent prefix collisions (e.g. [NAME_1] vs [NAME_10]).
"""

import re
import logging
from typing import Dict

# Regular expression patterns for sensitive data detection
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
IPV4_REGEX = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')
IPV6_REGEX = re.compile(r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|\b(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}:?){0,7}\b')
# Detects common API keys (OpenAI sk-..., Anthropic sk-ant-..., GitHub ghp-...)
API_KEY_REGEX = re.compile(r'\b(?:sk-[a-zA-Z0-9]{32,}|sk-ant-[a-zA-Z0-9-]{40,}|ghp_[a-zA-Z0-9]{36,})\b')
CREDIT_CARD_REGEX = re.compile(r'\b(?:\d[ -]*?){13,16}\b')
PHONE_REGEX = re.compile(r'\b(?:\+\d{1,3}[- ]?)?\(?\d{2,4}\)?[- ]?\d{3,4}[- ]?\d{3,6}\b')
ZIP_STREET_REGEX = re.compile(
    r'\bplz\s*\d{5}\b|\b\d{5}\s+[A-ZÄÖÜa-zäöüß]+\b|\b[A-ZÄÖÜ][a-zäöüß]+(?:str\.|straße|weg|platz|allee)\b',
    re.IGNORECASE
)
NAME_CONTEXT_REGEX = re.compile(
    r'\b(?:herr|frau|dr\.|prof\.)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)\b|'
    r'\b(?:name\s+(?:ist|is|name:))\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)\b',
    re.IGNORECASE
)

logger = logging.getLogger("minisashimi.middleware.anonymizer")


class Anonymizer:
    """Middleware for masking personally identifiable and sensitive information (Boundary Anonymization).
    
    Maintains a session translation map to allow unmasking (re-hydration) of LLM completion outputs.
    """

    def __init__(self):
        """Initializes an empty session mapping table and counter."""
        self.mask_map: Dict[str, str] = {}
        self.counter = 0

    def _replace_and_store(self, match: re.Match, token_type: str) -> str:
        """Assigns a surrogate placeholder for an entity match, reusing existing tokens for identical values."""
        original = match.group(0)
        for placeholder, val in self.mask_map.items():
            if val == original:
                return placeholder
        
        # Defense-in-depth: limit mask_map capacity to prevent memory exhaustion (CWE-400 / LLM10:2025)
        if len(self.mask_map) >= 5000:
            logger.warning("Maximum anonymization mapping capacity reached (5000 entries limit). Gracefully skipping further masking.")
            return original
            
        self.counter += 1
        placeholder = f"[{token_type}_{self.counter}]"
        self.mask_map[placeholder] = original
        return placeholder

    def mask(self, text: str) -> str:
        """Masks detected sensitive data in the input text with categorized placeholders.
        
        Args:
            text: Raw input prompt string.
            
        Returns:
            Anonymized string safe for transmission to third-party LLM providers.
        """
        if not text:
            return text
            
        # 1. Mask API Keys
        text = API_KEY_REGEX.sub(lambda m: self._replace_and_store(m, "API_KEY"), text)
        
        # 2. Mask E-Mails
        text = EMAIL_REGEX.sub(lambda m: self._replace_and_store(m, "EMAIL"), text)
        
        # 3. Mask IPs
        text = IPV4_REGEX.sub(lambda m: self._replace_and_store(m, "IP"), text)
        text = IPV6_REGEX.sub(lambda m: self._replace_and_store(m, "IP"), text)
        
        # 4. Mask Credit Cards
        text = CREDIT_CARD_REGEX.sub(lambda m: self._replace_and_store(m, "CREDIT_CARD"), text)
        
        # 5. Mask Phone Numbers
        text = PHONE_REGEX.sub(lambda m: self._replace_and_store(m, "PHONE"), text)
        
        # 6. Mask Addresses & Postal codes
        text = ZIP_STREET_REGEX.sub(lambda m: self._replace_and_store(m, "ADDRESS"), text)
        
        # 7. Contextual name masking
        def name_replacer(match: re.Match) -> str:
            name = match.group(1) or match.group(2)
            if name:
                class fake_match:
                    def __init__(self, val): self.val = val
                    def group(self, idx): return self.val
                placeholder = self._replace_and_store(fake_match(name), "NAME")
                return match.group(0).replace(name, placeholder)
            return match.group(0)
            
        text = NAME_CONTEXT_REGEX.sub(name_replacer, text)
        return text

    def unmask(self, text: str) -> str:
        """Replaces surrogate placeholders in LLM response text with original values.
        
        Args:
            text: Anonymized LLM output string.
            
        Returns:
            De-anonymized output text with restored entity values.
        """
        if not text:
            return text
            
        unmasked = text
        # Sort placeholders descending by length to prevent partial replacement corruption
        sorted_placeholders = sorted(self.mask_map.keys(), key=len, reverse=True)
        for placeholder in sorted_placeholders:
            original = self.mask_map[placeholder]
            unmasked = unmasked.replace(placeholder, original)
        return unmasked
