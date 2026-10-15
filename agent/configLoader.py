from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import tomllib

@dataclass(frozen=True)
class ModelConfig:
    model_name: str = ""
    base_url: str = ""
    api_key_env: str = ""

@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 30
    step_mode: bool = False
    working_dir : str = "."
    system_prompt: str = ""

class PermissionDecision(Enum):
    ALLOW = "allow"  # 自动允许执行
    ASK = "ask"      # 需要经过 ApprovalController
    DENY = "deny"    # 直接拒绝执行

@dataclass(frozen=True)
class PermissionConfig:
    permission_map: dict[str, PermissionDecision] = field(default_factory=dict) # [tool name : decision]


class ConfigLoader:
    def get_config_content(self, config_path: str) -> dict[str, Any]:
        if config_path is None:
            project_dir = Path(__file__).resolve().parent  #得到当前文件所在路径的上一级目录
            config_path = project_dir / "agent" / "config.toml"
        
        with Path(config_path).open("rb") as file:
            return tomllib.load(file)


    def get_model_config(self, config: dict[str, Any]) -> ModelConfig:
        return ModelConfig(
            model_name = config["name"],
            base_url = config["base_url"],
            api_key_env = config["api_key_env"],
        )


    def get_agent_config(self, config: dict[str, Any]) -> AgentConfig:
        project_dir = Path(__file__).resolve().parent.parent

        # 获取 system prompt 的具体内容
        system_prompt_path = (project_dir / config["system_prompt_file"])

        return AgentConfig(
            max_steps = config["max_steps"],
            step_mode = config["step_mode"],
            working_dir = (project_dir / config["working_dir"]).resolve(),
            system_prompt = system_prompt_path.read_text(encoding="utf-8")
        )


    def get_permission_config(self, config: dict[str, Any]) -> PermissionConfig:
        permission_map: dict[str, PermissionDecision] = {}

        for tool_name, decision_text in config.items():
            try:
                permission_map[tool_name] = PermissionDecision(decision_text)
            except ValueError as error:
                raise ValueError(
                    f"Invalid permission for {tool_name}: {decision_text}"
                ) from error

        return PermissionConfig(permission_map=permission_map)

