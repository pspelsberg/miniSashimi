import httpx
from typing import List, Dict, Any, Tuple, Optional
from openai import AsyncOpenAI
import json
import logging
import asyncio
from app.core.database import get_api_key, get_setting
from app.core.security import sanitize_value

logger = logging.getLogger("minisashimi.llm_client")

# Globaler Client-Cache für OpenAI-kompatible Provider (F-027)
_openai_clients: Dict[str, AsyncOpenAI] = {}
_httpx_client: Optional[httpx.AsyncClient] = None

def get_httpx_client() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None:
        try:
            t = max(1.0, min(float(get_setting("llm_timeout_seconds", "300.0")), 3_600.0))
        except Exception:
            t = 300.0
        _httpx_client = httpx.AsyncClient(
            timeout=t,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _httpx_client

def get_openai_client(provider: str, api_key: str, base_url: str, extra_headers: Dict[str, str]) -> AsyncOpenAI:
    import hashlib
    # F-002: Hash the API key so the raw credential is never stored as a dict key (CWE-256)
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    cache_key = f"{provider}:{key_hash}:{base_url}"
    if cache_key not in _openai_clients:
        try:
            t = max(1.0, min(float(get_setting("llm_timeout_seconds", "300.0")), 3_600.0))
        except Exception:
            t = 300.0
        _openai_clients[cache_key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=extra_headers,
            timeout=t
        )
    return _openai_clients[cache_key]

async def call_llm(model_string: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> Tuple[str, Dict[str, Any]]:
    """
    Schnittstelle zu verschiedenen LLM-Providern.
    Unterstützt Modell-Präfixe wie:
    - openai/gpt-4o
    - mistral/mistral-small-latest
    - openrouter/anthropic/claude-3.5-sonnet
    - deepseek/deepseek-chat
    - anthropic/claude-3-5-sonnet-20241022
    - google/gemini-1.5-flash
    
    Gibt (AntwortText, UsageDict) zurück.
    """
    # Parse Provider und Modellname.  This value originates from settings or
    # a request and is also written to logs; reject control characters and
    # shell/URL delimiters at the outbound boundary.
    import re
    if not isinstance(model_string, str) or len(model_string) > 256 or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.:/-]+)?", model_string):
        raise ValueError("Invalid model identifier")
    parts = model_string.split("/", 1)
    if len(parts) == 2:
        provider, model = parts[0].lower(), parts[1]
    else:
        # Fallback auf OpenAI
        provider, model = "openai", model_string
        
    # Lokale Server benötigen keinen API-Key
    if provider in ("lmstudio", "ollama", "llama.cpp", "llama.ccp", "llamacpp", "llamaccp"):
        api_key = f"local-{provider}"
    else:
        api_key = get_api_key(provider)
    
    # Automatischer Upgrade von OpenRouter auf native APIs, falls spezifische Keys vorhanden sind
    if provider == "openrouter" and "/" in model:
        sub_parts = model.split("/", 1)
        sub_provider, sub_model = sub_parts[0].lower(), sub_parts[1]
        sub_provider_map = {
            "mistralai": "mistral",
            "anthropic": "anthropic",
            "openai": "openai",
            "google": "google",
            "deepseek": "deepseek"
        }
        mapped_provider = sub_provider_map.get(sub_provider)
        if mapped_provider:
            native_key = get_api_key(mapped_provider)
            if native_key:
                logger.info(f"Spezifischer API-Key fuer '{mapped_provider}' gefunden. Leite OpenRouter-Modell '{model_string}' nativ an '{mapped_provider}' um.")
                provider = mapped_provider
                model = sub_model
                api_key = native_key
    
    # Automatischer OpenRouter-Fallback bei fehlendem spezifischen Key
    if not api_key:
        openrouter_key = get_api_key("openrouter")
        if openrouter_key and provider != "openrouter":
            logger.info(f"Kein spezifischer API-Key fuer '{provider}' gefunden. Weiche auf OpenRouter aus fuer Modell: {provider}/{model}")
            model = f"{provider}/{model}"
            provider = "openrouter"
            api_key = openrouter_key
            
    if not api_key:
        raise ValueError(f"API Key fuer Provider '{provider}' ist nicht konfiguriert. Bitte im Dashboard eintragen.")

    max_retries = 3
    delay = 1.0  # Startverzögerung in Sekunden
    
    for attempt in range(1, max_retries + 1):
        try:
            # 1. Native Anthropic Integration (via REST API, falls kein OpenRouter genutzt wird)
            if provider == "anthropic":
                return await _call_anthropic_native(model, messages, api_key, temperature)

            # 2. OpenAI-kompatible Provider
            # Lokale Provider-Base-URLs sind konfigurierbar via Setting
            lmstudio_base = get_setting("lmstudio_base_url", "http://127.0.0.1:1234/v1")
            ollama_base = get_setting("ollama_base_url", "http://127.0.0.1:11434/v1")
            llama_cpp_base = get_setting("llama_cpp_base_url", "http://127.0.0.1:8080/v1")
            
            base_urls = {
                "openai": "https://api.openai.com/v1",
                "mistral": "https://api.mistral.ai/v1",
                "openrouter": "https://openrouter.ai/api/v1",
                "deepseek": "https://api.deepseek.com",
                "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "lmstudio": lmstudio_base,
                "ollama": ollama_base,
                "llama.cpp": llama_cpp_base,
                "llama.ccp": llama_cpp_base,
                "llamacpp": llama_cpp_base,
                "llamaccp": llama_cpp_base,
            }
            
            base_url = base_urls.get(provider)
            if not base_url:
                # Fallback: Versuche es als OpenAI-kompatibel mit Standard-Endpoint zu behandeln
                base_url = "https://api.openai.com/v1"
                logger.warning(f"Unbekannter Provider '{provider}'. Versuche standardmaessig OpenAI-Schnittstelle.")

            # Validate both the URL class and the provider-specific hostname.
            from app.core.security import is_allowed_base_url
            if not is_allowed_base_url(base_url, provider):
                raise ValueError("Outbound base URL is not approved for this provider")

            # OpenRouter benötigt spezielle Header
            extra_headers = {}
            if provider == "openrouter":
                extra_headers = {
                    "HTTP-Referer": "https://github.com/peppi/miniSashimi",
                    "X-Title": "miniSashimi Orchestrator"
                }

            client = get_openai_client(provider, api_key, base_url, extra_headers)
            
            try:
                t = max(1.0, min(float(get_setting("llm_timeout_seconds", "300.0")), 3_600.0))
            except Exception:
                t = 300.0
            kwargs = {
                "model": model,
                "messages": messages,  # type: ignore
                "timeout": t,
            }
            # Omit temperature for reasoning models that do not support it
            is_reasoning = any(x in model.lower() for x in ("o1-", "o3-", "thinking", "reasoning"))
            if not is_reasoning:
                kwargs["temperature"] = temperature

            try:
                response = await client.chat.completions.create(**kwargs)
            except Exception as api_err:
                err_msg = str(api_err)
                if "temperature" in err_msg and any(x in err_msg for x in ("support", "value", "default")):
                    logger.warning("Model %s does not support temperature; retrying without it. Error: %s", model, sanitize_value(err_msg))
                    if "temperature" in kwargs:
                        del kwargs["temperature"]
                    response = await client.chat.completions.create(**kwargs)
                else:
                    raise api_err
            content = response.choices[0].message.content or ""
            
            prompt_details = getattr(response.usage, "prompt_tokens_details", None)
            cached_tokens = 0
            if prompt_details:
                cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0
                
            usage = {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
                "cache_read_tokens": cached_tokens
            }
            return content, usage
            
        except Exception as e:
            import openai
            is_auth_error = False
            if isinstance(e, openai.APIStatusError) and e.status_code in (401, 403):
                is_auth_error = True
            elif isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403):
                is_auth_error = True
            elif isinstance(e, ValueError) and any(err in str(e) for err in ("401", "403", "Unauthorized", "Forbidden", "API key", "API-Key")):
                is_auth_error = True
                
            if is_auth_error:
                logger.error("Kritischer Authentifizierungsfehler (%s/%s). Retry abgebrochen: %s", provider, model, sanitize_value(str(e)))
                raise e

            logger.warning("LLM-Aufruf fehlgeschlagen (Versuch %s/%s) fuer %s/%s: %s", attempt, max_retries, provider, model, sanitize_value(str(e)))
            if attempt == max_retries:
                logger.error("Schwerer Fehler bei LLM-Aufruf nach %s Versuchen: %s", max_retries, sanitize_value(str(e)))
                raise e
            await asyncio.sleep(delay)
            delay *= 2.0  # exponentielles Backoff

async def _call_anthropic_native(model: str, messages: List[Dict[str, str]], api_key: str, temperature: float) -> Tuple[str, Dict[str, Any]]:
    """
    Direkter API-Aufruf an Anthropic (da nicht direkt OpenAI-kompatibel).
    Nativ asynchron über httpx (F-028).
    """
    url = "https://api.anthropic.com/v1/messages"
    
    # Konvertiere System-Meldungen (Anthropic erwartet System-Prompts separat)
    system_prompt = ""
    anthropic_messages = []
    
    for msg in messages:
        if msg["role"] == "system":
            system_prompt += msg["content"] + "\n"
        else:
            anthropic_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
            
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": 4096,
        "temperature": temperature
    }
    
    if system_prompt:
        payload["system"] = system_prompt.strip()

    client = get_httpx_client()
    try:
        try:
            t = max(1.0, min(float(get_setting("llm_timeout_seconds", "300.0")), 3_600.0))
        except Exception:
            t = 300.0
        response = await client.post(url, headers=headers, json=payload, timeout=t)
        response.raise_for_status()
        res_json = response.json()
        
        content = res_json["content"][0]["text"]
        usage = {
            "prompt_tokens": res_json["usage"].get("input_tokens", 0),
            "completion_tokens": res_json["usage"].get("output_tokens", 0),
            "total_tokens": res_json["usage"].get("input_tokens", 0) + res_json["usage"].get("output_tokens", 0),
            "cache_read_tokens": res_json["usage"].get("cache_read_input_tokens", 0)
        }
        return content, usage
        
    except httpx.HTTPStatusError as e:
        logger.error("Anthropic HTTP error: status=%s", e.response.status_code)
        raise ValueError("Anthropic API request failed") from e
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.error("Ungültige Anthropic-Antwort: %s", sanitize_value(str(e)))
        raise ValueError("Invalid response from Anthropic API") from e
    except Exception as e:
        logger.error("Fehler bei nativem Anthropic-Aufruf: %s", sanitize_value(str(e)))
        raise
