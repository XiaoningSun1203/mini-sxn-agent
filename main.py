# 2026.9.28 begin coding ...
from pathlib import Path
from agent.agent import Agent
from agent.model import FakeModel
from agent.tools import EchoTool, ToolRegistry, ShellTool
# 1. main.py 创建 FakeModel

# 2. main.py 创建 ShellTool

# 3. ShellTool 注册进 ToolRegistry

# 4. 创建 Agent

# 5. agent.run(task)

# 6. task → Message(user)

# 7. FakeModel.generate()
#       ↓
#    Action(shell, pwd)

# 8. ToolRegistry.get("shell")
#       ↓
#    ShellTool

# 9. ShellTool.execute("pwd")
#       ↓
#    OS 真正执行 pwd

# 10. Observation
#       ↓
#     Message(tool)

# 11. FakeModel.generate()
#       ↓
#     final_answer

# 12. Agent return
model = FakeModel()

tools = ToolRegistry([
    ShellTool(working_dir=Path.cwd())
    ])

agent = Agent(
    model=model,
    tools=tools,
    max_steps=10
)

result = agent.run("Show me the current working directory.")
print(result)