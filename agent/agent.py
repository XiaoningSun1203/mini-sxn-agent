from .messages import Message
from .model import Model

class Agent:

    def __init__(self, model: Model):
        self.model = model # self.model 是给对象添加 model 属性
        self.messages: list[Message] = []

    def run(self, task: str) -> str:
        self.messages.append(
            Message(
                role = "user",
                content = task
            )
        )
        response = self.model.generate(self.messages)
        return response


