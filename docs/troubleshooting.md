# 连接与画面排查

## 网页打不开

1. ARK 和相机应已供电、网口灯亮。连接 ARK 的 `192.168.1.12` 对外网口，等待启动，不要接相机专用网口。
2. 确认有线网卡启用，拥有未冲突的 `192.168.1.x/24` 地址。若脚本提示地址冲突，先恢复，再选择其他空闲地址。
3. Wi-Fi 同样使用 `192.168.1.x` 时，确认 `.12/32` 路由指向有线网卡：

   ```powershell
   Get-NetAdapter
   Get-NetIPAddress -AddressFamily IPv4
   Get-NetRoute -DestinationPrefix 192.168.1.12/32
   Test-NetConnection 192.168.1.12 -Port 8770
   curl.exe --noproxy '*' http://192.168.1.12:8770/api/cameras
   ```

4. 已安装 Python 时可绑定有线源地址进行诊断：

   ```powershell
   python check_cameras.py --source-address 192.168.1.200
   ```

   成功但网页失败时检查主机路由、浏览器代理。指定源地址只影响这个程序。

5. ARK 本机访问 `http://127.0.0.1:8770/api/cameras`。本机也失败时检查任务与日志；本机成功、客户端失败时检查接线、地址、路由和 TCP 8770 入站规则。不要默认关闭整个防火墙。

`ping` 失败不能单独证明服务失败，ICMP 可能被阻拦，应以 TCP 8770、HTTP 和递增帧号为准。

## 缺一路或相机失败

在 ARK 检查相机供电、专用网口和相机/主机网卡的 IP 匹配。客户端接入无需修改相机 IP。

启动枚举时完全未发现的设备需接好后正常重启服务；已枚举设备临时断连时线程会重试。

`0x80000203` / access denied 通常是 MVS/MvToolkit 或其他程序持有控制连接。先在持有程序中停止采集并关闭设备连接。Monitor 能读部分参数，不代表另一独占连接存在时能取帧。

## 两路在线但很暗或全黑

先看帧号是否递增。递增且快照可读说明连接已建立，再检查照明、镜头盖、镜头朝向、光圈、曝光与增益。可在网页逐步增大曝光/增益或开启设备支持的自动曝光。

5000 µs = 5 ms。长曝光可能降低帧率并增加运动模糊。ROI 裁剪改变视野，不能代替调焦或曝光。

## 延迟与帧率

2000 万像素全分辨率默认每台约 4.7–5 fps，并非 30 fps 视频。优先用千兆有线；不要随意在未端到端支持的链路开启巨帧。

观察 `fps`、`frame_age_seconds`、`lost_packets_total`。慢客户端会跳帧，反复下载全分辨率 JPEG 也增加 CPU/带宽开销，日常预览使用 MJPEG。

## 恢复网络

管理员 PowerShell，在原项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Restore-ClientNetwork.ps1
```

保留 `client-network.local.json`。恢复脚本依它识别自己添加的设置，不能恢复全部手动改动。记录丢失时需手动删除所选网卡的客户端静态地址及 `.12/32` 路由，原来使用 DHCP 的网卡再改回自动获取。
