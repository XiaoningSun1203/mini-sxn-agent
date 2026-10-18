import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Step N
# ├── request        # 这一轮真正送给模型的输入
# ├── response       # 模型真正返回的原始输出
# └── observations   # 这一轮所有 ToolCall 的实际执行结果

class Trajectory:

    def __init__(
        self,
        task: str,
        output_dir: str = "trajectories"
    ):
        self.task = task
        self.output_dir = Path(output_dir)

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M"
        )

        self.path = (
            self.output_dir /
            f"trajectory_{timestamp}.json"
        )

        self.data = {
            "task": task,
            "created_at": datetime.now().isoformat(),
            "steps": []
        }

        self.current_step: dict[str, Any] | None = None

        self._save()


    def record_step_number(
        self,
        step: int
    ) -> None:

        new_step = {
            "step": step,
            "model_input": None,
            "model_output": None,
            "observations": []
        }

        self.data["steps"].append(new_step)

        self.current_step = new_step

        self._save()


    def record_model_input(
        self,
        input: Any
    ) -> None:

        self._require_current_step()

        self.current_step["model_input"] = (
            self._to_serializable(input)
        )

        self._save()


    def record_model_output(
        self,
        output: Any
    ) -> None:

        self._require_current_step()

        self.current_step["model_output"] = (
            self._to_serializable(output)
        )

        self._save()


    def record_toolcall_result(
        self,
        observation: Any
    ) -> None:

        self._require_current_step()

        self.current_step["observations"].append(
            self._to_serializable(observation)
        )

        self._save()


    def _require_current_step(self) -> None:

        if self.current_step is None:
            raise RuntimeError(
                "No active trajectory step. "
                "Call record_step_number() first."
            )


    def _to_serializable(
        self,
        value: Any
    ) -> Any:

        if hasattr(value, "model_dump"):
            return value.model_dump()

        if is_dataclass(value):
            return asdict(value)

        if isinstance(value, dict):
            return {
                key: self._to_serializable(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                self._to_serializable(item)
                for item in value
            ]

        return value


    def _save(self) -> None:

        with self.path.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                self.data,
                file,
                ensure_ascii=False,
                indent=2
            )