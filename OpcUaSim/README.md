# OpcUaSim

一个由 CSV 变量表驱动的 OPC UA 仿真环境，包含：

- OPC UA Server：按 CSV 创建节点，默认监听 `opc.tcp://0.0.0.0:4855/xuse_sim/`
- Handshake Agent：仿真 Type A/B/C/D 四类 PLC 握手
- Web GUI：管理变量提取、Server、Agent 以及可选的 InoProShop MCP 功能
- MCP CLI：打开/编辑/编译 InoProShop 工程并从 GVL 提取 CSV

核心 OPC UA 仿真不依赖其他仓库。InoProShop 工程操作属于可选功能，需要使用者
自行安装 InoProShop、Node.js，并提供有权使用的 MCP bundle。

## 快速开始

### 原生安装包（无需 Python）

从 [GitHub Releases](https://github.com/raoyi971102-gif/PLC-Sim/releases) 下载与系统匹配的安装包：

- Windows 10/11 x64：`OpcUaSim-Setup-Windows-x64-*.exe`；
- Apple Silicon Mac（M 系列芯片）：`OpcUaSim-macOS-arm64-*.dmg`；
- Intel Mac：`OpcUaSim-macOS-x64-*.dmg`。

Windows 安装后可从开始菜单启动。macOS 打开 DMG 后把 `OpcUaSim.app` 拖入
“Applications”目录即可。应用会自动打开 Web GUI，不需要另外安装 Python 或依赖。

当前安装包没有商业代码签名证书。Windows 可能显示 SmartScreen 提示；macOS
使用临时签名但尚未经过 Apple 公证，首次启动请按住 Control 点击应用，选择“打开”。

### pip 安装

需要 Python 3.10 或更高版本。从已克隆的仓库安装：

```bash
python -m pip install ./OpcUaSim
```

也可以直接从 GitHub 安装（私有仓库需要本机 Git 已授权）：

```bash
python -m pip install \
  "git+https://github.com/raoyi971102-gif/PLC-Sim.git#subdirectory=OpcUaSim"
```

每个 `opcua-sim-v*` GitHub Release 也会附带经过校验的 wheel 和源码包；下载
`unilab_opcua_sim-*-py3-none-any.whl` 后可直接执行：

```bash
python -m pip install ./unilab_opcua_sim-*-py3-none-any.whl
```

安装后使用统一命令；不传子命令时默认启动 Web GUI：

```bash
opcua-sim
opcua-sim gui --host 127.0.0.1 --port 18765
opcua-sim server --host 127.0.0.1 --port 4855
opcua-sim handshake --url opc.tcp://127.0.0.1:4855/xuse_sim/
opcua-sim szlab-handshake --workflow szlab_s09_pipetting_workflow
```

如果系统没有将 Python Scripts 目录加入 `PATH`，可以等价运行：

```bash
python -m opcua_sim
python -m opcua_sim server --help
```

wheel 中的演示 CSV、YAML 配置和 GUI 静态文件为只读包资源。上传的 CSV、
提取结果和运行状态会写入用户数据目录：

- macOS：`~/Library/Application Support/OpcUaSim`；
- Windows：`%LOCALAPPDATA%\OpcUaSim`；
- Linux：`$XDG_DATA_HOME/opcua-sim` 或 `~/.local/share/opcua-sim`。

可用 `OPCUASIM_DATA_DIR` 统一覆盖上述目录。在源码仓库中运行时仍保留原有
`OpcUaSim/data/` 路径，不影响 `.command` 和 `.bat` 启动器。

### macOS 一键启动

需要 Python 3.10 或更高版本。进入 `OpcUaSim` 目录后，在 Finder 中双击：

- `start_gui.command`：推荐入口，启动 Web GUI 并自动打开浏览器；
- `start_all.command`：同时启动 OPC UA Server 和默认 XUSE Handshake Agent。

首次启动会自动创建 `.venv` 并安装 `requirements.txt`，后续启动会复用环境；
依赖文件变化时会自动同步，不需要手动运行 Python 文件。

如果 macOS 首次阻止打开，按住 Control 点击 `.command` 文件，选择“打开”，再确认一次。
也可以从终端运行：

```bash
./start_gui.command
# 或同时启动 Server + Agent
./start_all.command
```

加载自己的变量表：

```bash
./start_all.command "/path/to/xuse_variables.csv"
```

macOS 支持 OPC UA Server、Handshake Agent 和 Web GUI。InoProShop 本体仅支持
Windows，因此 GUI 中依赖 InoProShop 的工程编辑、编译和下载功能在 macOS 上不可用。

### Windows 一键安装

需要 Python 3.10 或更高版本：

```bat
setup_venv.bat
start_all.bat
```

`setup_venv.bat` 会在当前目录创建 `.venv` 并安装 `requirements.txt`。
`start_all.bat` 会分别启动 Server 和 Handshake Agent。

运行端到端验证：

```bat
.venv\Scripts\python.exe tests\integration\xuse_handshake_check.py
```

测试覆盖：

- Type D：初始化
- Type C：参数下发
- Type A：编码触发与位置代码回写
- Type B：请求—加工—完成

### 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

另开一个终端：

```powershell
.\.venv\Scripts\python.exe handshake_agent.py
```

## 默认变量表

仓库包含 [`data/demo_variables.csv`](data/demo_variables.csv)，覆盖四类握手所需的
16 个公开演示节点。克隆仓库后不传任何参数即可运行。

真实设备的完整变量表通常属于具体 PLC/驱动工程，不在本仓库分发。使用自己的
CSV 有三种方式：

```powershell
# 临时指定
.\start_all.bat "C:\project\xuse_variables.csv"

# Python CLI
python server.py --csv "C:\project\xuse_variables.csv"
python handshake_agent.py --csv "C:\project\xuse_variables.csv"

# 环境变量（Server 和 Agent 都会读取）
$env:OPCUASIM_CSV = "C:\project\xuse_variables.csv"
.\start_all.bat
```

Server 与 Agent 必须加载同一份 CSV。

CSV 至少包含以下列：

```csv
Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId
工站初始化,Station_Initialize,VARIABLE,BOOLEAN,Chinese,ns=4;s=uniab|工站初始化
```

支持的数据类型为 `BOOLEAN`、`INT16`、`INT32`、`FLOAT`、`STRING`。

## 常用启动入口

| macOS | Windows | 用途 |
|---|---|---|
| `start.command` | `start.bat` | 只启动 OPC UA Server |
| `start_handshake.command` | `start_handshake.bat` | 只启动默认 XUSE Handshake Agent |
| `start_szlab_handshake.command` | `start_szlab_handshake.bat` | 只启动 SZLab Poly Studio 握手仿真 |
| `start_all.command` | `start_all.bat` | 同时启动 Server 和默认 XUSE Agent |
| `start_gui.command` | `start_gui.bat` | 启动 Web GUI，默认地址 `http://127.0.0.1:18765/` |
| — | `pick.bat` | 通过文件选择器加载一份或多份 CSV |
| 启动器自动完成 | `setup_venv.bat` | 创建项目虚拟环境并安装依赖 |

macOS 启动器按以下顺序选择 Python：

1. 项目内 `.venv`
2. `PYTHON` 环境变量
3. `PATH` 中的 Python 3.10 或更高版本

`start_all.command` 默认使用端口 `4855`。如需改端口，可在终端设置
`OPCUASIM_PORT`；Server 与 Agent 会自动使用相同端口：

```bash
OPCUASIM_PORT=4860 ./start_all.command
```

GUI 诊断脚本位于 `tools/diagnose.ps1`。

Windows 启动脚本按以下顺序选择 Python：

1. 项目内 `.venv`
2. `PYTHON` 环境变量
3. 已知的 Miniforge 路径
4. `PATH` 中的非 WindowsApps Python

## 命令行参数

Server：

```powershell
python server.py --host 0.0.0.0 --port 4855 `
  --csv data/demo_variables.csv `
  --ns-uri urn:xuse:sim --ns-index 4
```

可重复传入 `--csv` 合并多份变量表。`--no-occupancy-true` 可禁止将名称以
“占位”或“空闲”结尾的节点初始化为 `TRUE`。

Handshake Agent：

```powershell
python handshake_agent.py `
  --url opc.tcp://127.0.0.1:4855/xuse_sim/ `
  --csv data/demo_variables.csv `
  --config config/xuse_handshake.yaml
```

`config/xuse_handshake.yaml` 可覆盖握手延时。

## SZLab Poly Studio 握手仿真

`szlab_handshake_agent.py` 根据 Uni-Lab-OS 的 SZLab 设备驱动契约模拟 PLC
侧响应，覆盖：

- Robot 任务 `7/8/11/12/13/15/16` 的允许写入、任务完成和回环复位；
- Robot 在 S04、S06、S071、S072 放取料时对应的物料在位传感器联动；
- S04 六个磁搅位置（工艺 `1-3`）；
- S05 拍照完成与 OK 结果；
- S06 加液（工艺 `1-3`，参数写入标志复位后重新允许加工）；
- S07 固体加样（工艺 `1-3`）；
- S08 开关盖（工艺 `1-6`，等待瓶盖暂存位一并复位）；
- S09 移液（工艺 `1-10`，即使未捕获到短暂的参数完成脉冲也能接单）。

先启动包含 SZLab 节点的 OPC UA Server，再运行：

```bat
start_szlab_handshake.bat
```

也可以指定其他 endpoint：

```bat
start_szlab_handshake.bat opc.tcp://127.0.0.1:4855/xuse_sim/
```

GUI 的“握手代理”中可将“仿真协议”切换为 `SZLab Poly Studio`。
切换后会显示“工作流调试参数”，可从 Uni-Lab-SZLab 当前 13 个工作流中
选择一个定向调试。代理只解析、初始化和轮询该工作流实际使用的节点；选择
“全部工作流”时保持原有的全工位兼容模式。

命令行也支持同样的选择和参数覆盖：

```bash
python szlab_handshake_agent.py \
  --workflow s04_robot_stirring_workflow \
  --position 2 \
  --pump 1 \
  --delay-ms 250 \
  --poll-ms 40 \
  --s09-remaining-volume-ml 100
```

| 参数 | 用途 |
|---|---|
| `--workflow` | `all` 或 13 个 SZLab 工作流 ID 之一 |
| `--position` | S04 调试位置，范围 `1-6` |
| `--pump` | S06 储液瓶，`1`、`2` 或 `3`（双泵） |
| `--delay-ms` | 统一覆盖无设备时间参数的动作延时；S04 磁搅优先使用本次动作的磁搅时间 |
| `--poll-ms` | OPC UA 轮询间隔，最小 5 ms |
| `--s09-remaining-volume-ml` | S09 1-5 号液体瓶的初始余量 |

仿真驱动优先使用实机格式
`ns=4;s=上位机通讯|<变量名>`，找不到时按 BrowseName 递归匹配。
缺失某个工位节点时默认只跳过该工位；命令行增加 `--strict` 可改为立即报错。

`szlab_stack_s05_s06_workflow` 同时兼容原有的 S05/S06 联调流程和
`szlab-parallel-robot-lock-rev-1` revision。后者会并行执行 S05 拍照与 S06
加液，随后两条分支依次申请同一个 Robot 任务 25（S08 倒料类型 1、2），汇合后
执行 S04 搅拌。握手器在第一条 Robot 握手复位前不会接收第二条，从而模拟设备锁。

延时、工作流和 PLC 侧初始值位于 `config/szlab_handshake.yaml`。命令行或 GUI
显式参数优先于该配置：

- `workflow`：`all` 或指定工作流 ID；
- `position`：S04 定向调试位置；
- `pump`：初始化为在位的 S06 储液瓶，取值 `1`、`2` 或 `3`（两瓶）；
- `s06_robot_workflow`：启用后，S06 烧杯传感器由机器人任务 `11/12` 放置和取走；
- `s09_pipetting_workflow`：初始化 S09 工位、液体余量并响应全部内部工艺；
- `s09_remaining_volume_ml`：S09 1-5 号液体瓶的初始余量；
- `cleanup_on_exit`：正常停止时清理仿真器拥有的 PLC 输出，但保留 PC 写入的任务号、
  工艺号和参数标志。

S04 磁搅接单后会读取 `磁搅时间设置_上位机[position-1]`，该值单位为
毫秒。例如单点动作的 `duration=30` 会写入 `30000`，代理在 30 秒后才反馈
S04 加工完成。如果 Server 不提供该节点，代理仍使用 `delays.s04` 的固定仿真延时。

S09 启动时如果已经存在非零工艺号，但没有参数完成信号，代理会将它视为上次
中断留下的残留值，等待工艺号变化或新的参数完成脉冲，不会提前反馈完成。

默认配置已开启完整的 S06 机器人和 S09 移液工作流。物料在位传感器会在启动时
初始化，并由对应机器人任务自动更新，无需设置 `SKIP_SENSOR_PRECHECK`。

当前 Uni-Lab-SZLab 驱动默认使用 `szlab_plc_0730.csv`。已校验该表包含仿真器
所需的全部 106 个节点；启动仿真 Server 时应加载这份节点表，或加载从更新 PLC
工程提取且包含同等节点的 CSV。

## Web GUI

```text
macOS:  start_gui.command
Windows: start_gui.bat
```

GUI 提供三个独立工作区：

- **提取变量**：发现 GVL、预览并导出 OPC UA 变量 CSV。
- **编辑程序块**：浏览和修改 POU、GVL、DUT。
- **OPC UA 仿真**：管理 Server/Agent；从全部变量中搜索、勾选节点并加入
  监控栏，在监控栏中定时读取或手动刷新，并进行变量写入。写入值会按 CSV
  声明的数据类型校验，并在写入后回读确认。GUI 会根据规范化变量定义计算
  CSV 指纹，并在当前浏览器中分别保存每份变量表的监控列表；刷新页面或切回
  相同 CSV 后会自动恢复。
- **客户端连接**：展示当前 TCP 连接数、已激活的 OPC UA Session 数，以及
  客户端 IP、源端口、Session 状态和连接时长。客户端源端口由客户端操作系统
  临时分配，重连后可能变化。

即使没有 MCP bundle，GUI 仍能启动 Server 和 Agent。项目打开、POU 编辑、编译、
下载尝试和 GVL 提取需要配置下面的 MCP 依赖。

### 远程 Linux 挂接

当 OPC UA Server 和 Agent 由 Supervisor 或 systemd 托管时，GUI 可以只挂接现有
服务，不再尝试占用端口或结束外部进程：

```bash
python -m gui.backend \
  --host 0.0.0.0 \
  --port 18765 \
  --no-open \
  --attach-url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --attach-csv data/demo_variables.csv
```

挂接模式保留在线变量读取和写入，但会禁用 GUI 内的 Server/Agent 启停按钮。
浏览器和服务器不在同一台机器时，可在 GUI 上传 CSV；文件会保存到
`data/uploads/`，该目录不会提交到 Git。远程挂接的完整自检入口为：

```bash
python tests/integration/remote_attach_check.py
```

客户端连接遥测默认写入 `data/runtime/server-connections.json`。外部托管时，
Server 与 GUI 必须使用同一项目目录；如果两个进程的运行目录不同，请为两者
设置相同的 `OPCUASIM_CONNECTION_STATE` 绝对路径。该运行时目录不会提交到 Git。

## 可选：InoProShop MCP

系统要求：

- Windows
- Node.js 18 或更高版本
- InoProShop V1.9.1.6（SP11 内核）
- 与该版本匹配且拥有使用权的 `bundle.min.js`

可把 bundle 放到自动发现位置：

```text
OpcUaSim/vendor/inoproshop-mcp/bundle.min.js
```

由于当前取得的 bundle 未声明标准开源再分发许可，本仓库只保留放置说明，不直接
提交该第三方文件。

配置优先级为：显式参数 > 环境变量 > 用户 MCP JSON > 自动探测。

支持的环境变量：

| 变量 | 说明 |
|---|---|
| `OPCUASIM_MCP_BUNDLE` | `bundle.min.js` 路径 |
| `OPCUASIM_INOPROSHOP_EXE` | `InoProShop.exe` 路径 |
| `OPCUASIM_INOPROSHOP_PROFILE` | InoProShop profile |
| `OPCUASIM_MCP_WORKSPACE` | MCP 工作区 |
| `OPCUASIM_NODE` | `node` 命令或绝对路径 |
| `OPCUASIM_MCP_CONFIG` | 自定义 MCP JSON 路径 |

也会自动检查：

- `%USERPROFILE%\.cursor\mcp.json`
- `%USERPROFILE%\.mcp.json`
- 常见 `C:\Program Files` / `D:\Program Files` InoProShop 安装路径

参考配置见 [`.env.example`](.env.example)。

### MCP CLI 示例

```powershell
python -m ino_mcp.cli structure `
  --project "C:\project\XUSE.project"

python -m ino_mcp.cli extract `
  --project "C:\project\XUSE.project" `
  --out extracted\XUSE.csv --all
```

每条命令也支持：

```text
--bundle
--codesys-path
--codesys-profile
--workspace
--node
--mcp-server
```

## 目录结构

```text
OpcUaSim/
├── config/                       # XUSE / SZLab 握手配置
├── data/                         # 开箱即用的 CSV 示例
├── gui/                          # FastAPI Web GUI 与前端资源
├── ino_mcp/                      # 可选 MCP 客户端、配置、业务封装和 CLI
├── scripts/                      # 启动器共用的内部脚本
├── tests/
│   ├── fixtures/                 # 测试数据
│   └── integration/              # 可独立运行的端到端检查
├── tools/                        # 诊断工具
├── vendor/inoproshop-mcp/        # 可选第三方 bundle 放置点
├── common.py
├── cli.py                         # pip 安装后的统一命令分发
├── server.py
├── handshake_agent.py
├── szlab_handshake_agent.py      # SZLab Robot / S04-S09 握手仿真
├── pyproject.toml                 # unilab-opcua-sim wheel 元数据
├── requirements.txt
├── setup_venv.bat
└── start*.bat
```

## 安全与部署说明

- OPC UA Server 默认允许匿名访问且使用 `NoSecurity`，仅适合开发、测试或受控网络。
- `0.0.0.0` 会监听所有网卡；只需本机使用时可传 `--host 127.0.0.1`。
- MCP 的在线下载属于非幂等设备操作；当前工具的可靠路径仍是保存和编译，真实下载
  前应在 InoProShop 中确认目标设备与工程版本。
