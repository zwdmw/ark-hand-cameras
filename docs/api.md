# HTTP / Python 接入

基地址 `http://192.168.1.12:8770`，ARK 本机可用 `http://127.0.0.1:8770`。将 `{serial}` 替换为 `DA9255744` 或 `DA9255841`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 双路预览与调参网页 |
| GET | `/api/cameras` | 相机列表、状态、帧率、帧信息及缓存参数 |
| GET | `/api/status` | 与 `/api/cameras` 相同 |
| GET | `/api/cameras/{serial}/frame` | 当前帧号、时间戳、尺寸、丢包数、图像年龄 |
| GET | `/api/cameras/{serial}/parameters` | 实时读回参数、范围、步进、枚举 |
| PATCH / POST | `/api/cameras/{serial}/parameters` | 修改参数并返回实际读回值 |
| GET | `/stream/{serial}.mjpg` | MJPEG 实时视频 |
| GET | `/snapshot/{serial}.jpg` | 最新预览 JPEG |
| GET | `/snapshot/{serial}.jpg?full=1` | 当前采集分辨率 JPEG |
| POST | `/api/shutdown` | 正常停止整个服务、释放两台相机；JSON `{"stop":true}` |

## 读取画面

```python
import cv2

url = 'http://192.168.1.12:8770/stream/DA9255744.mjpg'
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
try:
    ok, bgr = cap.read()
    if not ok:
        raise RuntimeError('No camera frame received')
    print(bgr.shape)
finally:
    cap.release()
```

两路同时读取应各用独立线程，完整实现见 `client_example.py`。只读验收用 `check_cameras.py`，它仅依赖标准库，也支持 `--source-address` 指定有线 IPv4。

保存全分辨率快照：

```python
import urllib.request
from pathlib import Path

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
url = 'http://192.168.1.12:8770/snapshot/DA9255744.jpg?full=1'
with opener.open(url, timeout=15) as response:
    Path('camera-full.jpg').write_bytes(response.read())
```

## 调整参数

曝光单位 **µs**，增益 **dB**。先 GET 参数接口获取实际范围和枚举。手动修改曝光/增益会关闭对应自动模式；自动模式开启时网页禁用对应手动输入。

```python
import json
import urllib.request

base = 'http://192.168.1.12:8770'
serial = 'DA9255744'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(
    f'{base}/api/cameras/{serial}/parameters',
    data=json.dumps({'ExposureTime': 10000, 'Gain': 6}).encode(),
    method='PATCH',
    headers={'Content-Type': 'application/json'},
)
with opener.open(request, timeout=15) as response:
    print(json.load(response))
```

此例会实际修改相机参数。调参会短暂停流后恢复。无效参数返回 HTTP 400，设备拒绝更新返回 HTTP 409，并尝试恢复旧参数和取流。修改只影响当前运行状态，不保存到相机永久用户配置。

常用参数：`ExposureTime`、`Gain`、`ExposureAuto`、`GainAuto`、`BalanceWhiteAuto`、`AcquisitionFrameRate`、`Width`、`Height`、`OffsetX`、`OffsetY`、`ReverseX`、`ReverseY`。Gamma 仅在设备支持时显示。宽高与偏移是 ROI 裁剪，会改变可见范围，并非预览缩放。

## 帧信息

MJPEG 每帧有 `Content-Length`、`X-Frame-Count`。快照有 `X-Frame-Count`、JSON 编码的 `X-Frame-Metadata`。

- `frame_count`：服务采集线程累计帧数，重启后重新计数。
- `sdk_frame_number`：相机帧号。
- `device_timestamp_ticks`：相机设备时钟计数。
- `host_timestamp_ms`：SDK 主机时间戳。
- `frame_age_seconds`：服务收到最新图像后经过的秒数，用于识别旧帧。
- `lost_packets_total`：SDK 图像丢包累计计数，不等于客户端网络丢包。

每台相机仅一个 SDK 取流线程，客户端共享最新 JPEG，慢客户端会跳过旧帧。预览宽 1280，默认采集仍为 5472 × 3648；全分辨率快照是 JPEG，并非原始 Bayer 数据。两台相机无硬件同步，设备时钟未同步，不能直接相减作为同步双目依据。
