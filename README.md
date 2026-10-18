# ARK 双手部海康相机

让其他电脑通过一根网线访问机器人手部的两台海康机器人（Hikrobot）工业相机。ARK 使用 MVS SDK 采集，其他电脑通过浏览器、HTTP 或 OpenCV 读取两路画面。

**已有机器人不需要重装服务。** 新电脑完成一次网卡配置后，以后插上网线、等待 ARK 开机，即可打开 <http://192.168.1.12:8770/>。浏览器客户端不需要 Python、MVS SDK 或相机驱动。

## 接线与地址

```text
其他电脑 ── 网线（或交换机）── ARK 对外网口 192.168.1.12:8770
                               ├─ 相机专用网口 192.168.1.11 ── DA9255744
                               └─ 相机专用网口 169.254.162.3 ── DA9255841
```

网线接到 ARK 上配置为 **192.168.1.12 的对外网口**。两台相机原有接线保持接在 ARK 上，新电脑通过 ARK 共享画面，不直接连接工业相机。

| 设备 | 地址 | 用途 |
| --- | --- | --- |
| ARK 对外网口 | `192.168.1.12/24` | 客户端入口，TCP 8770 |
| 新电脑有线网卡 | 例如 `192.168.1.200/24` | 每台选用不同且未占用的地址 |
| 相机 DA9255744 | `192.168.1.85` | ARK 通过 `192.168.1.11` 采集 |
| 相机 DA9255841 | `169.254.158.48` | ARK 通过 `169.254.162.3` 采集 |

型号均为 `MV-CS200-10GC`，原始分辨率 **5472 × 3648**。序列号与左右手的对应需现场确认，仓库按序列号区分。

## Windows 新电脑：首次使用

1. 给机器人、ARK 和相机供电，将电脑连接到上面说明的 ARK 对外网口。多台电脑可通过交换机接入，无需互联网。
2. 下载本仓库 ZIP 并解压，或者安装 Git 后执行：

   ```powershell
   git clone https://github.com/zwdmw/ark-hand-cameras.git
   cd ark-hand-cameras
   ```

3. **以管理员身份打开 PowerShell**，进入项目目录，查看实际网卡名称：

   ```powershell
   Get-NetAdapter
   ```

   将下面的 `以太网` 替换成接机器人的有线网卡名称，英文系统通常叫 `Ethernet`：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-ClientNetwork.ps1 -InterfaceAlias "以太网" -ClientAddress 192.168.1.200
   ```

   脚本添加 `/24` 静态地址和 `192.168.1.12/32` 持久主机路由，让相机连接走这根网线。**所选有线网卡的 DHCP 会关闭，不设置网关或 DNS，不修改 Wi-Fi 设置。** Wi-Fi 同样使用 `192.168.1.x` 时，主机路由可避免 ARK 连接走错网卡。

   多台电脑分别使用 `.200`、`.201`、`.202` 等未占用地址，不要占用 ARK、相机或其他设备的地址。静态地址空闲情况请结合现场规划确认，Windows 冲突检测不能代替地址规划。

4. 显示 `PASS` 后，用 Chrome、Edge 或 Firefox 打开：

   **<http://192.168.1.12:8770/>**

   网页同时显示两路视频，可以读取/调整曝光、增益、白平衡、帧率和裁剪，并保存原始分辨率快照。

可先预览而不修改网络：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-ClientNetwork.ps1 -InterfaceAlias "以太网" -ClientAddress 192.168.1.200 -WhatIf
```

## 以后使用

插上同一张有线网卡的网线，等待 ARK 完成启动，然后打开网址；或者双击 **`Open-Cameras.cmd`**，检测两台相机持续出帧后自动打开网页。日常查看不需要管理员权限。

ARK 现有后台任务在 Windows 启动约 15 秒后尝试启动，不需要登录桌面。通常预留 30–60 秒；若网卡或相机未就绪，启动失败会重试。

这里的“插线即可使用”指首次配置以后，且 ARK 和相机已供电、服务可用。新电脑没有同网段地址时，单纯插线不会自动获得静态配置。

只检查连接：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-Cameras.ps1
```

恢复脚本添加的网络设置（原项目目录，管理员权限）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Restore-ClientNetwork.ps1
```

恢复依据 `client-network.local.json` 删除本脚本添加的地址/路由，并在原先启用 DHCP 时重新启用。保留该本地记录直到恢复完成，它不上传 Git。

## 在自己的程序里读取

纯 Python 标准库验证两路真实 MJPEG 和快照，无需第三方库：

```powershell
python check_cameras.py
```

指定有线源地址，适合尚未配置主机路由时诊断：

```powershell
python check_cameras.py --source-address 192.168.1.200 --save-dir captures
```

OpenCV 双路示例：

```powershell
python -m pip install -r requirements-client.txt
python client_example.py --base-url http://192.168.1.12:8770
```

按 `Q` 退出；无窗口运行可加 `--no-gui --seconds 10`。完整接口见 [HTTP / Python 接入](docs/api.md)。

| 内容 | 入口 |
| --- | --- |
| 双路网页 | <http://192.168.1.12:8770/> |
| 设备状态 | <http://192.168.1.12:8770/api/cameras> |
| 相机 1 视频 | <http://192.168.1.12:8770/stream/DA9255744.mjpg> |
| 相机 2 视频 | <http://192.168.1.12:8770/stream/DA9255841.mjpg> |
| 全分辨率快照示例 | <http://192.168.1.12:8770/snapshot/DA9255744.jpg?full=1> |

## 其他系统、服务端与排查

- [Linux / macOS 与手动网络配置](docs/client-network.md)
- [ARK 安装、开机启动与停止](docs/server-install.md)
- [HTTP / Python 接入与参数说明](docs/api.md)
- [连接失败、缺一路、黑画面等问题](docs/troubleshooting.md)

默认预览为 1280 × 853 JPEG，已有全分辨率配置下每台实测约 4.7–5 fps。两个取流线程相互独立，没有硬件同步。多个客户端共享采集结果，无需争抢相机 SDK 控制连接。

服务面向可信机器人局域网，当前没有账号认证和 TLS；同网段访问者可调参或调用停止接口。安装脚本仅开放相应局域网的 TCP 8770，部署时不应把此端口转发到公网。

## 项目与验证

核心采集：`server.py`、`probe_camera.py`；网页：`index.html`；OpenCV 示例：`client_example.py`；只读验收：`check_cameras.py`；Windows 配置和运维：`scripts/`。

不依赖真实相机的协议回归测试：

```powershell
python -m unittest discover -s tests -v
```

真实设备验收用 `check_cameras.py`，检查两台状态、JPEG 快照、两路并发 MJPEG 的递增帧号和图像新鲜度，不修改相机参数。GitHub Actions 执行协议回归测试与 PowerShell 语法检查，CI 不连接机器人。
