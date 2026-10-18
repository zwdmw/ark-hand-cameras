# ARK 服务端安装与运维

## 已有机器人

当前 ARK 已部署相机服务，目录为 `D:\HandCameraSDK`，启动任务为 `HandCameraSDK-Server`，监听端口为 8770。新电脑连接只需 README 的客户端步骤，不需要重新安装本节内容。

本节适用于更换主机、重新部署或维护服务。不要在现有服务取流时启动第二个采集进程。

## 环境与文件

- Windows 10/11 x64，管理员权限。
- 64 位 Python 3.10–3.12；建议用项目虚拟环境。
- 海康机器人 MVS，安装 GigE 驱动、64 位运行库和 Python 开发示例；已有机器验证版本为 **4.8.2.2**。
- ARK 的相机网卡和对外网卡按 README 地址配置，相机供电、网线和 IP 匹配。

仓库不附带海康 SDK、驱动或运行库，请从[海康机器人官网](https://www.hikrobotics.com/)下载 MVS 并按其许可安装。

将本仓库放在所有本地用户及 SYSTEM 可访问的位置，例如 `D:\HandCameraSDK`；服务启动后不要移动目录或删除虚拟环境。

```powershell
cd D:\HandCameraSDK
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-server.txt
.\.venv\Scripts\python.exe .\scripts\check_server_environment.py
```

默认查找路径：

```text
Python SDK: C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport
Runtime:    C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64
```

安装在其他位置时设置实际路径后再检查：

```powershell
$env:MVS_PYTHON_PATH = 'D:\MVS\Development\Samples\Python\MvImport'
$env:MVS_RUNTIME_PATH = 'D:\MVS-Runtime\Win64_x64'
.\.venv\Scripts\python.exe .\scripts\check_server_environment.py
```

## 首次前台运行

在 MVS/MvToolkit 中停止采集，并关闭两台设备的连接，使 SDK 控制连接空闲。关闭预览窗口本身不一定释放相机。

```powershell
.\.venv\Scripts\python.exe .\probe_camera.py
.\.venv\Scripts\python.exe .\server.py --host 0.0.0.0 --port 8770
```

另开窗口验证：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-Cameras.ps1 -BaseUrl http://127.0.0.1:8770
```

浏览器打开 `http://127.0.0.1:8770/`。默认连接全部已发现的 GigE 相机；有其他相机时可显式选择：

```powershell
.\.venv\Scripts\python.exe .\server.py --serial DA9255744 --serial DA9255841
```

停止前台服务使用 `Ctrl+C`，等待正常退出。服务取流时临时关闭原触发模式；正常退出时尝试恢复并释放句柄。不要直接结束 Python 进程或拔电代替正常停止。

## 安装开机后台任务

前台测试结束后，**以管理员身份打开 PowerShell**：

```powershell
cd D:\HandCameraSDK
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Server.ps1 -PythonExe 'D:\HandCameraSDK\.venv\Scripts\python.exe'
```

自定义 SDK 路径可附加 `-MvsPythonPath '实际的MvImport目录' -MvsRuntimePath '实际的Win64_x64目录'`。

安装脚本检查依赖和 SDK 导入，保存本地配置 `server-config.local.json`，注册 SYSTEM 开机任务并立即启动。任务延迟 15 秒，启动失败每隔 1 分钟最多重试 5 次，无需登录桌面。仅添加允许局域网 `192.168.1.0/24`、`169.254.0.0/16` 访问 TCP 8770 的防火墙规则，不关闭系统防火墙。

SYSTEM 必须能读取 Python、虚拟环境、MVS 和项目目录。不要把依赖放在映射盘、加密个人目录或必须交互登录才可访问的位置。

已有同名任务时默认拒绝覆盖。计划重装时，先正常停止旧服务，再用 `-ReplaceExistingTask` 安装。已有机器的旧启动脚本无需为了客户端接入而替换。

## 检查、启动、停止

```powershell
Get-ScheduledTask -TaskName HandCameraSDK-Server
Get-ScheduledTaskInfo -TaskName HandCameraSDK-Server
Start-ScheduledTask -TaskName HandCameraSDK-Server
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-Cameras.ps1 -BaseUrl http://127.0.0.1:8770
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Stop-Server.ps1
```

停止脚本应在 ARK 本机执行，正常关闭并等待任务退出。重新用 MVS 采集前先停止服务；结束 MVS 采集并释放连接后再启动后台任务。

日志：`server.log`、`server-stdout.log`、`server-stderr.log`、`server-launch-error.log`。相机掉线后线程会间隔约 3 秒重试打开已发现设备；启动时完全未发现的设备需接好后重启服务重新枚举。

## 验证范围

本项目基于已有两台真实相机的运行服务整理。此次发布验证页面 HTTP、两路快照和并发 MJPEG 连续帧，没有为了测试安装脚本重装或重启现有 ARK 服务。新安装脚本经过 PowerShell 语法检查，任务和防火墙的安装过程仍应在计划重装时按本节验收。
