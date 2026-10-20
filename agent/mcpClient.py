# MCP层的主要作用：
# agent能够建立连接、管理session和关闭 与MCP server的通信
# step1:通过session 执行list_tools(),发现 MCP server 提供的工具;
# step2:执行 call_tool(),向server发送工具调用请求
# step3:接收执行结果，处理超时或者连接异常

# 10/20: streambleHTTP MCP 
# 1.连接 GitHub提供的 URL：https://api.githubcopilot.com/mcp/x/repos/readonly
# 2.在环境上 export github Token
# 3.初始化 MCP Session。
# 4.调用 list_tools() 获取工具定义。
# 5.调用 call_tool() 执行工具，处理结果。

import asyncio
from mcp import ClientSession
from mcp.client.streambleHTTP import streamble_http_client

class McpClient:
    # def __init__(self, mcp_config: McpConfig):
        # self.access_token = mcp_config.access_token
        # self.url = mcp_config.url
    def __init__(self):
        self.access_token = os.environ(GITHUB_TOKEN)
        self.url = "https://api.githubcopilot.com/mcp/x/repos/readonly"

    def initialize(self):
        

        