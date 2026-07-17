import re
from typing import Dict, Tuple

# Regex Muster für sensible Daten
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
IPV4_REGEX = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')
IPV6_REGEX = re.compile(r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|\b(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}:?){0,7}\b')
# Erkennt typische API-Keys (OpenAI sk-..., Anthropic sk-ant-..., GitHub ghp-...)
API_KEY_REGEX = re.compile(r'\b(?:sk-[a-zA-Z0-9]{32,}|sk-ant-[a-zA-Z0-9-]{40,}|ghp_[a-zA-Z0-9]{36,})\b')
# Kreditkarten
CREDIT_CARD_REGEX = re.compile(r'\b(?:\d[ -]*?){13,16}\b')
# Telefonnummern
PHONE_REGEX = re.compile(r'\b(?:\+\d{1,3}[- ]?)?\(?\d{2,4}\)?[- ]?\d{3,4}[- ]?\d{3,6}\b')
# ZIP und Straßennamen (z.B. PLZ 12345 oder Hauptstraße/Hauptstr. / 12345 Berlin)
ZIP_STREET_REGEX = re.compile(
    r'\bplz\s*\d{5}\b|\b\d{5}\s+[A-ZÄÖÜa-zäöüß]+\b|\b[A-ZÄÖÜ][a-zäöüß]+(?:str\.|straße|weg|platz|allee)\b',
    re.IGNORECASE
)
# Kontextbasierte Erkennung von Namen (z.B. Herr/Frau Schmidt, Mein Name ist John)
NAME_CONTEXT_REGEX = re.compile(
    r'\b(?:herr|frau|dr\.|prof\.)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)\b|'
    r'\b(?:name\s+(?:ist|is|name:))\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)\b',
    re.IGNORECASE
)

import logging
logger = logging.getLogger("minisashimi.middleware.anonymizer")

class Anonymizer:
    """
    Middleware zur Maskierung von personenbezogenen und sensiblen Daten (Boundary Anonymization).
    Ermöglicht auch die Demaskierung (Rückübersetzung) von LLM-Outputs.
    """
    def __init__(self):
        # Cache, um maskierte Werte für die De-Anonymisierung zu speichern
        # Key: Platzhalter, Value: Originalwert
        self.mask_map: Dict[str, str] = {}
        self.counter = 0

    def _replace_and_store(self, match: re.Match, token_type: str) -> str:
        original = match.group(0)
        # Wenn der Wert bereits maskiert wurde, nutze den bestehenden Platzhalter
        for placeholder, val in self.mask_map.items():
            if val == original:
                return placeholder
        
        # Defense-in-depth: limit mask_map capacity to prevent memory exhaustion / DoS (CWE-400 / LLM10:2025)
        if len(self.mask_map) >= 5000:
            logger.warning("Maximum anonymization mapping capacity reached (5000 entries limit). Gracefully skipping further masking.")
            return original
            
        self.counter += 1
        placeholder = f"[{token_type}_{self.counter}]"
        self.mask_map[placeholder] = original
        return placeholder

    def mask(self, text: str) -> str:
        """
        Maskiert sensible Daten im übergebenen Text.
        """
        if not text:
            return text
            
        # 1. API Keys maskieren
        text = API_KEY_REGEX.sub(lambda m: self._replace_and_store(m, "API_KEY"), text)
        
        # 2. E-Mails maskieren
        text = EMAIL_REGEX.sub(lambda m: self._replace_and_store(m, "EMAIL"), text)
        
        # 3. IPs maskieren
        text = IPV4_REGEX.sub(lambda m: self._replace_and_store(m, "IP"), text)
        text = IPV6_REGEX.sub(lambda m: self._replace_and_store(m, "IP"), text)
        
        # 4. Kreditkarten maskieren
        text = CREDIT_CARD_REGEX.sub(lambda m: self._replace_and_store(m, "CREDIT_CARD"), text)
        
        # 5. Telefonnummern maskieren
        text = PHONE_REGEX.sub(lambda m: self._replace_and_store(m, "PHONE"), text)
        
        # 6. Postanschriften/PLZ/Straßen maskieren
        text = ZIP_STREET_REGEX.sub(lambda m: self._replace_and_store(m, "ADDRESS"), text)
        
        # 7. Namen im Kontext maskieren
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
        """
        Ersetzt Platzhalter wieder durch die Originaldaten.
        """
        if not text:
            return text
            
        unmasked = text
        # Sort placeholders descending by length to prevent partial replacement corruption (e.g., [NAME_1] prefix of [NAME_10])
        sorted_placeholders = sorted(self.mask_map.keys(), key=len, reverse=True)
        for placeholder in sorted_placeholders:
            original = self.mask_map[placeholder]
            unmasked = unmasked.replace(placeholder, original)
        return unmasked
