# 2026.9.28 begin coding ...
# 定义agent世界里面的基本数据：message、action、observation

from agent.agent import Agent
from agent.model import FakeModel
from agent.tools import EchoTool, ToolRegistry

# model = FakeModel()
# agent = Agent(model)
# result = agent.run("fix the bug")

# print(result)

echo_tool = EchoTool()
tools = ToolRegister([
    echo_tool
    ])
tool = tools.get("echo")

tool.execute({
    "text": "Hello Agent1001"
})

print(obser)