# 定义agent世界里面的基本数据：message、action、observation
from dataclasses import dataclass
from typing import Any

@dataclass
class Message:
    role: str
    content: str


@dataclass
class Action:
    tool: str | None
    arguements: dict[str, Any]
    final_answer: str | None = None


@dataclass
class Observation:
    content: str
    success: bool
