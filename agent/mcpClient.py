# MCP层的主要作用：
# agent能够建立连接、管理session和关闭 与MCP server的通信
# step1:通过session 执行list_tools(),发现 MCP server 提供的工具;
# step2:执行 call_tool(),向server发送工具调用请求
# step3:接收执行结果，处理超时或者连接异常
# 10/20: streambleHTTP MCP 
# class McpClient:
#     def __init__(self):
        