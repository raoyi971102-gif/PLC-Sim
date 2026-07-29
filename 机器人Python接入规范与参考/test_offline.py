"""协议示例离线测试；不创建Socket，不连接机器人。"""

from __future__ import annotations

import unittest

from common import MOTION_CONFIRM_TEXT, require_motion_confirmation, vector6
from dobot_v3_client import DobotV3Client
from elite_cs_client import EliteCsClient
from elite_ec_client import EliteEcClient
from siasun_duco_client import DucoResultUnknown, DucoSdkClient


class CommonTests(unittest.TestCase):
    def test_vector6(self) -> None:
        self.assertEqual(vector6(range(6), name="point"), (0, 1, 2, 3, 4, 5))
        with self.assertRaises(ValueError):
            vector6([1, 2], name="point")

    def test_motion_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            require_motion_confirmation(False, MOTION_CONFIRM_TEXT)
        with self.assertRaises(PermissionError):
            require_motion_confirmation(True, "wrong")
        require_motion_confirmation(True, MOTION_CONFIRM_TEXT)


class DobotV3Tests(unittest.TestCase):
    def test_build_commands(self) -> None:
        self.assertEqual(
            DobotV3Client.build_movj_pose(
                [400, 0, 300, 180, 0, 0],
                user=0,
                tool=1,
                speed_percent=10,
                acc_percent=20,
            ),
            "MovJ(400,0,300,180,0,0,User=0,Tool=1,SpeedJ=10,AccJ=20)",
        )
        self.assertEqual(
            DobotV3Client.build_joint_movj([0, -45, -90, 0, 90, 0]),
            "JointMovJ(0,-45,-90,0,90,0,SpeedJ=10,AccJ=10)",
        )


class EliteEcTests(unittest.TestCase):
    def test_build_move_by_joint_params(self) -> None:
        params = EliteEcClient.build_move_by_joint_params(
            [0, -45, -90, 0, 90, 0], speed=10, acc=20, dec=30
        )
        self.assertEqual(params["targetPos"], [0, -45, -90, 0, 90, 0])
        self.assertEqual(params["speed"], 10)
        self.assertEqual(params["acc"], 20)
        self.assertEqual(params["dec"], 30)
        self.assertEqual(params["cond_type"], 0)


class EliteCsTests(unittest.TestCase):
    def test_build_movej_script(self) -> None:
        script = EliteCsClient.build_movej_script(
            [0, -1, -1.5, -1, 1.57, 0],
            acceleration_rad_s2=0.3,
            velocity_rad_s=0.15,
        )
        self.assertTrue(script.startswith("def pc_move():\n"))
        self.assertIn("movej([0,-1,-1.5,-1,1.57,0]", script)
        self.assertTrue(script.endswith("end\n"))

    def test_build_movel_script(self) -> None:
        script = EliteCsClient.build_movel_script(
            [0.4, 0, 0.3, 3.14159, 0, 0],
            acceleration_m_s2=0.1,
            velocity_m_s=0.05,
        )
        self.assertIn("movel(p[0.4,0,0.3,3.14159,0,0]", script)

    def test_multi_point_rejects_arbitrary_code(self) -> None:
        with self.assertRaises(ValueError):
            EliteCsClient.build_multi_point_script(["socket_open('x', 1)"])


class FakeDucoSdk:
    instances: list["FakeDucoSdk"] = []
    motion_result = 4

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.__class__.instances.append(self)

    def open(self) -> int:
        self.calls.append(("open", ()))
        return 0

    def close(self) -> int:
        self.calls.append(("close", ()))
        return 0

    def movej2(self, *args: object) -> int:
        self.calls.append(("movej2", args))
        return self.__class__.motion_result

    def stop(self, *args: object) -> int:
        self.calls.append(("stop", args))
        return 4

    def rpc_heartbeat(self, *args: object) -> None:
        self.calls.append(("rpc_heartbeat", args))


class SiasunDucoTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeDucoSdk.instances.clear()
        FakeDucoSdk.motion_result = 4

    def test_build_movej_args_uses_radians_and_blocking(self) -> None:
        args = DucoSdkClient.build_movej_args(
            [0, -0.5, -1.0, 0, 1.57, 0],
            velocity_rad_s=0.1,
            acceleration_rad_s2=0.2,
        )
        self.assertEqual(args[0], [0, -0.5, -1.0, 0, 1.57, 0])
        self.assertEqual(args[1:], (0.1, 0.2, 0.0, True))

    def test_build_movel_args_uses_meters_and_q_near(self) -> None:
        args = DucoSdkClient.build_movel_args(
            [0.4, 0.1, 0.3, 3.14, 0, 0],
            q_near_rad=[0, -0.5, -1.0, 0, 1.57, 0],
            tool="tool0",
            wobj="wobj0",
            velocity_m_s=0.03,
            acceleration_m_s2=0.05,
        )
        self.assertEqual(args[0], [0.4, 0.1, 0.3, 3.14, 0, 0])
        self.assertEqual(args[4], [0, -0.5, -1.0, 0, 1.57, 0])
        self.assertEqual(args[5:], ("tool0", "wobj0", True))

    def test_motion_requires_finished_task_state(self) -> None:
        client = DucoSdkClient("192.0.2.10", FakeDucoSdk)
        client.connect(start_heartbeat=False)
        try:
            result = client.movej_joint(
                [0, -0.5, -1.0, 0, 1.57, 0],
                execute=True,
                confirmation=MOTION_CONFIRM_TEXT,
            )
            self.assertEqual(result, 4)
            self.assertEqual(FakeDucoSdk.instances[0].calls[1][0], "movej2")
        finally:
            client.close()

    def test_disconnect_result_is_unknown_and_not_retried(self) -> None:
        FakeDucoSdk.motion_result = -1
        client = DucoSdkClient("192.0.2.10", FakeDucoSdk)
        client.connect(start_heartbeat=False)
        try:
            with self.assertRaises(DucoResultUnknown):
                client.movej_joint(
                    [0, -0.5, -1.0, 0, 1.57, 0],
                    execute=True,
                    confirmation=MOTION_CONFIRM_TEXT,
                )
            move_calls = [
                call for call in FakeDucoSdk.instances[0].calls if call[0] == "movej2"
            ]
            self.assertEqual(len(move_calls), 1)
        finally:
            client.close()

    def test_stop_uses_independent_sdk_object(self) -> None:
        client = DucoSdkClient("192.0.2.10", FakeDucoSdk)
        client.connect(start_heartbeat=False)
        try:
            self.assertEqual(client.stop(), 4)
            self.assertEqual(len(FakeDucoSdk.instances), 2)
            self.assertIn(("stop", (True,)), FakeDucoSdk.instances[1].calls)
        finally:
            client.close()

    def test_heartbeat_uses_independent_sdk_object(self) -> None:
        client = DucoSdkClient("192.0.2.10", FakeDucoSdk)
        client.connect(start_heartbeat=True, heartbeat_timeout_ms=200)
        try:
            self.assertEqual(len(FakeDucoSdk.instances), 2)
            self.assertIn(
                ("rpc_heartbeat", (200,)),
                FakeDucoSdk.instances[1].calls,
            )
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
