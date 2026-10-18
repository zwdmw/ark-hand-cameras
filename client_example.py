"""Read both MJPEG streams and the parameter API from another program."""
import argparse
import json
import threading
import time
import urllib.request

import cv2


def api(base_url, path, body=None):
    request = urllib.request.Request(
        base_url.rstrip('/') + path,
        data=json.dumps(body).encode('utf-8') if body is not None else None,
        method='PATCH' if body is not None else 'GET',
        headers={'Content-Type': 'application/json'} if body is not None else {},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def set_parameters(base_url, serial, values):
    """Example values: {'ExposureTime': 6000, 'Gain': 12}. Values use SDK units."""
    return api(base_url, '/api/cameras/' + serial + '/parameters', values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://192.168.1.12:8770')
    parser.add_argument('--seconds', type=float, default=0, help='0 runs until Q or Ctrl+C')
    parser.add_argument('--no-gui', action='store_true')
    args = parser.parse_args()
    cameras = api(args.base_url, '/api/cameras')['cameras']
    print(json.dumps({'cameras': [c['serial'] for c in cameras]}), flush=True)
    stop = threading.Event()
    lock = threading.Lock()
    latest, counts, errors = {}, {}, {}
    cv2.setNumThreads(2)

    def read_camera(serial):
        stream = cv2.VideoCapture(args.base_url.rstrip('/') + '/stream/' + serial + '.mjpg', cv2.CAP_FFMPEG)
        try:
            if not stream.isOpened():
                raise RuntimeError('Cannot open MJPEG stream for ' + serial)
            while not stop.is_set():
                ok, image = stream.read()
                if not ok:
                    raise RuntimeError('Video stream ended: ' + serial)
                with lock:
                    latest[serial] = image
                    counts[serial] = counts.get(serial, 0) + 1
        except Exception as error:
            with lock:
                errors[serial] = str(error)
        finally:
            stream.release()

    workers = [threading.Thread(target=read_camera, args=(c['serial'],), daemon=True) for c in cameras]
    for worker in workers:
        worker.start()
    before = time.monotonic()
    try:
        while not args.seconds or time.monotonic() - before < args.seconds:
            with lock:
                images = dict(latest)
                failures = dict(errors)
            if failures:
                raise RuntimeError(str(failures))
            if not args.no_gui:
                for serial, image in images.items():
                    cv2.imshow(serial, cv2.resize(image, (960, round(image.shape[0] * 960 / image.shape[1]))))
                if cv2.waitKey(10) & 255 == ord('q'):
                    break
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=3)
        if not args.no_gui:
            cv2.destroyAllWindows()
    if any(counts.get(c['serial'], 0) == 0 for c in cameras):
        raise RuntimeError('At least one camera delivered no frames.')
    print(json.dumps({'received_frames': counts, 'image_sizes': {serial: list(img.shape) for serial, img in latest.items()}}), flush=True)


if __name__ == '__main__':
    main()
