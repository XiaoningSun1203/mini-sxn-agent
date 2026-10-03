from abc import ABC, abstractmethod
from .messages import Action, Observation
from typing import Any
import subprocess
from pathlib import Path

# 执行者
class Tool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    # 比如 Action(tool="shell", arguments={"command": "pytest"})
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


class ShellTool(Tool):

    def __init__(self, working_dir: Path, timeout: int = 30):
        self.working_dir = working_dir
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "shell"

    def execute(self, arguements: dict[str, Any]) -> Observation:
        command = arguements.get("command")

        if not isinstance(command, str) or not command.strip():
            return Observation(
                content="Missing or invalid arguememt: command",
                success=False
            )

        try:
            result = subprocess.run(
                command,
                shell=True,            # 通过 shell 执行这条字符串命令
                cwd=self.working_dir,  # current working directory
                capture_output=True,   # 不要只是往终端打印，把输出保存到 result 里面
                text=True,             # 让output直接以字符串形式出来，而不是其他形式
                timeout=self.timeout   # 超过值会抛出 TimeoutExpired
            )
        except subprocess.TimeoutExpired:
            return Observation(
                content=f"Command timed out after {self.timeout} seconds",
                success=False
            )

        output = result.stdout

        if result.stderr:
            output += result.stderr

        return Observation(
            content=output,
            success=result.returncode == 0
        )


