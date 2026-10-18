"""Read camera discovery and access metadata with the installed Hikrobot MVS SDK."""
import ctypes as ct
import json
import os
import sys
from pathlib import Path

SDK = Path(os.environ.get('MVS_PYTHON_PATH', r'C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport'))
RUNTIME = Path(os.environ.get('MVS_RUNTIME_PATH', r'C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64'))
if sys.platform != 'win32':
    raise RuntimeError('The camera server requires Windows and the Hikrobot MVS SDK. HTTP clients work on any OS.')
if not (SDK / 'MvCameraControl_class.py').is_file() or not RUNTIME.is_dir():
    raise RuntimeError('Install the 64-bit Hikrobot MVS SDK with Python samples, or set MVS_PYTHON_PATH and MVS_RUNTIME_PATH. See docs/server-install.md.')
os.environ['PATH'] = str(RUNTIME) + os.pathsep + os.environ.get('PATH', '')
DLL_DIRECTORY = os.add_dll_directory(str(RUNTIME))
sys.path.insert(0, str(SDK))
from MvCameraControl_class import *


def decode(array):
    data = bytes(array).split(b'\0', 1)[0]
    for encoding in ('utf-8', 'gbk', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue


def ipv4(value):
    return '.'.join(str((value >> shift) & 255) for shift in (24, 16, 8, 0))


def accessible(info, mode):
    # The native return type is C bool; the bundled wrapper uses c_uint.
    function = MvCamCtrldll.MV_CC_IsDeviceAccessible
    function.argtypes = [ct.c_void_p, ct.c_uint]
    function.restype = ct.c_bool
    return bool(function(ct.byref(info), mode))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    result = MvCamera.MV_CC_Initialize()
    if result:
        raise RuntimeError(f'MV_CC_Initialize: 0x{result:08x}')
    try:
        devices = MV_CC_DEVICE_INFO_LIST()
        mask = MV_GIGE_DEVICE | MV_USB_DEVICE | MV_GENTL_CAMERALINK_DEVICE | MV_GENTL_CXP_DEVICE | MV_GENTL_XOF_DEVICE
        result = MvCamera.MV_CC_EnumDevices(mask, devices)
        if result:
            raise RuntimeError(f'MV_CC_EnumDevices: 0x{result:08x}')
        entries = []
        for index in range(devices.nDeviceNum):
            info = ct.cast(devices.pDeviceInfo[index], ct.POINTER(MV_CC_DEVICE_INFO)).contents
            entry = {'index': index, 'transport': info.nTLayerType}
            if info.nTLayerType in (MV_GIGE_DEVICE, MV_GENTL_GIGE_DEVICE):
                detail = info.SpecialInfo.stGigEInfo
                entry.update(model=decode(detail.chModelName), serial=decode(detail.chSerialNumber),
                             manufacturer=decode(detail.chManufacturerName), name=decode(detail.chUserDefinedName),
                             ip=ipv4(detail.nCurrentIp), host_interface=ipv4(detail.nNetExport),
                             netmask=ipv4(detail.nCurrentSubNetMask))
            elif info.nTLayerType == MV_USB_DEVICE:
                detail = info.SpecialInfo.stUsb3VInfo
                entry.update(model=decode(detail.chModelName), serial=decode(detail.chSerialNumber),
                             manufacturer=decode(detail.chManufacturerName), name=decode(detail.chUserDefinedName))
            entry['access'] = {name: accessible(info, mode)
                               for name, mode in [('exclusive', MV_ACCESS_Exclusive), ('control', MV_ACCESS_Control), ('monitor', MV_ACCESS_Monitor)]}
            entries.append(entry)
        report = {'sdk_version': f'0x{MvCamera.MV_CC_GetSDKVersion():08x}', 'devices': entries}
        output = Path(__file__).resolve().parent / 'camera-discovery.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        MvCamera.MV_CC_Finalize()


if __name__ == '__main__':
    main()
