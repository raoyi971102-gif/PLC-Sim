# 新松/多可 DUCO Python 接入说明

适用对象：使用 DUCO Core、六轴 GCR 类协作机器人，通过上位机下发已审核关节点或
末端位姿。现场型号、Core、SDK 和安全配置必须与厂家确认。

## 1. 已核对资料

| 资料 | 得到的结论 |
|---|---|
| 官方《二次开发说明书 V3.8》 | Python/C++/C# 远程 SDK；固定端口7003；MoveJ/MoveL签名、单位、任务状态和心跳要求 |
| 官方/本地《接口手册 V1.8》 | TCP 2000命令、TCP 2001固定1468字节/10Hz状态帧及字段偏移 |
| 本地《硬件手册 V4.3》 | LAN口支持 TCP/IP 与 Modbus TCP，部分机型 LAN2 可能供示教器使用 |
| 本地《新松机器人操作步骤和注意事项》 | 现场脚本、Web页面、MoveJ/MoveL操作步骤；确认脚本使用 m/rad |
| 本地 `新松机器人.xls` | 站点 IP、Web端口、安全字节、PLC_OUT 记录，只能作为本机现场线索 |
| 本地 `hand_*.jspf` | 夹爪 RS485 初始化/开/合脚本，与机械臂点位运动协议分开管理 |

`192.168.1.10`、Web 7000/7200 和 PLC_OUT 分配属于现场资料，不能推广为所有新松
机器人默认设置。

## 2. 接口选择

| 能力 | 端口/方式 | 建议 |
|---|---|---|
| MoveJ、MoveJ-Pose、MoveL | 厂家远程 SDK，TCP 7003 | 上位机直接点位运动首选 |
| 上电、使能、任务启停、运行控制器程序 | SDK 7003 或文本 TCP 2000 | 与普通运动权限分离 |
| 周期状态 | TCP 2001，1468字节，10Hz | 监控、到位复核、审计 |
| 注入完整控制器脚本 | TCP 2000 `<start>...<end>` | 只允许审核后的固定模板，不接收业务层任意文本 |

2001 帧中的主要现场字段：

| 字节偏移 | 内容 | 单位/值 |
|---:|---|---|
| 0～27 | 7个 float，前6个为有效关节实际位置 | rad |
| 368～391 | TCP 实际位姿6个 float | m、rad |
| 660 | 全局速度 | 百分比 |
| 1448 | 操作模式 | 0手动、1自动、2远程 |
| 1449 | 机器人状态 | 4下电、5下使能、6上使能 |
| 1450 | 程序状态 | 0停止、2运行、3暂停等 |
| 1451～1453 | 安全状态、碰撞标志、碰撞轴 | 枚举/标志 |
| 1456～1459 | 机器人错误码 | uint32 |

手册没有在该表中明确声明数值字节序，因此生产解析器不得凭经验写死；应向厂家确认
当前控制器字节序，或用已知静止点与 SDK 读数进行逐字段标定后再启用。

## 3. SDK 准备

1. 从厂家或集成商取得与现场 Core 版本匹配的 Python SDK。不要用名字相似的第三方
   PyPI 包替代。
2. 厂家手册 V3.8 的远程 API 适用 Core V3.4.1 及以上；现场仍需确认补丁版本。
3. 将厂家 SDK 目录放入项目虚拟环境可导入路径。示例默认模块名为
   `DucoCobotApi_py.DucoCobot`；若交付包结构不同，用 `--sdk-module` 指定。
4. 先执行离线测试和 dry-run，再在仿真或隔离工作站以 5%～20% 全局速度验证。

## 4. 运动与单位

厂家 SDK 关键调用：

```python
DucoCobot(ip, 7003)
movej2(joints_rad, velocity_rad_s, acceleration_rad_s2, 0.0, True)
movej_pose2(pose_m_rad, velocity_rad_s, acceleration_rad_s2, 0.0,
            q_near_rad, tool, wobj, True)
movel(pose_m_rad, velocity_m_s, acceleration_m_s2, 0.0,
      q_near_rad, tool, wobj, True)
```

规则：

- 关节是 rad；末端位置是 m，姿态是 Rx/Ry/Rz rad。
- 示例的单点调用固定 `rad=0.0`，不做轨迹融合，避免“经过而未停在目标点”。
- 末端位姿必须同时保存已验收的 `q_near`，避免逆运动学落到错误分支。
- `tool` 和 `wobj` 必须显式且在控制器中已标定；示例值 `default` 仅用于演示。
- 阻塞调用返回 `4 (ST_Finished)` 才算正常完成；`5 (ST_Interrupt)`、`6
  (ST_Error)`、`7 (ST_Illegal)`、`8 (ST_ParameterMismatch)` 均不得当作到位。
- 返回 `-1` 或调用异常时，运动结果不明确，禁止自动重发。

## 5. 示例使用

只检查默认 MoveJ 参数，不连接机器人：

```powershell
python 机器人Python接入规范与参考\siasun_duco_example.py
```

检查 MoveL 参数：

```powershell
python 机器人Python接入规范与参考\siasun_duco_example.py `
  --motion movel `
  --target 0.40 0.10 0.30 3.14 0 0 `
  --q-near 0 -0.5 -1.0 0 1.57 0 `
  --tool default --wobj default
```

真机执行必须同时增加：

```text
--execute --confirm I_HAVE_VERIFIED_THE_ROBOT_CELL
```

真机执行前还必须由独立维护流程完成模式、上电、清警和使能，不由示例自动完成。

## 6. 心跳、停止和并发

- `rpc_heartbeat(ms)` 必须由独立线程中的独立 `DucoCobot` 对象周期调用。示例客户端
  默认建立心跳，延时 1000 ms。
- 厂家手册明确要求多线程使用不同的 `DucoCobot` 对象。
- 普通运动调用是阻塞的；`stop` 也应通过独立对象调用。示例的 `stop()` 已采用独立
  连接。
- 一个机器人只能有一个上位机运动所有者。心跳失败、网络中断或进程重启后，先现场
  对账，再决定是否接管。

## 7. 上线验收

1. 记录机器人型号、序列号、Core/SDK版本、IP、TCP、负载、Tool、Wobj。
2. 验证远程模式值为2、机器人上使能值为6、安全控制器为正常运行。
3. 空载、低速执行单个短距离 MoveJ；核对返回状态和实际关节。
4. 以已验收 q_near 执行 MoveJ-Pose；确认逆解分支。
5. 对 MoveL 全路径做碰撞、奇异、关节限位和工装干涉检查。
6. 拔网测试心跳停机行为；验证断线后系统进入结果不明确且不自动重发。
7. 验证独立 stop 通道、急停、安全门和保护停止。
8. 完成点位版本、操作人、请求ID、开始/完成状态和最终实际位置审计。

