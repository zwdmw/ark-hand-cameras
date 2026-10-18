"""Verify live camera metadata, JPEG snapshots and advancing MJPEG frames (stdlib only)."""
import argparse
import http.client
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit


class Client:
    def __init__(self, base_url, source_address=None, timeout=10):
        self.url = urlsplit(base_url)
        if self.url.scheme != 'http' or not self.url.hostname or self.url.path not in ('', '/') or self.url.username:
            raise ValueError('Use the HTTP service root, e.g. http://192.168.1.12:8770')
        self.source_address = (source_address, 0) if source_address else None
        self.timeout = timeout

    def connect(self, path):
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port or 80,
                                                timeout=self.timeout, source_address=self.source_address)
        try:
            connection.request('GET', path, headers={'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f'{path}: HTTP {response.status}')
            return connection, response
        except Exception:
            connection.close()
            raise

    def get(self, path, limit=8 * 1024 * 1024):
        connection, response = self.connect(path)
        try:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise RuntimeError(f'{path}: response too large')
            return body
        finally:
            connection.close()

    def status(self):
        return json.loads(self.get('/api/cameras'))

    def stream(self, serial, frames=3):
        connection, response = self.connect(f'/stream/{serial}.mjpg')
        try:
            boundary = response.headers.get_param('boundary')
            if response.headers.get_content_type() != 'multipart/x-mixed-replace' or not boundary:
                raise RuntimeError(f'{serial}: invalid MJPEG content type')
            marker = b'--' + boundary.encode('ascii')
            sequences = []
            for _ in range(frames):
                for _ in range(32):
                    line = response.readline(4097)
                    if not line or len(line) > 4096:
                        raise RuntimeError(f'{serial}: truncated or invalid MJPEG boundary')
                    if line.strip() == marker:
                        break
                else:
                    raise RuntimeError(f'{serial}: MJPEG boundary not found')
                headers = {}
                for _ in range(32):
                    line = response.readline(4097)
                    if not line or len(line) > 4096:
                        raise RuntimeError(f'{serial}: truncated or invalid MJPEG headers')
                    if not line.strip():
                        break
                    key, value = line.decode('ascii').split(':', 1)
                    headers[key.lower()] = value.strip()
                else:
                    raise RuntimeError(f'{serial}: too many MJPEG headers')
                length = int(headers.get('content-length', '0'))
                if not 4 <= length <= 16 * 1024 * 1024:
                    raise RuntimeError(f'{serial}: invalid JPEG size')
                jpeg = response.read(length)
                validate_jpeg(jpeg)
                if len(jpeg) != length:
                    raise RuntimeError(f'{serial}: truncated JPEG')
                sequence = int(headers['x-frame-count'])
                if sequences and sequence <= sequences[-1]:
                    raise RuntimeError(f'{serial}: MJPEG frame numbers did not advance')
                sequences.append(sequence)
            return {'serial': serial, 'received_frames': len(sequences), 'sequences': sequences}
        finally:
            connection.close()


def validate_jpeg(data):
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise RuntimeError('Invalid or truncated JPEG image')


def live_cameras(status, expected, max_age):
    cameras = status.get('cameras', [])
    if status.get('service') != 'Hikrobot dual-camera server' or status.get('status') != 'running':
        raise RuntimeError('The camera service is not ready')
    if len(cameras) != expected or len({c['serial'] for c in cameras}) != expected:
        raise RuntimeError(f'Expected {expected} distinct cameras, received {len(cameras)}')
    for camera in cameras:
        age = camera.get('frame_age_seconds')
        if camera.get('status') != 'running' or not camera.get('last_frame') or age is None or not 0 <= age <= max_age:
            raise RuntimeError(f"{camera['serial']}: not live, stale or missing image")
    return cameras


def check(client, expected=2, max_age=5, save_dir=None):
    initial = live_cameras(client.status(), expected, max_age)
    if save_dir:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
    for camera in initial:
        serial = camera['serial']
        jpeg = client.get(f'/snapshot/{serial}.jpg')
        validate_jpeg(jpeg)
        if save_dir:
            if not serial.isalnum():
                raise RuntimeError('Invalid serial number for a snapshot filename')
            (save_dir / f'{serial}.jpg').write_bytes(jpeg)
    with ThreadPoolExecutor(max_workers=expected) as pool:
        streams = list(pool.map(lambda c: client.stream(c['serial']), initial))
    final = live_cameras(client.status(), expected, max_age)
    before = {c['serial']: c['frame_count'] for c in initial}
    for camera in final:
        if camera['serial'] not in before or camera['frame_count'] <= before[camera['serial']]:
            raise RuntimeError('Camera identity changed or frame counter did not advance')
    return {'status': 'passed', 'cameras': [
        {'serial': c['serial'], 'model': c['model'], 'width': c['last_frame']['width'],
         'height': c['last_frame']['height'], 'fps': c['fps'], 'lost_packets_total': c['lost_packets_total']}
        for c in final], 'streams': streams}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://192.168.1.12:8770')
    parser.add_argument('--source-address', help='Bind to the wired IPv4 when Wi-Fi shares the same subnet')
    parser.add_argument('--expected-cameras', type=int, default=2)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--save-dir', type=Path, help='Optionally save the latest preview JPEG from each camera')
    args = parser.parse_args()
    if not 1 <= args.expected_cameras <= 16 or args.timeout <= 0:
        parser.error('Use 1..16 cameras and a positive timeout')
    try:
        report = check(Client(args.base_url, args.source_address, args.timeout), args.expected_cameras, save_dir=args.save_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, http.client.HTTPException) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
