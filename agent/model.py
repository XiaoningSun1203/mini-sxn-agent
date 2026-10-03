from abc import ABC, abstractmethod
from .messages import Message, Action

class Model(ABC):

    @abstractmethod  # model只规定generate()必须存在，但不负责真正实现。
    def generate(self, messages: list[Message]) -> Action:
        pass
    # 真实的LLM可能返回json也可能是API原生的function calling。
    # 这些东西最后都由Model层转换成Action，Agent loop不需要知道模型供应商的具体格式。

class FakeModel(Model):

    def __init__(self):
        self.call_count = 0

    def generate(self, messages: list[Message]) -> Action:
        self.call_count += 1

        if self.call_count == 1:
            return Action(
                tool="shell",
                arguements={
                    "command": "pwd"
                }
            )
        return Action(
            tool=None,
            arguements={},
            final_answer="Task compeleted."
        )
