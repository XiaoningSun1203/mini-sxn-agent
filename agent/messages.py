from dataclasses import dataclasses
from typing import Any

@dataclasses
class Message:
    role: str
    content: str

@dataclasses
class Action:
    tool: str
    arguements: dict[str, Any]

@dataclasses
class Observation:
    content: str
    success: bool
