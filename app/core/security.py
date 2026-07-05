import os
import base64
from cryptography.fernet import Fernet
import logging
import re
from typing import Any
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Masking regex for secrets/API keys (Zero-Leak-Policy)
SECRET_MASK_REGEX = re.compile(
    r'\b(?:sk-[a-zA-Z0-9]{32,}|sk-ant-[a-zA-Z0-9-]{40,}|ghp_[a-zA-Z0-9]{36,}|key-[a-zA-Z0-9]{32,})\b',
    re.IGNORECASE
)

class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = SECRET_MASK_REGEX.sub("••••••••••••", record.msg)
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(SECRET_MASK_REGEX.sub("••••••••••••", arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)
        return True

def sanitize_value(val: Any) -> Any:
    if isinstance(val, str):
        return SECRET_MASK_REGEX.sub("••••••••••••", val)
    elif isinstance(val, dict):
        return {k: sanitize_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    return val

def is_safe_url(url: str) -> bool:
    """
    Validiert eine URL, um SSRF-Angriffe (z. B. auf Cloud-Metadaten-IPs) zu verhindern.
    """
    from urllib.parse import urlparse
    import socket
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
            
        # Cloud Metadata IPs blockieren
        blocked_ips = {"169.254.169.254"}
        
        if hostname in blocked_ips:
            return False
            
        # Resolve Hostname to check resolved IPs
        try:
            ips = socket.getaddrinfo(hostname, None)
            for ip in ips:
                ip_addr = ip[4][0]
                if ip_addr in blocked_ips:
                    return False
        except Exception:
            pass
        return True
    except Exception:
        return False

env = os.getenv("SASHIMI_ENV", "production").lower()
if env != "production":
    KEY_DIR = os.getenv("SASHIMI_KEYS_DIR", os.path.expanduser(f"~/.minisashimi_{env}"))
else:
    KEY_DIR = os.getenv("SASHIMI_KEYS_DIR", os.path.expanduser("~/.minisashimi"))

try:
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    os.chmod(KEY_DIR, 0o700)
    KEY_FILE = os.path.join(KEY_DIR, "enc_key")
    SALT_FILE = os.path.join(KEY_DIR, "enc_salt")
except Exception:
    KEY_FILE = ".enc_key"
    SALT_FILE = ".enc_salt"

# Holen oder Generieren des Salts (Wird bei Erststart generiert, CRA-konform)
SASHIMI_ENCRYPTION_SALT = os.getenv("SASHIMI_ENCRYPTION_SALT")
if not SASHIMI_ENCRYPTION_SALT:
    if os.path.exists(SALT_FILE):
        try:
            with open(SALT_FILE, "r") as f:
                SASHIMI_ENCRYPTION_SALT = f.read().strip()
        except Exception:
            pass

if not SASHIMI_ENCRYPTION_SALT:
    import secrets
    SASHIMI_ENCRYPTION_SALT = secrets.token_hex(16)
    try:
        fd = os.open(SALT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(SASHIMI_ENCRYPTION_SALT)
    except Exception as e:
        raise RuntimeError(f"Failed to create secure salt file: {str(e)}")


def _make_fernet_compatible(key: bytes) -> bytes:
    """
    Stellt sicher, dass der Schlüssel Fernet-kompatibel ist (32 url-safe base64-kodierte Bytes).
    Falls nicht, leiten wir mittels PBKDF2HMAC einen kompatiblen Schlüssel ab.
    """
    try:
        decoded = base64.urlsafe_b64decode(key)
        if len(decoded) == 32:
            return key
    except Exception:
        pass
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    
    salt = SASHIMI_ENCRYPTION_SALT.encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600000,
    )
    derived = kdf.derive(key)
    return base64.urlsafe_b64encode(derived)


def _get_or_create_key() -> bytes:
    """
    Holt den Verschlüsselungsschlüssel aus den Umgebungsvariablen
    oder generiert einen persistenten Schlüssel in einer lokalen Datei.
    """
    # 1. Priorisiere Umgebungsvariable (CRA-konform)
    env_key = os.getenv("SASHIMI_ENCRYPTION_KEY")
    if env_key:
        return _make_fernet_compatible(env_key.encode())

    # 2. Nutze lokalen Key-File Fallback
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return _make_fernet_compatible(f.read())

    # 3. Generiere neuen Schlüssel mit kryptographisch sicherem Zufall (CSPRNG)
    key = Fernet.generate_key()
    
    # Erstelle die Datei direkt mit restriktiven Rechten über os.open (Mode 0o600) (F-015)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    try:
        fd = os.open(KEY_FILE, flags, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except Exception as e:
        raise RuntimeError(f"Failed to create secure key file: {str(e)}")
        
    return key

# Fernet Instanz initialisieren
_key = _get_or_create_key()
_cipher_suite = Fernet(_key)

# Kryptographisch sicherer Ableitungsschlüssel für Audit-Logs (F-003)
import hashlib
import hmac
_audit_signing_key = hmac.new(_key, b"miniSashimi-Audit-Log-Signing-Key-Salt", hashlib.sha256).digest()


def encrypt_key(plain_text: str) -> str:
    """
    Verschlüsselt einen API-Key.
    """
    if not plain_text:
        return ""
    encrypted_text = _cipher_suite.encrypt(plain_text.encode())
    return encrypted_text.decode()

def decrypt_key(encrypted_text: str) -> str:
    """
    Entschlüsselt einen API-Key.
    """
    if not encrypted_text:
        return ""
    try:
        decrypted_text = _cipher_suite.decrypt(encrypted_text.encode())
        return decrypted_text.decode()
    except Exception:
        # Falls Entschlüsselung fehlschlägt (z. B. Key geändert)
        return ""


import json
import hmac
import hashlib
from datetime import datetime, timezone

AUDIT_DIR = os.getenv("SASHIMI_AUDIT_DIR", os.path.expanduser("~/.minisashimi"))

def log_audit_event(event_type: str, details: dict):
    """
    Erstellt einen revisionssicheren und signierten Logeintrag für ein Audit-Log (F-030).
    Sichert sensible Secrets gegen unbeabsichtigtes Leaking (CWE-532 / Zero-Leak-Policy).
    """
    sanitized_details = sanitize_value(details)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": sanitized_details
    }
    event_str = json.dumps(event)
    signature = hmac.new(_audit_signing_key, event_str.encode(), hashlib.sha256).hexdigest()
    log_line = f"{event_str} | sig:{signature}\n"
    
    try:
        os.makedirs(AUDIT_DIR, mode=0o700, exist_ok=True)
        os.chmod(AUDIT_DIR, 0o700)
        audit_file = os.path.join(AUDIT_DIR, "audit.log")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(audit_file, flags, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(log_line)
    except Exception as e:
        import logging
        logging.getLogger("minisashimi.security").error(f"Failed to write secure audit log: {str(e)}")
