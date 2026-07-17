"""Pytest configuration: ensure test environment variables are set early."""
import os

os.environ.setdefault("SASHIMI_ENV", "test")
os.environ.setdefault("SASHIMI_COOKIE_SECURE", "false")
os.environ.setdefault("SASHIMI_ALLOWED_BASE_HOSTS", "test-ollama-url,test-llama-cpp-url")
os.environ.setdefault("SASHIMI_SANDBOX_COMMAND", "")
