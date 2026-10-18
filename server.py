"""Hikrobot dual-camera HTTP/MJPEG server. SDK access belongs to each worker."""
import argparse
import ctypes as ct
import json
import logging
import math
import queue
import signal
import threading
import time
from collections import deque
from concurrent.futures import Future, TimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from probe_camera import *
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
STOP = threading.Event()
cv2.setNumThreads(2)
WRITABLE = {
    'ExposureTime': 'float', 'Gain': 'float', 'AcquisitionFrameRate': 'float',
    'Gamma': 'float', 'ExposureAuto': 'enum', 'GainAuto': 'enum',
    'BalanceWhiteAuto': 'enum', 'AcquisitionFrameRateEnable': 'bool',
    'GammaEnable': 'bool', 'ReverseX': 'bool', 'ReverseY': 'bool',
    'Width': 'int', 'Height': 'int', 'OffsetX': 'int', 'OffsetY': 'int',
}
READONLY = {'SensorWidth': 'int', 'SensorHeight': 'int', 'PayloadSize': 'int',
            'ResultingFrameRate': 'float', 'DeviceTemperature': 'float',
            'PixelFormat': 'enum', 'TriggerMode': 'enum', 'TriggerSource': 'enum'}
GEOMETRY = {'Width', 'Height', 'OffsetX', 'OffsetY'}


class CameraError(RuntimeError):
    pass


def checked(code, operation):
    if code:
        message = f'{operation}: 0x{code:08x}'
        if code == 0x80000203:
            message += ' — close this device connection in MVS/MvToolkit before using the server'
        raise CameraError(message)


def parameters(camera):
    result = {}
    for name, kind in {**WRITABLE, **READONLY}.items():
        entry = {'type': kind, 'writable': name in WRITABLE}
        if kind == 'int':
            value = MVCC_INTVALUE_EX()
            code = camera.MV_CC_GetIntValueEx(name, value)
            entry.update(value=int(value.nCurValue), min=int(value.nMin), max=int(value.nMax), step=max(1, int(value.nInc)))
        elif kind == 'float':
            value = MVCC_FLOATVALUE()
            code = camera.MV_CC_GetFloatValue(name, value)
            entry.update(value=float(value.fCurValue), min=float(value.fMin), max=float(value.fMax))
        elif kind == 'bool':
            value = ct.c_bool()
            code = camera.MV_CC_GetBoolValue(name, value)
            entry['value'] = bool(value.value)
        else:
            value = MVCC_ENUMVALUE()
            code = camera.MV_CC_GetEnumValue(name, value)
            options = []
            supported = getattr(value, 'nSupportValue', [])
            for number in list(supported)[:int(value.nSupportedNum)]:
                symbolic = MVCC_ENUMENTRY()
                symbolic.nValue = number
                enum_code = camera.MV_CC_GetEnumEntrySymbolic(name, symbolic)
                options.append({'value': int(number), 'label': decode(symbolic.chSymbolic) if not enum_code else str(number)})
            entry.update(value=int(value.nCurValue), options=options)
            entry['symbol'] = next((o['label'] for o in options if o['value'] == entry['value']), str(entry['value']))
        if code == 0:
            result[name] = entry
    return result


def set_node(camera, name, value):
    kind = WRITABLE[name]
    method = {'float': camera.MV_CC_SetFloatValue, 'int': camera.MV_CC_SetIntValueEx,
              'enum': camera.MV_CC_SetEnumValue, 'bool': camera.MV_CC_SetBoolValue}[kind]
    checked(method(name, value), 'Set ' + name)


def validate_patch(schema, patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError('Supply a nonempty JSON object of camera parameters.')
    validated = {}
    for name, value in patch.items():
        if name not in WRITABLE:
            raise ValueError(f'{name} is unknown or read-only.')
        if name not in schema:
            raise ValueError(f'{name} is not supported by this camera.')
        entry, kind = schema[name], WRITABLE[name]
        if kind == 'bool':
            if not isinstance(value, bool):
                raise ValueError(f'{name} must be true or false.')
        elif kind == 'enum':
            options = entry['options']
            if isinstance(value, str):
                matched = next((o for o in options if o['label'] == value), None)
                if matched is None:
                    raise ValueError(f'{name}: unsupported option {value}.')
                value = matched['value']
            if isinstance(value, bool) or not isinstance(value, int) or value not in [o['value'] for o in options]:
                raise ValueError(f'{name}: unsupported enum value.')
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number.')
            if kind == 'int':
                if value != int(value):
                    raise ValueError(f'{name} must be an integer.')
                value = int(value)
            # Geometry upper limits are checked against the final combined ROI.
            maximum = entry['max']
            if name in GEOMETRY:
                sensor = 'SensorWidth' if name in ('Width', 'OffsetX') else 'SensorHeight'
                maximum = schema.get(sensor, {}).get('value', maximum)
            if value < entry['min'] or value > maximum:
                raise ValueError(f'{name} must be in [{entry["min"]}, {maximum}].')
            if kind == 'int' and (value - entry['min']) % entry['step']:
                raise ValueError(f'{name} must increment by {entry["step"]} from {entry["min"]}.')
        validated[name] = value
    for manual, automatic in [('ExposureTime', 'ExposureAuto'), ('Gain', 'GainAuto')]:
        if manual in validated and automatic in schema:
            if validated.get(automatic, 0) != 0:
                raise ValueError(f'Set {automatic}=Off to edit {manual}.')
            validated.setdefault(automatic, 0)
    if 'AcquisitionFrameRate' in validated and 'AcquisitionFrameRateEnable' in schema:
        if validated.get('AcquisitionFrameRateEnable', True) is False:
            raise ValueError('Enable acquisition frame rate control to edit the frame rate.')
        validated.setdefault('AcquisitionFrameRateEnable', True)
    if GEOMETRY.intersection(validated):
        combined = {name: validated.get(name, schema[name]['value']) for name in GEOMETRY}
        for dimension, offset, sensor in [('Width', 'OffsetX', 'SensorWidth'), ('Height', 'OffsetY', 'SensorHeight')]:
            if combined[dimension] + combined[offset] > schema[sensor]['value']:
                raise ValueError(f'{dimension} + {offset} exceeds {sensor}.')
    return validated


class CameraWorker:
    def __init__(self, info, preview_width, quality):
        # Copy SDK enumeration memory before it can be reused by another enumeration.
        self.info = MV_CC_DEVICE_INFO.from_buffer_copy(ct.string_at(ct.byref(info), ct.sizeof(info)))
        detail = info.SpecialInfo.stGigEInfo
        self.serial = decode(detail.chSerialNumber)
        self.identity = {'serial': self.serial, 'model': decode(detail.chModelName),
                         'ip': ipv4(detail.nCurrentIp), 'host_interface': ipv4(detail.nNetExport)}
        self.preview_width, self.quality = preview_width, quality
        self.condition = threading.Condition()
        self.commands = queue.Queue(maxsize=16)
        self.state = {**self.identity, 'status': 'connecting', 'frame_count': 0, 'fps': 0,
                      'lost_packets_total': 0, 'last_frame': None, 'parameters': {}, 'error': None}
        self.jpeg, self.full_image, self.sequence = None, None, 0
        self.times = deque(maxlen=25)
        self.thread = threading.Thread(target=self.run, name='Camera-' + self.serial, daemon=True)
        self.camera = None
        self.grabbing = False

    def snapshot(self):
        with self.condition:
            value = dict(self.state)
            last = value['last_frame']
            value['frame_age_seconds'] = time.time() - last['received_at'] if last else None
            return value

    def command(self, operation, payload=None):
        if self.snapshot()['status'] not in ('running', 'waiting_for_frame'):
            raise CameraError('Camera is not connected: ' + (self.snapshot()['error'] or self.serial))
        future = Future()
        try:
            self.commands.put_nowait((future, operation, payload))
        except queue.Full:
            raise CameraError('Camera command queue is full; retry shortly.')
        try:
            return future.result(timeout=12)
        except TimeoutError:
            future.cancel()
            raise CameraError('Camera command timed out.')

    def update_state(self, **changes):
        with self.condition:
            self.state.update(changes)
            self.condition.notify_all()

    def apply_patch(self, payload):
        before = parameters(self.camera)
        patch = validate_patch(before, payload)
        original = {name: before[name]['value'] for name in patch}
        if GEOMETRY.intersection(patch):
            original.update({name: before[name]['value'] for name in GEOMETRY})
        checked(self.camera.MV_CC_StopGrabbing(), 'Stop grabbing for parameter update')
        self.grabbing = False
        try:
            self.write_values(patch, before)
            checked(self.camera.MV_CC_StartGrabbing(), 'Restart grabbing')
            self.grabbing = True
        except Exception as error:
            try:
                self.write_values(original, before)
                checked(self.camera.MV_CC_StartGrabbing(), 'Restart after rollback')
                self.grabbing = True
            except Exception as rollback:
                raise CameraError(f'Update failed: {error}; rollback failed: {rollback}') from rollback
            raise CameraError(f'Update failed and previous values were restored: {error}') from error
        schema = parameters(self.camera)
        with self.condition:
            self.jpeg, self.full_image = None, None
            self.times.clear()
            self.state.update(parameters=schema, last_frame=None, status='waiting_for_frame', fps=0)
        logging.info('Camera %s updated %s', self.serial, patch)
        return {'serial': self.serial, 'applied': patch, 'parameters': schema}

    def write_values(self, values, previous):
        geometry = GEOMETRY.intersection(values)
        if geometry:
            target = {name: values.get(name, previous[name]['value']) for name in GEOMETRY}
            set_node(self.camera, 'OffsetX', 0)
            set_node(self.camera, 'OffsetY', 0)
            for name in ('Width', 'Height', 'OffsetX', 'OffsetY'):
                set_node(self.camera, name, target[name])
        # Disable auto loops before writing manual values; enable them last.
        autos = {'ExposureAuto', 'GainAuto', 'BalanceWhiteAuto'}
        for name in ('ExposureAuto', 'GainAuto'):
            if ('ExposureTime' if name == 'ExposureAuto' else 'Gain') in values and name in previous:
                set_node(self.camera, name, 0)
        if 'AcquisitionFrameRateEnable' in values:
            set_node(self.camera, 'AcquisitionFrameRateEnable', values['AcquisitionFrameRateEnable'])
        for name, value in values.items():
            if name not in GEOMETRY | autos | {'AcquisitionFrameRateEnable'}:
                set_node(self.camera, name, value)
        for name in autos:
            if name in values:
                set_node(self.camera, name, values[name])

    def process_commands(self):
        for _ in range(16):
            try:
                future, operation, payload = self.commands.get_nowait()
            except queue.Empty:
                break
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if operation == 'parameters':
                    result = parameters(self.camera)
                    self.update_state(parameters=result)
                else:
                    result = self.apply_patch(payload)
                future.set_result(result)
            except Exception as error:
                future.set_exception(error)
                if not self.grabbing:
                    raise

    def run(self):
        while not STOP.is_set():
            original_trigger = None
            opened = created = False
            try:
                self.camera = MvCamera()
                checked(self.camera.MV_CC_CreateHandle(self.info), 'Create handle')
                created = True
                checked(self.camera.MV_CC_OpenDevice(MV_ACCESS_Control, 0), 'Open camera')
                opened = True
                schema = parameters(self.camera)
                original_trigger = schema.get('TriggerMode', {}).get('value')
                if original_trigger != 0:
                    checked(self.camera.MV_CC_SetEnumValue('TriggerMode', 0), 'Enable continuous acquisition')
                self.camera.MV_CC_SetImageNodeNum(3)
                self.camera.MV_CC_SetGrabStrategy(1)  # LatestImagesOnly
                checked(self.camera.MV_CC_StartGrabbing(), 'Start grabbing')
                self.grabbing = True
                self.update_state(status='waiting_for_frame', error=None, parameters=parameters(self.camera),
                                  original_trigger_mode=original_trigger)
                last_frame_time = time.monotonic()
                while not STOP.is_set():
                    self.process_commands()
                    frame = MV_FRAME_OUT()
                    result = self.camera.MV_CC_GetImageBuffer(frame, 700)
                    if result:
                        if result not in (0x80000007, 0x8000000b):
                            checked(result, 'Get image buffer')
                        if time.monotonic() - last_frame_time > 3:
                            self.update_state(status='waiting_for_frame', error='No frame received for more than 3 seconds.')
                        if time.monotonic() - last_frame_time > 10:
                            raise CameraError('Camera stopped delivering frames; reconnecting.')
                        continue
                    try:
                        meta = frame.stFrameInfo
                        width, height = int(meta.nExtendWidth or meta.nWidth), int(meta.nExtendHeight or meta.nHeight)
                        if not frame.pBufAddr or width <= 0 or height <= 0:
                            raise CameraError('Invalid SDK frame buffer.')
                        # Converted arrays are immutable after publication, so snapshot clients share them safely.
                        bgr = np.empty((height, width, 3), dtype=np.uint8)
                        conversion = MV_CC_PIXEL_CONVERT_PARAM()
                        conversion.nWidth, conversion.nHeight = width, height
                        conversion.enSrcPixelType, conversion.pSrcData = meta.enPixelType, frame.pBufAddr
                        conversion.nSrcDataLen = int(meta.nFrameLenEx or meta.nFrameLen)
                        conversion.enDstPixelType = PixelType_Gvsp_BGR8_Packed
                        conversion.pDstBuffer = bgr.ctypes.data_as(ct.POINTER(ct.c_ubyte))
                        conversion.nDstBufferSize = bgr.nbytes
                        checked(self.camera.MV_CC_ConvertPixelType(conversion), 'Convert image to BGR')
                        timestamp = (int(meta.nDevTimeStampHigh) << 32) | int(meta.nDevTimeStampLow)
                        record = {'width': width, 'height': height, 'sdk_frame_number': int(meta.nFrameNum),
                                  'pixel_format_code': int(meta.enPixelType), 'raw_bytes': int(meta.nFrameLenEx or meta.nFrameLen),
                                  'device_timestamp_ticks': timestamp, 'host_timestamp_ms': int(meta.nHostTimeStamp),
                                  'lost_packets': int(meta.nLostPacket), 'received_at': time.time()}
                        record['offset_x'] = int(self.state['parameters'].get('OffsetX', {}).get('value', 0))
                        record['offset_y'] = int(self.state['parameters'].get('OffsetY', {}).get('value', 0))
                        record['reverse_x'] = bool(self.state['parameters'].get('ReverseX', {}).get('value', False))
                        record['reverse_y'] = bool(self.state['parameters'].get('ReverseY', {}).get('value', False))
                        record['configured_exposure_us'] = float(self.state['parameters'].get('ExposureTime', {}).get('value', 0))
                    finally:
                        checked(self.camera.MV_CC_FreeImageBuffer(frame), 'Free image buffer')
                    preview_width = min(self.preview_width, width)
                    preview = cv2.resize(bgr, (preview_width, max(1, round(height * preview_width / width))), interpolation=cv2.INTER_AREA)
                    ok, encoded = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                    if not ok:
                        raise CameraError('JPEG encoding failed.')
                    last_frame_time = time.monotonic()
                    self.times.append(last_frame_time)
                    fps = (len(self.times) - 1) / (self.times[-1] - self.times[0]) if len(self.times) > 1 else 0
                    record.update(preview_width=preview.shape[1], preview_height=preview.shape[0])
                    with self.condition:
                        self.jpeg, self.full_image = encoded.tobytes(), bgr
                        self.sequence += 1
                        self.state.update(status='running', error=None, last_frame=record,
                                          frame_count=self.sequence, fps=fps,
                                          lost_packets_total=self.state['lost_packets_total'] + record['lost_packets'])
                        self.condition.notify_all()
            except Exception as error:
                logging.exception('Camera %s failed', self.serial)
                with self.condition:
                    self.jpeg = self.full_image = None
                    self.state.update(status='error', error=str(error), last_frame=None, fps=0)
                    self.condition.notify_all()
            finally:
                if self.grabbing:
                    self.camera.MV_CC_StopGrabbing()
                    self.grabbing = False
                if opened:
                    if original_trigger is not None:
                        code = self.camera.MV_CC_SetEnumValue('TriggerMode', original_trigger)
                        logging.info('Camera %s trigger restore result 0x%08x', self.serial, code)
                    self.camera.MV_CC_CloseDevice()
                if created:
                    self.camera.MV_CC_DestroyHandle()
                while not self.commands.empty():
                    future, _, _ = self.commands.get_nowait()
                    if not future.done():
                        future.set_exception(CameraError('Camera disconnected.'))
            if not STOP.is_set():
                STOP.wait(3)
        self.update_state(status='stopped')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *arguments):
        if self.command not in ('GET', 'OPTIONS'):
            logging.info('%s %s', self.client_address[0], fmt % arguments)

    def send(self, code, payload, mime='application/json; charset=utf-8', headers=None):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        self.wfile.write(payload)

    def route(self):
        path = urlparse(self.path)
        return path.path.strip('/').split('/'), parse_qs(path.query)

    def camera(self, serial):
        camera = self.server.cameras.get(serial)
        if camera is None:
            raise KeyError('Unknown camera serial: ' + serial)
        return camera

    def do_GET(self):
        try:
            parts, query = self.route()
            if parts == ['']:
                self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif parts in (['api', 'cameras'], ['api', 'status']):
                cameras = [camera.snapshot() for camera in self.server.cameras.values()]
                self.send(200, {'service': 'Hikrobot dual-camera server', 'status': 'running' if cameras and all(c['status'] == 'running' for c in cameras) else 'starting',
                                'cameras': cameras, 'stream_format': 'MJPEG', 'sdk_version': self.server.sdk_version})
            elif len(parts) == 4 and parts[:2] == ['api', 'cameras']:
                camera = self.camera(parts[2])
                if parts[3] == 'parameters':
                    self.send(200, {'serial': camera.serial, 'parameters': camera.command('parameters')})
                elif parts[3] == 'frame':
                    self.send(200, camera.snapshot())
                else:
                    self.send(404, {'error': 'Endpoint not found.'})
            elif len(parts) == 2 and parts[0] == 'stream' and parts[1].endswith('.mjpg'):
                self.stream(self.camera(parts[1][:-5]))
            elif len(parts) == 2 and parts[0] == 'snapshot' and parts[1].endswith('.jpg'):
                camera = self.camera(parts[1][:-4])
                with camera.condition:
                    jpeg, full, sequence = camera.jpeg, camera.full_image, camera.sequence
                    frame_metadata = dict(camera.state['last_frame']) if camera.state['last_frame'] else None
                if full is None or jpeg is None:
                    raise CameraError('Frame is not ready.')
                if query.get('full', ['0'])[0] == '1':
                    ok, encoded = cv2.imencode('.jpg', full, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    if not ok:
                        raise CameraError('Could not encode the full image.')
                    jpeg = encoded.tobytes()
                self.send(200, jpeg, 'image/jpeg', {'X-Frame-Count': sequence,
                          'X-Frame-Metadata': json.dumps(frame_metadata, separators=(',', ':'))})
            elif parts == ['favicon.ico']:
                self.send(204, b'', 'image/x-icon')
            else:
                self.send(404, {'error': 'Endpoint not found.'})
        except (ConnectionError, TimeoutError):
            pass
        except KeyError as error:
            self.send(404, {'error': str(error)})
        except (CameraError, ValueError) as error:
            self.send(503, {'error': str(error)})
        except Exception:
            logging.exception('GET failed')
            self.send(500, {'error': 'Server error; see server.log.'})

    def stream(self, camera):
        with camera.condition:
            if camera.jpeg is None:
                raise CameraError('Frame is not ready; retry when the camera is running.')
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.connection.settimeout(8)
        last = -1
        while not STOP.is_set():
            with camera.condition:
                camera.condition.wait_for(lambda: STOP.is_set() or camera.sequence != last or camera.state['status'] == 'error', timeout=2)
                if STOP.is_set() or camera.state['status'] == 'error':
                    return
                jpeg, sequence = camera.jpeg, camera.sequence
            if not jpeg or sequence == last:
                continue
            last = sequence
            header = f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Frame-Count: {sequence}\r\n\r\n'
            self.wfile.write(header.encode('ascii') + jpeg + b'\r\n')
            self.wfile.flush()

    def do_PATCH(self):
        self.change()

    def do_POST(self):
        self.change()

    def change(self):
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Content-Type must be application/json.')
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 8192:
                raise ValueError('JSON body size must be between 1 and 8192 bytes.')
            payload = json.loads(self.rfile.read(length))
            parts, _ = self.route()
            if parts == ['api', 'shutdown'] and self.command == 'POST':
                if payload != {'stop': True}:
                    raise ValueError('Supply {"stop":true}.')
                self.send(200, {'status': 'stopping'})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif len(parts) == 4 and parts[:2] == ['api', 'cameras'] and parts[3] == 'parameters':
                camera = self.camera(parts[2])
                self.send(200, camera.command('patch', payload))
            else:
                self.send(404, {'error': 'Endpoint not found.'})
        except KeyError as error:
            self.send(404, {'error': str(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self.send(400, {'error': str(error)})
        except CameraError as error:
            self.send(409, {'error': str(error)})
        except (ConnectionError, TimeoutError):
            pass
        except Exception:
            logging.exception('Parameter request failed')
            self.send(500, {'error': 'Parameter update failed; see server.log.'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--preview-width', type=int, default=1280)
    parser.add_argument('--jpeg-quality', type=int, default=85)
    parser.add_argument('--serial', action='append')
    args = parser.parse_args()
    if not 160 <= args.preview_width <= 3840 or not 30 <= args.jpeg_quality <= 100:
        parser.error('Invalid preview width or JPEG quality.')
    logging.basicConfig(filename=ROOT / 'server.log', level=logging.INFO,
                        format='%(asctime)s %(threadName)s %(levelname)s %(message)s')
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    checked(MvCamera.MV_CC_Initialize(), 'Initialize SDK')
    server.cameras = {}
    server.sdk_version = f'0x{MvCamera.MV_CC_GetSDKVersion():08x}'
    try:
        devices = MV_CC_DEVICE_INFO_LIST()
        checked(MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE, devices), 'Enumerate cameras')
        if not devices.nDeviceNum:
            raise CameraError('No GigE cameras discovered; retry after the network adapters are ready.')
        for index in range(devices.nDeviceNum):
            info = ct.cast(devices.pDeviceInfo[index], ct.POINTER(MV_CC_DEVICE_INFO)).contents
            serial = decode(info.SpecialInfo.stGigEInfo.chSerialNumber)
            if args.serial and serial not in args.serial:
                continue
            worker = CameraWorker(info, args.preview_width, args.jpeg_quality)
            server.cameras[serial] = worker
            worker.thread.start()
        logging.info('Server listening on %s:%s; cameras %s', args.host, args.port, list(server.cameras))
        def shutdown(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        server.serve_forever(poll_interval=0.2)
    finally:
        STOP.set()
        for worker in server.cameras.values():
            with worker.condition:
                worker.condition.notify_all()
        for worker in server.cameras.values():
            worker.thread.join(timeout=15)
        server.server_close()
        MvCamera.MV_CC_Finalize()
        logging.info('Server stopped; camera handles released.')


if __name__ == '__main__':
    main()
