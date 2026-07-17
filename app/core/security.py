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
    r"(?i)(?:"
    r"sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"key-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|"
    r"(?:authorization|x-api-key|api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?"
    r"[A-Za-z0-9._~+/=-]{12,}"
    r")"
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
    """Validate an outbound URL against private-network and metadata SSRF targets."""
    from urllib.parse import urlparse
    import ipaddress
    import socket

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        def allowed_ip(raw_ip: str) -> bool:
            address = ipaddress.ip_address(raw_ip)
            if str(address) == "169.254.169.254":
                return False
            # Loopback is required for explicitly configured local LLM services.
            if address.is_loopback:
                return True
            # Translate IPv4-mapped and NAT64-wrapped IPv6 addresses so that
            # they are evaluated against the embedded IPv4 range instead of
            # being reported as link-local / reserved by the IPv6 classifier.
            if isinstance(address, ipaddress.IPv6Address):
                packed = address.packed
                if packed[:12] == b"\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff":
                    address = ipaddress.IPv4Address(packed[12:])
                elif packed[:4] == b"\x00\x64\xff\x9b":
                    address = ipaddress.IPv4Address(int.from_bytes(packed[12:], "big"))
                elif address.sixtofour is not None:
                    address = ipaddress.IPv4Address(address.sixtofour[1])
            return not (
                address.is_private
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            )

        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            return allowed_ip(str(literal))

        # Operators can pre-register placeholder hostnames (e.g. local test
        # environments) that have no public DNS record. Such entries bypass
        # the IP address check entirely.
        if hostname in _extra_hosts():
            return True

        # Resolve every returned address; DNS-rebinding or local destinations
        # are caught when at least one address fails the allowlist.
        try:
            addresses = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            return False
        if not addresses:
            return False
        for item in addresses:
            if not allowed_ip(item[4][0]):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def allowed_ip_local(raw_ip: str) -> bool:
    """Local-provider heuristic: loopback or RFC1918, never metadata."""
    import ipaddress
    address = ipaddress.ip_address(raw_ip)
    if str(address) == "169.254.169.254":
        return False
    if isinstance(address, ipaddress.IPv6Address):
        packed = address.packed
        if packed[:12] == b"\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff":
            address = ipaddress.IPv4Address(packed[12:])
    return address.is_loopback or (address.is_private and not address.is_link_local)


def is_allowed_base_url(url: str, provider: str) -> bool:
    """Allow only known public provider hosts or loopback local services."""
    from urllib.parse import urlparse

    if not is_safe_url(url):
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    local_providers = {"lmstudio", "ollama", "llama.cpp", "llama.ccp", "llamacpp", "llamaccp"}
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if provider in local_providers:
        # Local providers may be configured against any loopback or RFC1918
        # address, or a hostname listed in SASHIMI_ALLOWED_BASE_HOSTS.
        # Reject cloud metadata and link-local destinations only.
        try:
            import ipaddress
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                return host in _extra_hosts()
            return allowed_ip_local(str(address))
        except Exception:
            return False
    public_hosts = {
        "api.openai.com", "api.mistral.ai", "openrouter.ai",
        "api.deepseek.com", "generativelanguage.googleapis.com",
        "api.anthropic.com",
    }
    return host in public_hosts or host in _extra_hosts() or host in local_hosts


def _extra_hosts() -> set[str]:
    return {
        entry.strip().lower()
        for entry in os.getenv("SASHIMI_ALLOWED_BASE_HOSTS", "").split(",")
        if entry.strip()
    }

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
