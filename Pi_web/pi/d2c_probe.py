#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2C(HW) 探测脚本：验证 Gemini Pro（SV1301S_U3）能否通过 ctypes 打开硬件深度-彩色对齐。

判定逻辑：
  1) 读设备属性 OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL(42) 的支持情况与当前值（只读，不改写）
  2) 用 ob_config_set_align_mode(cfg, ALIGN_D2C_HW_MODE) 开流，抓若干帧
  3) 观察深度帧分辨率：若从原生 640x400 变为与彩色一致的 640x480，说明 D2C 生效

用法（Windows 开发机，与 camera_service 同样的 SDK 路径约定）：
  set ORBBEC_SDK_LIB=C:\\Users\\jiaren\\Desktop\\K3\\P2_camro\\开发文档\\SDK\\lib
  C:\\Users\\jiaren\\Desktop\\K3\\P2_camro\\venv-cam\\Scripts\\python.exe pi\\d2c_probe.py

注意：相机独占，运行前确保 camera 服务 / Orbbec Viewer 已关闭。
"""

import ctypes
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---------------- SDK 加载（与 camera_service.py 同一套候选路径） ----------------

def _find_sdk_lib() -> str:
    if sys.platform == "win32":
        candidates = [
            os.path.join(ROOT, "开发文档", "SDK", "lib", "OrbbecSDK.dll"),
            os.path.join(ROOT, "..", "开发文档", "SDK", "lib", "OrbbecSDK.dll"),
        ]
        env = os.environ.get("ORBBEC_SDK_LIB")
        if env:
            candidates.insert(0, os.path.join(env, "OrbbecSDK.dll"))
    else:
        candidates = [
            os.path.join(ROOT, "OrbbecSDK_linux", "lib", "libOrbbecSDK.so"),
            os.path.join(ROOT, "..", "OrbbecSDK_linux", "lib", "libOrbbecSDK.so"),
        ]
        env = os.environ.get("ORBBEC_SDK_LIB")
        if env:
            candidates.insert(0, os.path.join(env, "libOrbbecSDK.so"))
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    raise RuntimeError(f"找不到 OrbbecSDK 动态库，已尝试：{candidates}")


_SDK_LIB_PATH = _find_sdk_lib()
if sys.platform == "win32":
    os.add_dll_directory(os.path.dirname(_SDK_LIB_PATH))
    _bin = os.path.join(os.path.dirname(os.path.dirname(_SDK_LIB_PATH)), "Example", "bin")
    if os.path.isdir(_bin):
        os.add_dll_directory(_bin)
ob = ctypes.CDLL(_SDK_LIB_PATH)

c_err_p = ctypes.POINTER(ctypes.c_void_p)


def _proto(name, restype, argtypes):
    f = getattr(ob, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


ob_create_pipeline = _proto("ob_create_pipeline", ctypes.c_void_p, [c_err_p])
ob_delete_pipeline = _proto("ob_delete_pipeline", None, [ctypes.c_void_p, c_err_p])
ob_pipeline_get_device = _proto("ob_pipeline_get_device", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_pipeline_start_with_config = _proto("ob_pipeline_start_with_config", None,
                                       [ctypes.c_void_p, ctypes.c_void_p, c_err_p])
ob_pipeline_stop = _proto("ob_pipeline_stop", None, [ctypes.c_void_p, c_err_p])
ob_pipeline_wait_for_frameset = _proto("ob_pipeline_wait_for_frameset", ctypes.c_void_p,
                                       [ctypes.c_void_p, ctypes.c_uint32, c_err_p])
ob_create_config = _proto("ob_create_config", ctypes.c_void_p, [c_err_p])
ob_delete_config = _proto("ob_delete_config", None, [ctypes.c_void_p, c_err_p])
ob_config_enable_video_stream = _proto("ob_config_enable_video_stream", None,
                                       [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, c_err_p])
ob_config_set_align_mode = _proto("ob_config_set_align_mode", None,
                                  [ctypes.c_void_p, ctypes.c_int, c_err_p])
ob_frameset_color_frame = _proto("ob_frameset_color_frame", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_frameset_depth_frame = _proto("ob_frameset_depth_frame", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_delete_frame = _proto("ob_delete_frame", None, [ctypes.c_void_p, c_err_p])
ob_video_frame_width = _proto("ob_video_frame_width", ctypes.c_uint32, [ctypes.c_void_p, c_err_p])
ob_video_frame_height = _proto("ob_video_frame_height", ctypes.c_uint32, [ctypes.c_void_p, c_err_p])
ob_frame_format = _proto("ob_frame_format", ctypes.c_int, [ctypes.c_void_p, c_err_p])
ob_depth_frame_get_value_scale = _proto("ob_depth_frame_get_value_scale", ctypes.c_float,
                                        [ctypes.c_void_p, c_err_p])
ob_device_is_property_supported = _proto("ob_device_is_property_supported", ctypes.c_bool,
                                         [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, c_err_p])
ob_device_get_bool_property = _proto("ob_device_get_bool_property", ctypes.c_bool,
                                     [ctypes.c_void_p, ctypes.c_int, c_err_p])
ob_error_message = _proto("ob_error_message", ctypes.c_char_p, [ctypes.c_void_p])
ob_delete_error = _proto("ob_delete_error", None, [ctypes.c_void_p])

# ObTypes.h 枚举
OB_STREAM_COLOR, OB_STREAM_DEPTH = 2, 3
OB_FORMAT_MJPG, OB_FORMAT_Y11 = 5, 11
OB_PERMISSION_READ, OB_PERMISSION_WRITE = 1, 2
OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL = 42
ALIGN_DISABLE, ALIGN_D2C_HW_MODE = 0, 1

COLOR_PROFILE = (640, 480, 30, OB_FORMAT_MJPG)
DEPTH_PROFILE = (640, 400, 30, OB_FORMAT_Y11)

report = {"sdk": _SDK_LIB_PATH, "steps": []}


def step(name, **kv):
    report["steps"].append({"step": name, **kv})
    print(f"[{name}] " + "  ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


def call(func, *args):
    err = ctypes.c_void_p(None)
    ret = func(*args, ctypes.byref(err))
    if err.value:
        msg = ob_error_message(err)
        msg = msg.decode("utf-8", "replace") if msg else "unknown error"
        ob_delete_error(err)
        raise RuntimeError(msg)
    return ret


def main() -> int:
    # ---- 步骤 1：属性 42（硬件 D2C 开关）支持情况，只读 ----
    pipe = call(ob_create_pipeline)
    try:
        dev = call(ob_pipeline_get_device, pipe)
        try:
            sup_r = call(ob_device_is_property_supported, dev,
                         OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL, OB_PERMISSION_READ)
            sup_w = call(ob_device_is_property_supported, dev,
                         OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL, OB_PERMISSION_WRITE)
            cur = None
            if sup_r:
                cur = bool(call(ob_device_get_bool_property, dev,
                                OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL))
            step("prop42", read_supported=sup_r, write_supported=sup_w, current=cur)
        except RuntimeError as exc:
            step("prop42", error=str(exc))
    finally:
        call(ob_delete_pipeline, pipe)

    # ---- 步骤 2：config 设置 ALIGN_D2C_HW_MODE 开流 ----
    pipe = call(ob_create_pipeline)
    cfg = call(ob_create_config)
    frames = {"color": [], "depth": []}
    scales = set()
    try:
        call(ob_config_enable_video_stream, cfg, OB_STREAM_COLOR, *COLOR_PROFILE)
        call(ob_config_enable_video_stream, cfg, OB_STREAM_DEPTH, *DEPTH_PROFILE)
        try:
            call(ob_config_set_align_mode, cfg, ALIGN_D2C_HW_MODE)
            step("set_align_mode", mode="ALIGN_D2C_HW_MODE", ok=True)
        except RuntimeError as exc:
            step("set_align_mode", mode="ALIGN_D2C_HW_MODE", ok=False, error=str(exc))
            report["verdict"] = "API 拒绝设置硬件 D2C，设备/固件不支持走 config 对齐"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        call(ob_pipeline_start_with_config, pipe, cfg)
        step("pipeline_start", ok=True)

        t0 = time.time()
        while time.time() - t0 < 2.0:  # 预热
            fs = call(ob_pipeline_wait_for_frameset, pipe, 1000)
            if fs:
                call(ob_delete_frame, fs)

        got = 0
        t0 = time.time()
        while got < 10 and time.time() - t0 < 5.0:
            fs = call(ob_pipeline_wait_for_frameset, pipe, 1000)
            if not fs:
                continue
            try:
                cf = call(ob_frameset_color_frame, fs)
                if cf:
                    try:
                        frames["color"].append((call(ob_video_frame_width, cf),
                                                call(ob_video_frame_height, cf)))
                    finally:
                        call(ob_delete_frame, cf)
                df = call(ob_frameset_depth_frame, fs)
                if df:
                    try:
                        frames["depth"].append((call(ob_video_frame_width, df),
                                                call(ob_video_frame_height, df)))
                        scales.add(round(call(ob_depth_frame_get_value_scale, df), 6))
                    finally:
                        call(ob_delete_frame, df)
                got += 1
            finally:
                call(ob_delete_frame, fs)
    finally:
        try:
            call(ob_pipeline_stop, pipe)
        except RuntimeError:
            pass
        call(ob_delete_pipeline, pipe)
        call(ob_delete_config, cfg)

    color_sizes = sorted(set(frames["color"]))
    depth_sizes = sorted(set(frames["depth"]))
    step("frames", color_sizes=color_sizes, depth_sizes=depth_sizes,
         depth_value_scale=sorted(scales))

    # ---- 判定 ----
    if depth_sizes and all((w, h) == (640, 480) for w, h in depth_sizes):
        verdict = ("D2C(HW) 生效：深度帧已从 640x400 变为 640x480（与彩色对齐），"
                   "可以做真彩色点云")
        ok = True
    elif depth_sizes and all((w, h) == (640, 400) for w, h in depth_sizes):
        verdict = ("align_mode 被接受但深度帧仍是 640x400：可能固件忽略了对齐设置，"
                   "或需配合 D2C 专用 profile 列表（ob_get_d2c_depth_profile_list），"
                   "建议按不支持处理，先做无色点云")
        ok = False
    else:
        verdict = f"未抓到有效深度帧（depth_sizes={depth_sizes}），无法判定"
        ok = False
    report["verdict"] = verdict
    report["d2c_hw_ok"] = ok
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
