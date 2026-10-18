"""把一次 web 请求变成一个跑起来的 Agent。

这里刻意不 import main.py：main.py 一被 import 就会立刻跑一个写死的任务，
而且它把 API key 固定成从环境变量里取。网页端要的是「每个请求自己构造 Agent、
跑完即丢」，所以配置加载这段在这里独立实现一份。

API key 的来源是**凭据柜**：请求体里只走凭据名，真身在这里才被取出来，
注入模型之后就一直待在服务端内存里 —— 不写盘、不进 trajectory、不回浏览器。

界面上那些「每一步实时显示」的事件不是 Agent 发出来的 —— agent/agent.py 保持原版，
事件由 observer.py 从外面包住 model / tools 观测出来。
"""
import json
import os
import re
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import vault
from .observer import run_with_events

# 项目根目录（webui/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 和 main.py 读的是同一份配置。agent/config.toml 是更早的遗留文件，
# 里面没有 [permission]，step_mode 也还是 true —— 网页端不能用它。
CONFIG_PATH = PROJECT_ROOT / "config.toml"

# 出口脱敏：任何要回给浏览器的文本都过一遍，
# 防止 key 混在异常信息或堆栈里被回显出去。
_SECRET = re.compile(r"(sk-|github_pat_)[A-Za-z0-9_\-]{6,}")

# 网页端遇到「需要批准」的工具时怎么办。
#   deny  —— 默认。网页里没有可交互的批准入口，宁可拒绝也不能卡住。
#   allow —— 显式放开，适合自己本机信任的环境。
_APPROVAL_ENV = "MINI_AGENT_WEB_APPROVAL"


class RunCancelled(Exception):
    """客户端断开 / 点了停止时，由事件回调抛出，用来打断 Agent.run()。"""


def safe_error(text: Any) -> str:
    """把可能含密钥的文本擦干净再回给浏览器。"""
    return _SECRET.sub(r"\1***", str(text or ""))


def load_config() -> dict:
    import tomllib

    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"config not found: {CONFIG_PATH}. "
            "The web entry reads the same config.toml as main.py."
        )
    with CONFIG_PATH.open("rb") as file:
        return tomllib.load(file)


def build_tools(workspace: Path):
    from agent.environment import LocalEnvironment
    from agent.tools import ReadFileTool, ShellTool, ToolRegistry, WriteFileTool

    environment = LocalEnvironment(working_dir=workspace, timeout=30)
    return ToolRegistry([
        ShellTool(environment),
        ReadFileTool(environment),
        WriteFileTool(environment),
    ])


class WebApprovalController:
    """替代 agent/approvalController.py 里那个用 input() 等 stdin 的版本。

    命令行版 `input()` 是合理的；网页端的工作线程根本没有 stdin，
    真调下去会**永久阻塞**，界面上表现为卡住不动，也不会有错误。
    所以这里按环境变量给一个确定的答案，绝不阻塞。

    只在 webui 里把 agent.agent 模块上的这个名字换掉，
    agent/ 目录下任何文件都不改。
    """

    def query_user_approval(self) -> bool:
        return os.environ.get(_APPROVAL_ENV, "deny").strip().lower() == "allow"


def _install_web_approval() -> None:
    """把 Agent.run() 里 `ApprovalController()` 换成本地的无阻塞版本。

    Agent.run() 用的是它自己模块命名空间里的名字（`from .approvalController
    import ApprovalController`），所以要改 agent.agent 上的属性，改原模块没用。

    上游哪天把这个类改名/删掉，这里会**当场报错**，
    而不是悄悄退回 input() 把线程挂死 —— 这是这套补丁唯一的保险。
    """
    import agent.agent as agent_module

    if not hasattr(agent_module, "ApprovalController"):
        raise RuntimeError(
            "agent.agent no longer exposes ApprovalController; "
            "the web approval shim cannot be installed, and falling back to "
            "the built-in controller would block a worker thread on stdin."
        )
    agent_module.ApprovalController = WebApprovalController


def _build_model(tools, model_config, api_key: str):
    """构造 DeepSeekModel，把凭据柜里的 key 递进去。

    新版 DeepSeekModel 只认 `model_config.api_key_env`，没有 `api_key=` 参数。
    这里的做法是造一个**本进程内唯一**的环境变量名，构造完立刻删掉：

      - 不复用 DEEPSEEK_API_KEY，免得覆盖用户自己设的值；
      - 用完就删，agent 后面跑 ShellTool 时子进程继承的环境里不会有它
        （shell 工具是 subprocess.run(shell=True)，环境变量会跟着下去）。
    """
    from agent.model import DeepSeekModel

    if not api_key:
        raise RuntimeError("no API key was resolved from the credential vault")

    env_name = f"MINI_AGENT_WEB_KEY_{os.getpid()}_{threading.get_ident()}"
    os.environ[env_name] = api_key
    try:
        return DeepSeekModel(
            tools=tools,
            model_config=replace(model_config, api_key_env=env_name),
        )
    finally:
        os.environ.pop(env_name, None)


def build_agent(api_key: str, workspace: Path):
    from agent.agent import Agent
    from agent.configLoader import ConfigLoader

    config = load_config()
    loader = ConfigLoader()

    # step_mode 必须关掉：网页里没有「按回车继续」这个动作，
    # 留着会让 agent 卡在 input() 上。working_dir 用 web 自己的工作目录。
    agent_config = replace(
        loader.get_agent_config(config["agent"]),
        step_mode=False,
        working_dir=str(workspace),
    )
    model_config = loader.get_model_config(config["model"])
    permission_config = loader.get_permission_config(config["permission"])

    trajectory_dir = (PROJECT_ROOT / config["trajectory"]["directory"]).resolve()
    tools = build_tools(workspace)

    # 假模型开关：没有 openai、没有网络、不想烧额度时，也能把整条链路跑通做测试。
    # 走这条分支时 openai 完全不会被 import。
    if os.environ.get("MINI_AGENT_WEB_FAKE_MODEL") == "1":
        model = FakeModel()
    else:
        model = _build_model(tools, model_config, api_key)

    # 必须在 Agent.run() 之前装好 —— run() 每次进来都会 new 一个。
    _install_web_approval()

    return Agent(
        model=model,
        tools=tools,
        agent_config=agent_config,
        permission_config=permission_config,
        trajectory_dir=str(trajectory_dir),
    )


class FakeModel:
    """离线测试替身：不联网、不花钱、不需要装 openai。

    第 1 步让 agent 真的跑一次 shell 工具（验证工具链路是通的），
    第 2 步给最终回答，并把「它收到的 user 消息是什么」写进回答里，
    这样测试就能断言任务文字确实原样传到了模型这一层。
    """

    def __init__(self):
        self.trajectory = None
        self.step = 0
        # 步间人为延迟（秒）。默认 0。测试「SSE 是不是真流式」时把它调大，
        # 这样每帧的到达时间会被拉开，一眼能看出服务端有没有在攒数据。
        try:
            self.delay = float(os.environ.get("MINI_AGENT_WEB_FAKE_DELAY") or 0)
        except ValueError:
            self.delay = 0.0

    def set_trajectory(self, trajectory) -> None:
        # Agent.run() 一定会调这个，替身也必须实现
        self.trajectory = trajectory

    def generate(self, messages):
        import time

        from agent.messages import AssistantMessage, ToolCall

        if self.delay:
            time.sleep(self.delay)

        user_messages = [
            message for message in messages
            if getattr(message, "role", "") == "user"
        ]
        content = user_messages[-1].content if user_messages else None

        # 真实的 Model 适配器会把这一轮的输入记进 trajectory，替身也照做，
        # 否则「请求体确实进了 trajectory」这条路径在测试里走不到。
        if self.trajectory is not None:
            self.trajectory.record_model_input([
                {
                    "role": getattr(message, "role", "assistant"),
                    "content": getattr(message, "content", None),
                }
                for message in messages
            ])

        self.step += 1

        if self.step == 1:
            return AssistantMessage(
                content="Let me take a look at the working directory first.",
                tool_calls=[ToolCall(
                    tool="shell",
                    tool_call_id="fake_call_1",
                    arguments={"command": "echo hello-from-fake-model"},
                )],
            )

        return AssistantMessage(
            content=f"[FAKE MODEL] done. Task received: {content}",
            tool_calls=[],
        )


def run_task(payload: dict, event_queue, stop_event, workspace: Path) -> None:
    """工作线程入口：跑完一个任务，把所有事件推进队列。

    事件回调在这里把 agent 的每一步翻译成界面能渲染的 JSON。
    客户端断开时 stop_event 会被置位，回调抛出 RunCancelled 打断 agent。
    """
    def put(event: dict) -> None:
        if stop_event.is_set():
            raise RunCancelled()
        event_queue.put(event)

    try:
        task = str(payload.get("task") or "").strip()

        # 假模型模式压根不联网、也用不到密钥，就别去动柜子 ——
        # 否则跑离线测试还得先往真柜子里塞一把假 key。
        if os.environ.get("MINI_AGENT_WEB_FAKE_MODEL") == "1":
            api_key = ""
        else:
            # 密钥在这里才被解密取出。整个进程里它只出现在这一个局部变量里，
            # 往下直接进 OpenAI 客户端，不落任何日志和文件。
            api_key = vault.get_secret(str(payload.get("credential") or ""))

        agent = build_agent(api_key=api_key, workspace=workspace)

        # 事件是观测出来的，不是 Agent 发出来的 —— 见 observer.py
        run_with_events(agent, task, put)

        event_queue.put({"type": "done"})

    except RunCancelled:
        event_queue.put({"type": "cancelled"})

    except Exception as error:
        # 任何失败都要变成界面上的一条错误提示，不能让线程静悄悄地死掉
        event_queue.put({
            "type": "error",
            "message": safe_error(f"{type(error).__name__}: {error}"),
            "detail": safe_error(traceback.format_exc())[-2000:],
        })
