from sqlmodel import SQLModel, create_engine, Session, select
from app.domain.models import ApiKeysTable, SettingsTable
from app.core.security import encrypt_key, decrypt_key
import os
from datetime import datetime, timezone

env = os.getenv("SASHIMI_ENV", "production").lower()
if env == "development":
    DATABASE_FILE = "minisashimi_dev.db"
elif env == "test":
    DATABASE_FILE = "minisashimi_test.db"
else:
    DATABASE_FILE = "minisashimi.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def init_db():
    """Initialisiert die Datenbank-Tabellen und Standardeinstellungen."""
    SQLModel.metadata.create_all(engine)
    
    # Standardeinstellungen anlegen falls noch nicht vorhanden (SOTA 2026 - Sonnet 4.6, GLM 5.2, Opus 4.8, ChatGPT 5.5)
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
    with Session(engine) as session:
        yield session

# Hilfsfunktionen für API-Keys
def get_api_key(provider: str) -> Optional[str]:
    """Holt einen API-Key und entschlüsselt ihn."""
    with Session(engine) as session:
        statement = select(ApiKeysTable).where(ApiKeysTable.provider == provider)
        result = session.exec(statement).first()
        if result and result.encrypted_key:
            return decrypt_key(result.encrypted_key)
    # Fallback auf Environment-Variablen in Nicht-Produktionsumgebungen zur Agent-Isolierung (Zero-Leak)
    is_prod = os.getenv("SASHIMI_ENV", "production").lower() == "production"
    if not is_prod:
        env_var_name = f"{provider.upper().replace('-', '_')}_API_KEY"
        return os.getenv(env_var_name)
    return None

def save_api_key(provider: str, api_key: str):
    """Verschlüsselt und speichert einen API-Key."""
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
    """Löscht einen API-Key aus der Datenbank für den gegebenen Provider."""
    with Session(engine) as session:
        statement = select(ApiKeysTable).where(ApiKeysTable.provider == provider)
        result = session.exec(statement).first()
        if result:
            session.delete(result)
            session.commit()
            return True
    return False

# Hilfsfunktionen für Settings
def get_setting(key: str, default: str = "") -> str:
    with Session(engine) as session:
        statement = select(SettingsTable).where(SettingsTable.key == key)
        result = session.exec(statement).first()
        return result.value if result else default

def save_setting(key: str, value: str):
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
