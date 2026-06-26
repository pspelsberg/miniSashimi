import os
# Configure environment variables required by security module before import
os.environ["SASHIMI_ENCRYPTION_SALT"] = "test_salt_for_unit_testing_compliance"
os.environ["SASHIMI_ENCRYPTION_KEY"] = "test_key_minimum_length_required_for_fernet"

from app.core.security import encrypt_key, decrypt_key, log_audit_event, AUDIT_DIR

def test_encrypt_decrypt_key():
    secret = "sk-openai-test-api-key-12345"
    encrypted = encrypt_key(secret)
    assert encrypted != secret
    assert len(encrypted) > 0
    
    decrypted = decrypt_key(encrypted)
    assert decrypted == secret

def test_decrypt_empty_or_invalid():
    assert decrypt_key("") == ""
    assert decrypt_key("invalid_encrypted_data") == ""

def test_log_audit_event():
    # Test secure audit logging
    log_audit_event("test_event", {"test_key": "test_value"})
    audit_file_path = os.path.join(AUDIT_DIR, "audit.log")
    
    assert os.path.exists(audit_file_path)
    with open(audit_file_path, "r") as f:
        content = f.read()
        assert "test_event" in content
        assert "test_value" in content
        assert "sig:" in content

def test_rate_limiter_capacity():
    import time
    import asyncio
    from app.main import RateLimiter
    
    limiter = RateLimiter(requests_limit=5, window_seconds=10)
    
    # Fill the history to 10,000 unique client IDs
    for i in range(10000):
        limiter.history[f"client_{i}"] = [time.time()]
        
    assert len(limiter.history) == 10000
    
    # Try to check a new client ID. It should trigger cleanup.
    # Since all are active (within 10s), capacity is still 10,000, so it should return True (rate limited/rejected)
    async def run_check():
        return await limiter.check_rate_limit("client_new")
        
    result = asyncio.run(run_check())
    assert result is True  # Rejected due to capacity limit
    
    # Clean up history by pushing timestamps into past
    past = time.time() - 20.0
    for i in range(5000):
        limiter.history[f"client_{i}"] = [past]
        
    # Check again. It should trigger cleanup, drop the 5000 expired entries, and accept the new client!
    result2 = asyncio.run(run_check())
    assert result2 is False  # Accepted because capacity was freed

def test_google_provider_config():
    from app.core.database import init_db, save_api_key, get_api_key, delete_api_key
    from app.main import ALLOWED_PROVIDERS
    
    init_db()
    assert "google" in ALLOWED_PROVIDERS
    
    save_api_key("google", "test-google-key-value-12345")
    retrieved = get_api_key("google")
    assert retrieved == "test-google-key-value-12345"
    
    deleted = delete_api_key("google")
    assert deleted is True
    assert get_api_key("google") is None
    
    deleted_again = delete_api_key("google")
    assert deleted_again is False

def test_cost_limit_enforcement():
    from app.orchestrator.engine import OrchestratorEngine
    
    engine = OrchestratorEngine("test-wf-cost-limit", "dummy prompt")
    engine.state.accumulated_cost_usd = 15.0  # Above default limit of 10.0
    
    # Try calling check_cost_limit directly
    try:
        engine.check_cost_limit()
        assert False, "Should have raised ValueError due to cost limit exceeded"
    except ValueError as e:
        assert "Kostenlimit" in str(e)
        
    # Also verify add_cost triggers check_cost_limit
    engine2 = OrchestratorEngine("test-wf-add-cost", "dummy prompt")
    # Setting cost to just under the limit so a new add_cost pushes it over
    engine2.state.accumulated_cost_usd = 9.999
    
    try:
        # Add some costs to push it over 10.0
        engine2.add_cost({"prompt_tokens": 1000, "completion_tokens": 1000}, "gpt-4")
        assert False, "Should have raised ValueError in add_cost due to cost limit"
    except ValueError as e:
        assert "Kostenlimit" in str(e)

def test_anonymizer_capacity_limit():
    from app.middleware.anonymizer import Anonymizer
    anonymizer = Anonymizer()
    
    # Fill anonymizer map to 5000 unique entries
    for i in range(5000):
        anonymizer.mask_map[f"[TEST_{i}]"] = f"secret_value_{i}"
        
    # Attempting to add one more entry should raise a ValueError
    import re
    class FakeMatch:
        def group(self, index):
            return "new_secret_value"
            
    try:
        anonymizer._replace_and_store(FakeMatch(), "TEST")
        assert False, "Should have raised ValueError due to anonymizer capacity limit"
    except ValueError as e:
        assert "capacity" in str(e)

def test_ssrf_url_validation():
    from app.core.security import is_safe_url
    
    assert is_safe_url("http://localhost:11434/v1") is True
    assert is_safe_url("http://127.0.0.1:1234/v1") is True
    assert is_safe_url("https://generativelanguage.googleapis.com") is True
    
    # Unsafe / Blocked URLs
    assert is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert is_safe_url("http://169.254.169.254") is False
    assert is_safe_url("gopher://127.0.0.1") is False
    assert is_safe_url("ftp://169.254.169.254") is False
