# 协作机器人 Python 接入开发规范

版本：1.1  
适用范围：越疆 Dobot CR/Nova V3、CRA/E6/CRAF/NovaLite V4，艾利特 EC/EA、
CS/ES，新松/多可 DUCO GCR 六轴协作机器人  
目标：上位机通过 TCP/SDK 将机器人安全、可追踪地运动到已审核目标点

> 本目录是独立协议接入参考，不会被 `eit_ptlc` 生产运行时自动导入。当前工程的越疆
> V4 生产路径仍是 `eit_ptlc.driver.dobot_tcp_driver.DobotTcpRobotTransport`。

## 0. Python 环境

需要 Python 3.10 或更高版本。基础 TCP/Socket 示例只使用标准库：

```powershell
python -m pip install -r requirements.txt
```

当前 `requirements.txt` 因此不包含公共 PyPI 包。越疆 V4 工程示例需要本地
`eit_ptlc` 项目，新松真实连接需要厂家提供的 `DucoCobotApi_py` SDK；两者的
安装说明已写在 requirements 文件注释和本文第 10 节中。

## 1. 协议选型

| 厂商与控制器 | Python 接入方式 | 控制端口 | 状态端口 | 推荐用途 |
|---|---|---:|---:|---|
| 越疆 CR/Nova V3 | TCP 文本协议或官方 V3 SDK | 29999、30003 | 30004 | 老控制器单点/队列运动 |
| 越疆 CRA/E6 等 V4 | 官方 V4 SDK或项目内 V4 驱动 | 29999 | 30004 | 新项目首选 |
| 艾利特 EC/EA | JSON-RPC Socket | 8055 | 8056 | EC/EA 单点、JBI、状态与IO |
| 艾利特 CS/ES | Dashboard + 脚本/SDK | 29999、30001 | 30001、30004 | CS/ES 单点、脚本、RTSI |
| 新松/多可 DUCO | 厂家远程控制 SDK | 7003 | SDK查询；原始状态流2001 | 六轴单点 MoveJ/MoveL 首选 |
| 新松/多可 DUCO | TCP/IP文本接口 | 2000 | 2001 | 程序/脚本控制、10Hz状态监视 |

必须以示教器上的系列、控制器软件版本和对应版本手册为准。型号相同但控制器大版本
不同，命令格式也可能不同。

官方资料：

- 越疆 V3：
  <https://github.com/Dobot-Arm/TCP-IP-Protocol-6AXis-V3>
- 越疆 V4：
  <https://github.com/Dobot-Arm/TCP-IP-Python-V4>
- 艾利特下载中心：
  <https://www.elibot.com/service/technical_13>
- 艾利特 EC 8055：
  <https://www.elibot.com/service/articles/list/204>
- 艾利特 CS/ES 端口：
  <https://docs.elibot.cn/cs/88fdd/9bd1f/12e52/b2cb3>
- 艾利特 CS/ES 30001：
  <https://docs.elibot.cn/cs/88fdd/9bd1f/12e52/100f6>
- 艾利特 CS SDK：
  <https://github.com/Elite-Robots/Elite_Robots_CS_SDK>
- 新松 DUCO 二次开发说明书：
  <https://docs.ducorobots.cn/zh/develop/3.8/>
- 新松 DUCO TCP及IP接口：
  <https://docs.ducorobots.cn/zh/interface/latest/01TCP及IP接口/>
- 新松 DUCO 下载中心：
  <https://docs.ducorobots.cn/zh/download.html>

上述资料的本地离线副本和 SHA-256 校验值见
[`官方参考资料/README.md`](官方参考资料/README.md)。

### 1.1 新松 DUCO 接口结论

1. 直接把机器人运动到指定关节或末端点，优先使用厂家 Python SDK 的
   `DucoCobot(ip, 7003)`、`movej2`、`movej_pose2` 和 `movel`。
2. 厂家 SDK 不随本示例分发。必须向厂家/集成商取得与现场 Core 版本匹配的 SDK；
   V3.8 二次开发手册注明适用 Core V3.4.1 及以上，但仍须逐一核对现场版本。
3. SDK 多线程时每个线程使用不同的 `DucoCobot` 对象。心跳 `rpc_heartbeat(ms)`
   也必须在独立线程、独立对象中周期调用；否则远程连接断开后控制器不会主动生成
   `stop`。
4. 7003 阻塞运动返回任务状态。零融合单点只有 `4 (ST_Finished)` 表示正常完成；
   `-1` 表示通信断开，此时结果不明确，禁止自动重发。
5. 2000 端口支持 `run`、`<start>...<end>` 脚本、`stop`、`state` 等；2001
   每 100 ms 推送固定 1468 字节状态帧。原始状态帧的关节和 TCP 单位分别为 rad、
   m/rad。上位机单点运动不建议拼接任意脚本字符串。
6. 本地现场文档中的 `192.168.1.10`、Web 7000/7200 是某个站点记录，不是厂家
   通用默认值。实际 IP、网口和远程模式必须从现场控制器确认。

更详细的落地步骤见 [`新松DUCO接入说明.md`](新松DUCO接入说明.md)。

## 2. 分层边界

生产程序应采用以下结构：

```text
业务流程/调度
    ↓ 只传 point_id、motion、profile
RobotService
    ↓ 点位许可、安全互锁、单动作锁、审计
RobotAdapter
    ↓ 厂商协议、状态解析、到位确认
TCP/SDK
    ↓
机器人控制器
```

业务层禁止直接获得以下能力：

- 任意字符串命令；
- 任意关节角/笛卡尔位姿；
- 任意 DO 写入；
- 自动清警、自动使能；
- 断线后的自动运动重发。

## 3. 统一接口约定

建议适配器至少实现：

```python
class RobotAdapter:
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def query(self) -> RobotFeedback: ...
    def move_j(self, point: RobotPoint, profile: MotionProfile) -> RobotFeedback: ...
    def move_l(self, point: RobotPoint, profile: MotionProfile) -> RobotFeedback: ...
    def stop(self) -> None: ...
```

统一异常：

| 异常 | 含义 | 是否允许自动重试 |
|---|---|---|
| `RobotConnectionError` | 建连失败或连接断开 | 仅只读重连允许 |
| `RobotProtocolError` | 报文格式、版本或字段异常 | 否 |
| `RobotRejectedError` | 控制器明确拒绝命令 | 否 |
| `RobotMotionError` | 运动、碰撞、不可达、奇异等失败 | 否 |
| `RobotResultUnknown` | 已发送运动，但无法确认最终结果 | 绝对禁止 |

运动是非幂等物理动作。Socket 超时只说明“没有收到结果”，不等于“机器人没有执行”，
所以不得因超时自动再次发送同一运动。

## 4. 点位数据规范

禁止在业务代码中散落六维数组。点位应有稳定 ID，并保存以下数据：

```json
{
  "point_id": "robot-main.home",
  "vendor_name": "P1",
  "pose": [400.0, 0.0, 300.0, 180.0, 0.0, 0.0],
  "joint": [0.0, -45.0, -90.0, 0.0, 90.0, 0.0],
  "pose_unit": "mm_deg",
  "joint_unit": "deg",
  "user_frame": 0,
  "tool_frame": 1,
  "allowed_motion": ["move_j"],
  "validation_status": "validated",
  "version": "2026-07-29"
}
```

规则：

1. `pose` 与 `joint` 都保留。MoveJ 优先使用已验收关节解，避免同一位姿产生不同逆解。
2. `move_l` 使用笛卡尔位姿，并单独验收直线路径。
3. `validation_status != validated` 的点不允许运动。
4. 每个点声明允许的运动类型、User/Tool、速度档位和来源版本。
5. 单位必须显式：
   - 越疆 V3/V4常见为 `mm + deg`；
   - 艾利特 CS 脚本常见为 `m + rad`；
   - 艾利特 EC 的关节运动目标常见为 `deg`；
   - 新松 DUCO 示教器界面常显示 `mm + deg`，但远程 SDK/脚本必须使用
     `m + rad`，关节目标使用 `rad`。
6. 单位换算只允许在适配器边界发生一次，禁止业务层自行猜测。

## 5. 状态机与完成判据

统一动作生命周期：

```text
SUBMITTED → VALIDATED → ACCEPTED → RUNNING
                                  ├→ DONE
                                  ├→ REJECTED
                                  ├→ ERROR
                                  ├→ CANCELLED
                                  └→ RESULT_UNKNOWN
```

“控制器接受命令”不等于“已到位”。生产程序必须使用控制器提供的命令 ID、运行状态、
轨迹状态或实际位置反馈确认完成。

- 越疆 V4：应核对命令响应中的 CommandId、30004 `CurrentCommandId` 和 RobotMode。
- 越疆 V3：可使用 `Sync()`，同时建议核对实际位姿/关节反馈。
- 艾利特 EC：按对应版本手册查询 `getRobotState`/轨迹状态并核对实际位置。
- 艾利特 CS：30001发送脚本本身不提供可靠业务完成结果；生产程序应结合 RTSI、
  寄存器握手或官方 SDK 的结果回调。
- 新松 DUCO：阻塞零融合单点必须返回 `ST_Finished(4)`，再用
  `get_robot_state`/实际位置或 2001 状态流复核。状态向量依次为机器人、程序、
  安全控制器、操作模式；远程操作模式值为 `2`。

## 6. 连接与并发规范

1. 一个机器人只允许一个上位机运动控制者。
2. 每个机器人实例使用单动作互斥锁，MovJ/MovL 不得并发。
3. `stop`/急停通道可以绕过普通动作锁，但仍需保护 Socket 收发完整性。
4. TCP 按字节流处理，必须考虑半包、粘包和多帧缓存。
5. 建议超时：
   - 建连：3～5秒；
   - 单次命令应答：3～5秒；
   - 物理运动：按工作站路径单独配置，不与命令应答超时混用。
6. 断线重连后先查询状态并对账；机器人仍在运动、暂停或命令 ID 已改变时拒绝接管。

## 7. 安全规范

上线前必须满足：

- 机器人和上位机处于独立受控网段；
- 示教器已设置正确的远程/TCP控制模式；
- 急停、保护停止、安全门和碰撞检测有效；
- TCP、负载、重心、安装方向、User/Tool坐标已确认；
- 全局速度比先限制为 5%～20%；
- 第一次只跑空载、单点、短距离；
- 现场人员能立即触发急停；
- 运动点和路径完成风险评估；
- 不用 `ClearError` 掩盖碰撞、限位或安全配置问题。

使能、清警、释放抱闸必须由独立维护动作触发，不能作为普通 `move_*` 的隐含步骤。

## 8. 审计日志

每次运动至少记录：

- `request_id`、时间、调用者；
- 机器人型号、IP、控制器版本；
- `point_id`、点位版本、User/Tool；
- 运动类型与速度参数；
- 下发前实际位置和状态；
- 厂商命令 ID/请求 ID；
- 接受、开始、完成时间；
- 最终实际位置；
- 错误码、原始响应摘要；
- 是否人工停止、急停或结果不明确。

日志中不要记录账户密码、Token或厂商维护口令。

## 9. 示例文件

| 文件 | 用途 |
|---|---|
| `common.py` | 六维数据校验、显式运动确认 |
| `dobot_v3_client.py` | 越疆 V3 原始 TCP 示例 |
| `dobot_v4_project_example.py` | 复用本工程越疆 V4 生产驱动 |
| `elite_ec_client.py` | 艾利特 EC/EA 8055 JSON-RPC |
| `elite_cs_client.py` | 艾利特 CS/ES 29999 + 30001 |
| `siasun_duco_client.py` | 新松 DUCO 厂家 SDK 7003 安全封装 |
| `siasun_duco_example.py` | 新松 MoveJ/MoveJ-Pose/MoveL 命令行示例 |
| `test_offline.py` | 不连接机器人，只验证报文生成 |

所有示例默认不运动。只有调用 `require_motion_confirmation(True, MOTION_CONFIRM_TEXT)`
后才会下发运动。

离线检查：

```powershell
python -m py_compile 机器人Python接入规范与参考\*.py
python 机器人Python接入规范与参考\test_offline.py
python 机器人Python接入规范与参考\siasun_duco_example.py
```

## 10. 本工程落地建议

当前项目已经具备越疆 V4 的生产级适配：

- `eit_ptlc/driver/dobot_tcp_driver.py`：29999/30004、半包粘包、命令 ID、
  状态对账、断线结果不明确处理；
- `eit_ptlc/controller/robot_controller.py`：命名点许可、Home锚点、User/Tool；
- `eit_ptlc/action/executor.py`：动作状态归一和模式门控；
- `eit_ptlc/config/points/robot/`：机器人点位单一事实源。

如果最终采购艾利特或新松，不应让业务层直接改成调用本目录示例，而应新增
`EliteEcRobotTransport`、`EliteCsRobotTransport` 或 `DucoRobotTransport`
实现现有 `RobotTransport`，继续复用
`RobotController → ActionExecutor → mini-VM` 主线。

`dobot_v4_project_example.py` 复用了 `pTLC_platformUI\pTLC_platformUI` 中的
`eit_ptlc` 包。运行该示例前，应先在对应项目根目录执行开发安装：

```powershell
pip install -e pTLC_platformUI\pTLC_platformUI
```

## 11. Uni-Lab Edge 设备包示例

本目录新增了
[`unilab_robot_edge_package_example`](unilab_robot_edge_package_example/README.md)，
用于把本规范和厂家客户端收敛成当前 Uni-Lab 的
`ProfileV1 + DeviceSpecV2 + unilabos.drivers` 设备包形态。

该示例包含：

- 可安装的 driver entry point；
- `package.yaml`、`device.yaml` 和 edge 配置示例；
- Resource/Site 到 Interaction Profile 的生产动作解析；
- Commissioning Session 与命名点调试动作；
- SQLite 幂等日志、Motion Permit、受控停止和 `UNKNOWN` 对账；
- 不连接真机的模拟 connection 和测试；
- [完整机械臂设备包规范](unilab_robot_edge_package_example/docs/机械臂设备包规范.md)。
