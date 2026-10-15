from enum import Enum
from .configLoader import PermissionConfig

class PermissionDecision(Enum):
    ALLOW = "allow"  # 自动允许执行
    ASK = "ask"      # 需要经过 ApprovalController
    DENY = "deny"    # 直接拒绝执行


class PermissionController:
   def __init__(self, config: PermissionConfig):
       self.config = config

   def check(self, tool_name: str) -> PermissionDecision:
       # return 此toolcall 在配置文件里的权限
        return self.config.permission_map.get(
            tool_name,
            PermissionDecision.DENY,
        )
    