# CS机器人Ethernet/IP总线功能使用：Codesys示例
 
## Step1. 启用机器人Ethernet/IP功能

1.  将机器人 FB1 Network 接入到和Ethernet/IP扫描器同一局域网中。  
<div align=center><img src="./img/robot_config/1.png" width="60%"></div>  

2.  在界面中点击启用，等待至指示灯变为黄色，该操作启用了机器人的Ethernet/IP功能。  
<div align=center><img src="./img/robot_config/2.png" width="60%"></div>  

## Step2. 导入EDS文件

1.  首先需要先创建一个codesys项目，菜单栏选择：文件->新建工程。  
<div align=center><img src="./img/new_project/1.png" width="60%"></div>  

2. 在弹出的窗口中选择：Standardproject，并设置相应的工程名和工程保存路径。  
<div align=center><img src="./img/new_project/2.png" width="60%"></div>  

3. 稍等一会，在弹出的窗口中，设备选择：CODESYS Control Win V3 X64(3S - Smart Software Solutions GmbH); PLC_PRG一栏的选项是选择工程中 PLC_PRG 的编程语言，这里我选择 ST。  
<div align=center><img src="./img/new_project/3.png" width="60%"></div>  

4. 等待一会，工程就创建好了。
<div align=center><img src="./img/new_project/4.png" width="60%"></div>  

5.  然后是导入EDS文件（EDS文件可于本附件中取得）。菜单栏选择： 工具->设备存储库。  
<div align=center><img src="./img/eds_install/1.png" width="60%"></div>  

6.  在弹出的窗口中选择：安装->选择eds目录下的EliteEthernetIP.eds->打开。留意将显示的文件类型调整为"EDS和DCF文件"。  
<div align=center><img src="./img/eds_install/2.png" width="60%"></div>  
<div align=center><img src="./img/eds_install/3.png" width="60%"></div>  

7. 启动PLC和网关：在Windows任务栏可以看到这两个图标，当它们为彩色的时候则证明网关/PLC已经启动，若为灰色则没有启动，可以右键这两个图标选择Start xxx 来启动。  
<div align=center><img src="./img/download_program/1.png" width="60%"></div>

8. 在Codesys界面中选择左侧设备栏的Device双击，然后点击“扫描网络”。  
<div align=center><img src="./img/download_program/2.png" width="60%"></div>

9. 选中扫描到的设备（也就是你的电脑）后点击确定。  
<div align=center><img src="./img/download_program/3.png" width="60%"></div>

10. 点击如图所示的按钮来编译程序。  
<div align=center><img src="./img/download_program/4.png" width="60%"></div>

11. 点击如图所示的按钮来下载、登录到设备。  
<div align=center><img src="./img/download_program/5.png" width="60%"></div>

12. 点击如图所示按钮来运行程序
<div align=center><img src="./img/download_program/6.png" width="60%"></div>

## Step3. 添加机器人到项目中并通过Ethernet/IP读取数据

1.  右键设备栏中的 Device，选择“添加设备”。  
<div align=center><img src="./img/add_dev/1.png" width="60%"></div>

2.  选择“以太网适配器”->Ethernet，并点击“添加设备”.  
<div align=center><img src="./img/add_dev/2.png" width="60%"></div>

3.  右键Ethernet选择“添加设备”。  
<div align=center><img src="./img/add_dev/3.png" width="60%"></div>

4.  在弹出的窗口中选择EthernetIP扫描器。  
<div align=center><img src="./img/add_dev/4.png" width="60%"></div>

5.  右键 EtherNet_IP_Scanner 选择添加设备。  
<div align=center><img src="./img/add_dev/5.png" width="60%"></div>

6.  选择 Elite Robot CS 并点“添加设备”。  
<div align=center><img src="./img/add_dev/6.png" width="60%"></div>

7.  双击 Ethernet ，选择 "通用"，点击 "Browse..."。  
<div align=center><img src="./img/add_dev/7.png" width="60%"></div>

8.  选择使用的网卡并点确认，这里需根据实际情况选择。  
<div align=center><img src="./img/add_dev/8.png" width="60%"></div>

9.  双击 Elite_Robot_CS ，选择“通用”，在"IP地址"处填入机器人FB1的IP。  
<div align=center><img src="./img/add_dev/9.png" width="60%"></div>
<div align=center><img src="./img/add_dev/10.png" width="60%"></div>

10.   点击 Ethernet/IPI/O映射 将“总是更新变量”选为“启用2(总是在总线周期任务中)”。  
<div align=center><img src="./img/add_dev/11.png" width="60%"></div>

11. 根据前文步骤，编译、下载、运行程序。

12.  双击 Elite_Robot_CS，选择Ethernet/IPI/O映射，展开Exlusive Owner-1，便可以看到机器人的数据。  
<div align=center><img src="./img/add_dev/12.png" width="60%"></div>

## Step4. 向机器人写入数据

1. 查看需要写入的地址，例如此处我需要设置数字输出IO，其地址就为 %QW4 和 %QW5。  
<div align=center><img src="./img/set_var/1.png" width="60%"></div>

2. 双击 "MainTask" 下的 "PLC_PRG"。  
<div align=center><img src="./img/set_var/2.png" width="60%"></div>

3. 在出现的ST代码编辑框里输入如下赋值语句：  
```
%QW4 := 16#FFFF;
%QW5 := 16#FFFF;
```
<div align=center><img src="./img/set_var/3.png" width="60%"></div>

4. 根据前文步骤，编译、下载、运行程序。  

5. 在连接上之后，可以看到数字输出均被设置为高。  
<div align=center><img src="./img/set_var/4.png" width="60%"></div>

## Step5. 数据分模块使用
控制器版本 2.12.x 之后在保留原来单个连接的基础上将的数据分到9个连接中，也就是现在总共有10个连接，分别如下：
- Exlusive Owner-1 包含了EthernetIP的所有输入、输出数据。（读写）
- RobotState Slot 机器人状态数据。（只读）
- BoardIO Slot 主板IO数据。（只读）
- Tool Slot 工具数据。（只读）
- Joint Slot 关节数据。（只读）
- Tcp Slot TCP数据。（只读）
- BoolRegisters Slot 布尔寄存器数据。（读写）
- IntRegisters Slot 整数寄存器数据。（读写）
- FloatRegisters Slot 浮点寄存器数据。（读写）
- RobotIO Slot 机器人IO数据。（只写）

### 添加连接的方式
1. 使用最新的EDS文件。
2. 点击设备中的CS机器人->“连接”->“添加连接”。  
<div align=center><img src="./img/connection/1.png" width="60%"></div>

3. 选择需要的连接进行添加。  
<div align=center><img src="./img/connection/2.png" width="60%"></div>

4. EtherNet/IP/I/O映射中可以查看添加的连接对应的数据。  
<div align=center><img src="./img/connection/3.png" width="60%"></div>


> 注意：  
> 1. 上面只读和只写的连接中会看到如下样式的数据项，此类数据项无意义，可以不用管. <div align=center><img src="./img/connection/4.png" width="60%"></div>
> 2. 上面连接中寄存器的“读”部分是机器人的output寄存器，“写”部分是机器人的input寄存器。
> 3. 使用建议上，如果使用了 `Exlusive Owner-1` 就不使用其他连接（因为已经包含了所有数据），但如果 `Exlusive Owner-1` 和其他连接一起使用，对于“只读”的数据没有影响，但写入的数据，将不会使用 `Exlusive Owner-1` 的值。例如，`Exlusive Owner-1` 中设置 `Input int register 0` 为 123 ，`IntRegisters Slot` 中设置 `Input int register 0` 为 456 ，那么生效的值应当是 456。
> 4. Ethernet/IP 功能中数据的来源是 RTSI，此功能将会订阅所有它需要的数据，不管是否添加了对应模块的连接。例如，就算没有添加 `Exlusive Owner-1` 和 `IntRegisters Slot` 连接，但依然会订阅 `input_int_register0` 至 `input_int_register23` 的RTSI数据项。