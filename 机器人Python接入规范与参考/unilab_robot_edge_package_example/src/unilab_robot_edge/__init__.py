"""Uni-Lab 机械臂 edge Profile package 示例。"""

from importlib import resources

from .driver import RobotEdgeDriver


def profile_path() -> str:
    """返回安装包内 ProfileV1 的文件系统路径。"""

    return str(resources.files(__package__).joinpath("profile/package.yaml"))


__all__ = ["RobotEdgeDriver", "profile_path"]
