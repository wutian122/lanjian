import pytest
import asyncio
import tempfile
import shutil
import os
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_project_dir():
    temp_dir = tempfile.mkdtemp(prefix="lanjian_test_")
    os.makedirs(os.path.join(temp_dir, "src"), exist_ok=True)
    os.makedirs(os.path.join(temp_dir, "config"), exist_ok=True)

    sql_vuln_code = '''
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE id = '{user_id}'"
    cursor.execute(query)
    return cursor.fetchone()

def search_users(name):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE name LIKE '%" + name + "%'")
    return cursor.fetchall()
'''

    cmd_vuln_code = '''
import os
import subprocess

def run_command(user_input):
    os.system(f"echo {user_input}")

def execute_script(script_name):
    subprocess.call(f"bash {script_name}", shell=True)
'''

    xss_vuln_code = '''
from flask import Flask, request, render_template_string

app = Flask(__name__)

@app.route("/greet")
def greet():
    name = request.args.get("name", "")
    return f"<h1>Hello, {name}!</h1>"

@app.route("/search")
def search():
    query = request.args.get("q", "")
    html = f"<p>Search: {query}</p>"
    return render_template_string(html)
'''

    path_vuln_code = '''
import os

def read_file(filename):
    filepath = os.path.join("/app/data", filename)
    with open(filepath, "r") as f:
        return f.read()

def download_file(user_path):
    with open(user_path, "rb") as f:
        return f.read()
'''

    secret_vuln_code = '''
DATABASE_URL = "postgresql://user:password123@localhost/db"
API_KEY = "sk-1234567890abcdef1234567890abcdef"
SECRET_KEY = "super_secret_key_dont_share"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

def connect_database():
    password = "admin123"
    return f"mysql://root:{password}@localhost/mydb"
'''

    safe_code = '''
import sqlite3
from typing import Optional

def get_user_safe(user_id: int) -> Optional[dict]:
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()

def validate_input(user_input: str) -> str:
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', user_input):
        raise ValueError("Invalid input")
    return user_input
'''

    config_code = '''
import os

class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL")
    SECRET_KEY = os.environ.get("SECRET_KEY")
    DEBUG = False
'''

    requirements = '''
flask>=2.0.0
sqlalchemy>=2.0.0
requests>=2.28.0
'''

    with open(os.path.join(temp_dir, "src", "sql_vuln.py"), "w") as f:
        f.write(sql_vuln_code)
    with open(os.path.join(temp_dir, "src", "cmd_vuln.py"), "w") as f:
        f.write(cmd_vuln_code)
    with open(os.path.join(temp_dir, "src", "xss_vuln.py"), "w") as f:
        f.write(xss_vuln_code)
    with open(os.path.join(temp_dir, "src", "path_vuln.py"), "w") as f:
        f.write(path_vuln_code)
    with open(os.path.join(temp_dir, "src", "secrets.py"), "w") as f:
        f.write(secret_vuln_code)
    with open(os.path.join(temp_dir, "src", "safe_code.py"), "w") as f:
        f.write(safe_code)
    with open(os.path.join(temp_dir, "config", "settings.py"), "w") as f:
        f.write(config_code)
    with open(os.path.join(temp_dir, "requirements.txt"), "w") as f:
        f.write(requirements)

    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_llm_service():
    service = MagicMock()
    service.chat_completion_raw = AsyncMock(return_value={
        "content": "test response",
        "usage": {"total_tokens": 100},
    })
    # BaseAgent._get_timeout_config() calls get_agent_timeout_config()
    # when the attribute exists (MagicMock makes hasattr always True).
    # Return a real dict so float() works inside stream_llm_call.
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    # Agent ReAct loop calls stream_llm_call -> chat_completion_stream
    # (async generator), not chat_completion_raw. Provide a canned
    # Final Answer so the full agent.run() flow can be exercised.
    import json as _json
    _final_answer = {
        "tech_stack": {"languages": ["Python"], "frameworks": [], "databases": []},
        "entry_points": [{"type": "entry", "file": "src/sql_vuln.py", "description": "main module"}],
        "high_risk_areas": ["src/sql_vuln.py", "src/cmd_vuln.py", "src/secrets.py"],
        "initial_findings": [],
        "findings": [
            {
                "title": "SQL Injection",
                "vulnerability_type": "injection",
                "severity": "high",
                "file_path": "src/sql_vuln.py",
                "description": "f-string SQL query",
                "confidence": 0.9,
            }
        ],
        "summary": "mock recon/analysis result",
    }
    _response_text = "Thought: mock analysis complete.\nFinal Answer: " + _json.dumps(_final_answer, ensure_ascii=False)

    async def _mock_stream(messages=None, temperature=None, max_tokens=None):
        yield {"type": "token", "content": _response_text, "accumulated": _response_text}
        yield {"type": "done", "content": _response_text, "usage": {"total_tokens": 100}}

    service.chat_completion_stream = _mock_stream
    return service


@pytest.fixture
def mock_event_emitter():
    emitter = MagicMock()
    emitter.emit_info = AsyncMock()
    emitter.emit_warning = AsyncMock()
    emitter.emit_error = AsyncMock()
    emitter.emit_thinking = AsyncMock()
    emitter.emit_tool_call = AsyncMock()
    emitter.emit_tool_result = AsyncMock()
    emitter.emit_finding = AsyncMock()
    emitter.emit_progress = AsyncMock()
    emitter.emit_phase_start = AsyncMock()
    emitter.emit_phase_complete = AsyncMock()
    emitter.emit_task_complete = AsyncMock()
    emitter.emit = AsyncMock()
    return emitter


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    return session


@dataclass
class MockProject:
    id: str = "test-project-id"
    name: str = "Test Project"
    description: str = "Test project for unit tests"


@dataclass
class MockAgentTask:
    id: str = "test-task-id"
    project_id: str = "test-project-id"
    project: MockProject = None
    name: str = "Test Agent Task"
    status: str = "pending"
    current_phase: str = "planning"
    target_vulnerabilities: list = None
    verification_level: str = "sandbox"
    exclude_patterns: list = None
    target_files: list = None
    max_iterations: int = 50
    timeout_seconds: int = 1800

    def __post_init__(self):
        if self.project is None:
            self.project = MockProject()
        if self.target_vulnerabilities is None:
            self.target_vulnerabilities = []
        if self.exclude_patterns is None:
            self.exclude_patterns = []
        if self.target_files is None:
            self.target_files = []


@pytest.fixture
def mock_task():
    return MockAgentTask()


def assert_finding_valid(finding: Dict[str, Any]):
    required_fields = ["title", "severity", "vulnerability_type"]
    for field in required_fields:
        assert field in finding, f"Missing required field: {field}"
    valid_severities = ["critical", "high", "medium", "low", "info"]
    assert finding["severity"] in valid_severities, f"Invalid severity: {finding['severity']}"


def count_findings_by_type(findings: list, vuln_type: str) -> int:
    return sum(1 for f in findings if f.get("vulnerability_type") == vuln_type)


def count_findings_by_severity(findings: list, severity: str) -> int:
    return sum(1 for f in findings if f.get("severity") == severity)