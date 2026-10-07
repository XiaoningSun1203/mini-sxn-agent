from abc import ABC, abstractmethod
from .messages import ToolCall, Observation
from typing import Any
import subprocess
from pathlib import Path

# Agent
# │
# ├── 控制 Loop
# ├── 管理 Context
# └── 判断 Stop

# ToolRegistry
# │
# ├── 注册 Tool
# ├── 查找 Tool
# └── dispatch ToolCall

# Tool
# │
# └── 真正执行操作

# 执行者
class Tool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    # 增加 description
    @property
    @abstractmethod
    def description(self) -> str:
        pass

    # 增加 parameters
    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def execute(self, tool_call_id: str, arguments: dict[str, Any]) -> Observation:
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

    # 把所有 Tool 的说明书收集起来
    def schemas(self) -> list[dict[str, Any]]:
        schemas = []
        #遍历 Registry 中注册的每一个 Tool
        for tool in self._tools.values():
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            }
            schemas.append(schema)

        return schemas


    def execute(self, tool_call: ToolCall) -> Observation:
        # 1. 根据 tool_call.tool 找 Tool
        tool = self.get(tool_call.tool)

        # 如果工具不存在，构造 失败Observation
        if tool is None:
            return Observation(
                tool=tool_call.tool,
                tool_call_id=tool_call.tool_call_id,
                content=f"Unknown tool: {tool_call.tool}",
                success=False
            )
        #如果工具存在，调用tool.execute(tool_call.arguments)
        return tool.execute(tool_call.tool_call_id, tool_call.arguments)
#                ToolCall
#                    │
#                    ▼
#            ToolRegistry.execute
#                    │
#                    ▼
#              get(tool name)
#           ┌────────┴─────────┐
#           │                  │
#        不存在               存在
#           │                  │
#           ▼                  ▼
#    failure Observation   Tool.execute()
#                              │
#                              ▼
#                         Observation


class EchoTool(Tool):

    @property
    def name(self) -> str:
        return "echo"

    #给 LLM 看的rule
    @property
    def description(self) -> str:
        return "Return the provided text."

    #给 LLM 看的rule
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to return."
                }
            },
            "required": ["text"]
        }

    #给Harness执行的
    def execute(self, tool_call_id: str, arguments: dict[str, Any]) -> Observation:
        text = arguments.get("text")
        if text is None:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content = "Missing argument: text",
                success = False
            )

        return Observation(
            tool=self.name,
            tool_call_id=tool_call_id,
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

    #给 LLM 看的rule
    @property
    def description(self) -> str:
        return "Execute a shell command in the workspace."

    #给 LLM 看的rule
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute."
                }
            },
            "required": ["command"]
        }

    #给Harness执行的
    def execute(self, tool_call_id: str, arguments: dict[str, Any]) -> Observation:
        command = arguments.get("command")

        if not isinstance(command, str) or not command.strip():
            return Observation(
                tool=self.name,
                tool_call_id= tool_call_id,
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
                tool=self.name,
                tool_call_id= tool_call_id,
                content=f"Command timed out after {self.timeout} seconds",
                success=False
            )

        output = result.stdout

        if result.stderr:
            output += result.stderr

        return Observation(
            tool=self.name,
            tool_call_id= tool_call_id,
            content=output,
            success=result.returncode == 0
        )


class ReadFileTool(Tool):

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir.resolve() #把路径转换成一个更规范的绝对路径
    
    @property
    def name(self) -> str:
        return "read_file"

    #给 LLM 看的rule
    @property
    def description(self) -> str:
        return "Read the contents of a text file in the current workspace."

    #给 LLM 看的rule
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "File path relative to the workspace."
                }
            },
            "required": ["path"]
        }

    #给Harness执行的
    def execute(self, tool_call_id: str, arguments: dict[str, Any]) -> Observation:
        path_string = arguments.get("path")

        if not isinstance(path_string, str) or not path_string.strip():
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content="Missing or invalid argument: path",
                success=False
            )

        file_path = (self.working_dir / path_string).resolve()

        try:
            file_path.relative_to(self.working_dir)
        except ValueError:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Path is outside workspace: {path_string}",
                success=False
            )

        if not file_path.exists():
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"File does not exist: {path_string}",
                success=False
            )

        if not file_path.is_file():
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Path is not a file: {path_string}",
                success=False
            )

        try:
            #Path.read_text() 会读取文本文件并返回 str
            content = file_path.read_text(encoding="utf-8")
        except Exception as error:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Failed to read file: {error}",
                success=False
            )

        return Observation(
            tool=self.name,
            tool_call_id=tool_call_id,
            content=content,
            success=True
        )


class WriteFileTool(Tool):

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir.resolve()

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write text content to a file in the current workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the workspace."
                },
                "content": {
                    "type": "string",
                    "description": "The complete text content to write."
                }
            },
            "required": ["path", "content"]
        }

    def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, Any]
    ) -> Observation:

        path_string = arguments.get("path")
        content = arguments.get("content")

        if not isinstance(path_string, str) or not path_string.strip():
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content="Missing or invalid argument: path",
                success=False
            )

        if not isinstance(content, str):
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content="Missing or invalid argument: content",
                success=False
            )

        file_path = (self.working_dir / path_string).resolve()

        try:
            file_path.relative_to(self.working_dir)
        except ValueError:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Path is outside workspace: {path_string}",
                success=False
            )

        try:
            file_path.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            # 真正执行写文件动作
            file_path.write_text(
                content,
                encoding="utf-8"
            )

        except Exception as error:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Failed to write file: {error}",
                success=False
            )

        return Observation(
            tool=self.name,
            tool_call_id=tool_call_id,
            content=f"File written successfully: {path_string}",
            success=True
        )

