# 客户端网络配置

## 接线和必要条件

ARK 与两台相机保持原有接线和供电。客户端接到 ARK 配置为 `192.168.1.12/24` 的对外网口，或者通过同一个交换机接入。不要把新电脑的网线接到 ARK 的相机专用网口，也不要把两台相机从 ARK 拔下来接入电脑。

客户端用 `192.168.1.200/24` 等空闲地址，掩码 `255.255.255.0`，不设置默认网关和 DNS。第二、第三台客户端换用 `.201`、`.202` 等空闲地址。不要占用 `.11`、`.12`、`.85` 或现场已分配的其他地址。

相机服务地址始终是 `http://192.168.1.12:8770/`。第二台相机的 `169.254.x.x` 地址由 ARK 内部连接负责，新电脑无需配置第二个网段，也无需直接访问相机 IP。

## Windows 推荐方式

按 README 以管理员权限运行 `scripts/Setup-ClientNetwork.ps1`。它支持 Windows PowerShell 5.1 和 Windows 10/11 的 NetTCPIP 模块，允许使用 USB 转千兆网卡。网卡名称必须用 `Get-NetAdapter` 查到的实际名称。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-ClientNetwork.ps1 -InterfaceAlias "Ethernet" -ClientAddress 192.168.1.200
```

不传 `-InterfaceAlias` 时，仅在存在一张已连接的物理有线网卡时自动选择。多网卡电脑应明确指定。配置支持重复执行；更换网卡或地址前先在原项目目录执行恢复脚本。

脚本保存本机配置记录到项目根目录的 `client-network.local.json`。不要在多个项目副本里反复配置同一网卡，也不要删除记录后期待自动恢复。删除记录不会删除已经配置的 Windows 地址和路由。

## Windows 手动方式

1. 运行 `ncpa.cpl`，找到接机器人的有线网卡。
2. 属性 → Internet 协议版本 4 → 使用下面的 IP 地址。
3. IP 填 `192.168.1.200`，子网掩码填 `255.255.255.0`，默认网关和 DNS 留空。若该网卡已有必须保留的静态配置，可通过“高级”添加第二个地址。
4. Wi-Fi 也使用 `192.168.1.x` 时，以管理员身份添加只针对 ARK 的持久路由。把 7 改成实际有线网卡的 ifIndex：

   ```powershell
   Get-NetAdapter
   New-NetRoute -DestinationPrefix 192.168.1.12/32 -InterfaceIndex 7 -NextHop 0.0.0.0 -RouteMetric 1 -PolicyStore PersistentStore
   Get-NetRoute -DestinationPrefix 192.168.1.12/32 -PolicyStore ActiveStore
   ```

   如果最后一条未显示所选有线网卡的路由，再执行：

   ```powershell
   New-NetRoute -DestinationPrefix 192.168.1.12/32 -InterfaceIndex 7 -NextHop 0.0.0.0 -RouteMetric 1 -PolicyStore ActiveStore
   ```

`.12/32` 路由会让这个地址固定走机器人网线。如果 Wi-Fi 上另有同样使用 `.12` 的设备，也会优先走网线；断开机器人后可运行恢复脚本解除。不要修改默认路由来解决这个问题。

## Linux

以下为临时配置，重启或网络管理器重新连接后可能失效。用 `ip -br link` 找到有线接口，把 `enp3s0` 改成实际名称：

```bash
ip -br link
sudo ip link set enp3s0 up
sudo ip addr add 192.168.1.201/24 dev enp3s0
sudo ip route add 192.168.1.12/32 dev enp3s0 src 192.168.1.201
curl --noproxy '*' http://192.168.1.12:8770/api/cameras
```

地址或路由已经存在时不重复添加。持久配置请在所用 NetworkManager / netplan 中设置有线静态地址、`192.168.1.12/32` 的直连路由，并保留原有互联网默认路由；不同发行版方法不同。

## macOS

用 `networksetup -listallhardwareports` 找到 USB/有线网卡对应接口，例如 `en7`。以下也是临时配置：

```bash
networksetup -listallhardwareports
sudo ifconfig en7 inet 192.168.1.202 netmask 255.255.255.0 alias
sudo route -n add -host 192.168.1.12 -interface en7
curl --noproxy '*' http://192.168.1.12:8770/api/cameras
```

浏览器打开 `http://192.168.1.12:8770/`。持久地址可在“系统设置 → 网络 → 对应有线网卡 → TCP/IP”配置；主机路由仍需持久化或每次连接后重新添加。Linux / macOS 说明未在本次 Windows 实机环境中执行。

## 程序访问与代理

机器人局域网 HTTP 应直连。Python 示例禁用 HTTP 代理；`curl` 用 `--noproxy '*'`。浏览器开启全局代理时，把 `192.168.1.12` 加入绕过代理列表。

```bash
python check_cameras.py --source-address 192.168.1.201
```

指定源地址只影响验收工具自身，不修改系统路由；浏览器和 OpenCV 仍应配置主机路由。
