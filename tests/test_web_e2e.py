"""webui 的端到端测试：不需要浏览器、不联网、不花钱。

用假模型（MINI_AGENT_WEB_FAKE_MODEL=1）把整条链路跑通：
浏览器请求 → HTTP → SSE → 工作线程 → Agent loop → 工具执行 → 事件回传。

跑法：
    python3 -m unittest discover -s tests -v
或者直接：
    python3 tests/test_web_e2e.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# 让测试可以从项目根目录跑
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["MINI_AGENT_WEB_FAKE_MODEL"] = "1"

# 关键：把凭据柜指到临时目录。
# 必须在 import webui 之前设好 —— vault 在导入时就把路径定下来了。
# 少了这一行，测试会往用户真实的 ~/.mini-sxn-agent/credentials.json 里写东西。
_VAULT_TMP = tempfile.mkdtemp(prefix="mini-agent-vault-test-")
os.environ["MINI_AGENT_VAULT_DIR"] = _VAULT_TMP

from agent.agent import Agent  # noqa: E402
from agent.configLoader import (  # noqa: E402
    AgentConfig,
    PermissionConfig,
    PermissionDecision,
)
from agent.messages import AssistantMessage, Observation, ToolCall  # noqa: E402
from webui import observer, vault  # noqa: E402
from webui.observer import run_with_events  # noqa: E402
from webui.server import build_server  # noqa: E402

# 一个绝对不会出现在正常响应里的哨兵串，用来验证 key 没有被回显/落盘
CANARY = "sk-CANARY-must-not-leak-0123456789"

# 测试用的凭据名
CRED_NAME = "test-credential"


def setUpModule():
    """往临时柜子里放一把测试凭据。

    走的是真实路径（vault.add_credential），所以顺带也验证了
    「加密 → 落盘 → 再解密」这条路在当前平台上确实通。
    """
    vault.add_credential(CRED_NAME, CANARY)


class WebE2ETest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trajectory_dir = PROJECT_ROOT / "trajectories"
        cls.trajectory_dir.mkdir(exist_ok=True)

        # 端口传 0，让内核分配一个空闲端口，避免和别的测试抢
        cls.httpd = build_server(host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ---------- 工具 ----------

    def post_run(self, payload, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(
            "POST", "/api/run",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        response = conn.getresponse()
        # SSE 没有 Content-Length，读到连接关闭为止
        body = response.read().decode("utf-8")
        status = response.status
        response_headers = dict(response.getheaders())
        conn.close()
        return status, response_headers, body

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        conn.request(
            method, path, body=body,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        status = response.status
        conn.close()
        return status, text

    def get_json(self, path):
        status, text = self.request("GET", path)
        return status, {}, text

    def post_json(self, path, payload, headers=None):
        status, text = self.request("POST", path, payload, headers)
        return status, {}, text

    @staticmethod
    def parse_events(raw):
        events = []
        for frame in raw.split("\n\n"):
            if not frame or frame.startswith(":"):
                continue
            name, data = "message", ""
            for line in frame.split("\n"):
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
            if data:
                events.append((name, json.loads(data)))
        return events

    # ---------- 用例 ----------

    def test_01_text_task_runs_end_to_end(self):
        status, _, raw = self.post_run({"credential": CRED_NAME, "task": "run a self-check"})
        self.assertEqual(status, 200)

        events = self.parse_events(raw)
        names = [name for name, _ in events]

        for expected in ("step_start", "assistant", "observation", "final", "done"):
            self.assertIn(expected, names, f"missing {expected} event, got {names}")

        # 假模型第 2 步的回答里带着它收到的 user 消息 ——
        # 用它证明任务文字原样送到了模型这一层
        final = [d for n, d in events if n == "final"][0]
        self.assertIn("run a self-check", final["content"])

        # 工具真的执行了（FakeModel 第 1 步调的就是 shell）
        observation = [d for n, d in events if n == "observation"][0]
        self.assertTrue(observation["success"], observation)
        self.assertEqual(observation["tool"], "shell")
        self.assertIn("hello-from-fake-model", observation["content"])

    def test_02_canary_key_never_echoed(self):
        for payload in (
            {"credential": CRED_NAME, "task": "a normal task"},
            {"credential": CRED_NAME},                      # 故意触发 400
        ):
            status, _, raw = self.post_run(payload)
            self.assertNotIn(
                CANARY, raw,
                f"API key leaked into the response (status={status}): {raw[:400]}",
            )

        # 凭据列表是唯一一个主动「读柜子」的接口，也必须只回名字
        status, _, raw = self.get_json("/api/credentials")
        self.assertEqual(status, 200)
        self.assertIn(CRED_NAME, raw, "the name is not even listed")
        self.assertNotIn(CANARY, raw, "the credential list returned the secret")

        for item in json.loads(raw)["credentials"]:
            self.assertNotIn("secret", item, "a secret field appeared in a list item")

    def test_03_sse_headers_are_correct(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(
            "POST", "/api/run",
            body=json.dumps({"credential": CRED_NAME, "task": "headers"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}

        self.assertIn("text/event-stream", headers.get("content-type", ""))
        self.assertEqual(headers.get("connection", "").lower(), "close")
        # SSE 长度未知，绝不能有 Content-Length，否则会被截断
        self.assertNotIn("content-length", headers)

        response.read()
        conn.close()

    def test_04_rejects_cross_origin(self):
        status, _, _ = self.post_run(
            {"credential": CRED_NAME, "task": "x"},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_05_validation_errors(self):
        self.assertEqual(self.post_run({"task": "no credential"})[0], 400)
        self.assertEqual(self.post_run({"credential": CRED_NAME})[0], 400)
        # 名字打错的凭据要在起流之前就挡掉，别白跑一趟
        self.assertEqual(
            self.post_run({"credential": "no-such-credential", "task": "x"})[0], 400
        )

    def test_06_trajectory_records_task_but_never_the_key(self):
        # 轨迹文件名只精确到分钟，同一分钟内多次运行会写到同一个文件上。
        # 所以不能用「文件名是否出现过」判断，要看这次运行有没有把它改写过。
        before = {
            path: path.stat().st_mtime_ns
            for path in self.trajectory_dir.glob("trajectory_*.json")
        }

        self.post_run({
            "credential": CRED_NAME,
            "task": "trajectory check",
        })

        written = [
            path for path in self.trajectory_dir.glob("trajectory_*.json")
            if before.get(path) != path.stat().st_mtime_ns
        ]
        self.assertTrue(written, "no trajectory file was written")

        # 找到这次任务对应的那个
        target = None
        for path in written:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("task") == "trajectory check":
                target = data
                break
        self.assertIsNotNone(target, "no trajectory for this task was found")

        raw = json.dumps(target, ensure_ascii=False)

        # 任务文字要留在轨迹里，这是它的用途
        self.assertIn("trajectory check", raw)

        # key 绝不能被写进 trajectory
        self.assertNotIn(CANARY, raw, "the API key was written into the trajectory")


class StreamingTest(unittest.TestCase):
    """证明服务端是一帧一帧推的，不是跑完再一次性发。

    这是界面上能不能看到实时进度的关键，也是标准库手写 SSE 最容易翻车的地方
    （Content-Length、连接复用、忘了 flush 之类都会导致「攒完一起发」）。
    """

    @classmethod
    def setUpClass(cls):
        cls.httpd = build_server(host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_frames_arrive_progressively(self):
        import socket
        import time

        # 让假模型每步停 0.6 秒，这样才分得清「流式」和「攒完一起发」
        os.environ["MINI_AGENT_WEB_FAKE_DELAY"] = "0.6"
        try:
            payload = json.dumps({"credential": CRED_NAME, "task": "streaming check"}).encode()
            request = (
                b"POST /api/run HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
                + payload
            )

            sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
            arrivals = []
            try:
                start = time.monotonic()
                sock.sendall(request)

                buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    # 每收到一个完整帧就记一次到达时间
                    while b"\n\n" in buffer:
                        frame, buffer = buffer.split(b"\n\n", 1)
                        if frame.startswith(b"event:"):
                            arrivals.append(time.monotonic() - start)
            finally:
                sock.close()
        finally:
            os.environ.pop("MINI_AGENT_WEB_FAKE_DELAY", None)

        self.assertGreaterEqual(len(arrivals), 6, f"too few frames: {arrivals}")

        spread = arrivals[-1] - arrivals[0]
        # 两步各停 0.6s，首末帧的跨度应该接近 0.6s。
        # 若服务端在缓冲，所有帧会挤在最后一起到，跨度趋近 0。
        self.assertGreater(
            spread, 0.4,
            f"all frames arrived at nearly the same time (spread {spread:.3f}s): "
            "the server is probably buffering",
        )
        self.assertLess(
            arrivals[0], arrivals[-1] - 0.3,
            "the first frame did not arrive clearly before the last",
        )


class VaultTest(unittest.TestCase):
    """凭据柜本身。

    要守住的三条性质，每条都有对应用例：
      1. 写入一次 —— 存进去以后就没有任何接口能把明文取回来
      2. 系统加密 —— 落盘前过保护器；声称加密了就不许明文躺在文件里
      3. 用时注入 —— 服务端按名字取用，请求体里只有名字
    """

    # 这一组用例只用这两个名字，绝不碰模块级那把 CRED_NAME
    NAMES = ("vault-test-a", "vault-test-b")

    @classmethod
    def setUpClass(cls):
        cls.httpd = build_server(host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # 别的用例可能先把柜子清过，这里补回来，免得用例之间互相依赖执行顺序
        if CRED_NAME not in {item["name"] for item in vault.list_credentials()}:
            vault.add_credential(CRED_NAME, CANARY)
        for name in self.NAMES:
            vault.delete_credential(name)

    def tearDown(self):
        for name in self.NAMES:
            vault.delete_credential(name)

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        conn.request(
            method, path, body=body,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        status = response.status
        conn.close()
        return status, text

    # ---------- 1. 写入一次，永不回传 ----------

    def test_01_add_never_returns_the_secret(self):
        status, raw = self.request(
            "POST", "/api/credentials",
            {"name": self.NAMES[0], "api_key": CANARY},
        )
        self.assertEqual(status, 200, raw)
        self.assertNotIn(CANARY, raw, "the add endpoint echoed the secret back")

        status, listed = self.request("GET", "/api/credentials")
        self.assertEqual(status, 200)
        self.assertIn(self.NAMES[0], listed)
        self.assertNotIn(CANARY, listed, "the list endpoint returned the secret")

    def test_02_secret_is_recoverable_server_side(self):
        # 「取不回来」指对浏览器而言；服务端跑任务时必须取得到
        self.request("POST", "/api/credentials",
                     {"name": self.NAMES[0], "api_key": CANARY})
        self.assertEqual(vault.get_secret(self.NAMES[0]), CANARY)

    # ---------- 2. 落盘加密 ----------

    def test_03_claims_of_encryption_are_true(self):
        info = vault.current_protector_info()
        self.assertIn(info["protector"], {"dpapi", "keychain", "plain"})

        self.request("POST", "/api/credentials",
                     {"name": self.NAMES[0], "api_key": CANARY})

        entry = json.loads(vault.VAULT_FILE.read_text(encoding="utf-8"))
        stored = entry["credentials"][self.NAMES[0]]
        protector = vault.protector_by_key(stored["protector"])
        self.assertIsNotNone(protector, "this machine does not know the stored protector")

        if protector.key != "plain":
            # 声称用了系统加密，那密钥就不许明文躺在文件里
            raw = vault.VAULT_FILE.read_text(encoding="utf-8")
            self.assertNotIn(
                CANARY, raw,
                f"the protector claims {protector.key}, but the secret sits in "
                "the vault file as plaintext",
            )

    @unittest.skipIf(sys.platform == "win32",
                     "chmod is basically a no-op on Windows")
    def test_04_file_permissions_are_locked_down(self):
        import stat

        self.request("POST", "/api/credentials",
                     {"name": self.NAMES[0], "api_key": CANARY})

        file_mode = stat.S_IMODE(vault.VAULT_FILE.stat().st_mode)
        dir_mode = stat.S_IMODE(vault.VAULT_DIR.stat().st_mode)

        self.assertEqual(file_mode, 0o600, f"vault file mode is {oct(file_mode)}")
        self.assertEqual(dir_mode, 0o700, f"vault dir mode is {oct(dir_mode)}")

    def test_05_no_plaintext_leftover_in_temp_files(self):
        # 落盘走的是「写临时文件再改名」，中途的临时文件不能留下残骸
        self.request("POST", "/api/credentials",
                     {"name": self.NAMES[0], "api_key": CANARY})

        leftovers = [p for p in vault.VAULT_DIR.iterdir() if p.name.startswith(".credentials-")]
        self.assertEqual(leftovers, [], f"leftover temp files: {leftovers}")

    # ---------- 3. 删除与错误处理 ----------

    def test_06_delete_removes_it(self):
        self.request("POST", "/api/credentials",
                     {"name": self.NAMES[0], "api_key": CANARY})

        status, raw = self.request("POST", "/api/credentials/delete",
                                   {"name": self.NAMES[0]})
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)["removed"])

        self.assertNotIn(self.NAMES[0], vault.VAULT_FILE.read_text(encoding="utf-8"))
        with self.assertRaises(vault.VaultError):
            vault.get_secret(self.NAMES[0])

    def test_07_credential_routes_reject_cross_origin(self):
        # 少了这一道，别人网页上的一行 fetch 就能往你柜子里塞东西
        status, _ = self.request(
            "POST", "/api/credentials",
            {"name": self.NAMES[0], "api_key": CANARY},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertNotIn(self.NAMES[0], {i["name"] for i in vault.list_credentials()})

    def test_08_bad_names_and_empty_keys_are_refused(self):
        for bad in ("", "   ", "a" * 65, "../etc/passwd", "x/y"):
            status, raw = self.request(
                "POST", "/api/credentials", {"name": bad, "api_key": CANARY}
            )
            self.assertEqual(status, 400, f"{bad!r} was accepted: {raw}")

        for name in self.NAMES:
            status, _ = self.request(
                "POST", "/api/credentials", {"name": name, "api_key": "   "}
            )
            self.assertEqual(status, 400, "an empty API key was accepted")

    def test_09_errors_never_leak_the_key(self):
        # 错误文案会一路走到浏览器，绝不能顺手把密钥带出去
        try:
            vault.get_secret("no-such-credential")
            self.fail("expected VaultError")
        except vault.VaultError as error:
            self.assertNotIn(CANARY, str(error))

    def test_10_cjk_credential_names_are_accepted(self):
        # 凭据名允许中文（_NAME_RE 里那段 CJK 码位区间）。这里用 \u 转义写，
        # 源码保持纯 ASCII，同时把「中文名能存能用」这条行为钉住 ——
        # 否则哪天有人手滑把那截区间删掉，也不会有用例报错。
        name = "\u6d4b\u8bd5\u51ed\u636e"          # 测试凭据
        try:
            status, raw = self.request(
                "POST", "/api/credentials", {"name": name, "api_key": CANARY}
            )
            self.assertEqual(status, 200, raw)
            self.assertIn(name, {item["name"] for item in vault.list_credentials()})
            # 存得进，也要取得出 —— 名字只是名字，不该影响密钥往返
            self.assertEqual(vault.get_secret(name), CANARY)
        finally:
            vault.delete_credential(name)


class _ScriptedModel:
    """按脚本走：前 tool_steps 步调工具，之后给最终答案。"""

    def __init__(self, tool_steps=1, calls_per_step=1, final="all done"):
        self.tool_steps = tool_steps
        self.calls_per_step = calls_per_step
        self.final_text = final
        self.step = 0
        self.trajectory = None

    def set_trajectory(self, trajectory):
        self.trajectory = trajectory

    def generate(self, messages):
        self.step += 1
        if self.step <= self.tool_steps:
            return AssistantMessage(
                content=f"thinking {self.step}",
                tool_calls=[
                    ToolCall(tool="echo", tool_call_id=f"c{self.step}_{i}",
                             arguments={"n": self.step, "i": i})
                    for i in range(self.calls_per_step)
                ],
            )
        return AssistantMessage(content=self.final_text, tool_calls=[])


class _EchoTools:
    """最小工具表：execute() 回一段固定内容。observer 只需要这一个方法。"""

    def __init__(self, payload="ok"):
        self.payload = payload

    def execute(self, tool_call):
        return Observation(tool=tool_call.tool, tool_call_id=tool_call.tool_call_id,
                           content=self.payload, success=True)


class ObserverTest(unittest.TestCase):
    """界面事件是**从外面观测**出来的（webui/observer.py），Agent 一行没改。

    换来 agent.py 保持原版的代价，是事件顺序变成了「依赖 run() 内部调用顺序」的
    隐式契约：**改坏了不会报错，只会静默少发几帧**，界面上看起来像卡住不动。
    所以这个类把确切的序列和步骤号钉死 —— 它是那套观测唯一的防错网。
    """

    def _agent(self, model, tools=None, max_steps=30, permission=None):
        return Agent(
            model=model,
            tools=tools or _EchoTools(),
            agent_config=AgentConfig(
                max_steps=max_steps,
                step_mode=False,
                working_dir=".",
                system_prompt="SYS",
            ),
            permission_config=PermissionConfig(
                permission_map=permission or {
                    "echo": PermissionDecision.ALLOW,
                }
            ),
            trajectory_dir=tempfile.mkdtemp(prefix="traj-observer-"),
        )

    def test_01_event_sequence_is_exact(self):
        agent = self._agent(_ScriptedModel(tool_steps=1))
        events = []
        result = run_with_events(agent, "do it", events.append)

        self.assertEqual(result, "all done")
        # 一帧不多、一帧不少、步骤号一位不差。
        # 期望值来自 agent/agent.py 原始循环（步号→generate→工具→返回），
        # 不是从 observer.py 反推的。
        self.assertEqual(
            [(event["type"], event["step"]) for event in events],
            [
                ("step_start", 0), ("assistant", 0),
                ("tool_call", 0), ("observation", 0),
                ("step_start", 1), ("assistant", 1),
                ("final", 1),
            ],
        )
        self.assertFalse(events[-1]["stopped"])

    def test_02_several_tools_in_one_step_share_the_step_number(self):
        # 一步里调两个工具：两对 tool_call/observation 都必须落在同一步上。
        # 这正是「一步」跨过 generate 和全部工具调用、而包装器只看得到
        # generate 那一半的地方，off-by-one 最容易在这露头。
        agent = self._agent(_ScriptedModel(tool_steps=1, calls_per_step=2))
        events = []
        run_with_events(agent, "do it", events.append)

        self.assertEqual(
            [(event["type"], event["step"]) for event in events],
            [
                ("step_start", 0), ("assistant", 0),
                ("tool_call", 0), ("observation", 0),
                ("tool_call", 0), ("observation", 0),
                ("step_start", 1), ("assistant", 1),
                ("final", 1),
            ],
        )

    def test_03_hitting_max_steps_is_marked_stopped(self):
        # 模型永远要调工具，跑满 max_steps 也没收尾
        agent = self._agent(_ScriptedModel(tool_steps=99), max_steps=2)
        events = []
        result = run_with_events(agent, "loop forever", events.append)

        self.assertEqual(result, "Agent stopped: maximum steps reached.")
        self.assertEqual(events[-1]["type"], "final")
        self.assertTrue(events[-1]["stopped"], "被步数截停却没标出来")
        # 最后一步是 1（0 起、跑了两步）
        self.assertEqual(events[-1]["step"], 1)

    def test_04_long_tool_output_is_clipped_for_the_ui_only(self):
        long_output = "x" * (observer._UI_CLIP + 500)
        agent = self._agent(_ScriptedModel(tool_steps=1), tools=_EchoTools(long_output))
        events = []
        run_with_events(agent, "do it", events.append)

        shown = [e for e in events if e["type"] == "observation"][0]["content"]
        self.assertLess(len(shown), len(long_output), "界面这一帧没截断")
        self.assertIn("界面已截断", shown)

        # 关键不变量：截的只是「给界面看的那一份」。
        # 存进消息历史、下一步要交给模型的必须是完整内容。
        stored = [m for m in agent.messages if isinstance(m, Observation)][0]
        self.assertEqual(stored.content, long_output, "模型会拿到被截断的内容")

    def test_05_denied_tools_still_produce_frames(self):
        # 被权限策略拒绝的工具根本不会走到 tools.execute()，
        # 界面上不能因此只剩「准备调用」而没有结果 —— 那看起来就是卡住了。
        agent = self._agent(
            _ScriptedModel(tool_steps=1),
            permission={"echo": PermissionDecision.DENY},
        )
        events = []
        run_with_events(agent, "do it", events.append)

        self.assertEqual(
            [(event["type"], event["step"]) for event in events],
            [
                ("step_start", 0), ("assistant", 0),
                ("tool_call", 0), ("observation", 0),
                ("step_start", 1), ("assistant", 1),
                ("final", 1),
            ],
        )

        observation = [e for e in events if e["type"] == "observation"][0]
        self.assertTrue(observation["blocked"], "被拒绝的工具没有被标出来")
        self.assertFalse(observation["success"])
        self.assertIn("denied by system permission", observation["content"])

    def test_06_ask_never_blocks_on_stdin(self):
        # 命令行那版用 input() 等用户按 y；网页工作线程没有 stdin，
        # 真调下去会永久阻塞。这里确认 web 端的替身已经装好，并且真的会返回。
        import agent.agent as agent_module

        from webui.agent_factory import WebApprovalController, _install_web_approval

        _install_web_approval()
        self.assertIs(agent_module.ApprovalController, WebApprovalController)

        previous = os.environ.pop("MINI_AGENT_WEB_APPROVAL", None)
        try:
            agent = self._agent(
                _ScriptedModel(tool_steps=1),
                permission={"echo": PermissionDecision.ASK},
            )
            events = []
            run_with_events(agent, "do it", events.append)
        finally:
            if previous is not None:
                os.environ["MINI_AGENT_WEB_APPROVAL"] = previous

        observation = [e for e in events if e["type"] == "observation"][0]
        self.assertTrue(observation["blocked"], "未被批准的 ASK 应该按拒绝处理")
        # 上游在「用户拒绝」分支上忘了填文案，observer 补了一句人话，
        # 界面上不能是空框。
        self.assertTrue(observation["content"].strip())

    def test_07_blocked_tool_on_the_last_step_is_still_reported(self):
        # 步数刚好用尽时没有「下一次 generate」来触发补帧，
        # 收尾那次扫描要是漏了，最后一个被拒绝的工具就会在界面上凭空消失。
        agent = self._agent(
            _ScriptedModel(tool_steps=99),
            max_steps=1,
            permission={"echo": PermissionDecision.DENY},
        )
        events = []
        result = run_with_events(agent, "one shot", events.append)

        self.assertEqual(result, "Agent stopped: maximum steps reached.")
        observation = [e for e in events if e["type"] == "observation"]
        self.assertEqual(len(observation), 1, "最后一步被拒绝的工具没有补帧")
        self.assertTrue(observation[0]["blocked"])


class BindingSafetyTest(unittest.TestCase):
    """确认服务只会绑回环地址 —— 这条是安全底线，改错了等于把 shell 挂到网上。"""

    def test_non_loopback_is_refused(self):
        for host in ("0.0.0.0", "10.168.7.27", "192.168.1.5"):
            with self.assertRaises(SystemExit, msg=f"{host} was allowed to bind"):
                build_server(host=host, port=0)

    def test_loopback_is_allowed(self):
        httpd = build_server(host="127.0.0.1", port=0)
        try:
            self.assertEqual(httpd.server_address[0], "127.0.0.1")
        finally:
            httpd.server_close()

    def test_workspace_containing_the_vault_is_refused(self):
        # 柜子要是落在 agent 的工作目录里，会跟着 git add 被提交、
        # 会被 read_file 工具读到、也会被整个目录打包发出去。
        with self.assertRaises(SystemExit):
            build_server(host="127.0.0.1", port=0, workspace=vault.VAULT_DIR)
        with self.assertRaises(SystemExit):
            build_server(host="127.0.0.1", port=0, workspace=vault.VAULT_DIR.parent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
