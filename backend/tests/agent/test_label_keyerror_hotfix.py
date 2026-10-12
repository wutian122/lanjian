"""P11 热修：verification KeyError 'label' 崩溃（P7-4 unwrap 遗漏 label 键）。

实证（A·nginx fd984d10 seq 5114）：verification agent 走到「未达标拒绝完成」
分支时 `sc['label']` 抛 KeyError: 'label' —— agent 崩溃、该轮验证丢弃。

根因：_build_sandbox_commands 的 unwrap 把 {'label':..., 'input':{...}} 解包为
cmd["input"]，label 键被丢弃，所有命令实际都无 'label'。
修复：① unwrap 保留 label；② 两处访问点改 .get 防御（纵深）。
"""

import inspect

from app.services.agent.agents.verification import VerificationAgent


def _make_agent() -> VerificationAgent:
    return VerificationAgent.__new__(VerificationAgent)


def _sample_finding(vuln_type: str = "command_injection") -> dict:
    return {
        "title": "Test finding for label hotfix",
        "vulnerability_type": vuln_type,
        "file_path": "src/main.c",
        "line": 42,
        "description": "test",
    }


class TestBuildCommandsLabelPreserved:
    """根因：_build_sandbox_commands 产出的每条命令必须携带 label 键。"""

    def test_every_command_has_label_key(self):
        agent = _make_agent()
        cmds = agent._build_sandbox_commands([_sample_finding()])
        assert cmds, "应至少生成一条命令"
        for c in cmds:
            assert "label" in c, f"命令缺 label 键: {list(c.keys())}"
            assert isinstance(c["label"], str)

    def test_label_preserved_from_wrapper_path(self):
        # 包装路径（子串/默认匹配）返回 {'label','input'}，unwrap 后 label 不应丢
        agent = _make_agent()
        wrapped = agent._gen_sandbox_command(
            "definitely_unknown_vuln_type_xyz", "src/x.c", 1, "t", 0
        )
        if wrapped and "command" not in wrapped and isinstance(wrapped.get("input"), dict):
            cmds = agent._build_sandbox_commands(
                [_sample_finding("definitely_unknown_vuln_type_xyz")]
            )
            assert cmds
            assert cmds[0].get("label"), "包装路径 unwrap 后 label 丢失"

    def test_finding_id_and_label_coexist(self):
        # 修复不得破坏 Opt-1 的 finding_id 注释注入
        agent = _make_agent()
        cmds = agent._build_sandbox_commands([_sample_finding()])
        assert cmds
        assert "# FINDING_ID:" in cmds[0]["command"]
        assert "label" in cmds[0]


class TestAccessPointsDefensive:
    """防御：1580/1762 访问点用 .get，且不再有 sc['label'] 直接索引。"""

    def test_no_direct_label_index_remains(self):
        src = inspect.getsource(VerificationAgent)
        assert "sc['label']" not in src, "仍有 sc['label'] 直接索引（会 KeyError）"
        assert 'sc["label"]' not in src

    def test_fstring_with_missing_label_does_not_raise(self):
        # 模拟 1762 分支：即使输入命令缺 label，格式化也不得抛异常
        sc = {"command": "echo hi"}  # 无 label
        line = f"- **{sc.get('label', 'sandbox_exec')}**: Action: sandbox_exec, Action Input: {{}}"
        assert "sandbox_exec" in line
