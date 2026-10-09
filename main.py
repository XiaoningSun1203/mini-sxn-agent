# 2026.9.28 begin coding ...
from pathlib import Path
from agent.agent import Agent
from agent.model import DeepSeekModel
from agent.tools import ToolRegistry, ShellTool, ReadFileTool, WriteFileTool
from agent.messages import Message
from agent.environment import LocalEnvironment
import tomllib


project_dir = Path(__file__).resolve().parent  #得到当前文件所在路径的上一级目录
config_path = project_dir / "agent" / "config.toml"
with config_path.open("rb") as file:
    config = tomllib.load(file)

agent_config = config["agent"]
model_config = config["model"]
working_dir = (project_dir / agent_config["working_dir"]).resolve()

system_prompt_path = (project_dir / agent_config["system_prompt_file"])
system_prompt = system_prompt_path.read_text(encoding="utf-8")

trajectory_dir = (project_dir / config["trajectory"]["directory"]).resolve()

environment = LocalEnvironment(working_dir=working_dir, timeout=30)

tools = ToolRegistry([
    ShellTool(environment),
    ReadFileTool(environment),
    WriteFileTool(environment)
    ])

model = DeepSeekModel(
    tools=tools,
    model_name=model_config["name"],
    base_url=model_config["base_url"],
    api_key_env=model_config["api_key_env"])

agent = Agent(
    model=model,
    tools=tools,
    max_steps=agent_config["max_steps"],
    step_mode=agent_config["step_mode"],
    system_prompt=system_prompt,
    trajectory_dir=str(trajectory_dir)
)

result = agent.run("Read the trajectories directory, find the last finished file, "
                   "analyze and write a report.")
print(result)

# workspace = Path("sandbox-workspace")
# workspace.mkdir(exist_ok=True)

# environment = DockerEnvironment(workspace)

# try:
#     environment.start()

#     result = environment.run_command("pwd && python --version")
#     print(result.stdout)
#     print(result.stderr)
#     print(result.returncode)
# finally:
#     environment.close()

