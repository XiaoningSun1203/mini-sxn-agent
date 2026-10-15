from abc import ABC, abstractmethod
from pathlib import Path
import subprocess

class Environment(ABC):

    @abstractmethod
    def run_command(self, command: str) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def read_file(self, path_string: str) -> str:
        pass

    @abstractmethod
    def write_file(self, path_string: str, content: str) -> None:
        pass


class LocalEnvironment(Environment):
    def __init__(
            self,
            working_dir: str | Path,
            timeout: int = 30,
    ):
        self.working_dir = Path(working_dir).resolve()
        self.timeout = timeout

        if not self.working_dir.is_dir():
            raise ValueError(
                f"Working directory not exist: {self.working_dir}"
            )

    def run_command(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            shell=True,            # 通过 shell 执行这条字符串命令
            cwd=self.working_dir,  # current working directory
            capture_output=True,   # 不要只是往终端打印，把输出保存到 result 里面
            text=True,             # 让output直接以字符串形式出来，而不是其他形式
            timeout=self.timeout   # 超过值会抛出 TimeoutExpired
        )

    def read_file(self, path_string: str) -> str:
        file_path = (self.working_dir / path_string).resolve()

        # 检查路径是否越界
        file_path.relative_to(self.working_dir)

        # 真正执行read文件动作
        return file_path.read_text(encoding="utf-8")

    def write_file(self, path_string: str, content: str) -> None:
        file_path = (self.working_dir / path_string).resolve()

        file_path.relative_to(self.working_dir)

        file_path.parent.mkdir(
                parents=True,
                exist_ok=True
            )

        # 真正执行写文件动作
        file_path.write_text(
                content,
                encoding="utf-8"
            )


class DockerEnvironment(Environment):

    def __init__(
            self,
            working_dir: str | Path,
            image: str = "python:3.11-slim",
            timeout: int = 30,
    ):
        #这是宿主机上授权给容器的目录。建议使用专门的练习目录，
        #不要直接传整个项目目录，否则项目里的源码和日志也可以被修改
        self.working_dir=Path(working_dir).resolve()
        self.image=image
        self.timeout=timeout
        self.container_id: str | None = None #用于确认容器是否在运行中

    # 创建容器，并建立目录映射
    def start(self) -> None:
        if self.container_id is not None:
            raise RuntimeError("Environment is already running")

        if not self.working_dir.is_dir():
            raise ValueError(
                f"Working directory does not exist: {self.working_dir}"
            )

        result = subprocess.run(
            [
                "docker", "run",
                "--detach",
                "--rm",
                "--network", "none",
                "--memory", "256m",
                "--cpus", "1",
                "--pids-limit", "64",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,src={self.working_dir},dst=/workspace",
                "--workdir", "/workspace",
                self.image,
                "sleep", "infinity",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.container_id = result.stdout.strip()

    def run_command(self, command: str) -> subprocess.CompletedProcess:
        if self.container_id is None:
            raise RuntimeError("Environment has not been started")

        try:
            return subprocess.run(
                [
                    "docker", "exec",
                    self.container_id,
                    "sh", "-c", command,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            self.close()
            raise

    def close(self) -> None:
        if self.container_id is None:
            return

        subprocess.run(
            ["docker", "rm", "--force", self.container_id],
            capture_output=True,
            text=True,
            check=True,
        )

        self.container_id = None

    def read_file(self, path_string: str) -> str:
        raise NotImplementedError("not implement read action for Docker file")

    def write_file(self, path_string: str, content: str) -> None:
        raise NotImplementedError("not implement write action for Docker file")