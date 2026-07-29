# InoProShop MCP bundle

此目录用于放置可选的 `bundle.min.js`：

```text
OpcUaSim/vendor/inoproshop-mcp/bundle.min.js
```

项目会自动发现该路径。也可以通过 `OPCUASIM_MCP_BUNDLE`、用户 MCP JSON
或 CLI 的 `--bundle` 指定其他位置。

仓库不直接分发 bundle：当前拿到的 InoProShop LIMIT MCP 包没有声明标准的
开源再分发许可。请从有权使用的来源取得文件，并遵守其许可证。OPC UA Server、
Handshake Agent 和 Web GUI 的仿真功能不依赖该 bundle；只有打开、编辑、编译
InoProShop 工程和提取 GVL 时需要它。
