
from pathlib import Path
from dataclasses import dataclass
from .permissionController import PermissionDecision
from typing import Any
import tomllib

@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    base_url: str
    api_key_env: str

@dataclass(frozen=True)
class AgentConfig:
    max_steps: int
    step_mode: bool
    working_dir : str
    system_prompt: str = ""


@dataclass(frozen=True)
class PermissionConfig:
    permission_map: dict[str, PermissionDecision]  # [tool name : decision]


class ConfigLoader:
    def get_config_content(config_path: str) -> dict[str, Any]:
        if config_path is None:
            project_dir = Path(__file__).resolve().parent  #得到当前文件所在路径的上一级目录
            config_path = project_dir / "agent" / "config.toml"
        
        with Path(config_path).open("rb") as file:
            return tomllib.load(file)


    def get_model_config(config: dict[str, Any]) -> ModelConfig:
        model_config = ModelConfig()
        model_config.model_name = config["name"]
        model_config.base_url = config["base_url"]
        model_config.api_key_env = config["api_key_env"]
        return model_config


    def get_agent_config(config: dict[str, Any]) -> AgentConfig:
        agent_config = AgentConfig()
        agent_config.max_steps = config["max_steps"]
        agent_config.step_mode = config["step_mode"]

        project_dir = Path(__file__).resolve().parent.parent
        agent_config.working_dir = (project_dir / config["working_dir"]).resolve()

        # 获取 system prompt 的具体内容
        system_prompt_path = (project_dir / config["system_prompt_file"])
        agent_config.system_prompt = system_prompt_path.read_text(encoding="utf-8")
        return agent_config


    def get_permission_config(config: dict[str, Any]) -> PermissionConfig:
        permission_map: dict[str, PermissionDecision] = {}

        for tool_name, decision_text in config.items():
            try:
                permission_map[tool_name] = PermissionDecision(decision_text)
            except ValueError as error:
                raise ValueError(
                    f"Invalid permission for {tool_name}: {decision_text}"
                ) from error

        return PermissionConfig(permission_map=permission_map)


