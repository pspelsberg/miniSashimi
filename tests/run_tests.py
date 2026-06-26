import os
import sys
import tempfile

# Create a temporary directory for unit testing to avoid writing to ~/.minisashimi (F-040)
test_temp_dir = tempfile.TemporaryDirectory()

# Configure environment variables required by security module before import
os.environ["SASHIMI_ENCRYPTION_SALT"] = "test_salt_for_unit_testing_compliance"
os.environ["SASHIMI_ENCRYPTION_KEY"] = "test_key_minimum_length_required_for_fernet"
os.environ["SASHIMI_KEYS_DIR"] = test_temp_dir.name
os.environ["SASHIMI_AUDIT_DIR"] = test_temp_dir.name

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_anonymizer import test_anonymizer_mask_and_unmask
from tests.test_security import test_encrypt_decrypt_key, test_decrypt_empty_or_invalid, test_log_audit_event, test_rate_limiter_capacity, test_google_provider_config, test_cost_limit_enforcement, test_anonymizer_capacity_limit, test_ssrf_url_validation
from tests.test_chat_completions import (
    test_extract_text_content, test_chat_message_validation, test_chat_completion_request_validation,
    test_call_llm_temperature_fallback, test_test_mode_override, test_websocket_serialization_safety,
    test_chat_completions_disconnect, test_delete_workflow_history, test_abort_workflow,
    test_chat_completions_streaming, test_parse_files_from_markdown,
    test_chat_completions_with_tools_non_streaming, test_chat_completions_with_tools_streaming,
    test_chat_completions_tool_response_shortcut_non_streaming, test_chat_completions_tool_response_shortcut_streaming,
    test_call_llm_local_models
)
from tests.test_extract_json import (
    test_extract_json_valid_clean,
    test_extract_json_with_markdown_blocks,
    test_extract_json_with_trailing_garbage,
    test_extract_json_invalid,
    test_conductor_task_schema_tolerance,
    test_parse_conductor_tasks
)

if __name__ == "__main__":
    print("Running miniSashimi Unit Tests...")
    try:
        test_extract_json_valid_clean()
        test_extract_json_with_markdown_blocks()
        test_extract_json_with_trailing_garbage()
        test_extract_json_invalid()
        test_conductor_task_schema_tolerance()
        test_parse_conductor_tasks()
        print("[PASS] test_extract_json_* & test_conductor_task_schema_tolerance & test_parse_conductor_tasks")

        test_anonymizer_mask_and_unmask()
        print("[PASS] test_anonymizer_mask_and_unmask")
        
        test_encrypt_decrypt_key()
        print("[PASS] test_encrypt_decrypt_key")
        
        test_decrypt_empty_or_invalid()
        print("[PASS] test_decrypt_empty_or_invalid")
        
        test_log_audit_event()
        print("[PASS] test_log_audit_event")
        
        test_rate_limiter_capacity()
        print("[PASS] test_rate_limiter_capacity")
        
        test_google_provider_config()
        print("[PASS] test_google_provider_config")
        
        test_cost_limit_enforcement()
        print("[PASS] test_cost_limit_enforcement")
        
        test_anonymizer_capacity_limit()
        print("[PASS] test_anonymizer_capacity_limit")
        
        test_ssrf_url_validation()
        print("[PASS] test_ssrf_url_validation")

        test_extract_text_content()
        print("[PASS] test_extract_text_content")

        test_chat_message_validation()
        print("[PASS] test_chat_message_validation")

        test_chat_completion_request_validation()
        print("[PASS] test_chat_completion_request_validation")

        test_call_llm_temperature_fallback()
        print("[PASS] test_call_llm_temperature_fallback")

        test_call_llm_local_models()
        print("[PASS] test_call_llm_local_models")
        
        test_test_mode_override()
        print("[PASS] test_test_mode_override")
        
        test_websocket_serialization_safety()
        print("[PASS] test_websocket_serialization_safety")
        
        test_chat_completions_disconnect()
        print("[PASS] test_chat_completions_disconnect")
        
        test_delete_workflow_history()
        print("[PASS] test_delete_workflow_history")
        
        test_abort_workflow()
        print("[PASS] test_abort_workflow")
        
        test_chat_completions_streaming()
        print("[PASS] test_chat_completions_streaming")

        test_parse_files_from_markdown()
        print("[PASS] test_parse_files_from_markdown")

        test_chat_completions_with_tools_non_streaming()
        print("[PASS] test_chat_completions_with_tools_non_streaming")

        test_chat_completions_with_tools_streaming()
        print("[PASS] test_chat_completions_with_tools_streaming")
        
        test_chat_completions_tool_response_shortcut_non_streaming()
        print("[PASS] test_chat_completions_tool_response_shortcut_non_streaming")
        
        test_chat_completions_tool_response_shortcut_streaming()
        print("[PASS] test_chat_completions_tool_response_shortcut_streaming")
        
        print("\nAll tests passed successfully!")
        sys.exit(0)
    except AssertionError as e:
        import traceback
        traceback.print_exc()
        print(f"\n[FAIL] Test assertion failed: {str(e)}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] Test error: {str(e)}")
        sys.exit(1)
    finally:
        test_temp_dir.cleanup()
