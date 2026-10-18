"""从外面观测一次 Agent 运行，把它翻译成界面事件。

这里**刻意一行都不改 agent/agent.py**。做法是包住 Agent 手里那两个对象 ——
model 和 tools —— 在它们被调用的前后插事件：

    ObservableModel.generate()   一次调用 = 一步
        进 → step_start
        出 → assistant
    ObservableTools.execute()    一次调用 = 一个工具
        进 → tool_call
        出 → observation
    最后 → agent.run() 的返回值就是 final

## 被拒绝的工具

新版 agent/agent.py 里，权限为 deny(或 ask 未被批准)的工具**根本不会**
走到 `tools.execute()` —— 它会直接构造一个 success=False 的 Observation
塞进消息历史。包装器看不见这个动作，界面上就会只剩「准备调用」而没有结果，
看起来像卡住。

所以这里还会在每步结束时扫一遍 `agent.messages`：凡是**没经过 execute()**
的新 Observation，补发一对 tool_call / observation 帧，并带上 `blocked: true`。
这仍然是只读观测，不改 agent 的任何行为。

## 这套东西的代价，必须说清楚

它**依赖 Agent.run() 内部的调用顺序和消息结构**，这是个隐式契约，不是接口。成立的前提是：

  a) 每一步开头调且只调一次 `model.generate()`
  b) 每个被执行的工具调且只调一次 `tools.execute()`
  c) 模型某一步不给 tool_calls 时，run() 就返回最终答案
  d) 被拒绝的工具会把 Observation 追加进 `agent.messages`

哪天有人改了 `agent/agent.py` 的流程（比如加个重试、把工具改成并发跑），
这里**不会报错，只会静默地少发几帧或者把步骤号算错** —— 界面上看起来像
「卡住不动了」，而不是崩掉。所以 tests/test_web_e2e.py 里那几个事件序列
断言不是走过场：它们是这套观测唯一的防错网。

换来的是 agent.py 保持原版，不用为了一个界面在核心循环里挖回调口子。
"""
from __future__ import annotations

from typing import Any, Callable

from agent.messages import Observation


# 推给界面的事件里，工具输出最多带这么多字符。
# 只截「给界面看的那一份」，agent 的消息历史里仍然是完整内容，模型看到的不会少。
_UI_CLIP = 8000

# Agent 走到步数上限时返回的就是这句话（agent/agent.py 最后一行里那句）。
# 拿它认「这次是被截停的」，好在界面上标出来。
_MAX_STEPS_RESULT = "Agent stopped: maximum steps reached."


def clip(text: str | None) -> str:
    """把工具输出截到界面能承受的长度。"""
    if not text:
        return ""
    if len(text) <= _UI_CLIP:
        return text
    return text[:_UI_CLIP] + "\n…（界面已截断，完整内容见 trajectory）"


class _Observer:
    """两个包装器共用的状态：发事件的出口 + 「现在走到第几步」。

    关键约定：`self.step` 始终是**正在进行的那一步**的编号（0 起）。

    推进它的时机是「下一步开始」，而不是「这一步结束」—— 因为一步的范围跨过
    `generate()` **和**它触发的全部工具调用，而包装器只看得见 generate() 那一半。
    等下一次 `begin_step()` 才 ++ ，工具调用期间读到的 `self.step` 就正好是
    它所属的那一步，调用方谁都不用做加减法。
    """

    def __init__(self, emit: Callable[[dict[str, Any]], None], agent: Any):
        self.emit = emit
        self.agent = agent
        self.step = 0
        self._begun = False

        # 被拒绝的工具补帧用：已经真正执行过的 tool_call_id 不重复发；
        # 已经补发过的也不重复发。
        self._executed: set[str] = set()
        self._reported: set[str] = set()
        self._calls: dict[str, dict[str, Any]] = {}
        self._scan_from = 0

    def begin_step(self) -> None:
        # 先补上一步被拒绝的工具，再推进步号 —— 那些工具属于上一步。
        self.flush_blocked()
        if self._begun:      # 上一步真的走完了，才换下一个编号
            self.step += 1
        self._begun = True
        self.emit({"type": "step_start", "step": self.step})

    # ---------------- 被拒绝的工具补帧 ----------------

    def remember_calls(self, tool_calls) -> None:
        """记下这一轮模型请求了哪些工具，补帧时要用它的参数。"""
        for call in tool_calls or []:
            self._calls[call.tool_call_id] = {
                "tool": call.tool,
                "arguments": call.arguments,
            }

    def mark_executed(self, tool_call_id: str) -> None:
        self._executed.add(tool_call_id)

    def flush_blocked(self) -> None:
        """把「进了消息历史但没经过 execute()」的 Observation 补成界面事件。"""
        messages = list(getattr(self.agent, "messages", None) or [])

        for message in messages[self._scan_from:]:
            if not isinstance(message, Observation):
                continue

            call_id = message.tool_call_id
            if call_id in self._executed or call_id in self._reported:
                continue
            self._reported.add(call_id)

            call = self._calls.get(call_id, {})
            self.emit({
                "type": "tool_call",
                "step": self.step,
                "tool": message.tool,
                "tool_call_id": call_id,
                "arguments": call.get("arguments", {}),
            })
            self.emit({
                "type": "observation",
                "step": self.step,
                "tool": message.tool,
                "tool_call_id": call_id,
                "success": False,
                # 界面靠这个标记区分「工具跑了但失败」和「根本没让跑」
                "blocked": True,
                # 上游在「用户拒绝」那条分支上会把 content 留空，
                # 这里补一句人话，免得界面上是个空框。
                "content": clip(message.content)
                or "工具未被执行（权限策略拒绝，或需要批准但未获批准）",
            })

        self._scan_from = len(messages)


class ObservableModel:
    """包住真实的 Model。

    除了 `generate()`，其余属性一律转发给被包的那个 —— `set_trajectory()`、
    `tool_calls`… 全部照旧，包装层不介入。`__getattr__` 只在类上找不到该属性时
    才触发，所以这里只拦截 generate() 一个。
    """

    def __init__(self, inner: Any, observer: _Observer):
        self._inner = inner
        self._observer = observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def generate(self, messages):
        observer = self._observer

        # run() 是「记步号 → 立刻调 generate()」，所以在这里发 step_start
        # 和原来在 run() 循环里发是同一个位置、同一个编号。
        observer.begin_step()

        message = self._inner.generate(messages)

        observer.remember_calls(message.tool_calls)

        observer.emit({
            "type": "assistant",
            "step": observer.step,
            "content": message.content,
            "tool_calls": [
                {
                    "tool": call.tool,
                    "tool_call_id": call.tool_call_id,
                    "arguments": call.arguments,
                }
                for call in (message.tool_calls or [])
            ],
        })
        return message


class ObservableTools:
    """包住 ToolRegistry。

    `schemas()` / `get()` / `register()` 全部原样转发，只有 `execute()` 中间插了
    两个事件。DeepSeekModel 内部那点 `self.tools.schemas()` 走的也是转发，不受影响。
    """

    def __init__(self, inner: Any, observer: _Observer):
        self._inner = inner
        self._observer = observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def execute(self, tool_call):
        observer = self._observer

        # 先记下来：这个工具真的被执行了，后面补帧时跳过它
        observer.mark_executed(tool_call.tool_call_id)

        observer.emit({
            "type": "tool_call",
            "step": observer.step,
            "tool": tool_call.tool,
            "tool_call_id": tool_call.tool_call_id,
            "arguments": tool_call.arguments,
        })

        observation = self._inner.execute(tool_call)

        observer.emit({
            "type": "observation",
            "step": observer.step,
            "tool": observation.tool,
            "tool_call_id": observation.tool_call_id,
            "success": observation.success,
            "blocked": False,
            # 截断只作用于这一帧；observation 本身原样交回给 run()
            "content": clip(observation.content),
        })
        return observation


def run_with_events(agent, task: str, emit: Callable[[dict[str, Any]], None]) -> str:
    """跑一次 agent，把过程翻译成事件推给 emit，返回最终答案。

    只替换 agent **实例上**的 model / tools，不动它们的类 —— 命令行那条路
    （main.py 自己 new 一个 Agent）根本不会走到这里，所以完全不受影响。
    """
    observer = _Observer(emit, agent)

    # 必须在 run() 之前换：run() 一开头就会调 self.model.set_trajectory()，
    # 那时包装层得已经在位，trajectory 才会照旧记在真实的 model 上。
    agent.model = ObservableModel(agent.model, observer)
    agent.tools = ObservableTools(agent.tools, observer)

    result = agent.run(task)

    # 最后一步如果卡在步数上限，就没有「下一次 generate」来触发补帧，
    # 这里收个尾，保证被拒绝的工具不会在界面上凭空消失。
    observer.flush_blocked()

    observer.emit({
        "type": "final",
        "step": observer.step,
        "content": result,
        # 靠返回值认「被截停」：这句话是 agent.py 步数耗尽时唯一的返回值。
        # 万一模型真的把这句话当答案说出来，会被误标成 stopped —— 纯显示，
        # 不影响任何逻辑，不值得为它去猜别的信号。
        "stopped": result == _MAX_STEPS_RESULT,
    })
    return result
