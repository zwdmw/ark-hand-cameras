"""Validate the host environment without opening or reconfiguring cameras."""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if struct.calcsize('P') != 8:
    raise RuntimeError('Install 64-bit Python to use the 64-bit MVS runtime')
import cv2
import numpy
import probe_camera

print(json.dumps({'python': sys.executable, 'opencv': cv2.__version__, 'numpy': numpy.__version__,
                  'mvs_python_path': str(probe_camera.SDK), 'mvs_runtime_path': str(probe_camera.RUNTIME)}))
