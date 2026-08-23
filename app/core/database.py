"""Database configuration, schema lifecycle, and key/settings repository for MiniSashimi.

Initializes SQLite database tables using SQLModel/SQLAlchemy, populates default
routing model matrix configurations, manages encrypted API key storage, and persists
session metadata using SHA-256 token digests.
"""

from typing import Optional
from sqlmodel import SQLModel, create_engine, Session, select
from app.domain.models import ApiKeysTable, SettingsTable, SessionTable
from app.core.security import encrypt_key, decrypt_key
import os
from datetime import datetime, timezone
import hashlib

env = os.getenv("SASHIMI_ENV", "production").lower()
if env == "development":
    DATABASE_FILE = "minisashimi_dev.db"
elif env == "test":
    DATABASE_FILE = "minisashimi_test.db"
else:
    DATABASE_FILE = "minisashimi.db"

DATABASE_FILE = os.getenv("SASHIMI_DATABASE_FILE", DATABASE_FILE)
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    """Initializes database schema, enforces file permissions (0600), and populates default settings."""
    SQLModel.metadata.create_all(engine)
    
    # Restrictive file permissions (0600) to protect database file locally
    if os.path.exists(DATABASE_FILE):
        try:
            os.chmod(DATABASE_FILE, 0o600)
        except OSError:
            pass
    
    # Default model matrix routing configuration
    default_settings = {
        "model_thinker": "openrouter/anthropic/claude-4.8-opus",
        "model_conductor": "openrouter/mistralai/mistral-small-latest",
        "model_worker_backend": "openrouter/openai/gpt-5.5",
        "model_worker_frontend": "openrouter/zhipu/glm-5.2",
        "model_test_architect": "openrouter/openai/gpt-5.5",
        "model_worker_integration": "openrouter/openai/gpt-4o-mini",
        "model_pre_verifier": "openrouter/zhipu/glm-5.2",
        "model_security_auditor": "openrouter/anthropic/claude-4.8-opus",
        "model_performance_scout": "openrouter/openai/gpt-5.5",
        "model_dependency_watchdog": "openrouter/anthropic/claude-4.6-sonnet",
        "model_visual_verifier": "openrouter/openai/gpt-5.5",
        "model_runtime_executor": "openrouter/mistralai/mistral-small-latest",
        "model_repository_janitor": "openrouter/mistralai/mistral-small-latest",
        "pii_masking_enabled": "true",
        "cost_limit_usd": "10.0",
        "llm_timeout_seconds": "300.0",
        "test_mode_enabled": "false",
        "lmstudio_base_url": "http://127.0.0.1:1234/v1",
        "ollama_base_url": "http://127.0.0.1:11434/v1",
        "llama_cpp_base_url": "http://127.0.0.1:8080/v1",
        "token_limit": "100000",
        "workflow_timeout_seconds": "300.0"
    }
    
    legacy_defaults = {
        "model_thinker": "openai/gpt-4o",
        "model_conductor": "mistral/mistral-small-latest",
        "model_worker_backend": "deepseek/deepseek-chat",
        "model_worker_frontend": "openrouter/google/gemini-2.5-flash",
        "model_test_architect": "deepseek/deepseek-chat",
        "model_worker_integration": "openai/gpt-4o-mini",
        "model_pre_verifier": "mistral/mistral-small-latest",
        "model_security_auditor": "deepseek/deepseek-chat",
        "model_performance_scout": "openai/gpt-4o",
        "model_dependency_watchdog": "openai/gpt-4o-mini",
        "model_visual_verifier": "openai/gpt-4o",
        "model_runtime_executor": "mistral/mistral-small-latest",
        "model_repository_janitor": "mistral/mistral-small-latest"
    }
    
    with Session(engine) as session:
        for k, v in default_settings.items():
            statement = select(SettingsTable).where(SettingsTable.key == k)
            result = session.exec(statement).first()
            if not result:
                setting = SettingsTable(key=k, value=v)
                session.add(setting)
            else:
                # Upgrade legacy defaults from both original and intermediate versions
                is_legacy = (
                    result.value == legacy_defaults.get(k) or
                    result.value in [
                        "openrouter/anthropic/claude-3.5-sonnet",
                        "openrouter/anthropic/claude-4.6-sonnet",
                        "openrouter/deepseek/deepseek-chat",
                        "openrouter/google/gemini-2.5-flash",
                        "openrouter/openai/gpt-4o-mini",
                        "openrouter/deepseek/deepseek-r1",
                        "openai/gpt-4o",
                        "openai/gpt-4o-mini"
                    ]
                )
                if is_legacy:
                    result.value = v
                    result.updated_at = datetime.now(timezone.utc)
                    session.add(result)
        session.commit()


def get_db_session():
    """FastAPI dependency yielding a managed SQLModel Session."""
    with Session(engine) as session:
        yield session


def get_api_key(provider: str) -> Optional[str]:
    """Retrieves and decrypts the stored API key for the specified provider."""
    with Session(engine) as session:
        statement = select(ApiKeysTable).where(ApiKeysTable.provider == provider)
        result = session.exec(statement).first()
        if result and result.encrypted_key:
            return decrypt_key(result.encrypted_key)
    # Non-production environment fallback
    is_prod = os.getenv("SASHIMI_ENV", "production").lower() == "production"
    if not is_prod:
        env_var_name = f"{provider.upper().replace('-', '_')}_API_KEY"
        return os.getenv(env_var_name)
    return None


def save_api_key(provider: str, api_key: str) -> None:
    """Encrypts and persists the API key for the specified provider."""
    with Session(engine) as session:
        statement = select(ApiKeysTable).where(ApiKeysTable.provider == provider)
        result = session.exec(statement).first()
        encrypted = encrypt_key(api_key)
        if result:
            result.encrypted_key = encrypted
            result.updated_at = datetime.now(timezone.utc)
            session.add(result)
        else:
            new_key = ApiKeysTable(provider=provider, encrypted_key=encrypted)
            session.add(new_key)
        session.commit()


def delete_api_key(provider: str) -> bool:
    """Deletes the stored API key for the specified provider."""
    with Session(engine) as session:
        statement = select(ApiKeysTable).where(ApiKeysTable.provider == provider)
        result = session.exec(statement).first()
        if result:
            session.delete(result)
            session.commit()
            return True
    return False


def get_setting(key: str, default: str = "") -> str:
    """Retrieves a configuration setting value from SettingsTable."""
    with Session(engine) as session:
        statement = select(SettingsTable).where(SettingsTable.key == key)
        result = session.exec(statement).first()
        return result.value if result else default


def save_setting(key: str, value: str) -> None:
    """Updates or creates a configuration key-value pair in SettingsTable."""
    with Session(engine) as session:
        statement = select(SettingsTable).where(SettingsTable.key == key)
        result = session.exec(statement).first()
        if result:
            result.value = value
            result.updated_at = datetime.now(timezone.utc)
            session.add(result)
        else:
            new_setting = SettingsTable(key=key, value=value)
            session.add(new_setting)
        session.commit()


def _digest(value: str) -> str:
    """Computes SHA-256 hex digest for credential hashing."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_session(session_id: str, token: str, csrf_token: str, expires_at: datetime) -> None:
    """Persists session metadata using SHA-256 hashes only (raw tokens never enter database)."""
    with Session(engine) as session:
        session.add(SessionTable(
            session_id=session_id,
            token_hash=_digest(token),
            csrf_hash=_digest(csrf_token),
            expires_at=expires_at,
        ))
        session.commit()


def get_session(session_id: str) -> Optional[SessionTable]:
    """Retrieves active session and automatically purges expired records."""
    with Session(engine) as session:
        result = session.get(SessionTable, session_id)
        if result is None:
            return None
        expires_at = result.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            session.delete(result)
            session.commit()
            return None
        return result


def delete_session(session_id: str) -> None:
    """Deletes an active session record upon logout."""
    with Session(engine) as session:
        result = session.get(SessionTable, session_id)
        if result:
            session.delete(result)
            session.commit()


def delete_expired_sessions() -> int:
    """Purges all expired sessions from SessionTable."""
    from sqlmodel import delete

    with Session(engine) as session:
        statement = delete(SessionTable).where(
            SessionTable.expires_at <= datetime.now(timezone.utc)
        )
        result = session.exec(statement)
        session.commit()
        return result.rowcount or 0
