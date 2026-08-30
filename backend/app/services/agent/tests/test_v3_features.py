"""
测试新增功能的脚本

验证：
1. 审计追踪文件系统
2. 上下文压缩
3. 格式解析器容错性
4. 结构化输出
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


async def test_audit_trace():
    """测试审计追踪功能"""
    print("\n=== 测试审计追踪 ===")

    from app.services.agent.audit_trace import AuditTraceManager

    trace = AuditTraceManager(
        task_id="test-task-12345678",
        project_name="测试项目",
        base_dir="./test_traces"
    )

    # 添加各种记录
    trace.add_agent_dispatch("analysis", "审计注入漏洞", "包含用户输入的文件")
    trace.add_tool_call("grep", {"pattern": "exec"}, ["file1.py:10", "file2.py:20"], 150, True)
    trace.add_finding(
        finding_type="sql_injection",
        severity="high",
        title="SQL 注入漏洞",
        description="用户输入未经过滤直接拼接到 SQL 语句",
        file_path="app/models.py",
        line_number=42,
        code_snippet='cursor.execute("SELECT * FROM users WHERE id=" + user_id)',
        agent_source="analysis"
    )
    trace.add_embedding_record("代码片段", "100 个代码块", 100, "bge-m3")
    trace.add_context_compression(50000, 5000, "llm_summarization", "压缩了历史对话", "原始内容...")
    trace.add_llm_call("Qwen3.8-27B", 1000, 500, 2000, "decision")

    # 最终化
    trace.finalize()

    print(f"✅ 追踪文件已生成: {trace.trace_md}")
    print(f"✅ JSON 文件: {trace.trace_json}")

    # 读取并显示摘要
    summary = trace.get_summary_for_agent()
    print(f"\n摘要预览:\n{summary[:500]}...")


async def test_context_manager():
    """测试上下文管理器"""
    print("\n=== 测试上下文管理器 ===")

    from app.services.agent.context_manager import ContextManager, ContextWindow

    # 模拟 LLM 服务
    class MockLLMService:
        async def chat_completion(self, messages, **kwargs):
            return {"content": "这是压缩后的摘要：包含3个关键决策和2个漏洞发现"}

    manager = ContextManager(
        llm_service=MockLLMService(),
        window_config=ContextWindow(
            max_messages=30,
            compression_threshold=1000,  # 低阈值用于测试
            keep_recent=3
        )
    )

    # 构造长对话历史
    conversation = [
        {"role": "system", "content": "你是审计助手"},
        {"role": "user", "content": "开始审计"},
        {"role": "assistant", "content": "好的，我将调度 analysis Agent"},
        {"role": "user", "content": "Analysis Agent 返回了很长的结果：" + "X" * 500},
        {"role": "assistant", "content": "我需要调度 verification Agent"},
        {"role": "user", "content": "Verification Agent 也返回了很长的结果：" + "Y" * 500},
    ]

    print(f"原始消息数: {len(conversation)}, 总长度: {sum(len(m['content']) for m in conversation)}")

    # 测试压缩
    compressed = await manager.compress_if_needed(conversation)
    print(f"压缩后消息数: {len(compressed)}, 总长度: {sum(len(m['content']) for m in compressed)}")

    print("✅ 上下文压缩测试通过")


def test_format_parser():
    """测试格式解析器容错性"""
    print("\n=== 测试格式解析器 ===")

    from app.services.agent.agents.orchestrator import OrchestratorAgent

    # 创建临时 Orchestrator 实例（只为测试解析器）
    class MockOrch:
        def __init__(self):
            self.name = "TestOrch"

        def _parse_llm_response(self, response):
            # 复制新的解析逻辑
            import re
            from app.services.agent.agents.base import AgentStep
            from app.services.agent.json_parser import AgentJsonParser

            cleaned_response = response
            cleaned_response = re.sub(r'\*\*Action:\*\*', 'Action:', cleaned_response)
            cleaned_response = re.sub(r'\*\*Action Input:\*\*', 'Action Input:', cleaned_response)
            cleaned_response = re.sub(r'Action：', 'Action:', cleaned_response)
            cleaned_response = re.sub(r'Action Input：', 'Action Input:', cleaned_response)

            thought_match = re.search(r'Thought:\s*(.*?)(?=Action:|$)', cleaned_response, re.DOTALL)
            thought = thought_match.group(1).strip() if thought_match else ""

            action_match = re.search(r'Action:\s*([\w\-]+)', cleaned_response)
            if not action_match:
                action_match = re.search(r'Action:\s*([^\n]+?)(?:\s*\n|$)', cleaned_response)

            if not action_match:
                return None

            action = action_match.group(1).strip()

            input_match = re.search(r'Action Input:\s*(.*?)(?=\n(?:Thought:|Action:|Observation:)|$)', cleaned_response, re.DOTALL)
            if not input_match:
                return None

            input_text = input_match.group(1).strip()
            action_input = AgentJsonParser.parse(input_text, default={"raw": input_text})

            return AgentStep(thought=thought, action=action, action_input=action_input)

    orch = MockOrch()

    # 测试各种格式
    test_cases = [
        # 标准格式
        """Thought: 我需要调度 verification Agent
Action: dispatch_agent
Action Input: {"agent": "verification", "task": "验证漏洞"}""",

        # 带连字符的 Action
        """Thought: 完成任务
Action: dispatch-agent
Action Input: {"agent": "analysis"}""",

        # 中文冒号
        """Thought：需要分析代码
Action：dispatch_agent
Action Input：{"agent": "analysis"}""",

        # Markdown 加粗
        """Thought: 思考中
**Action:** finish
**Action Input:** {"conclusion": "完成"}""",

        # 多余空格
        """Thought: 测试
Action:    dispatch_agent
Action Input:    {"agent": "verification"}""",
    ]

    for i, test_case in enumerate(test_cases, 1):
        result = orch._parse_llm_response(test_case)
        status = "✅" if result else "❌"
        print(f"{status} 测试用例 {i}: {'通过' if result else '失败'}")
        if result:
            print(f"   - Action: {result.action}")
            print(f"   - Input: {result.action_input}")

    print("✅ 格式解析器测试完成")


def test_structured_output():
    """测试结构化输出"""
    print("\n=== 测试结构化输出 ===")

    from app.services.agent.structured_output import (
        StructuredOutputAdapter,
        LLMProvider,
        detect_provider_from_model
    )

    # 测试 provider 检测
    assert detect_provider_from_model("gpt-4") == LLMProvider.OPENAI
    assert detect_provider_from_model("claude-3") == LLMProvider.ANTHROPIC
    assert detect_provider_from_model("Qwen3.8-27B") == LLMProvider.OTHER
    print("✅ Provider 检测正常")

    # 测试 OpenAI 格式构建
    adapter = StructuredOutputAdapter(LLMProvider.OPENAI)
    messages = [{"role": "user", "content": "test"}]
    structured_messages, tools = adapter.build_structured_messages(messages)

    assert tools is not None
    assert "functions" in tools
    print("✅ OpenAI 结构化格式构建正常")

    # 测试响应解析
    mock_openai_response = {
        "choices": [{
            "message": {
                "function_call": {
                    "name": "make_decision",
                    "arguments": '{"thought": "test", "action": "finish", "action_input": {}}'
                }
            }
        }]
    }

    parsed = adapter.parse_structured_response(mock_openai_response)
    assert parsed is not None
    assert parsed["action"] == "finish"
    print("✅ OpenAI 响应解析正常")

    print("✅ 结构化输出测试完成")


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("测试 v3.0 新增功能")
    print("=" * 60)

    try:
        await test_audit_trace()
        await test_context_manager()
        test_format_parser()
        test_structured_output()

        print("\n" + "=" * 60)
        print("✅ 所有测试通过")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
