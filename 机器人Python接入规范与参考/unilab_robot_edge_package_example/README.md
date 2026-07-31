# Uni-Lab 机械臂 Edge 设备包示例

这是一份对齐当前 Uni-Lab `ProfileV1 + DeviceSpecV2 + unilabos.drivers`
形态的可运行参考包。它把本目录已有的越疆、艾利特、新松 Python 接入经验，与
Uni-Lab 机械臂规范中的幂等、许可、点位、停止、完成见证和 `UNKNOWN` 语义合并到
同一个 edge package。

本示例默认只连接 `SimRobotConnection`。它不会自动连接真机，也不会隐含上电、清警、
使能、释放抱闸或急停复位。

## 包含内容

```text
pyproject.toml                   安装信息和 unilabos.drivers entry point
src/unilab_robot_edge/
├── profile/
│   ├── package.yaml             ProfileV1，绑定 driver 和现场 connection
│   └── device.yaml              DeviceSpecV2，声明动作、资源、恢复语义
├── driver.py                    Uni-Lab Runtime Driver
├── journal.py                   SQLite 命令日志和幂等记录
├── catalog.py                   Site/Interaction Profile/Point Set 解析
├── connection.py                厂家连接 seam
├── contracts.py                 状态、结果和异常
├── sim_connection.py            离线测试连接
└── config/
    ├── interactions.example.yaml
    └── points.example.yaml
examples/edge_config.py          Edge BasicConfig 配置示例
tests/                           不连接机器人运行的测试
docs/机械臂设备包规范.md            完整规范
```

## 外部 Interface

生产动作只有：

- `robot.pick(resource, command_id, source_boot_id, monotonic_sequence, parameters)`
- `robot.place(resource, target_mount_resource, target_site, command_id, ...)`

`pick` 从 Resource 的父载体和当前 Site 推导来源位置；`place` 只要求调用方表达尚不能
从 Resource 推导的目标载体和目标 Site。`skill_id`、程序版本、点位版本、工具和负载
由 Interaction Profile 解析，不由 Workflow 作者填写。

调试动作是另一套 Interface：

- `begin_commissioning`
- `move_to_point`
- `end_commissioning`

调试运动只接受 `point_ref`，不接受任意 pose、joint 或脚本。两套 Interface 都经过
同一个运动许可、控制器状态和命令日志门禁。

物料归属仍由 OS 的 `host.transfer_resource` 提交。`pick/place` 只执行物理动作，
不得直接修改 ResourceTreeSet。

## 离线运行

```bash
cd unilab_robot_edge_package_example
python3 -m pytest
```

用 Uni-Lab 的 Python 3.11 环境验证 Profile：

```bash
UNILAB_PY=/home/changjunhan/.micromamba/envs/unilab/bin/python
PYTHONPATH=src:/home/changjunhan/Uni-Lab-Core/Uni-Lab-OS \
  "$UNILAB_PY" -m pytest
```

实际 edge 部署前先安装 entry point：

```bash
"$UNILAB_PY" -m pip install -e .
```

然后在 Uni-Lab 配置中用 `unilab_robot_edge.profile_path()` 加入 Profile，并注入一个
实现 `RobotConnection` Interface 的现场连接对象。可参考
[examples/edge_config.py](examples/edge_config.py)。

## 适配已有厂家客户端

本目录现有客户端可作为 Robot Connection 的底层依赖，但不能直接暴露给 Workflow：

- 越疆 V4：用响应 CommandId、30004 `CurrentCommandId` 和 RobotMode 形成完成见证。
- 越疆 V3：`Sync()` 后仍需实际位置/状态复核。
- 艾利特 CS：30001 发送脚本不能作为完成见证，需 RTSI、寄存器握手或 SDK 回调。
- 艾利特 EC：JSON-RPC 请求 ID 只证明响应匹配，仍需机器人/轨迹终态。
- 新松 DUCO：阻塞零融合动作返回 `ST_Finished(4)` 后再读状态；`-1` 或通信异常进入
  `UNKNOWN`。

厂家单位换算、TCP 拆包、心跳和停止通道都留在 Robot Connection 内；Profile、
Workflow 和 Resource/Site 模型不感知厂家单位。

## 重要限制

这是工程参考，不是安全认证件。普通 Python、ROS2、OS Scheduler 和本设备包都不构成
安全功能。急停、安全门、安全速度、人员检测和安全区必须由经过风险评估与验证的安全
系统承担。

当前 Uni-Lab Profile runtime 还不会自动把 `run_id/node_id` 注入 driver，所以示例把
`command_id/source_boot_id/monotonic_sequence` 保留为运行态必填字段。它们应由
OS Runtime/编译层生成，而不是让实验 Workflow 作者手工填写。
