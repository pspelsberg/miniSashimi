from pathlib import Path
"""Ratchet tests for CodeUltra findings fixed in the inbound/outbound seams."""
import pytest


def test_model_and_nested_payload_bounds():
    from app.domain.models import ChatCompletionRequest, ChatMessage
    with pytest.raises(ValueError):
        ChatCompletionRequest(model="openai/gpt\n4", messages=[ChatMessage(role="user", content="ok")])
    with pytest.raises(ValueError):
        ChatMessage(role="user", content=[{"text": "x" * 100_001}])


def test_tool_path_rejects_dot_and_control_characters():
    from app.adapters.inbound.rest import validate_tool_path
    with pytest.raises(ValueError):
        validate_tool_path(".")
    with pytest.raises(ValueError):
        validate_tool_path("safe/\x1b[31mfile.py")


def test_public_provider_urls_require_tls(monkeypatch):
    from app.core.security import is_allowed_base_url
    assert is_allowed_base_url("https://api.openai.com/v1", "openai")
    assert not is_allowed_base_url("http://api.openai.com/v1", "openai")
    assert not is_allowed_base_url("https://api.openai.com:22/v1", "openai")
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    assert not is_allowed_base_url("https://api.openai.com/v1", "openai")
    monkeypatch.setenv("SASHIMI_ALLOWED_BASE_HOSTS", "unresolvable-local")
    assert is_allowed_base_url("http://unresolvable-local/v1", "ollama")


def test_graph_edges_are_indexed_and_bounded():
    from app.orchestrator.graph_engine import GraphEngine

    async def node(state):
        return state

    engine = GraphEngine()
    engine.add_node("a", node)
    engine.add_node("b", node)
    engine.add_edge("a", "b")
    assert engine.topology()["edges"][0]["from"] == "a"


def test_login_limiter_is_separate_from_api_limiter():
    from app.middleware.rate_limiter import api_rate_limiter, login_rate_limiter
    assert login_rate_limiter is not api_rate_limiter
    assert login_rate_limiter.requests_limit <= 5


def test_unspecified_local_addresses_and_sandbox_policy():
    from app.core.security import allowed_ip_local
    from app.evaluation.humaneval_runner import HumanEvalRunner
    assert not allowed_ip_local("0.0.0.0")
    assert not allowed_ip_local("::")
    with pytest.raises(ValueError):
        HumanEvalRunner._build_bwrap_command(
            "bwrap --unshare-net python3 {script}", "/tmp/work", Path("/tmp/work/candidate.py")
        )
    command = HumanEvalRunner._build_bwrap_command(
        "bwrap --unshare-net --unshare-pid --unshare-ipc --unshare-uts "
        "--unshare-user --die-with-parent --new-session --clearenv "
        "--tmpfs / --bind {workdir} /work --chdir /work --uid 65534 --gid 65534 "
        "--ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib "
        "/usr/bin/python3 {script}",
        "/tmp/work", Path("/tmp/work/candidate.py"),
    )
    assert command[-1] == "/work/candidate.py"


def test_aggregate_chat_and_dpo_payload_limits():
    from app.domain.models import ChatCompletionRequest
    from app.adapters.inbound.next_level import DPORequest
    with pytest.raises(ValueError):
        ChatCompletionRequest(
            model="openai/test",
            messages=[{"role": "user", "content": "x" * 100_000}] * 25,
        )
    with pytest.raises(ValueError):
        DPORequest(
            model_name="local/model",
            pairs=[{"prompt": "p", "chosen": "c", "rejected": "r"}] * 257,
        )


def test_humaneval_runner_execute_test_resolution(monkeypatch):
    from app.evaluation.humaneval_runner import HumanEvalRunner, HumanEvalProblem
    runner = HumanEvalRunner(model="local/test")
    problem = HumanEvalProblem(
        task_id="test/0",
        prompt="def add(a, b):\n",
        test="assert add(1, 2) == 3\n",
        entry_point="add"
    )
    # When code execution is disabled, it returns disabled status safely
    monkeypatch.setenv("SASHIMI_ENABLE_CODE_EXECUTION", "false")
    passed, error = runner._execute_test(problem, "    return a + b\n")
    assert not passed
    assert error == "code_execution_disabled"

    # When enabled with invalid sandbox command, returns sandbox_isolation_not_configured or external_sandbox_not_configured without NameError
    monkeypatch.setenv("SASHIMI_ENABLE_CODE_EXECUTION", "true")
    monkeypatch.setenv("SASHIMI_SANDBOX_COMMAND", "")
    passed, error = runner._execute_test(problem, "    return a + b\n")
    assert not passed
    assert error == "external_sandbox_not_configured"

