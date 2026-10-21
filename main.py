# 2026.9.28 begin coding ...
from pathlib import Path
from agent.agent import Agent
from agent.model import DeepSeekModel
from agent.tools import ToolRegistry, ShellTool, ReadFileTool, WriteFileTool
from agent.messages import Message
from agent.environment import LocalEnvironment
from agent.configLoader import ConfigLoader
from agent.mcpClient import McpClient
import tomllib
import asyncio

mcp_client = McpClient()
await mcp_client.initialize()
await mcp_client.list_tools()

project_dir = Path(__file__).resolve().parent  #得到当前文件所在路径的上一级目录
config_path = project_dir / "config.toml"
with config_path.open("rb") as file:
    config = tomllib.load(file)

config_loader = ConfigLoader()
agent_config = config_loader.get_agent_config(config["agent"])
model_config = config_loader.get_model_config(config["model"])
permission_config = config_loader.get_permission_config(config["permission"])
trajectory_dir = (project_dir / config["trajectory"]["directory"]).resolve()

environment = LocalEnvironment(working_dir=agent_config.working_dir, timeout=30)

tools = ToolRegistry([
    ShellTool(environment),
    ReadFileTool(environment),
    WriteFileTool(environment)
    ])

model = DeepSeekModel(
    tools=tools,
    model_config=model_config)

agent = Agent(
    model=model,
    tools=tools,
    agent_config=agent_config,
    permission_config=permission_config,
    trajectory_dir=str(trajectory_dir)
)

result = agent.run("Read the trajectories directory, find the last finished file, "
                   "analyze and write a report.")
print(result)
mcp_client.close()

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

