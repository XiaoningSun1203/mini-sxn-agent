#!/usr/bin/env python3
"""mini-agent 的浏览器界面入口。

用法：
    python web.py                     # 起在 http://127.0.0.1:8765/
    python web.py --port 9000
    python web.py --workspace .       # 让 agent 在项目根目录里工作
    python web.py --no-open           # 不自动打开浏览器

API key 走界面上的「凭据柜」，不是命令行参数 —— 起服务前先在网页里存一把。
柜子落在 ~/.mini-sxn-agent/（故意不放在项目目录里），由操作系统按当前用户加密。
想单独确认加密在你机器上真的生效：

    python -m webui.vault

命令行版本仍然是 python main.py，两者互不影响。
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from webui.server import HOST_DEFAULT, PORT_DEFAULT, serve


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mini-agent 浏览器界面",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "说明：agent 默认在 sandbox-workspace/ 里工作，不是项目根目录。\n"
            "因为 shell 工具是无沙箱的 shell=True，跑在项目根目录时，\n"
            "被提示注入的模型可以改写自己的源码。确实需要读项目文件时，\n"
            "用 --workspace . 显式打开。"
        ),
    )
    parser.add_argument(
        "--host", default=HOST_DEFAULT,
        help=f"监听地址（只允许回环地址，默认 {HOST_DEFAULT}）",
    )
    parser.add_argument(
        "--port", type=int, default=PORT_DEFAULT,
        help=f"监听端口（默认 {PORT_DEFAULT}）",
    )
    parser.add_argument(
        "--workspace", default=None,
        help="agent 的工作目录（默认 sandbox-workspace/）",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="不要自动打开浏览器",
    )

    args = parser.parse_args()

    serve(
        host=args.host,
        port=args.port,
        workspace=Path(args.workspace) if args.workspace else None,
        open_browser=not args.no_open,
    )


if __name__ == "__main__":
    main()
