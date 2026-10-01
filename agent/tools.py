from abc import ABC, abstractmethod
from .messages import Action, Observation
from typing import Any

# 执行者
class Tool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def execute(self, arguements: dict[str, Any]) -> Observation:
        pass



# 负责找到执行者
class ToolRegistry:

    # 创建的 ToolRegistory object 的同时注册了xxTool
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        if tools is not None:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    # 目标是把 str 映射成 Tool，例如 "echo" -> EchoTool object
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


class EchoTool(Tool):

    @property
    def name(self) -> str:
        return "echo"

    def execute(self, arguements: dict[str, Any]) -> Observation:
        text = arguements.get("text")
        if text is None:
            return Observation(
                content = "Missing argument: text",
                success = False
            )
        #sxnQ：具体在哪里真的执行了？？？？？？
        return Observation(
            content=text,
            success=True
        )