# GarrisonFarm_plus

基于 [GarrisonFarm-Personal](https://github.com/FJH3576/GarrisonFarm-Personal) 个人优化版扩展的《明日方舟》卫戍协议「极速模拟」辅助脚本。

本仓库提供启动器、连接向导、MuMu 增强触控适配代码及配置说明。**本仓库这些文件不能单独运行，也不包含原 EXE 的完整源代码。**

## 下载与运行

请到本仓库 **Releases** 下载维护者发布的完整运行包并解压。完整包需要包含 `GarrisonFarm.exe`、`_internal`、`tiles.json` 和本仓库的脚本文件；GitHub 的 “Code → Download ZIP” 仅下载仓库内容。

若当前尚无 Release，请等待维护者上传完整包。更新代码时，应配套使用支持加载 `mumu_touch.py` 的个人优化版 EXE 和对应运行库，不能保证与任意上游版本直接兼容。

1. 安装 MAA 和 MuMu，在 MAA 中确认能连接目标设备。
2. 将模拟器设为 **1920×1080 横屏**，游戏使用默认界面主题。
3. 进入「卫戍协议」主界面，双击 `开始刷角标.bat`。
4. 首次启动按向导填写 MuMu 路径、MAA 路径和设备 ADB 端口；之后自动读取。首次链接可参考'连接配置说明.txt'
5. 选择刷取方式、总局数，再选择原有四种模式。


## 连接与增强触控

MuMu 查看端口：目标设备窗口右上角「⋯」→「设备设置」→左下角「问题诊断」→「网络信息」→「ADB 调试端口」。若显示两个端口，例如 `5557,16416`，向导填写 `16416`。不同版本菜单可能略有区别。

- 更换设备或路径：运行 `重新配置连接.bat`。
- 检查增强触控：运行 `检查增强触控.bat`，仅检查连接，不发送点击或拖动。
- MuMu SDK 增强触控及快速截图保持开启；运行时会显示是否启用成功。
- 多开时为每个设备使用独立的完整目录，并分别配置端口；同一设备同时只运行一份控制脚本。

当前连接向导面向 MuMu，其他模拟器未通过这套向导验证。保持 1920×1080 横屏与默认主题；游戏更新导致布局变化后可能需要调整。

## 仓库文件

| 文件 | 用途 |
| --- | --- |
| `mumu_touch.py` | 增强触控、快速截图、固定选人与识别适配 |
| `launcher.ps1` | 刷取方式、行列、局数菜单及程序启动 |
| `connection.ps1` | 首次连接配置及设备重绑 |
| 三个 `.bat` 文件 | 启动、重新配置和增强触控检查入口 |
| `mumu_touch.json` | 不含设备绑定的默认增强功能配置 |
| `tiles.json` | 部署坐标配置 |
| `连接配置说明.txt` | 连接配置步骤 |
| `个人优化说明与耗时对比.txt` | 保留上游个人优化版说明及其历史测试记录 |
| `.gitignore` | 排除本机配置、日志、备份与完整运行包 |

`connection.json`、`settings.json`、`strategies_done.json` 是本地生成的配置或进度，不随仓库发布。连接向导会把设备路径写入本地 `mumu_touch.json`；发布时应使用仓库中的无绑定默认内容。

## 来源

- [GarrisonFarm-Personal](https://github.com/FJH3576/GarrisonFarm-Personal)
- [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights)

保留原项目及个人优化版的作者说明。历史耗时数据来自原说明中的测试环境，不代表每台设备的运行速度。
