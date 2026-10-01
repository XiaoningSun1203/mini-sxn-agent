from dataclasses import dataclass
from typing import Any

@dataclass
class Message:
    role: str
    content: str


@dataclass
class Action:
    tool: str
    arguements: dict[str, Any]


@dataclass
class Observation:
    content: str
    success: bool
