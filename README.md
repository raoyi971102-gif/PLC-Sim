# PLC-Sim

面向 Uni-Lab 设备联调的 OPC UA 仿真、握手代理与测试工具。

## 目录

- [`OpcUaSim`](./OpcUaSim/)：CSV 驱动的 OPC UA Server、PTLC/SZLab 仿真运行时及 Web GUI。SZLab 默认一次启动整个设备包，常驻 Robot、S04-S09 和 S1 HTTP Adapter。

项目已经包含公开演示变量表和 Python 依赖声明。源码运行仅支持 Python 3.11.x：

- pip：使用 Python 3.11 执行 `python -m pip install ./OpcUaSim`，然后运行 `opcua-sim`；
- macOS：在 Finder 中双击 `start_gui.command`；启动器会自动创建环境并安装依赖；
- Windows：运行 `setup_venv.bat`，再运行 `start_all.bat`。

也可以直接安装运行依赖：

```powershell
python -m pip install -r OpcUaSim\requirements.txt
```
