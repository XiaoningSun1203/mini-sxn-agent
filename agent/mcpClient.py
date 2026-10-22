# MCP层的主要作用：
# agent能够建立连接、管理session和关闭 与MCP server的通信
# step1:通过session 执行list_tools(),发现 MCP server 提供的工具;
# step2:执行 call_tool(),向server发送工具调用请求
# step3:接收执行结果，处理超时或者连接异常

# 10/20: streambleHTTP MCP 
# 1.连接 GitHub提供的 URL：
# https://api.githubcopilot.com/mcp/x/repos/readonly
# 2.在环境上 export github Token
# 3.初始化 MCP Session。
# 4.调用 list_tools() 获取工具定义。
# 5.调用 call_tool() 执行工具，处理结果。

import os
import httpx2
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def log_error_response(response):
    if response.status_code >= 400:
        await response.aread()
        print("HTTP 状态码：", response.status_code)
        print("HTTP 错误正文：", response.text)


class McpClient:
    # def __init__(self, mcp_config: McpConfig):
        # self.access_token = mcp_config.access_token
        # self.url = mcp_config.url
    def __init__(self):
        self.access_token = os.environ["GITHUB_TOKEN"]
        self.url = "https://api.githubcopilot.com/mcp/x/repos/readonly"
        self.stack = AsyncExitStack()
        self.session = None

    async def initialize(self):

        # 提供 HTTP 客户端
        http_client = await self.stack.enter_async_context( #
            #创建了一个 客户端 对象，并配置 HTTP 请求头
            httpx2.AsyncClient(
                headers={"Authorization" : f"Bearer {self.access_token}"},
                event_hooks={"response": [log_error_response]},
            )
        )
        
        # 使用 HTTP 客户端, 提供读写通道.Connect to a streamable HTTP server
        read_stream, write_stream = await self.stack.enter_async_context(
            streamable_http_client(self.url, http_client=http_client)
        )
 
        # Create a session. 使用读写通道，处理MCP请求
        self.session = await self.stack.enter_async_context(
            #用读写通道创建session对象
            ClientSession(read_stream, write_stream)
        )

        # MCP 初始化握手，交换协议版本、能力等信息，为后续请求做好准备
        # init_result = await self.session.initialize()
        # 处理初始化异常
        try:
            init_result = await self.session.initialize()
        except Exception as exc:
            print("异常类型：", type(exc))
            print("错误码：", getattr(exc, "code", None))
            print("错误消息：", getattr(exc, "message", None))
            print("错误详情：", getattr(exc, "data", None))
            raise


    async def list_tools(self):
        result = await self.session.list_tools()

        text = result.model_dump_json(indent=2)
        print("\n".join(text.splitlines()[:100]))

    
    async def close(self):
        await self.stack.aclose()
        # 依次退出：
        # 1. ClientSession 的异步上下文。
        # 2. streamable_http_client 的异步上下文。
        # 3. httpx.AsyncClient 的异步上下文。

    def mcp_format_convert_to_model_schema_format(self):
        # MCP fromat -> agent Tool schema
        pass


    def function_calling_format_convert_to_mcp_input(self):
        # tool
        pass

    def mcp_output_convert_to_model_input_format(self):

        