from app.orchestrator.engine import extract_json

def test_extract_json_valid_clean():
    data = '[{"title": "Task 1", "assigned_model": "worker_backend", "prompt_payload": "Do something"}]'
    res = extract_json(data)
    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["title"] == "Task 1"

def test_extract_json_with_markdown_blocks():
    data = '```json\n[{"title": "Task 1", "assigned_model": "worker_backend", "prompt_payload": "Do something"}]\n```'
    res = extract_json(data)
    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["title"] == "Task 1"

def test_extract_json_with_trailing_garbage():
    data = '[{"title": "Task 1", "assigned_model": "worker_backend", "prompt_payload": "Do something"}]Some extra trailing text from the LLM!'
    res = extract_json(data)
    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["title"] == "Task 1"

def test_extract_json_invalid():
    data = 'Invalid content not containing any json'
    try:
        extract_json(data)
        assert False, "Should raise ValueError for invalid JSON"
    except ValueError:
        pass

def test_conductor_task_schema_tolerance():
    from app.orchestrator.engine import ConductorTaskSchema
    
    # 1. Missing prompt_payload, but contains alternative key "prompt"
    t1 = ConductorTaskSchema(**{"title": "Task 1", "assigned_model": "worker_backend", "prompt": "My instruction"})
    assert t1.prompt_payload == "My instruction"
    assert t1.assigned_model == "worker_backend"
    
    # 2. Missing prompt_payload and prompt, but has description
    t2 = ConductorTaskSchema(**{"title": "Task 2", "assigned_model": "worker_backend", "description": "Desc"})
    assert t2.prompt_payload == "Desc"
    
    # 3. Missing prompt_payload entirely (falls back to title)
    t3 = ConductorTaskSchema(**{"title": "Task 3", "assigned_model": "worker_backend"})
    assert t3.prompt_payload == "Task 3"
    
    # 4. Alternative model key "model" and abbreviation mapping
    t4 = ConductorTaskSchema(**{"title": "Task 4", "model": "backend", "prompt_payload": "..."})
    assert t4.assigned_model == "worker_backend"
    
    t5 = ConductorTaskSchema(**{"title": "Task 5", "model": "frontend", "prompt_payload": "..."})
    assert t5.assigned_model == "worker_frontend"
    
    t6 = ConductorTaskSchema(**{"title": "Task 6", "model": "integration", "prompt_payload": "..."})
    assert t6.assigned_model == "worker_integration"

    t7 = ConductorTaskSchema(**{"title": "Task 7", "model": "test_architect", "prompt_payload": "..."})
    assert t7.assigned_model == "test_architect"
    
    # 5. Missing title (falls back to prompt preview)
    t8 = ConductorTaskSchema(**{"model": "worker_backend", "prompt": "Implement user login routing module"})
    assert t8.title == "Task: Implement user login routing module..."

def test_parse_conductor_tasks():
    from app.orchestrator.engine import parse_conductor_tasks
    
    # Standard array format
    j1 = '[{"title": "T1", "assigned_model": "worker_backend", "prompt_payload": "Do it"}]'
    tasks1 = parse_conductor_tasks(j1)
    assert len(tasks1) == 1
    assert tasks1[0]["title"] == "T1"
    
    # Wrapped in dictionary {"tasks": [...]} (The user's failure case!)
    j2 = '{"tasks": [{"title": "T2", "assigned_model": "worker_frontend", "prompt_payload": "Build UI"}]}'
    tasks2 = parse_conductor_tasks(j2)
    assert len(tasks2) == 1
    assert tasks2[0]["title"] == "T2"
    assert tasks2[0]["assigned_model"] == "worker_frontend"
    
    # Single dictionary task representation (no array wrapper)
    j3 = '{"title": "T3", "assigned_model": "test_architect", "prompt_payload": "Test UI"}'
    tasks3 = parse_conductor_tasks(j3)
    assert len(tasks3) == 1
    assert tasks3[0]["title"] == "T3"
    assert tasks3[0]["assigned_model"] == "test_architect"
    
    # Dictionary with other array fields (e.g. {"workflow": [...]})
    j4 = '{"workflow": [{"title": "T4", "assigned_model": "worker_integration", "prompt_payload": "Integrate"}]}'
    tasks4 = parse_conductor_tasks(j4)
    assert len(tasks4) == 1
    assert tasks4[0]["title"] == "T4"
    assert tasks4[0]["assigned_model"] == "worker_integration"


