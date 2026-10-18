"""mini-agent 的浏览器界面。

这个包只依赖标准库，不引入任何第三方 web 框架 ——
项目本身已经很轻（只依赖 openai），为一个本地单用户的界面再拉一串依赖不划算。

- server.py         标准库 http.server + SSE
- agent_factory.py  把一次 web 请求变成一个跑起来的 Agent
- static/           前端（无构建步骤、无 CDN）
"""
