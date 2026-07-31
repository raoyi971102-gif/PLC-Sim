"""Uni-Lab edge Profile 配置示例；默认使用离线 SimRobotConnection。"""

from typing import ClassVar

from unilab_robot_edge import profile_path
from unilab_robot_edge.sim_connection import SimRobotConnection


class BasicConfig:
    runtime_profile_paths: ClassVar[list[str]] = [profile_path()]
    runtime_connections: ClassVar[dict[str, object]] = {
        "ROBOT_CONNECTION": SimRobotConnection(),
    }
