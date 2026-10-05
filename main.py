# 2026.9.28 begin coding ...
from pathlib import Path
from agent.agent import Agent
from agent.model import DeepSeekModel
from agent.tools import ToolRegistry, ShellTool
from agent.messages import Message

tools = ToolRegistry([
    ShellTool(working_dir=Path.cwd())
    ])

model = DeepSeekModel(tools)

agent = Agent(
    model=model,
    tools=tools,
    max_steps=10,
    step_mode=True
)

result = agent.run("Show me all the recursive files in the current working directory.")
print(result)