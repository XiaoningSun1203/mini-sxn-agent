"""标准库实现的 HTTP + SSE 服务，零第三方依赖。

为什么手写 SSE 而不用现成框架：这个项目只依赖 openai 一个包，
为了一个本地单用户的界面再拉一串传递依赖不划算。
http.server 够用，代价是下面这些坑得自己躲 —— 每条都标了「为什么」。
"""
import json
import os
import queue
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import vault
from .agent_factory import PROJECT_ROOT, load_config, run_task, safe_error

HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8765

# 只绑回环。这个 agent 注册了 ShellTool（subprocess.run(shell=True)）和写文件能力，
# 绑 0.0.0.0 等于把一台能执行任意命令的机器挂到局域网上，
# 顺带把界面里填的 API key 也开放给任何能访问该端口的人。
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 固定路由表：用户输入从不参与路径拼接，所以不存在目录穿越。
_STATIC_ROUTES = {
    "/": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
}

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

# DeepSeek 的请求体上限是 48 MiB，这里给到 64 MiB 留余量；
# 它同时也是挡住超大 body 的第一道闸。
MAX_BODY_BYTES = 64 * 1024 * 1024

# 只在读请求体时设 socket 超时。读完必须解除，
# 否则流式阶段的 sendall 会被这个超时打断，长回答写到一半就炸。
BODY_READ_TIMEOUT = 30

# 队列空转多少轮（每轮 1 秒）发一次 SSE 注释帧，用来保活兼探测断连
KEEPALIVE_TICKS = 15


class Handler(BaseHTTPRequestHandler):

    # BaseHTTPRequestHandler 默认是 HTTP/1.0。这里必须显式设成 1.1，
    # 因为我们要自己控制连接何时关闭：SSE 没有 Content-Length，靠关连接表示流结束。
    protocol_version = "HTTP/1.1"
    server_version = "mini-sxn-agent-web/1.0"

    # ---------------- 路由 ----------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in _STATIC_ROUTES:
            return self._serve_static(path)

        if path == "/api/health":
            return self._serve_json({
                "ok": True,
                "model": self.server.model_name,
                "fake_model": self.server.fake_model,
                "workspace": str(self.server.workspace),
                "vault": vault.current_protector_info(),
                "vault_path": str(vault.vault_path()),
            })

        # 只有名字和元信息 —— **这个接口永远不会回密钥**，设计上就没有这条通路。
        if path == "/api/credentials":
            return self._serve_credential_list()

        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/run":
            return self._serve_run()
        if path == "/api/credentials":
            return self._serve_credential_add()
        if path == "/api/credentials/delete":
            return self._serve_credential_delete()

        return self.send_error(404)

    def do_OPTIONS(self):
        # 不回任何 CORS 许可头，跨站预检直接失败。
        # 这是挡住「浏览器里别的网页偷偷打 127.0.0.1」的一环。
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------- 静态文件与 JSON ----------------

    def _serve_static(self, path: str) -> None:
        name = _STATIC_ROUTES[path]
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            return self.send_error(404)

        self.send_response(200)
        self.send_header("Content-Type", _MIME[Path(name).suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            # 有些拒绝发生在读请求体之前（比如 Origin / Host 校验），
            # 这时请求体还原封不动躺在 socket 里。HTTP/1.1 默认复用连接，
            # 那些残留字节会被当成下一个请求去解析，报 "Bad request version"。
            # 出错就关连接，把这个坑堵掉。
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    # ---------------- 公共前置：跨站校验 + 读 body ----------------

    def _guard_origin(self) -> bool:
        """反 DNS-rebinding 与跨站防护，通过了返回 True。

        浏览器发起的跨站请求一定带 Origin；带了就必须是我们自己的来源。
        Host 也校验，防止有人用 hosts 把别的域名指到 127.0.0.1 再借浏览器打进来。

        凡是会「改变状态」的接口都必须先过这里。凭据接口尤其重要：
        少了这一道，别人网页上的一行 fetch 就能往你的凭据柜里塞东西。
        """
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self._serve_json({"error": "禁止的来源"}, 403)
            return False

        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if host not in _LOOPBACK:
            self._serve_json({"error": "禁止的 Host"}, 403)
            return False

        return True

    def _read_json_body(self) -> dict | None:
        """读并解析请求体。失败时响应已经发出去了，这里返回 None。"""
        # 必须把 body 读干净再回响应，否则连接会被 RST。
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._serve_json({"error": "Content-Length 不合法"}, 400)
            return None

        if length <= 0 or length > MAX_BODY_BYTES:
            self._serve_json({"error": "请求体为空或过大"}, 413)
            return None

        self.connection.settimeout(BODY_READ_TIMEOUT)
        try:
            raw = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self._serve_json({"error": "读取请求体超时"}, 408)
            return None
        finally:
            # 关键：流式阶段不能带超时
            self.connection.settimeout(None)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._serve_json({"error": "请求体不是合法 JSON"}, 400)
            return None

        if not isinstance(payload, dict):
            self._serve_json({"error": "请求体必须是 JSON 对象"}, 400)
            return None

        return payload

    # ---------------- 凭据柜 ----------------

    def _serve_credential_list(self) -> None:
        try:
            payload = {
                "credentials": vault.list_credentials(),
                "protector": vault.current_protector_info(),
                "vault_path": str(vault.vault_path()),
            }
        except vault.VaultError as error:
            return self._serve_json({"error": safe_error(error)}, 500)
        self._serve_json(payload)

    def _serve_credential_add(self) -> None:
        if not self._guard_origin():
            return

        # 请求体里这一次带着明文密钥 —— 全程不许进日志、不许进 trajectory。
        payload = self._read_json_body()
        if payload is None:
            return

        try:
            info = vault.add_credential(
                str(payload.get("name") or ""),
                str(payload.get("api_key") or ""),
            )
        except vault.VaultError as error:
            return self._serve_json({"error": safe_error(error)}, 400)
        except Exception as error:
            return self._serve_json(
                {"error": safe_error(f"{type(error).__name__}: {error}")}, 500
            )

        # 回给浏览器的是元信息，**一个字节的密钥都没有**
        self._serve_json({
            "ok": True,
            "credential": info,
            "protector": vault.current_protector_info(),
        })

    def _serve_credential_delete(self) -> None:
        if not self._guard_origin():
            return

        payload = self._read_json_body()
        if payload is None:
            return

        name = str(payload.get("name") or "")
        try:
            removed = vault.delete_credential(name)
        except vault.VaultError as error:
            return self._serve_json({"error": safe_error(error)}, 400)

        self._serve_json({"ok": True, "removed": removed})

    # ---------------- 核心：POST /api/run → SSE ----------------

    def _serve_run(self) -> None:
        # --- 1. 反 DNS-rebinding ---
        if not self._guard_origin():
            return

        # --- 2. 读请求体 ---
        payload = self._read_json_body()
        if payload is None:
            return

        # 请求体里**只有凭据名**，没有密钥本身 —— 密钥压根没来过浏览器。
        credential = str(payload.get("credential") or "").strip()
        if not credential:
            return self._serve_json(
                {"error": "请先选择一个凭据；没有的话点旁边的「管理」新增一把"}, 400
            )

        # 提前挡掉「名字打错」这种情况，免得白起一条流再报错。
        # 这一步不解密，真正的解密在跑任务时由 agent_factory 做。
        try:
            known = {item["name"] for item in vault.list_credentials()}
        except vault.VaultError as error:
            return self._serve_json({"error": safe_error(error)}, 500)

        if credential not in known:
            return self._serve_json(
                {"error": f"凭据柜里没有「{credential}」，请重新选一个"}, 400
            )

        task = str(payload.get("task") or "").strip()
        if not task:
            return self._serve_json({"error": "请输入任务"}, 400)

        # --- 3. 起工作线程 ---
        # Agent.run() 是阻塞的，里面还会 print()，绝不能放在请求线程里跑。
        event_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        worker = threading.Thread(
            target=run_task,
            args=(payload, event_queue, stop_event, self.server.workspace),
            name="mini-agent-worker",
            daemon=True,
        )
        worker.start()

        # --- 4. SSE 响应头 ---
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        # 流长度事先不知道，不能发 Content-Length；用关连接表示流结束。
        self.send_header("Connection", "close")
        self.end_headers()

        # HTTP/1.1 下默认会在这个连接上继续等下一个请求，必须明说不再复用，
        # 否则浏览器会一直挂在那儿等更多数据。
        self.close_connection = True

        # --- 5. queue → socket ---
        # 工作线程是先起、后发响应头的：它一开始就可能连推好几帧，
        # 队列会先把这些帧存住，等这里开始取的时候一帧都不会丢。
        #
        # 循环有两个出口，缺一不可：
        #   a) 收到终帧（done / error / cancelled）—— 正常结束；
        #   b) 队列空转时发现工作线程已经不在了 —— 线程意外死掉时兜底，
        #      否则连接会一直挂着不关，浏览器端就永远等不到流结束。
        idle_ticks = 0
        try:
            while True:
                try:
                    event = event_queue.get(timeout=1.0)
                except queue.Empty:
                    if not worker.is_alive():
                        break
                    idle_ticks += 1
                    if idle_ticks >= KEEPALIVE_TICKS:
                        self.wfile.write(b": keepalive\n\n")
                        idle_ticks = 0
                    continue

                idle_ticks = 0
                self._write_event(event)

                if event.get("type") in ("done", "error", "cancelled"):
                    break

        except (BrokenPipeError, ConnectionResetError, OSError):
            # 用户关了标签页或点了停止 —— 这是正常路径，不是异常。
            # 让 worker 在下一个检查点自己停下。
            stop_event.set()

    def _write_event(self, event: dict) -> None:
        event_type = event.get("type", "message")
        data = json.dumps(event, ensure_ascii=False)
        # self.wfile 是 socketserver._SocketWriter，而 StreamRequestHandler 的
        # wbufsize 默认是 0，所以 write() 内部直接就是 sendall —— 每帧立刻上线，
        # 不需要 flush()。（"SSE 必须 flush" 是旧版本的说法，这里已不适用。）
        self.wfile.write(
            f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
        )

    def log_message(self, fmt, *args):
        # 默认实现会把请求行打到 stderr。API key 只走 POST body（不进请求行），
        # 这里再兜一层脱敏，确保任何情况下 key 都不会出现在日志里。
        sys.stderr.write(
            "%s - %s\n" % (self.address_string(), safe_error(fmt % args))
        )


class AgentHTTPServer(ThreadingHTTPServer):
    # 一条 SSE 会占住一个线程很久，ThreadingHTTPServer 才不会把整个服务堵死
    # （用单线程的 HTTPServer 的话，一条长回答期间连首页都打不开）。
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, model_name, fake_model, workspace):
        self.model_name = model_name
        self.fake_model = fake_model
        self.workspace = workspace
        self.allowed_origins: set[str] = set()
        super().__init__(address, handler)


def build_server(
        host: str = HOST_DEFAULT,
        port: int = PORT_DEFAULT,
        workspace: Path | str | None = None
) -> AgentHTTPServer:
    """按参数建好服务器但还不启动，方便测试里用端口 0 起临时实例。"""
    if host not in _LOOPBACK:
        raise SystemExit(
            f"refusing to bind non-loopback address {host!r}.\n"
            "This agent carries a shell tool (subprocess.run(shell=True)) and "
            "can write files,\n"
            "so anyone who can reach this port could run arbitrary commands "
            "as you on this machine,\n"
            "and the API keys held in the vault would be exposed along with it.\n"
            "If you really need remote access, use an SSH port forward instead:\n"
            f"    ssh -L {PORT_DEFAULT}:127.0.0.1:{PORT_DEFAULT} <your-host>"
        )

    if workspace is None:
        workspace = PROJECT_ROOT / "sandbox-workspace"
    workspace = Path(workspace).resolve()

    # 凭据柜绝不能落在 agent 的工作目录里。
    # 落进去的话，它会跟着 git add 一起被提交、会被 read_file 工具读到、
    # 也会被整个目录打包发出去 —— 加密做得再好也架不住这么漏。
    # 检查放在 mkdir 之前：要拒绝就别先把目录建出来。
    vault_dir = vault.VAULT_DIR.resolve()
    if workspace == vault_dir or workspace in vault_dir.parents:
        raise SystemExit(
            f"refusing to use {workspace} as the agent workspace:\n"
            f"the vault lives at {vault_dir} inside it, so the key file would be "
            f"committed,\n"
            f"read and packed up together with the workspace. Pick a directory "
            f"that does not contain it, e.g.:\n"
            f"    python web.py --workspace ./sandbox-workspace"
        )

    workspace.mkdir(parents=True, exist_ok=True)

    config = load_config()

    httpd = AgentHTTPServer(
        (host, port),
        Handler,
        model_name=config["model"]["name"],
        fake_model=os.environ.get("MINI_AGENT_WEB_FAKE_MODEL") == "1",
        workspace=workspace,
    )

    # 端口传 0 时由内核分配，真正的端口要等绑定之后才知道
    actual_port = httpd.server_address[1]
    httpd.allowed_origins = {
        f"http://127.0.0.1:{actual_port}",
        f"http://localhost:{actual_port}",
    }
    return httpd


def serve(
        host: str = HOST_DEFAULT,
        port: int = PORT_DEFAULT,
        workspace: Path | str | None = None,
        open_browser: bool = False
) -> None:
    httpd = build_server(host, port, workspace)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    print(f"[web] mini-agent 界面已启动：{url}")
    print(f"[web] agent 的工作目录：{httpd.workspace}")

    protector = vault.current_protector_info()
    print(f"[web] 凭据柜：{vault.vault_path()}")
    print(f"[web] 凭据保护方式：{protector['label']}")
    if not protector["encrypted"]:
        print("[web] ⚠ 本机没有可用的系统加密，密钥是明文存的（目录已设成只有你能读）")

    if httpd.fake_model:
        print("[web] 注意：当前是假模型模式（MINI_AGENT_WEB_FAKE_MODEL=1），不会真的调用 DeepSeek")
    print("[web] 按 Ctrl-C 停止")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] 已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(open_browser=True)
