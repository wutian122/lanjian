"""LLMMessage 孤立代理字符清洗（R-C3）测试。

背景（2026-09-26 生产实证，A 机任务 e7332bb3，nginx C 项目）：源码树含非法
UTF-8 字节，经 surrogateescape 解码后以孤立代理项（如 U+DCFB）进入 LLM 消息；
openai SDK 序列化时抛 `'utf-8' codec can't encode character '\udcfb'
(surrogates not allowed)' → Analysis 调用连环失败（40 分钟 134 次），重试
永远携带同样的坏数据，任务空转。

契约：LLMMessage 构造时对 content 做单点清洗——孤立代理项丢弃、合法内容
（含全部常规中英文/emoji）原样保留；无孤立代理时零变化。
"""

import pytest

from app.services.llm.types import LLMMessage


class TestLLMMessageSurrogateSanitizer:
    def test_lone_surrogate_stripped(self):
        """孤立代理项（U+DCFB）在构造时被丢弃，content 可安全 UTF-8 编码"""
        msg = LLMMessage(role="user", content="审计 src/core/ngx_\udcfbmain.c 的注入风险")
        msg.content.encode("utf-8")  # 不抛 UnicodeEncodeError
        assert "\udcfb" not in msg.content
        assert "审计 src/core/ngx_" in msg.content

    def test_normal_content_untouched(self):
        """常规中英文/换行/emoji 零变化"""
        text = "审计路径遍历\npath: /etc/passwd ✓ emoji🚀结束"
        msg = LLMMessage(role="assistant", content=text)
        assert msg.content == text

    def test_empty_and_none_safe(self):
        """空串安全"""
        assert LLMMessage(role="user", content="").content == ""

    def test_full_surrogate_string(self):
        """整串孤立代理（坏文件名字节）→ 清洗后为合法可编码字符串"""
        bad = "nginx-release\udcff\udcfe\udcfd.tar"
        msg = LLMMessage(role="user", content=bad)
        msg.content.encode("utf-8")
        assert "nginx-release" in msg.content
