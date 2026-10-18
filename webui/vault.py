"""API key 的凭据柜。

照 CompanyOS 凭据柜的思路做的本地版，三条性质必须保住：

  1. **写入一次**：密钥只在「存入」那一下经过网络，之后浏览器再也拿不到它。
     列表接口只回名字和元信息，**永远不回密钥**。
  2. **系统加密**：落盘时交给操作系统按当前用户加密（Windows DPAPI / macOS 钥匙串）。
     取不到系统能力时才退化成 0600 权限的明文文件 —— 而且会把这件事明确告诉用户，
     不能让他在不知情的情况下以为密钥被加密了。
  3. **用时注入**：跑任务时请求体里只有凭据名，服务端从柜子里取出密钥注入模型。

柜子放在 `~/.mini-sxn-agent/`，**故意不放在项目目录里**：这个 agent 带 shell 工具
（subprocess.run(shell=True)），凭据文件要是落在它的工作目录里，等于把钥匙和锁
放在同一个抽屉。server.py 启动时会检查这一点，撞上直接拒绝启动。
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

def _default_vault_dir() -> Path:
    """柜子放哪。

    默认 `~/.mini-sxn-agent/`。环境变量 MINI_AGENT_VAULT_DIR 可以改掉它，
    主要给测试用（测试必须指向临时目录，绝不能往用户真实的柜子里写东西），
    顺带也让「装在 U 盘上带着走」成为可能。
    """
    override = os.environ.get("MINI_AGENT_VAULT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".mini-sxn-agent"


VAULT_DIR = _default_vault_dir()
VAULT_FILE = VAULT_DIR / "credentials.json"
SCHEMA_VERSION = 1

# 凭据名允许中文、字母、数字、下划线、短横线、点、空格；1~64 个字符。
# 收得紧一点，免得名字被拿去做路径拼接或写进日志时出岔子。
# CJK 范围写成 \u 转义而不是字面量，源码保持纯 ASCII。
_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff .\-]{0,63}$")

_MAX_SECRET_CHARS = 8192

# 柜子是多线程 HTTP 服务在读写的，落盘要串行
_LOCK = threading.RLock()


class VaultError(Exception):
    """凭据柜操作失败。

    约定：这个异常的消息**绝不允许包含密钥本身** —— 它会一路走到浏览器。
    """


# =====================================================================
# 保护器：把一段明文变成「可以安全写进 JSON 的一小段文本」
# =====================================================================

class Protector(ABC):
    key: str = ""     # 写进 JSON 的标识
    label: str = ""   # 给人看的名字

    @classmethod
    def available(cls) -> bool:
        """当前这台机器上能不能用。"""
        return False

    @abstractmethod
    def protect(self, plaintext: str) -> str:
        """加密。返回的字符串会被原样存进 JSON。"""

    @abstractmethod
    def unprotect(self, blob: str) -> str:
        """解密。blob 是当初 protect() 的返回值。"""


# ---------------------------------------------------------------------
# Windows：DPAPI（用 ctypes 调系统接口，纯标准库，不加任何依赖）
# ---------------------------------------------------------------------

_crypt32_cache: tuple | None = None


def _crypt32():
    """惰性加载 DPAPI 绑定。只有 Windows 上才调得到。"""
    global _crypt32_cache
    if _crypt32_cache is not None:
        return _crypt32_cache

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # 必须显式声明 argtypes/restype：64 位下不声明的话指针会被当成 int 截断
    for function in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        function.argtypes = [
            ctypes.POINTER(DATA_BLOB),   # pDataIn
            wintypes.LPCWSTR,            # szDataDescr
            ctypes.POINTER(DATA_BLOB),   # pOptionalEntropy
            ctypes.c_void_p,             # pvReserved
            ctypes.c_void_p,             # pPromptStruct
            wintypes.DWORD,              # dwFlags
            ctypes.POINTER(DATA_BLOB),   # pDataOut
        ]
        function.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    _crypt32_cache = (ctypes, wintypes, DATA_BLOB, crypt32, kernel32)
    return _crypt32_cache


_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi(data: bytes, unprotect: bool) -> bytes:
    ctypes, wintypes, DATA_BLOB, crypt32, kernel32 = _crypt32()

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    function = (crypt32.CryptUnprotectData if unprotect
                else crypt32.CryptProtectData)

    ok = function(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


class DpapiProtector(Protector):
    key = "dpapi"
    label = "Windows DPAPI（由操作系统按你的登录账号加密）"

    @classmethod
    def available(cls) -> bool:
        return sys.platform == "win32"

    def protect(self, plaintext: str) -> str:
        return base64.b64encode(_dpapi(plaintext.encode("utf-8"), False)).decode("ascii")

    def unprotect(self, blob: str) -> str:
        return _dpapi(base64.b64decode(blob), True).decode("utf-8")


# ---------------------------------------------------------------------
# macOS：系统钥匙串
# ---------------------------------------------------------------------

class KeychainProtector(Protector):
    key = "keychain"
    label = "macOS 钥匙串"
    SERVICE = "mini-sxn-agent"

    @classmethod
    def available(cls) -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    def protect(self, plaintext: str) -> str:
        # 钥匙串是按 (account, service) 存的，所以这里为每条凭据生成一个随机账号名，
        # 把它当作 blob 存进 JSON —— 真正的密钥在钥匙串里。
        account = secrets.token_hex(8)
        result = subprocess.run(
            ["security", "add-generic-password",
             "-a", account, "-s", self.SERVICE, "-U", "-w"],
            input=plaintext + "\n", text=True,
            capture_output=True, timeout=20,
        )
        if result.returncode != 0:
            raise OSError(
                f"keychain write failed: {(result.stderr or '').strip()[:200]}"
            )
        return account

    def unprotect(self, blob: str) -> str:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", blob, "-s", self.SERVICE, "-w"],
            text=True, capture_output=True, timeout=20,
        )
        if result.returncode != 0:
            raise OSError(
                f"keychain read failed: {(result.stderr or '').strip()[:200]}"
            )
        return result.stdout.rstrip("\n")


# ---------------------------------------------------------------------
# 兜底：0600 权限的明文文件
# ---------------------------------------------------------------------

class PlainProtector(Protector):
    key = "plain"
    label = ("明文 + 文件权限（本机没有可用的系统加密，"
             "密钥以明文存在 0600 权限的文件里）")

    @classmethod
    def available(cls) -> bool:
        return True   # 永远可用，但永远排在最后

    def protect(self, plaintext: str) -> str:
        # 不做 base64 —— 那只是让密钥看起来不显眼，属于障眼法，
        # 反而会让人误以为有保护。就是明文，界面上也如实这么写。
        return plaintext

    def unprotect(self, blob: str) -> str:
        return blob


_ALL_PROTECTORS: tuple[type[Protector], ...] = (
    DpapiProtector, KeychainProtector, PlainProtector,
)
_BY_KEY = {p.key: p for p in _ALL_PROTECTORS}


def best_protector() -> Protector:
    """当前机器上能用的最好的保护器。"""
    for candidate in _ALL_PROTECTORS:
        if candidate.available():
            return candidate()
    return PlainProtector()


def protector_by_key(key: str) -> Protector | None:
    cls = _BY_KEY.get(key)
    if cls is None or not cls.available():
        return None
    return cls()


# =====================================================================
# 柜子本身
# =====================================================================

def vault_path() -> Path:
    return VAULT_FILE


def _harden(path: Path, mode: int) -> None:
    """收紧权限。Windows 上 chmod 基本是空操作，失败也无所谓。"""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _load() -> dict[str, Any]:
    if not VAULT_FILE.exists():
        return {"version": SCHEMA_VERSION, "credentials": {}}

    try:
        data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VaultError(
            f"cannot read vault file {VAULT_FILE}: {error}"
        ) from error

    if not isinstance(data, dict) or not isinstance(data.get("credentials"), dict):
        raise VaultError(f"malformed vault file: {VAULT_FILE}")

    data.setdefault("version", SCHEMA_VERSION)
    return data


def _save(data: dict[str, Any]) -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    _harden(VAULT_DIR, 0o700)

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    # 先写临时文件再原子改名：写到一半崩了也不会把柜子弄坏
    handle, tmp_name = tempfile.mkstemp(
        dir=str(VAULT_DIR), prefix=".credentials-", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        _harden(Path(tmp_name), 0o600)
        _harden(Path(tmp_name).parent, 0o700)
        VAULT_FILE.unlink(missing_ok=True)
        os.replace(tmp_name, VAULT_FILE)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    _harden(VAULT_FILE, 0o600)


def _public_view(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """给浏览器看的元信息 —— 只有这里会出去，且**永远不含密钥**。"""
    protector = protector_by_key(str(entry.get("protector", "")))
    return {
        "name": name,
        "created_at": entry.get("created_at", ""),
        "protector": entry.get("protector", ""),
        # 柜子里存的东西，换台机器可能就读不出来了，如实标出来
        "readable_here": protector is not None,
        "protector_label": (protector.label if protector
                            else _BY_KEY.get(str(entry.get("protector")), PlainProtector).label),
    }


def list_credentials() -> list[dict[str, Any]]:
    with _LOCK:
        data = _load()
    items = [_public_view(name, entry)
             for name, entry in data["credentials"].items()]
    items.sort(key=lambda item: item.get("created_at", ""))
    return items


def current_protector_info() -> dict[str, str]:
    """当前机器上会用什么来加密新凭据 —— 界面要如实显示。"""
    protector = best_protector()
    return {
        "protector": protector.key,
        "label": protector.label,
        "encrypted": protector.key != "plain",
    }


def add_credential(name: str, api_key: str) -> dict[str, Any]:
    name = (name or "").strip()
    api_key = (api_key or "").strip()

    if not name:
        raise VaultError("credential name is required")
    if not _NAME_RE.match(name):
        raise VaultError(
            "credential name may only contain letters, digits, CJK characters, "
            "underscore, hyphen, dot and space, 1-64 chars"
        )
    if not api_key:
        raise VaultError("API key must not be empty")
    if len(api_key) > _MAX_SECRET_CHARS:
        raise VaultError(f"API key too long (limit {_MAX_SECRET_CHARS} chars)")

    protector = best_protector()

    # 加密刻意放在 _LOCK 外面做：DPAPI / 钥匙串都是进程外的系统调用，
    # 快的时候几毫秒，慢了能到几百毫秒。拿锁去等它会把整个柜子卡住，
    # 而这期间别处的读（比如列表接口）本来完全没有冲突。
    # 加锁只保护下面「读-改-写」这一小段。
    try:
        blob = protector.protect(api_key)
    except Exception as error:
        # 加密失败绝不能退化成明文存 —— 那是最糟的静默降级。
        # 宁可存不进去，也不能让人以为加了密。
        raise VaultError(
            f"encryption via {protector.key} failed, nothing was saved: {error}"
        ) from error

    with _LOCK:
        data = _load()
        existed = name in data["credentials"]
        data["credentials"][name] = {
            "protector": protector.key,
            "secret": blob,
            "created_at": (data["credentials"].get(name, {}).get("created_at")
                           or time.strftime("%Y-%m-%dT%H:%M:%S")),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save(data)
        entry = data["credentials"][name]

    result = _public_view(name, entry)
    result["replaced"] = existed
    return result


def delete_credential(name: str) -> bool:
    name = (name or "").strip()
    with _LOCK:
        data = _load()
        entry = data["credentials"].pop(name, None)
        if entry is None:
            return False
        _save(data)

    # 存在钥匙串里的那条也要一并删掉，别留下孤儿
    if entry.get("protector") == KeychainProtector.key:
        try:
            subprocess.run(
                ["security", "delete-generic-password",
                 "-a", str(entry.get("secret", "")),
                 "-s", KeychainProtector.SERVICE],
                capture_output=True, timeout=20,
            )
        except Exception:
            pass   # 删不掉也不该让整个删除失败，JSON 里那条已经没了

    return True


def get_secret(name: str) -> str:
    """取出密钥。**只能由服务端内部调用，绝不能挂到 HTTP 路由上。**"""
    name = (name or "").strip()
    with _LOCK:
        data = _load()
        entry = data["credentials"].get(name)

    if entry is None:
        raise VaultError(
            f"no credential named {name!r} in the vault: "
            f"open the vault dialog and add one first."
        )

    protector = protector_by_key(str(entry.get("protector", "")))
    if protector is None:
        key = str(entry.get("protector", ""))
        label = _BY_KEY.get(key, PlainProtector).label
        raise VaultError(
            f"credential {name!r} was stored with {label}, which is not "
            f"available on this machine and cannot decrypt it: delete it in "
            f"the vault dialog and store it again here."
        )

    try:
        return protector.unprotect(str(entry.get("secret", "")))
    except Exception as error:
        raise VaultError(
            f"cannot decrypt credential {name!r}: {error}. "
            f"(DPAPI blobs stop decrypting after a machine change or a "
            f"Windows password change -- just store the key again.)"
        ) from error


def selftest() -> dict[str, Any]:
    """在用户自己机器上验一遍「加密 → 落盘 → 解密」整条路。

    沙箱里只能验到 Linux 那条路，Windows / macOS 得由用户跑这个来确认。
    """
    protector = best_protector()
    sample = "sk-selftest-" + secrets.token_hex(12)
    result: dict[str, Any] = {
        "protector": protector.key,
        "label": protector.label,
        "encrypted": protector.key != "plain",
        "vault_path": str(VAULT_FILE),
    }

    blob = protector.protect(sample)
    result["roundtrip_ok"] = protector.unprotect(blob) == sample
    result["blob_is_not_plaintext"] = sample not in blob

    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(selftest())
