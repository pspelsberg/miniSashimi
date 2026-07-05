from app.domain.models import ChatMessage, ChatCompletionRequest
from app.main import extract_text_content

def test_extract_text_content():
    # String input
    assert extract_text_content("hello") == "hello"
    
    # List of strings input
    assert extract_text_content(["hello", "world"]) == "hello\nworld"
    
    # List of dictionaries input (Claude/OpenAI multimodal format)
    rich_content = [
        {"type": "text", "text": "Hello"},
        {"type": "image_url", "image_url": {"url": "http://example.com/image.png"}},
        {"type": "text", "text": "World"}
    ]
    assert extract_text_content(rich_content) == "Hello\nWorld"
    
    # None input
    assert extract_text_content(None) == ""

def test_chat_message_validation():
    # String content validation
    msg1 = ChatMessage(role="user", content="ping")
    assert msg1.content == "ping"
    
    # List content validation
    msg2 = ChatMessage(role="user", content=[{"type": "text", "text": "ping"}])
    assert isinstance(msg2.content, list)
    assert msg2.content[0]["text"] == "ping"

def test_chat_completion_request_validation():
    # String stop sequence
    req1 = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="ping")],
        stop="<|EOT|>"
    )
    assert req1.stop == "<|EOT|>"
    
    # List stop sequence
    req2 = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="ping")],
        stop=["<|EOT|>", "\n"]
    )
    assert isinstance(req2.stop, list)
    assert req2.stop[0] == "<|EOT|>"

def test_call_llm_temperature_fallback():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.adapters.outbound.llm.client import call_llm
    
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "pong"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 5
    mock_response.usage.total_tokens = 10
    mock_response.usage.prompt_tokens_details = None
    
    mock_create = AsyncMock()
    mock_create.side_effect = [
        ValueError("Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' does not support 0.7 with this model.\"}}"),
        mock_response
    ]
    
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    
    with patch("app.adapters.outbound.llm.client.get_openai_client", return_value=mock_client), \
         patch("app.adapters.outbound.llm.client.get_api_key", return_value="dummy_key"):
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            content, usage = loop.run_until_complete(
                call_llm("openai/gpt-4o", [{"role": "user", "content": "ping"}], temperature=0.7)
            )
            assert content == "pong"
            assert mock_create.call_count == 2
            
            first_call_kwargs = mock_create.call_args_list[0][1]
            assert first_call_kwargs["temperature"] == 0.7
            
            second_call_kwargs = mock_create.call_args_list[1][1]
            assert "temperature" not in second_call_kwargs
        finally:
            loop.close()

def test_test_mode_override():
    from app.orchestrator.engine import OrchestratorEngine
    from app.core.database import save_setting
    
    engine = OrchestratorEngine("test-workflow-id", "test prompt")
    
    # Normally should return the configured settings
    save_setting("test_mode_enabled", "false")
    save_setting("model_thinker", "openai/gpt-4o")
    assert engine.get_effective_model("model_thinker") == "openai/gpt-4o"
    
    # In test mode, should always override to mistral/mistral-small-latest
    save_setting("test_mode_enabled", "true")
    assert engine.get_effective_model("model_thinker") == "mistral/mistral-small-latest"
    
    # Restore default
    save_setting("test_mode_enabled", "false")

def test_websocket_serialization_safety():
    import json
    from app.domain.models import WorkflowState, Task, TaskStatus
    from datetime import datetime, timezone
    
    w = WorkflowState(
        workflow_id="test_wf_serial",
        client_prompt="test",
        tasks=[
            Task(
                title="Subtask",
                assigned_model="model",
                status=TaskStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc)
            )
        ],
        is_aborted=False,
        accumulated_cost_usd=0.05,
        status=TaskStatus.RUNNING,
        started_at=datetime.now(timezone.utc)
    )
    
    # This must not raise TypeError when serializing via json
    parsed = json.loads(w.model_dump_json())
    serialized = json.dumps({"type": "workflow_update", "data": parsed})
    assert "test_wf_serial" in serialized

def test_chat_completions_disconnect():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import HTTPException
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    # Simuliert, dass die Verbindung nach kurzer Zeit getrennt wird
    disconnect_calls = []
    async def mock_is_disconnected():
        disconnect_calls.append(True)
        if len(disconnect_calls) > 1:
            return True
        await asyncio.sleep(0.01)
        return False
    mock_request.is_disconnected = mock_is_disconnected
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="ping")]
    )
    
    # Mock OrchestratorEngine.run
    async def mock_run(self):
        try:
            for _ in range(30):
                self.check_abort()
                await asyncio.sleep(0.05)
            return "done"
        except (InterruptedError, asyncio.CancelledError) as e:
            # Simuliere das Exception-Handling der Engine
            from app.domain.models import TaskStatus
            from datetime import datetime, timezone
            self.state.status = TaskStatus.ABORTED
            self.state.completed_at = datetime.now(timezone.utc)
            raise e
            
    with patch("app.main.OrchestratorEngine.run", mock_run), \
         patch("app.main.get_or_create_dashboard_token", return_value="dummy_token"), \
         patch("app.main.log_audit_event"):
         
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            raised_exception = None
            try:
                loop.run_until_complete(
                    chat_completions(chat_req, mock_request)
                )
            except HTTPException as e:
                raised_exception = e
            
            assert raised_exception is not None, "HTTPException wurde nicht geworfen"
            assert raised_exception.status_code == 499
            assert raised_exception.detail == "Workflow durch den Benutzer abgebrochen."
        finally:
            loop.close()

def test_delete_workflow_history():
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import delete_workflow_history, active_workflows
    from app.domain.models import WorkflowState, TaskStatus
    from app.core.database import init_db
    
    # Init DB
    init_db()
    
    # Prep mock states
    running_wf = WorkflowState(workflow_id="wf_running", client_prompt="running prompt", status=TaskStatus.RUNNING)
    completed_wf = WorkflowState(workflow_id="wf_completed", client_prompt="completed prompt", status=TaskStatus.COMPLETED)
    
    active_workflows.clear()
    active_workflows["wf_running"] = running_wf
    active_workflows["wf_completed"] = completed_wf
    
    # Save completed & running to DB (simulate they were loaded/saved in DB)
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.domain.models import WorkflowsTable
    with Session(engine) as session:
        # Clear database table first
        from sqlmodel import delete
        session.exec(delete(WorkflowsTable))
        session.add(WorkflowsTable(workflow_id="wf_completed", state_json=completed_wf.model_dump_json()))
        session.add(WorkflowsTable(workflow_id="wf_running", state_json=running_wf.model_dump_json()))
        session.commit()
        
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    
    with patch("app.main.log_audit_event"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(delete_workflow_history(mock_request))
            assert res["status"] == "ok"
            
            # Check memory
            assert "wf_running" in active_workflows
            assert "wf_completed" not in active_workflows
            
            # Check DB
            with Session(engine) as session:
                rows = session.exec(select(WorkflowsTable)).all()
                wids = [r.workflow_id for r in rows]
                assert "wf_running" in wids
                assert "wf_completed" not in wids
        finally:
            loop.close()

def test_abort_workflow():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import abort_workflow, active_workflows, active_tasks
    from app.domain.models import WorkflowState, TaskStatus
    
    running_wf = WorkflowState(workflow_id="wf_to_abort", client_prompt="to abort prompt", status=TaskStatus.RUNNING)
    active_workflows.clear()
    active_workflows["wf_to_abort"] = running_wf
    
    # Simuliere einen aktiven Task
    mock_task = MagicMock()
    active_tasks.clear()
    active_tasks["wf_to_abort"] = mock_task
    
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    
    with patch("app.main.log_audit_event"), \
         patch("app.orchestrator.engine.notify_dashboard") as mock_notify:
         
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(abort_workflow("wf_to_abort", mock_request))
            assert res["status"] == "ok"
            
            # Verifiziere, dass cancel() aufgerufen wurde
            mock_task.cancel.assert_called_once()
            
            # Verifiziere, dass der Status geändert wurde
            assert active_workflows["wf_to_abort"].status == TaskStatus.ABORTED
            assert active_workflows["wf_to_abort"].is_aborted is True
            
            # Verifiziere, dass das Dashboard aktualisiert wurde
            mock_notify.assert_called_once_with("wf_to_abort")
        finally:
            loop.close()
            active_tasks.clear()

def test_chat_completions_streaming():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="ping")],
        stream=True
    )
    
    # We will patch OrchestratorEngine.run to put some items into stream_queue and then finish
    async def mock_run(self):
        await self.stream_queue.put("Chunk 1")
        await self.stream_queue.put("Chunk 2")
        await self.stream_queue.put(None)
        return "Combined outputs"
        
    with patch("app.main.OrchestratorEngine.run", mock_run), \
         patch("app.main.get_or_create_dashboard_token", return_value="dummy_token"), \
         patch("app.main.log_audit_event"):
         
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                chat_completions(chat_req, mock_request)
            )
            
            # Since stream is True, it returns a StreamingResponse
            from fastapi.responses import StreamingResponse
            assert isinstance(response, StreamingResponse)
            
            # Read all generated data from the response generator
            chunks = []
            async def read_chunks():
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
            
            loop.run_until_complete(read_chunks())
            
            # We expect chunks containing "Chunk 1" and "Chunk 2"
            combined_chunks_text = "".join(chunks)
            assert "Chunk 1" in combined_chunks_text
            assert "Chunk 2" in combined_chunks_text
            assert "[DONE]" in combined_chunks_text
            
        finally:
            loop.close()

def test_parse_files_from_markdown():
    from app.main import parse_files_from_markdown
    text = """
    We created these files.
    ### FILE: test_file.py
    ```python
    print("hello world")
    ```
    And also:
    ### FILE: subdir/config.json
    ```json
    {"port": 8080}
    ```
    """
    res = parse_files_from_markdown(text)
    assert len(res) == 2
    assert res[0][0] == "test_file.py"
    assert res[0][1].strip() == 'print("hello world")'
    assert res[1][0] == "subdir/config.json"
    assert res[1][1].strip() == '{"port": 8080}'

def test_chat_completions_with_tools_non_streaming():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="please make some files")],
        tools=[{
            "type": "function",
            "function": {
                "name": "write",
                "parameters": {}
            }
        }],
        stream=False
    )
    
    async def mock_run(self):
        return """
        Here is the code:
        ### FILE: index.html
        ```html
        <h1>Hello</h1>
        ```
        """
        
    with patch("app.main.OrchestratorEngine.run", mock_run), \
         patch("app.main.get_or_create_dashboard_token", return_value="dummy_token"), \
         patch("app.main.log_audit_event"):
         
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                chat_completions(chat_req, mock_request)
            )
            assert len(response.choices) == 1
            choice = response.choices[0]
            assert choice.finish_reason == "tool_calls"
            assert choice.message.tool_calls is not None
            assert len(choice.message.tool_calls) == 1
            tool_call = choice.message.tool_calls[0]
            assert tool_call["function"]["name"] == "write"
            import json
            args = json.loads(tool_call["function"]["arguments"])
            assert args["path"] == "index.html"
            assert "<h1>Hello</h1>" in args["content"]
        finally:
            loop.close()

def test_chat_completions_with_tools_streaming():
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="please make some files")],
        tools=[{
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {}
            }
        }],
        stream=True
    )
    
    async def mock_run(self):
        await self.stream_queue.put("Some output text")
        await self.stream_queue.put(None)
        return """
        Here is the final output with file blocks:
        ### FILE: test.js
        ```js
        console.log("hello");
        ```
        """
        
    with patch("app.main.OrchestratorEngine.run", mock_run), \
         patch("app.main.get_or_create_dashboard_token", return_value="dummy_token"), \
         patch("app.main.log_audit_event"):
         
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                chat_completions(chat_req, mock_request)
            )
            
            chunks = []
            async def read_chunks():
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
            
            loop.run_until_complete(read_chunks())
            
            combined_chunks_text = "".join(chunks)
            # Should contain the initial text content
            assert "Some output text" in combined_chunks_text
            # Should contain tool call start indicator and the function name "write_file"
            assert "write_file" in combined_chunks_text
            assert "test.js" in combined_chunks_text
            # Final chunk should end with tool_calls finish_reason
            assert '"finish_reason": "tool_calls"' in combined_chunks_text
            assert "[DONE]" in combined_chunks_text
            
        finally:
            loop.close()


def test_chat_completions_tool_response_shortcut_non_streaming():
    import asyncio
    from unittest.mock import MagicMock, patch
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[
            ChatMessage(role="user", content="Erstelle hello.py"),
            ChatMessage(role="assistant", content="Ich werde hello.py erstellen.", tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "write", "arguments": "{}"}}]),
            ChatMessage(role="tool", content="Successfully written")
        ],
        stream=False
    )
    
    with patch("app.main.log_audit_event"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                chat_completions(chat_req, mock_request)
            )
            assert response.choices[0].message.content == "Ich habe die Ergebnisse der Dateioperationen empfangen. Alle Dateien wurden erfolgreich angelegt und verifiziert. Der Workflow ist abgeschlossen."
            assert response.choices[0].finish_reason == "stop"
        finally:
            loop.close()


def test_chat_completions_tool_response_shortcut_streaming():
    import asyncio
    from unittest.mock import MagicMock, patch
    from app.main import chat_completions
    from app.domain.models import ChatCompletionRequest, ChatMessage
    
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    
    chat_req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[
            ChatMessage(role="user", content="Erstelle hello.py"),
            ChatMessage(role="assistant", content="Ich werde hello.py erstellen.", tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "write", "arguments": "{}"}}]),
            ChatMessage(role="tool", content="Successfully written")
        ],
        stream=True
    )
    
    with patch("app.main.log_audit_event"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                chat_completions(chat_req, mock_request)
            )
            
            chunks = []
            async def read_chunks():
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
            
            loop.run_until_complete(read_chunks())
            combined_chunks_text = "".join(chunks)
            
            assert "Ich habe die Ergebnisse der Dateioperationen empfangen" in combined_chunks_text
            assert '"finish_reason": "stop"' in combined_chunks_text
            assert "[DONE]" in combined_chunks_text
        finally:
            loop.close()


def test_call_llm_local_models():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.adapters.outbound.llm.client import call_llm

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "local response"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 5
    mock_response.usage.total_tokens = 10
    mock_response.usage.prompt_tokens_details = None

    mock_create = AsyncMock(return_value=mock_response)
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create

    def mock_get_setting(key, default=""):
        if key == "ollama_base_url":
            return "http://test-ollama-url/v1"
        if key == "llama_cpp_base_url":
            return "http://test-llama-cpp-url/v1"
        return default

    with patch("app.adapters.outbound.llm.client.get_openai_client", return_value=mock_client) as mock_get_client, \
         patch("app.adapters.outbound.llm.client.get_setting", side_effect=mock_get_setting), \
         patch("app.adapters.outbound.llm.client.get_api_key", return_value=None):

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Test Ollama
            content, usage = loop.run_until_complete(
                call_llm("ollama/some-model", [{"role": "user", "content": "ping"}], temperature=0.7)
            )
            assert content == "local response"
            mock_get_client.assert_any_call("ollama", "local-ollama", "http://test-ollama-url/v1", {})

            # Test llama.cpp
            content, usage = loop.run_until_complete(
                call_llm("llama.cpp/another-model", [{"role": "user", "content": "ping"}], temperature=0.7)
            )
            assert content == "local response"
            mock_get_client.assert_any_call("llama.cpp", "local-llama.cpp", "http://test-llama-cpp-url/v1", {})

            # Test llama.ccp (typo variation)
            content, usage = loop.run_until_complete(
                call_llm("llama.ccp/typo-model", [{"role": "user", "content": "ping"}], temperature=0.7)
            )
            assert content == "local response"
            mock_get_client.assert_any_call("llama.ccp", "local-llama.ccp", "http://test-llama-cpp-url/v1", {})
        finally:
            loop.close()





