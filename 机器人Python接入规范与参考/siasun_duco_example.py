"""新松 DUCO 指定点运动命令行示例。

默认仅打印经过校验的 SDK 参数，不导入 SDK、不连接机器人。真机模式需要同时提供
``--execute`` 与固定确认文本；上电、清警、使能仍须在独立维护流程中完成。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from common import MOTION_CONFIRM_TEXT
from siasun_duco_client import DucoSdkClient, load_sdk_factory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="新松 DUCO 指定点运动示例")
    parser.add_argument("--host", default="192.168.1.10")
    parser.add_argument(
        "--motion",
        choices=("movej", "movej-pose", "movel"),
        default="movej",
    )
    parser.add_argument(
        "--target",
        type=float,
        nargs=6,
        default=[0.0, -0.6, -1.2, 0.0, 1.57, 0.0],
        metavar=("A", "B", "C", "D", "E", "F"),
        help="movej 为六轴 rad；其余为 [x,y,z,rx,ry,rz] m/rad",
    )
    parser.add_argument(
        "--q-near",
        type=float,
        nargs=6,
        default=[0.0, -0.6, -1.2, 0.0, 1.57, 0.0],
        metavar=("J1", "J2", "J3", "J4", "J5", "J6"),
        help="末端位姿对应的已验收近似关节解，单位 rad",
    )
    parser.add_argument("--tool", default="default")
    parser.add_argument("--wobj", default="default")
    parser.add_argument(
        "--sdk-module",
        default="DucoCobotApi_py.DucoCobot",
        help="厂家 SDK 中 DucoCobot 类所在模块",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    return parser


def build_call(args: argparse.Namespace) -> tuple[str, tuple[object, ...]]:
    if args.motion == "movej":
        return "movej2", DucoSdkClient.build_movej_args(args.target)
    if args.motion == "movej-pose":
        return (
            "movej_pose2",
            DucoSdkClient.build_movej_pose_args(
                args.target,
                q_near_rad=args.q_near,
                tool=args.tool,
                wobj=args.wobj,
            ),
        )
    return (
        "movel",
        DucoSdkClient.build_movel_args(
            args.target,
            q_near_rad=args.q_near,
            tool=args.tool,
            wobj=args.wobj,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    method, call_args = build_call(args)
    print(
        json.dumps(
            {"host": args.host, "sdk_method": method, "args": call_args},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.execute:
        print("DRY-RUN：未连接机器人。完成现场审核后再使用 --execute。")
        return 0
    if args.confirm != MOTION_CONFIRM_TEXT:
        raise PermissionError(
            "真机运动确认文本不匹配；必须先完成现场安全检查，再传入 "
            f"--confirm {MOTION_CONFIRM_TEXT}"
        )

    factory = load_sdk_factory(args.sdk_module)
    client = DucoSdkClient(args.host, factory)
    client.connect(start_heartbeat=True, heartbeat_timeout_ms=1000)
    try:
        if args.motion == "movej":
            result = client.movej_joint(
                args.target,
                execute=True,
                confirmation=args.confirm,
            )
        elif args.motion == "movej-pose":
            result = client.movej_pose(
                args.target,
                q_near_rad=args.q_near,
                tool=args.tool,
                wobj=args.wobj,
                execute=True,
                confirmation=args.confirm,
            )
        else:
            result = client.movel_pose(
                args.target,
                q_near_rad=args.q_near,
                tool=args.tool,
                wobj=args.wobj,
                execute=True,
                confirmation=args.confirm,
            )
        print(f"运动正常完成：TaskState={result}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

