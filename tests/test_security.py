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
    assert result is False  # Not rejected, oldest keys were evicted instead!
    assert len(limiter.history) < 10000  # Eviction freed space
    
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
    from app.core.database import init_db, save_setting

    init_db()
    save_setting("cost_limit_usd", "10.0")
    
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
            
    res = anonymizer._replace_and_store(FakeMatch(), "TEST")
    assert res == "new_secret_value"  # Gracefully returns original value without throwing
    assert len(anonymizer.mask_map) == 5000  # Map size remains capped

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

def test_token_limit_enforcement():
    from app.orchestrator.engine import OrchestratorEngine
    from app.core.database import save_setting
    
    engine = OrchestratorEngine("test-wf-token-limit", "dummy prompt")
    engine.state.total_tokens = 150000
    
    # Set limit to 100,000
    save_setting("token_limit", "100000")
    
    try:
        engine.check_limits()
        assert False, "Should have raised ValueError due to token limit exceeded"
    except ValueError as e:
        assert "Token-Limit" in str(e)

def test_cookie_auth_and_csrf_validation():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.adapters.inbound.rest import get_or_create_dashboard_token
    from app.core.database import init_db

    init_db()
    client = TestClient(app)
    dashboard_token = get_or_create_dashboard_token()
    
    # 1. Test POST /api/login sets cookie sashimi_session
    res = client.post("/api/login", json={"token": dashboard_token})
    assert res.status_code == 200
    assert "csrf_token" in res.json()
    csrf_token = res.json()["csrf_token"]
    assert "sashimi_session" in client.cookies
    
    # 2. Test GET /api/verify returns csrf_token when cookie is present
    res = client.get("/api/verify")
    assert res.status_code == 200
    assert res.json()["csrf_token"] == csrf_token
    
    # 3. Test mutating endpoint with cookie but WITHOUT CSRF header -> must return 403
    res = client.post("/api/settings", json={"cost_limit_usd": "12.0"})
    assert res.status_code == 403
    assert "CSRF" in res.json()["detail"]
    
    # 4. Test mutating endpoint with cookie AND correct CSRF header -> must return 200
    res = client.post("/api/settings", json={"cost_limit_usd": "12.0"}, headers={"X-CSRF-Token": csrf_token})
    assert res.status_code == 200


def test_database_permissions():
    from app.core.database import init_db, DATABASE_FILE
    
    init_db()
    assert os.path.exists(DATABASE_FILE)
    
    mode = os.stat(DATABASE_FILE).st_mode
    # Ensure that group and others permissions are 0 (e.g. 0o600 or 0o700 equivalent)
    assert (mode & 0o077) == 0


def test_quantization_unsafe_files_protection():
    import tempfile
    import shutil
    from pathlib import Path
    from app.finetuning.quantization import QuantizationHelper
    
    tmpdir = tempfile.mkdtemp()
    try:
        helper = QuantizationHelper()
        # Mock self.MODEL_ROOT to allow our test paths
        helper.MODEL_ROOT = Path(tmpdir)
        
        # Create a model dir with unsafe file
        model_dir = Path(tmpdir) / "test_model"
        model_dir.mkdir()
        unsafe_file = model_dir / "pytorch_model.bin"
        unsafe_file.write_text("dummy binary content")
        
        # Output path
        output_dir = Path(tmpdir) / "output_model"
        
        # Call quantize_gguf
        import asyncio
        async def run_gguf():
            return await helper.quantize_gguf(str(model_dir), str(output_dir))
            
        result = asyncio.run(run_gguf())
        assert result.success is False
        assert "Unsafe model files" in result.error
        
        # Bypass check by setting env var
        os.environ["SASHIMI_ALLOW_UNSAFE_MODELS"] = "true"
        try:
            result_bypass = asyncio.run(run_gguf())
            # It should bypass the unsafe check and fail on convert.py not found/existing, but NOT with Unsafe error
            assert "Unsafe model files" not in (result_bypass.error or "")
        finally:
            del os.environ["SASHIMI_ALLOW_UNSAFE_MODELS"]
            
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


