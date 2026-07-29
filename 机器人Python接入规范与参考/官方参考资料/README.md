# 官方参考资料离线副本

下载日期：2026-07-29  
原则：只保存厂家官网或厂家官方 GitHub 组织提供的资料。

## 越疆 Dobot

| 本地文件 | 官方来源 | 用途 |
|---|---|---|
| `越疆/TCP_IP远程控制接口文档（V3）_20250508_cn.pdf` | <https://github.com/Dobot-Arm/TCP-IP-Protocol-6AXis-V3> | CR/Nova V3 TCP/IP 协议，89 页 |
| `越疆/TCP-IP-Protocol-6AXis-V3_main.zip` | <https://github.com/Dobot-Arm/TCP-IP-Protocol-6AXis-V3> | V3 官方仓库完整快照 |
| `越疆/DOBOT_TCP_IP二次开发接口文档_V4.6.6_20260410_cn.pdf` | <https://github.com/Dobot-Arm/TCP-IP-Python-V4> | CRA/E6/CRAF/NovaLite V4 接口，163 页 |
| `越疆/TCP-IP-Python-V4_feature-v4-optimization.zip` | <https://github.com/Dobot-Arm/TCP-IP-Python-V4> | V4 Python SDK、示例和测试完整快照 |

V4 ZIP 对应下载时官方仓库的 `feature/v4-optimization` 分支；开发前仍应核对现场控制器
版本是否满足 V4 手册和 SDK 的要求。

## 艾力特 Elite

| 本地文件 | 官方来源 | 用途 |
|---|---|---|
| `艾力特/Elite_Robots_CS_SDK_main.zip` | <https://github.com/Elite-Robots/Elite_Robots_CS_SDK> | CS 系列官方 C++ SDK、示例和接口定义 |
| `艾力特/EC机器人8055端口使用_官方网页.html` | <https://www.elibot.com/service/articles/list/204> | EC/EA 8055 JSON-RPC 使用说明 |
| `艾力特/CS_ES端口说明_官方网页.html` | <https://docs.elibot.cn/cs/88fdd/9bd1f/12e52/b2cb3> | 29999、30001、30004、30011、30020、40011 端口说明 |
| `艾力特/CS_ES_30001接口说明_官方网页.html` | <https://docs.elibot.cn/cs/88fdd/9bd1f/12e52/100f6> | CS/ES 30001 状态与脚本接口说明 |
| `艾力特/艾力特下载中心_官方网页.html` | <https://www.elibot.com/service/technical_13> | 厂家手册与软件下载入口的离线页面 |

HTML 文件保存了下载时网页正文，但图片、样式或动态附件可能仍依赖官网。艾力特 EC 页面
列出的 `TS20240703E-8055端口使用-EC技术文档.pdf` 需要通过厂家资料申请表获取，本目录
未绕过该流程下载附件。

## 新松/多可 DUCO

| 本地文件 | 官方来源 | 用途 |
|---|---|---|
| `新松/duco-develop-v3.8-zh.pdf` | <https://docs.ducorobots.cn/download/manual/zh/duco-develop-v3.8-zh.pdf> | 远程控制 SDK、7003、MoveJ/MoveL、状态和心跳，130页 |
| `新松/duco-interface-v1.8-zh.pdf` | <https://docs.ducorobots.cn/download/manual/zh/duco-interface-v1.8-zh.pdf> | TCP 2000/2001、1468字节状态帧 |
| `新松/DUCO远程控制API_函数说明_官方网页.html` | <https://docs.ducorobots.cn/zh/develop/3.8/远程控制API/03函数说明/> | V3.8远程 API 函数在线版 |
| `新松/DUCO_TCP_IP接口_官方网页.html` | <https://docs.ducorobots.cn/zh/interface/latest/01TCP及IP接口/> | TCP及IP接口在线版 |
| `新松/DUCO文档下载中心_官方网页.html` | <https://docs.ducorobots.cn/zh/download.html> | 官方版本与下载日期入口 |

厂家网站公开手册，但 Python SDK 本体需从厂家或集成商取得，并与现场 Core 版本匹配。
本目录不包含也不伪造厂商 SDK。

## SHA-256

```text
26C5BF5FFC2DE755E7560D84E9C584538239FB2FCD601FDDDB3343287FF48E1A  CS_ES_30001接口说明_官方网页.html
E4AEADC3583E5D6705473835616F0B5FFF4F5A7CF8BB26D26FD38847015D0561  CS_ES端口说明_官方网页.html
1C94D4FBB6D10EF0AC407232A09C489B38EC12E15BE2FC0A487C12945E7AD298  EC机器人8055端口使用_官方网页.html
D0FA2BFA39370AC480EEF8DDDAD39BA50A97649D1E3B4180BFF588CE8A14E885  Elite_Robots_CS_SDK_main.zip
875B2346D64AA71EB388458E5D28C07634B002EF035486ABB26E67C2DE9CEDDD  艾力特下载中心_官方网页.html
5DF4DBA11CFABD4EFEBEE2AF5917954FBE644A9FAA16F8D163A106DB9603BE47  DOBOT_TCP_IP二次开发接口文档_V4.6.6_20260410_cn.pdf
8DCF1A92CA22C9351C0F5B9E0FAC181940B6B11CC369D9F606A17EAE1FC1A48B  TCP_IP远程控制接口文档（V3）_20250508_cn.pdf
D1DE6FE6C54BA6DAD237CEFA40CC0C0489CACB3FB8448D97A29BFA03FBF1713B  TCP-IP-Protocol-6AXis-V3_main.zip
0555B95A07CEF569DBD116753ED762F344AA0AA2C394E5398156853301C94654  TCP-IP-Python-V4_feature-v4-optimization.zip
C9AF9828B09F1F0598261482305456B702A70C07E8F7CF58BE4FDF37C908B2ED  duco-develop-v3.8-zh.pdf
3606AEDAC97097B84308B596D6C0B9797DBD9E19C6A257D71FC88AE4F6237DB5  duco-interface-v1.8-zh.pdf
E3092EBAFC44FF13145E5E114899DB386AD00FB8CF34ACD2417CED79CE79DEDF  DUCO_TCP_IP接口_官方网页.html
4897BBC871CC645F0DC012B61618EC88A6273B6381089068489CF3D217352820  DUCO文档下载中心_官方网页.html
141A707C1EB88C6DAE30EFFE2CC40D628FB38700234CBE3C297C2460D99CB37F  DUCO远程控制API_函数说明_官方网页.html
```
