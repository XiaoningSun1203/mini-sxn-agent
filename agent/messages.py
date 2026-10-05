# 定义agent世界里面的基本数据：message、action、observation
from dataclasses import dataclass
from typing import Any, Literal

#调用工具后的执行结果
@dataclass
class Observation:
    tool: str
    tool_call_id: str
    content: str
    success: bool

@dataclass
class ToolCall:
    tool: str
    tool_call_id: str
    arguments: dict[str, Any]

# @dataclass
# class Finish:
#     content: str

#AssistantMessage → Model.generate() 的返回结果
@dataclass
class AssistantMessage:
    content: str | None
    tool_calls: list[ToolCall]

#Message → 下一轮真正送给模型的历史消息格式
@dataclass
class Message:
    role: Literal["user", "system"]
    content: str

HistoryItem = Message | AssistantMessage| Observation

