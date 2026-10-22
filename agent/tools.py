from abc import ABC, abstractmethod
from .messages import ToolCall, Observation
from .environment import Environment
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

#sxn
    def tool_discovery(self, new_tools: list[dict[str, Any]]):
        for tool in new_tools:
            self._tools.register(tool)



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

    def __init__(self, environment: Environment):
        self.environment=environment

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
            result = self.environment.run_command(command)
        except subprocess.TimeoutExpired as error:
            return Observation(
                tool=self.name,
                tool_call_id= tool_call_id,
                content=f"Command timed out after {error.timeout} seconds",
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

    def __init__(self, environment: Environment):
        self.environment=environment #把路径转换成一个更规范的绝对路径
    
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
                "path": {
                    "type": "string",
                    "description": "File path relative to the workspace."
                }
            },
            "required": ["path"]
        }

    #给Harness执行的
    def execute(self, tool_call_id: str, arguments: dict[str, Any]) -> Observation:
        path_string = arguments.get("path")

        # tool层检查参数类型、是否缺失, environment层进行路径解析和范围检查
        if not isinstance(path_string, str) or not path_string.strip():
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content="Missing or invalid argument: path",
                success=False
            )

        try:
            #Path.read_text() 会读取文本文件并返回 str
            content = self.environment.read_file(path_string)
        except (OSError, ValueError, RuntimeError) as error:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Failed to read file: {error}",
                success=False,
            )

        return Observation(
            tool=self.name,
            tool_call_id=tool_call_id,
            content=content,
            success=True
        )


class WriteFileTool(Tool):

    def __init__(self, environment: Environment):
        self.environment = environment

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
        # tool层检查参数类型、是否缺失, environment层进行路径解析和范围检查
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

        try:
            self.environment.write_file(path_string, content)
        except (OSError, ValueError, RuntimeError) as error:
            return Observation(
                tool=self.name,
                tool_call_id=tool_call_id,
                content=f"Failed to write file: {error}",
                success=False,
            )

        return Observation(
            tool=self.name,
            tool_call_id=tool_call_id,
            content=f"File written successfully: {path_string}",
            success=True
        )


class McpTool(Tool):
    @property
    def name(self) -> str:
        return "mcp_tool"

    #给 LLM 看的rule
    @property
    def description(self) -> str:
        return "Tools get from MCP server."

    #给 LLM 看的rule
    @property
    def parameters(self) -> dict[str, Any]:
        pass
