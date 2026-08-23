"""Cryptographic security, credential protection, and SSRF validation for MiniSashimi.

Provides:
- Symmetric Fernet credential encryption (PBKDF2-HMAC-SHA256 key derivation) for API keys.
- Secret masking filters (Zero-Leak Policy) to prevent credential disclosure in application logs.
- Outbound URL and SSRF validators blocking private network ranges, AWS/cloud metadata services (169.254.169.254),
  and DNS-rebinding tricks.
- Cryptographically signed tamper-evident audit logging via HMAC-SHA256.
"""

import os
import base64
from cryptography.fernet import Fernet
import logging
import re
from typing import Any
import json
import hmac
import hashlib
from datetime import datetime, timezone
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
    """Logging filter that scrubs sensitive API tokens, passwords, and private keys from log streams."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitizes the record message and its arguments before logging."""
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return SECRET_MASK_REGEX.sub("••••••••••••", value)
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, tuple):
                return tuple(redact(item) for item in value)
            # Exceptions and custom objects can format to provider responses.
            if value is not None and not isinstance(value, (int, float, bool)):
                return SECRET_MASK_REGEX.sub("••••••••••••", str(value))
            return value

        record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(arg) for arg in record.args) if isinstance(record.args, tuple) else redact(record.args)
        return True


def sanitize_value(val: Any) -> Any:
    """Recursively replaces detected secret substrings in nested data structures with bullet masks."""
    if isinstance(val, str):
        return SECRET_MASK_REGEX.sub("••••••••••••", val)
    elif isinstance(val, dict):
        return {k: sanitize_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    return val


def is_safe_url(url: str, *, allow_loopback: bool = True) -> bool:
    """Validates an outbound URL against private-network and metadata SSRF targets.
    
    Args:
        url: Destination URL string to validate.
        allow_loopback: If True, allows localhost/loopback addresses for local LLMs (Ollama, LM Studio).
        
    Returns:
        True if URL is verified safe against SSRF vulnerabilities, False otherwise.
    """
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
                return allow_loopback
            # Translate IPv4-mapped and NAT64-wrapped IPv6 addresses
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

        # Resolve every returned address to protect against DNS-rebinding attacks
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
    """Local-provider heuristic: loopback or RFC1918 private IP, never cloud metadata service."""
    import ipaddress
    address = ipaddress.ip_address(raw_ip)
    if str(address) == "169.254.169.254":
        return False
    if isinstance(address, ipaddress.IPv6Address):
        packed = address.packed
        if packed[:12] == b"\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff":
            address = ipaddress.IPv4Address(packed[12:])
        elif packed[:4] == b"\x00\x64\xff\x9b":
            address = ipaddress.IPv4Address(int.from_bytes(packed[12:], "big"))
        elif address.sixtofour is not None:
            address = ipaddress.IPv4Address(address.sixtofour[1])
    return (
        address.is_loopback
        or (address.is_private and not address.is_link_local
            and not address.is_unspecified and not address.is_reserved
            and not address.is_multicast)
    )


def is_allowed_base_url(url: str, provider: str) -> bool:
    """Allows only approved HTTPS provider hosts or explicitly configured local services.

    Args:
        url: Provider endpoint URL.
        provider: Provider identifier string.
        
    Returns:
        True if the host is explicitly authorized.
    """
    from urllib.parse import urlparse
    import ipaddress

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment or not parsed.hostname:
        return False

    host = parsed.hostname.lower().rstrip(".")
    local_providers = {"lmstudio", "ollama", "llama.cpp", "llama.ccp", "llamacpp", "llamaccp"}
    if provider in local_providers:
        if host in _extra_hosts():
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return is_safe_url(url) and host == "localhost"
        return parsed.scheme == "http" and allowed_ip_local(str(address))

    # Public credentials must never be transmitted over plaintext HTTP
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return False
    public_hosts = {
        "api.openai.com", "api.mistral.ai", "openrouter.ai",
        "api.deepseek.com", "generativelanguage.googleapis.com",
        "api.anthropic.com",
    }
    if host not in public_hosts and host not in _extra_hosts():
        return False
    return is_safe_url(url, allow_loopback=False)


def _extra_hosts() -> set[str]:
    """Returns extra allowed hosts specified via SASHIMI_ALLOWED_BASE_HOSTS."""
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

# Load or generate cryptographic salt (mode 0600)
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
    """Ensures that the key is valid Fernet (32 url-safe base64 bytes), deriving via PBKDF2 if needed."""
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
    """Retrieves or creates a secure symmetric Fernet key with mode 0600 file permissions."""
    # 1. Prioritize environment variable
    env_key = os.getenv("SASHIMI_ENCRYPTION_KEY")
    if env_key:
        return _make_fernet_compatible(env_key.encode())

    # 2. Use local key-file fallback
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return _make_fernet_compatible(f.read())

    # 3. Generate new CSPRNG Fernet key
    key = Fernet.generate_key()
    
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    try:
        fd = os.open(KEY_FILE, flags, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except Exception as e:
        raise RuntimeError(f"Failed to create secure key file: {str(e)}")
        
    return key


# Initialize Fernet cipher suite
_key = _get_or_create_key()
_cipher_suite = Fernet(_key)

# Cryptographically derived HMAC key for audit logs
_audit_signing_key = hmac.new(_key, b"miniSashimi-Audit-Log-Signing-Key-Salt", hashlib.sha256).digest()


def encrypt_key(plain_text: str) -> str:
    """Encrypts plaintext API keys using Fernet symmetric encryption."""
    if not plain_text:
        return ""
    encrypted_text = _cipher_suite.encrypt(plain_text.encode())
    return encrypted_text.decode()


def decrypt_key(encrypted_text: str) -> str:
    """Decrypts ciphertext API keys using Fernet symmetric encryption."""
    if not encrypted_text:
        return ""
    try:
        decrypted_text = _cipher_suite.decrypt(encrypted_text.encode())
        return decrypted_text.decode()
    except Exception:
        return ""


AUDIT_DIR = os.getenv("SASHIMI_AUDIT_DIR", os.path.expanduser("~/.minisashimi"))


def log_audit_event(event_type: str, details: dict) -> None:
    """Creates a tamper-evident, HMAC-SHA256 signed audit log record (Zero-Leak Policy).
    
    Args:
        event_type: Event category (e.g. 'API_KEY_STORED', 'WORKFLOW_ABORTED').
        details: Metadata dict sanitized of any raw credentials.
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
        logging.getLogger("minisashimi.security").error(f"Failed to write secure audit log: {str(e)}")
