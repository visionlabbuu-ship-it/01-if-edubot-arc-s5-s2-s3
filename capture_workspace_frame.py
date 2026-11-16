import sys
from ctypes import POINTER, byref, cast, c_ubyte, memmove, memset, sizeof
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = r"D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan"
OUTPUT_PATH = Path(__file__).resolve().parent / "artifacts" / "captures" / "workspace_frame_capture.png"


def main():
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    from MvImport.MvCameraControl_class import MV_ACCESS_Exclusive, MV_CC_DEVICE_INFO, MV_CC_DEVICE_INFO_LIST, MV_FRAME_OUT, MvCamera

    device_list = MV_CC_DEVICE_INFO_LIST()
    ret = MvCamera.MV_CC_EnumDevices(0x00000004, device_list)
    if ret != 0 or device_list.nDeviceNum == 0:
        print("camera_error: no camera detected")
        return 1

    cam = MvCamera()
    device_info = cast(device_list.pDeviceInfo[0], POINTER(MV_CC_DEVICE_INFO)).contents
    cam.MV_CC_CreateHandle(device_info)
    cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
    cam.MV_CC_StopGrabbing()
    cam.MV_CC_SetEnumValue("PixelFormat", 17301512)
    cam.MV_CC_SetEnumValue("BalanceWhiteAuto", 2)
    cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
    cam.MV_CC_SetBoolValue("GammaEnable", True)
    cam.MV_CC_SetFloatValue("ExposureTime", 52000.0)
    cam.MV_CC_SetFloatValue("Gain", 10.0)
    cam.MV_CC_SetFloatValue("Gamma", 0.7)
    cam.MV_CC_SetFloatValue("AcquisitionFrameRate", 60.0)
    cam.MV_CC_SetIntValue("Sharpness", 50)
    cam.MV_CC_StartGrabbing()

    frame_out = MV_FRAME_OUT()
    memset(byref(frame_out), 0, sizeof(frame_out))

    try:
        ret = cam.MV_CC_GetImageBuffer(frame_out, 2000)
        if ret != 0:
            print(f"camera_error: frame read failed code={ret}")
            return 1

        data = (c_ubyte * frame_out.stFrameInfo.nFrameLen)()
        memmove(data, frame_out.pBufAddr, frame_out.stFrameInfo.nFrameLen)
        image = np.frombuffer(data, dtype=np.uint8)
        height = frame_out.stFrameInfo.nHeight
        width = frame_out.stFrameInfo.nWidth
        image = image.reshape((height, width))
        display_img = cv2.cvtColor(image, cv2.COLOR_BayerGB2BGR)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(OUTPUT_PATH), display_img)
        print(OUTPUT_PATH)
    finally:
        try:
            cam.MV_CC_FreeImageBuffer(frame_out)
        except Exception:
            pass
        try:
            cam.MV_CC_StopGrabbing()
        except Exception:
            pass
        try:
            cam.MV_CC_CloseDevice()
        except Exception:
            pass
        try:
            cam.MV_CC_DestroyHandle()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
