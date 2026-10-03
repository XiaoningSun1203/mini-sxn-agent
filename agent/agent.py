from .messages import Message
from .model import Model
from .tools import ToolRegistry

class Agent:

    def __init__(
            self,
            model: Model,
            tools: ToolRegistry,
            max_steps: int = 10
    ):
        self.model = model # self.model 是给对象添加 model 属性
        self.tools = tools
        self.max_steps = max_steps
        self.messages: list[Message] = []

    def run(self, task: str) -> str:

        self.messages.append(
            Message(
                role="user",
                content=task
            )
        )

        for step in range(self.max_steps):
            action = self.model.generate(self.messages)

            if action.final_answer is not None:
                return action.final_answer

            if action.tool is None:
                return "Model returned neither a tool nor a final answer."

            tool = self.tools.get(action.tool)

            if tool is None:
                observation = (
                    f"Unknow tool: {action.tool}"
                )
            else:
                observation = tool.execute(action.arguements)
                observation_content = observation.content
                print(f"step {step} :observation_content={observation_content}")

            self.messages.append(
                Message(
                    role="tool",
                    content=observation_content
                )
            )

        return "Agent stopped: maximum steps reached."
        

        


