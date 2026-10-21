import os
import json
from openai import OpenAI
from abc import ABC, abstractmethod
from .messages import Message, AssistantMessage, Observation, ToolCall, HistoryItem
from .tools import ToolRegistry
from .trajectory import Trajectory
from .configLoader import ModelConfig


class Model(ABC):

    @abstractmethod  # model只规定generate()必须存在，但不负责真正实现。
    def generate(self, messages: list[HistoryItem]) -> AssistantMessage:
        pass
    # 真实的LLM可能返回json也可能是API原生的function calling。
    # 不同模型供应商的返回格式由 Model 层转换成 AssistantMessage，
    # Agent loop不需要知道模型供应商的具体格式。


class DeepSeekModel(Model):

    def __init__(
            self, 
            tools: ToolRegistry,
            model_config: ModelConfig
    ):
        self.trajectory: Trajectory | None = None
        self.tools = tools

        self.model_config = model_config

        api_key_env=os.environ[self.model_config.api_key_env]
        if not api_key_env:
            raise RuntimeError(f"please export Model API key first!")

        self.client = OpenAI(
            api_key=api_key_env,
            base_url=self.model_config.base_url
        )


    # Model Adaptor
    #转换函数，把 “我们的消息”转换成deepseek api能识别的
    #这三个究竟怎么转换？Message、AssistantMessage、Observation
    def _convert_messages(self, messages: list[HistoryItem]) -> list[dict]:
        result = []

        for message in messages:
            #如果是Observation类型
            if isinstance(message, Observation):
                item = {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": json.dumps({
                        "tool": message.tool,
                        "content": message.content,
                        "success": message.success
                    })
                }
                result.append(item)
                continue

            #如果是AssistantMessage类型
            if isinstance(message, AssistantMessage):
                item = {
                    "role": "assistant",
                    "content": message.content,
                }

                if message.tool_calls:
                    api_tool_calls = []
                    for tool_call in message.tool_calls:
                        api_tool_calls.append(
                            {
                                "id": tool_call.tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.tool,
                                    "arguments": json.dumps(tool_call.arguments)
                                }
                            }
                        )

                    item["tool_calls"] = api_tool_calls

                result.append(item)
                continue

            # 如果是 Message 类型
            if isinstance(message, Message):
                item = {
                    "role": message.role,
                    "content": message.content
                }

                result.append(item)
                continue

            raise TypeError(f"Unknown HistoryItem:{type(message)}")

        return result


    def generate(self, messages: list[HistoryItem]) -> AssistantMessage:
        # 需要转换成符合 LLM 格式的消息
        api_messages = self._convert_messages(messages)

        self.trajectory.record_model_input(api_messages)

        response = self.client.chat.completions.create(
            model=self.model_config.model_name,
            messages=api_messages,
            tools=self.tools.schemas()
        )

        assistant_message = response.choices[0].message

        self.trajectory.record_model_output(assistant_message)

        #此处涉及从 LLM response 转换成我们的AssistantMessage
        tool_calls = []
        if assistant_message.tool_calls is not None:
            for call in assistant_message.tool_calls:
                call = ToolCall(
                    tool_call_id=call.id,
                    tool=call.function.name,
                    arguments=json.loads(call.function.arguments)
                )
                tool_calls.append(call)

        return AssistantMessage(
            content=assistant_message.content,
            tool_calls=tool_calls
        )


    def set_trajectory(
        self,
        trajectory: Trajectory
    ) -> None:
        self.trajectory = trajectory

#DeepSeek真实response
# ChatCompletion(
#     id='8df5ab3a-20e3-4451-8b39-988a2fa83f1c',
#     choices=[
#         Choice(
#             finish_reason='tool_calls', #我现在不准备结束回答，我决定先调用工具
#             index=0,
#             logprobs=None,
#             message=ChatCompletionMessage(
#                 content="I'll list the files in the current directory for you.",
#                 refusal=None,
#                 role='assistant',
#                 annotations=None,
#                 audio=None,
#                 function_call=None,
#                 tool_calls=[
#                     ChatCompletionMessageFunctionToolCall(
#                         id='call_00_sNMAu1LRt2unu6WlBFUj8464',
#                         function=Function(
#                             arguments='{"command": "ls -la"}',
#                             name='shell'
#                         ), 
#                         type='function',
#                         index=0
#                     )
#                 ]
#             )
#         )
#     ],
#     created=1791107687,
#     model='deepseek-flash',
#     object='chat.completion',
#     metadata=None,
#     moderation=None,
#     service_tier=None,
#     system_fingerprint='aeb56401ca74e127821c4f9126dcb669',
#     usage=CompletionUsage(
#         completion_tokens=49,
#         prompt_tokens=284,
#         total_tokens=333,
#         completion_tokens_details=None,
#         prompt_tokens_details=PromptTokensDetails(
#             audio_tokens=None,
#             cache_write_tokens=None,
#             cached_tokens=0,
#             image_tokens=None,
#             text_tokens=None
#         ),
#         prompt_cache_hit_tokens=0,
#         prompt_cache_miss_tokens=284
#     )
# )


# class FakeModel(Model):

#     def __init__(self):
#         self.call_count = 0

#     def generate(self, messages: list[Message]) -> ModelOutput:
#         self.call_count += 1

#         if self.call_count == 1:
#             return ToolCall(
#                 tool="shell",
#                 arguments={"command": "pwd"}
#             )

#         if self.call_count == 2:
#             return ToolCall(
#                 tool="shell",
#                 arguments={"command": "ls"}
#             )

#         return Finish(
#             content="Finished inspecting the workspace."
#         )
