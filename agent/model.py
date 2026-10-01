from abc import ABC, abstractmethod
from .messages import Message

class Model(ABC):

    @abstractmethod  # model只规定generate()必须存在，但不负责真正实现。
    def generate(self, messages: list[Message]) -> str:
        pass


class FakeModel(Model):
    def generate(self, messages: list[Message]) -> str:
        return "Hello from FakeModel"
