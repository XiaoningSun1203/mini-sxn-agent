from .messages import HistoryItem, Message, Observation
from .model import Model
from .tools import ToolRegistry
from .trajectory import Trajectory

class Agent:

    def __init__(
            self,
            model: Model,
            tools: ToolRegistry,
            max_steps: int = 10,
            step_mode: bool = False
    ):
        self.model = model # self.model 是给对象添加 model 属性
        self.tools = tools
        self.max_steps = max_steps
        self.step_mode = step_mode
        self.messages: list[HistoryItem] = []

    def run(self, task: str) -> str:
        # agent 和 model 共享同一个 trajectory 文件，相同的task会记录到同一个文件里
        trajectory = Trajectory(task)
        self.model.set_trajectory(trajectory)

        self.messages.append(
            Message(
                role="user",
                content=task
            )
        )

        # Loop
        for step in range(self.max_steps):

            trajectory.record_step_number(step)

            assistant_message = self.model.generate(self.messages)
            # 先保存模型这一轮的完整输出
            self.messages.append(assistant_message)

            print(f"\n-------step {step}---------")
            print(f"content: {assistant_message.content}")
            print(f"tool_calls: {assistant_message.tool_calls}")


            #没有 ToolCall → 模型已经给出最终回答
            if not assistant_message.tool_calls:
                return assistant_message.content or "Task Finish"

            # 单步控制点
            self._wait_for_continue(step)

            # 一次 AssistantMessage 可能有多个 ToolCall
            for tool_call in assistant_message.tool_calls:
                observation = self.tools.execute(tool_call)
                self.messages.append(observation)
                trajectory.record_toolcall_result(observation)

            print(f"step {step} : done")

        return "Agent stopped: maximum steps reached."

    def _wait_for_continue(self, step: int) -> None:
        if self.step_mode:
            input(f"[step {step}] Press 'Enter' to continue ...")

