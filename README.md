# PLC-Sim

PLC 与机器人接入相关的仿真、测试和参考资料。

## 目录

- [`OpcUaSim`](./OpcUaSim/)：CSV 驱动的 OPC UA Server、四类握手代理及 Web GUI。
- [`机器人Python接入规范与参考`](./机器人Python接入规范与参考/)：机器人 Python 接入示例与参考资料。

OPC UA 仿真项目已经包含公开演示变量表和 Python 依赖声明。进入
`OpcUaSim` 后运行 `setup_venv.bat`，再运行 `start_all.bat` 即可开始。

两个目录分别维护依赖，不在仓库根目录混装：

```powershell
python -m pip install -r OpcUaSim\requirements.txt
python -m pip install -r 机器人Python接入规范与参考\requirements.txt
```
