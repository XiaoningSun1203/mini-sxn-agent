from .messages import HistoryItem, Message, Observation
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
        self.messages: list[HistoryItem] = []

    def run(self, task: str) -> str:

        self.messages.append(
            Message(
                role="user",
                content=task
            )
        )

        # Loop
        for step in range(self.max_steps):
            assistant_message = self.model.generate(self.messages)
            # 先保存模型这一轮的完整输出
            self.messages.append(assistant_message)

            #没有 ToolCall → 模型已经给出最终回答
            if not assistant_message.tool_calls:
                return assistant_message.content or "Task Finish"

            # 一次 AssistantMessage 可能有多个 ToolCall
            for tool_call in assistant_message.tool_calls:
                observation = self.tools.execute(tool_call)
                self.messages.append(observation)

            print(f"step {step} : done")

        return "Agent stopped: maximum steps reached."


