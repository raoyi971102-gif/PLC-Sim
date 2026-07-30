# OpcUaSim

一个由 CSV 变量表驱动的 OPC UA 仿真环境，包含：

- OPC UA Server：按 CSV 创建节点，默认监听 `opc.tcp://0.0.0.0:4855/xuse_sim/`
- Handshake Agent：仿真 Type A/B/C/D 四类 PLC 握手
- Web GUI：管理变量提取、Server、Agent 以及可选的 InoProShop MCP 功能
- MCP CLI：打开/编辑/编译 InoProShop 工程并从 GVL 提取 CSV

核心 OPC UA 仿真不依赖其他仓库。InoProShop 工程操作属于可选功能，需要使用者
自行安装 InoProShop、Node.js，并提供有权使用的 MCP bundle。

## 快速开始

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

| 文件 | 用途 |
|---|---|
| `start.bat` | 只启动 OPC UA Server |
| `start_handshake.bat` | 只启动 Handshake Agent |
| `start_szlab_handshake.bat` | 只启动 SZLab Poly Studio 握手仿真 |
| `start_all.bat` | 同时启动 Server 和 Agent |
| `start_gui.bat` | 启动 Web GUI，默认地址 `http://127.0.0.1:18765/` |
| `pick.bat` | 通过文件选择器加载一份或多份 CSV |
| `setup_venv.bat` | 创建项目虚拟环境并安装依赖 |

GUI 诊断脚本位于 `tools/diagnose.ps1`。

启动脚本按以下顺序选择 Python：

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

- Robot 任务允许写入、任务完成和回环复位；
- S04 六个磁搅位置；
- S05 拍照完成与 OK 结果；
- S06 加液；
- S07 固体加样；
- S08 开关盖；
- S09 移液和天平状态。

先启动包含 SZLab 节点的 OPC UA Server，再运行：

```bat
start_szlab_handshake.bat
```

也可以指定其他 endpoint：

```bat
start_szlab_handshake.bat opc.tcp://127.0.0.1:4855/xuse_sim/
```

GUI 的“握手代理”中可将“仿真协议”切换为 `SZLab Poly Studio`。
仿真驱动优先使用实机格式
`ns=4;s=上位机通讯|<变量名>`，找不到时按 BrowseName 递归匹配。
缺失某个工位节点时默认只跳过该工位；命令行增加 `--strict` 可改为立即报错。

延时和 PLC 侧初始值位于 `config/szlab_handshake.yaml`。默认只初始化握手、就绪和
允许加工信号，不伪造物料在位传感器；测试机器人纯握手时可在 Uni-LabOS 侧设置
`SKIP_SENSOR_PRECHECK=1`，或在 YAML 的 `initial_values` 中明确加入所需传感器。

注意：Uni-Lab-OS 当前 `szlab_poly_studio.json` 指向的旧版
`szlab_plc_0610.csv` 不包含后续加入的完整 Robot/S04/S06-S09 握手变量。
仿真 Server 应加载与当前设备驱动一致的节点表或从最新 PLC 工程提取的 CSV。

## Web GUI

```bat
start_gui.bat
```

GUI 提供三个独立工作区：

- **提取变量**：发现 GVL、预览并导出 OPC UA 变量 CSV。
- **编辑程序块**：浏览和修改 POU、GVL、DUT。
- **OPC UA 仿真**：管理 Server/Agent；从全部变量中搜索、勾选节点并加入
  监控栏，在监控栏中定时读取或手动刷新，并进行变量写入。写入值会按 CSV
  声明的数据类型校验，并在写入后回读确认。监控列表保存在当前浏览器中，
  刷新页面后仍会保留。

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
├── server.py
├── handshake_agent.py
├── szlab_handshake_agent.py      # SZLab Robot / S04-S09 握手仿真
├── requirements.txt
├── setup_venv.bat
└── start*.bat
```

## 安全与部署说明

- OPC UA Server 默认允许匿名访问且使用 `NoSecurity`，仅适合开发、测试或受控网络。
- `0.0.0.0` 会监听所有网卡；只需本机使用时可传 `--host 127.0.0.1`。
- MCP 的在线下载属于非幂等设备操作；当前工具的可靠路径仍是保存和编译，真实下载
  前应在 InoProShop 中确认目标设备与工程版本。
