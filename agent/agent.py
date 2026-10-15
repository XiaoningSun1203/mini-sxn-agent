from .messages import HistoryItem, Message, Observation
from .model import Model
from .tools import ToolRegistry
from .trajectory import Trajectory
from .configLoader import AgentConfig
from .approvalController import ApprovalController
from .configLoader import PermissionConfig
from .permissionController import PermissionController, PermissionDecision

class Agent:

    def __init__(
            self,
            model: Model,
            tools: ToolRegistry,
            agent_config: AgentConfig,
            permission_config: PermissionConfig,
            trajectory_dir: str = "trajectories"
    ):
        self.model = model # self.model 是给对象添加 model 属性
        self.tools = tools

        self.agent_config = agent_config
        self.permission_config = permission_config

        self.trajectory_dir = trajectory_dir
        self.messages: list[HistoryItem] = []

    def run(self, task: str) -> str:

        # agent 和 model 共享同一个 trajectory 文件，相同的task会记录到同一个文件里
        trajectory = Trajectory(task, output_dir=self.trajectory_dir)
        self.model.set_trajectory(trajectory)

        # 每个任务使用独立的消息历史
        self.messages = []

        # 添加 system prompt
        if self.agent_config.system_prompt.strip():
            self.messages.append(
                Message(
                    role="system",
                    content=self.agent_config.system_prompt
                )
            )
        self.messages.append(
            Message(
                role="user",
                content=task
            )
        )

        approval = ApprovalController()

        # Loop
        for step in range(self.agent_config.max_steps):

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
            self._step_controller()

            # 一次 AssistantMessage 可能有多个 ToolCall
            for tool_call in assistant_message.tool_calls:
                # TODO :permissionController + approval
                permission_controller = PermissionController(self.permission_config)
                permission = permission_controller.check(tool_call.tool)

                if permission == PermissionDecision.ASK:
                    approval_decision = approval.query_user_approval()
 
                if permission == PermissionDecision.ALLOW or (
                   permission == PermissionDecision.ASK and approval_decision == True):
                    observation = self.tools.execute(tool_call)
                else:
                    observation = Observation(
                        tool=tool_call.tool,
                        tool_call_id=tool_call.tool_call_id,
                        content="Tool execution denied by policy/user.",
                        success=False,
                    )
                

                self.messages.append(observation)
                trajectory.record_toolcall_result(observation)

            print(f"step {step} : done")

        return "Agent stopped: maximum steps reached."

    # 用户可以通过设置 step_mode = true 实现单步调试
    def _step_controller(self) -> None:
        if self.agent_config.step_mode:
            input(f"[Press 'Enter' to continue ...")

