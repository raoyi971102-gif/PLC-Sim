# Uni-Lab Robot Edge Package Context

本上下文描述 Uni-Lab edge 设备包如何表达机械臂、物料位置、生产运动与调试运动。

## Language

**Robot Edge Package**:
一个可安装、可发现的 Uni-Lab Profile package，声明机械臂动作契约并把动作交给一个现场 Robot Connection。
_Avoid_: 厂家 SDK 示例集合，安全控制器

**Robot Connection**:
由 edge 部署注入、负责一个具体厂家/控制器通信与完成见证的连接对象。
_Avoid_: 业务工作流，任意 Socket 透传

**Site**:
Warehouse、Rack 或工位载体上可持有物料或另一载体的稳定位置。
_Avoid_: 机器人点位，TCP 位姿

**Interaction Profile**:
一个 Site 在特定操作、工具、负载和标定上下文下绑定的已验证机器人技能。
_Avoid_: 原始点号，换工具轨迹

**Site Action**:
以 Resource 和目标 Site 表达的生产级 `pick` 或 `place` 动作，由设备包解析 Interaction Profile。
_Avoid_: 任意关节运动，库存记账动作

**Point Set**:
绑定机器人身份、工具、坐标系、标定和审批版本的一组已部署点位。
_Avoid_: 业务 Warehouse，未版本化六维数组

**Point Motion**:
Commissioning Session 内对一个已部署点位执行的受控调试运动。
_Avoid_: Site Action，物料转移

**Commissioning Session**:
在控制器 boot、Point Set、标定、工具、负载、外部轴和速度上限不变时有效的短期调试上下文。
_Avoid_: 生产工作流，绕过运动许可的维护模式

**Motion Permit**:
Cell Controller 或机器人控制器运动许可的只读、新鲜度受限镜像，只用于普通软件 fail-closed 门禁。
_Avoid_: 安全功能，Scheduler 锁

**Completion Witness**:
独立于 Socket 写成功的命令级终态证据，能够关联控制器命令、最终状态和观测时间。
_Avoid_: 发送成功，ROS Action accepted

**Result Unknown**:
命令可能已经下发但无法确认终态的持久状态；必须对账，禁止重新发送运动。
_Avoid_: 超时失败，可重试错误
