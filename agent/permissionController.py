from .configLoader import PermissionConfig, PermissionDecision


class PermissionController:
   def __init__(self, config: PermissionConfig):
       self.config = config

   def check(self, tool_name: str) -> PermissionDecision:
       # return 此toolcall 在配置文件里的权限
        return self.config.permission_map.get(
            tool_name,
            PermissionDecision.DENY,
        )
    