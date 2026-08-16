"""Deterministic architectural ratchets for the modular-monolith boundaries."""
import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
APP = ROOT / "app"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_inbound_adapters_do_not_import_outbound_adapters_directly():
    inbound = APP / "adapters" / "inbound"
    for path in inbound.rglob("*.py"):
        assert not any(name.startswith("app.adapters.outbound") for name in _imports(path)), path


def test_feature_slices_do_not_cross_import_each_other():
    features = APP / "features"
    if not features.exists():
        return
    feature_names = {path.name for path in features.iterdir() if path.is_dir()}
    for feature in feature_names:
        for path in (features / feature).rglob("*.py"):
            for name in _imports(path):
                if name.startswith("app.features."):
                    imported_feature = name.split(".")[2]
                    assert imported_feature == feature, (path, name)


def test_core_and_middleware_do_not_import_upstream_components():
    forbidden_prefixes = (
        "app.adapters", "app.features", "app.orchestrator",
        "app.evaluation", "app.finetuning"
    )
    for folder in ["core", "middleware"]:
        for path in (APP / folder).rglob("*.py"):
            for name in _imports(path):
                assert not any(name.startswith(p) for p in forbidden_prefixes), (path, name)


def test_memory_layer_is_independent_from_orchestrator_and_eval():
    forbidden_prefixes = (
        "app.adapters", "app.features", "app.orchestrator",
        "app.evaluation", "app.finetuning"
    )
    for path in (APP / "memory").rglob("*.py"):
        for name in _imports(path):
            assert not any(name.startswith(p) for p in forbidden_prefixes), (path, name)


def test_domain_layer_has_zero_framework_dependencies():
    forbidden_prefixes = (
        "app.adapters", "app.features", "app.orchestrator",
        "app.evaluation", "app.finetuning", "app.core",
        "app.memory", "app.middleware"
    )
    for path in (APP / "domain").rglob("*.py"):
        for name in _imports(path):
            assert not any(name.startswith(p) for p in forbidden_prefixes), (path, name)

