"""Protocol integration tests use an HTTP fixture, never the robot or its settings."""
import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from check_cameras import Client, check

JPEG = b'\xff\xd8fixture\xff\xd9'


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        state = self.server.fixture
        if state.get('http_error'):
            self.send_error(503)
            return
        if self.path == '/api/cameras':
            state['status_requests'] = state.get('status_requests', 0) + 1
            cameras = [{'serial': serial, 'model': 'fixture', 'status': 'running',
                        'frame_count': state['status_requests'] * 10, 'fps': 5,
                        'frame_age_seconds': state.get('age', 0.1), 'lost_packets_total': 0,
                        'last_frame': {'width': 1280, 'height': 853}}
                       for serial in state.get('serials', ['CAM1', 'CAM2'])]
            body = json.dumps({'service': 'Hikrobot dual-camera server', 'status': 'running', 'cameras': cameras}).encode()
            mime = 'application/json'
        elif self.path.startswith('/snapshot/'):
            body = b'invalid' if state.get('bad_snapshot') else JPEG
            mime = 'image/jpeg'
        elif self.path.startswith('/stream/'):
            frames = []
            for number in (1, 1, 1) if state.get('repeat_frames') else (1, 2, 3):
                frames.append(f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(JPEG)}\r\nX-Frame-Count: {number}\r\n\r\n'.encode() + JPEG + b'\r\n')
            body = b''.join(frames)
            if state.get('truncated'):
                body = body[:35]
            mime = 'multipart/x-mixed-replace; boundary=frame'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CameraProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        self.server.daemon_threads = True
        self.server.fixture = {}
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.client = Client(f'http://127.0.0.1:{self.server.server_port}', source_address='127.0.0.1', timeout=2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_two_concurrent_streams_and_source_bound_http(self):
        result = check(self.client)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([s['sequences'] for s in result['streams']], [[1, 2, 3], [1, 2, 3]])

    def test_stale_frames_are_rejected(self):
        self.server.fixture['age'] = 30
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            check(self.client)

    def test_missing_second_camera_is_rejected(self):
        self.server.fixture['serials'] = ['CAM1']
        with self.assertRaisesRegex(RuntimeError, 'distinct cameras'):
            check(self.client)

    def test_duplicate_camera_identity_is_rejected(self):
        self.server.fixture['serials'] = ['CAM1', 'CAM1']
        with self.assertRaisesRegex(RuntimeError, 'distinct cameras'):
            check(self.client)

    def test_repeated_mjpeg_frames_are_rejected(self):
        self.server.fixture['repeat_frames'] = True
        with self.assertRaisesRegex(RuntimeError, 'did not advance'):
            check(self.client)

    def test_truncated_mjpeg_does_not_hang(self):
        self.server.fixture['truncated'] = True
        with self.assertRaisesRegex(RuntimeError, 'truncated'):
            check(self.client)

    def test_invalid_snapshot_is_rejected(self):
        self.server.fixture['bad_snapshot'] = True
        with self.assertRaisesRegex(RuntimeError, 'JPEG'):
            check(self.client)

    def test_http_error_is_reported(self):
        self.server.fixture['http_error'] = True
        with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
            check(self.client)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell client')
    def test_powershell_client_accepts_live_advancing_cameras(self):
        result = self.run_powershell_client()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS', result.stdout)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell client')
    def test_powershell_client_rejects_stale_cameras(self):
        self.server.fixture['age'] = 30
        result = self.run_powershell_client()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('stale', result.stderr)

    def run_powershell_client(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'Test-Cameras.ps1'
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(script), '-BaseUrl', f'http://127.0.0.1:{self.server.server_port}'],
                              capture_output=True, text=True, timeout=20)


if __name__ == '__main__':
    unittest.main()
