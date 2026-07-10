from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


STRICT_PATH = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "strict_finding.py"
VERIFY_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "agent"
    / "agents"
    / "verification.py"
)


def load_strict_module():
    spec = spec_from_file_location("strict_finding_module", STRICT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strict_finding_accepts_minimal_valid():
    module = load_strict_module()
    finding = {"file_path": "app/main.py", "line_start": 1, "vulnerability_type": "xss"}
    assert module.is_strict_finding(finding) is True


def test_strict_finding_rejects_unknown_file_path():
    module = load_strict_module()
    finding = {"file_path": "unknown", "line_start": 1, "vulnerability_type": "xss"}
    assert module.is_strict_finding(finding) is False


def test_strict_finding_rejects_missing_line_start():
    module = load_strict_module()
    finding = {"file_path": "app/main.py", "vulnerability_type": "xss"}
    assert module.is_strict_finding(finding) is False


def test_strict_finding_rejects_missing_vulnerability_type():
    module = load_strict_module()
    finding = {"file_path": "app/main.py", "line_start": 1}
    assert module.is_strict_finding(finding) is False


def test_strict_finding_rejects_descriptive_only_patterns():
    module = load_strict_module()
    finding = {
        "file_path": "app/main.py",
        "line_start": 1,
        "vulnerability_type": "xss",
        "title": "路由结构清晰",
    }
    assert module.is_strict_finding(finding) is False


def test_verification_agent_staticmethod_delegates_to_strict_finding():
    content = VERIFY_PATH.read_text(encoding="utf-8")
    assert "from app.services.agent.strict_finding import is_strict_finding" in content
    assert "return is_strict_finding(finding)" in content
