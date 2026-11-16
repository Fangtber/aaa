#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬墙机器人相机服务：Gemini Pro（WHEELTEC × Orbbec，SV1301S_U3）四路画面 + 拍照。

与 robot_control_service.py（8008，风机/敲击）相互独立：
本服务只管相机，崩溃/重启不影响电机安全控制。

取流方式：ctypes 直连 OrbbecSDK v1 C API（该设备是 OpenNI2 老设备，SDK v2 不支持）。
实测三流并发（USB2.0 下即跑满 30fps）：
  color  640x480 MJPG   —— MJPG 原始字节即 JPEG，直接转发，免解码
  depth  640x400 Y11    —— SDK 默认解包成 Y16 交付，× value_scale = 毫米
  ir     640x400 Y10    —— SDK 默认解包成 Y16 交付（主动结构光散斑画面）
  cloud  点云预览画面    —— 由深度帧实时渲染（降采样+绕竖轴缓转），仅在有网页观众时渲染
注意：此设备不支持 frame sync，RGB/深度/红外三路之间有最大约 33ms 时间差。
D2C(HW)：开流时默认尝试硬件深度-彩色对齐（d2c_probe.py 实测本机支持），
对齐后深度帧变为 640x480 并与彩色逐像素对应，点云画面与导出 PLY 均为真彩色。

HTTP API（默认 0.0.0.0:8009，CORS 放开，仅供热点内网使用）：
  GET  /status                相机在线状态、各路 fps、D2C/内参、已保存对数
  GET  /stream?src=color|depth|ir|cloud   MJPEG 实时流（前端 <img> 直接显示）
  POST /snapshot              保存当前帧；可选 JSON body {"srcs": [...]} 只保存指定画面
  POST /safe-stop             安全停止：顺带 systemctl 停掉 robot-web，随后本服务 os._exit(0) 退出
  POST /shutdown-pi           关机树莓派：响应送达后执行 sudo poweroff，整台系统安全关机
  GET  /captures              已保存图像对列表（新的在前）
  GET  /captures/<文件名>     下载已保存的图像/点云文件（白名单校验）

部署：SDK 库放在脚本同级的 OrbbecSDK_linux/lib/（或上一级目录），
Windows 开发机则放 开发文档/SDK/lib/；详见 树莓派部署清单.md。
"""

import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(ROOT, "captures")

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8009

JPEG_QUALITY = 80
RETRY_INTERVAL_S = 2.0   # 相机掉线后的重试间隔
WARMUP_S = 2.0           # 开流后预热，丢自动曝光未稳定的帧
STREAM_TIMEOUT_MS = 1000

START_TIME = time.monotonic()


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------- SDK 加载（Windows / Linux 自动选择） ----------------

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
    # Windows 版 SDK 的部分依赖在 Example/bin
    _bin = os.path.join(os.path.dirname(os.path.dirname(_SDK_LIB_PATH)), "Example", "bin")
    if os.path.isdir(_bin):
        os.add_dll_directory(_bin)
ob = ctypes.CDLL(_SDK_LIB_PATH)

# ---------------- SDK 结构体（ObTypes.h，#pragma pack(1) 一字节对齐） ----------------


class OBCameraIntrinsic(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("fx", ctypes.c_float), ("fy", ctypes.c_float),
                ("cx", ctypes.c_float), ("cy", ctypes.c_float),
                ("width", ctypes.c_int16), ("height", ctypes.c_int16)]


class OBCameraDistortion(ctypes.Structure):
    _pack_ = 1
    _fields_ = [(k, ctypes.c_float) for k in
                ("k1", "k2", "k3", "k4", "k5", "k6", "p1", "p2")]


class OBD2CTransform(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("rot", ctypes.c_float * 9), ("trans", ctypes.c_float * 3)]


class OBCameraParam(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("depthIntrinsic", OBCameraIntrinsic),
                ("rgbIntrinsic", OBCameraIntrinsic),
                ("depthDistortion", OBCameraDistortion),
                ("rgbDistortion", OBCameraDistortion),
                ("transform", OBD2CTransform),
                ("isMirrored", ctypes.c_bool)]


# ---------------- C API 原型（用到的最小子集） ----------------

c_err_p = ctypes.POINTER(ctypes.c_void_p)


def _proto(name, restype, argtypes):
    f = getattr(ob, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


ob_create_pipeline = _proto("ob_create_pipeline", ctypes.c_void_p, [c_err_p])
ob_delete_pipeline = _proto("ob_delete_pipeline", None, [ctypes.c_void_p, c_err_p])
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
ob_frameset_color_frame = _proto("ob_frameset_color_frame", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_frameset_depth_frame = _proto("ob_frameset_depth_frame", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_frameset_ir_frame = _proto("ob_frameset_ir_frame", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_delete_frame = _proto("ob_delete_frame", None, [ctypes.c_void_p, c_err_p])
ob_frame_data = _proto("ob_frame_data", ctypes.c_void_p, [ctypes.c_void_p, c_err_p])
ob_frame_data_size = _proto("ob_frame_data_size", ctypes.c_uint32, [ctypes.c_void_p, c_err_p])
ob_frame_format = _proto("ob_frame_format", ctypes.c_int, [ctypes.c_void_p, c_err_p])
ob_video_frame_width = _proto("ob_video_frame_width", ctypes.c_uint32, [ctypes.c_void_p, c_err_p])
ob_video_frame_height = _proto("ob_video_frame_height", ctypes.c_uint32, [ctypes.c_void_p, c_err_p])
ob_depth_frame_get_value_scale = _proto("ob_depth_frame_get_value_scale", ctypes.c_float,
                                        [ctypes.c_void_p, c_err_p])
ob_config_set_align_mode = _proto("ob_config_set_align_mode", None,
                                  [ctypes.c_void_p, ctypes.c_int, c_err_p])
ob_pipeline_get_camera_param = _proto("ob_pipeline_get_camera_param", OBCameraParam,
                                      [ctypes.c_void_p, c_err_p])
ob_error_message = _proto("ob_error_message", ctypes.c_char_p, [ctypes.c_void_p])
ob_delete_error = _proto("ob_delete_error", None, [ctypes.c_void_p])

# ObTypes.h 枚举值
OB_STREAM_IR, OB_STREAM_COLOR, OB_STREAM_DEPTH = 1, 2, 3
OB_FORMAT_MJPG, OB_FORMAT_Y16, OB_FORMAT_Y8 = 5, 8, 9
OB_FORMAT_Y10, OB_FORMAT_Y11 = 10, 11
ALIGN_D2C_HW_MODE = 1  # 硬件深度-彩色对齐（d2c_probe.py 已实测本机可用）

# 实测 profile（见 enum_profiles.py 输出）：深度原生 Y11、红外原生 Y10，
# SDK 默认都解包成 Y16 交付
COLOR_PROFILE = (640, 480, 30, OB_FORMAT_MJPG)
DEPTH_PROFILE = (640, 400, 30, OB_FORMAT_Y11)
IR_PROFILE = (640, 400, 30, OB_FORMAT_Y10)

# 深度伪彩映射的固定量程（mm）：固定映射保证画面亮度不随场景跳动
DEPTH_VIS_MIN_MM = 300.0
DEPTH_VIS_MAX_MM = 5000.0


class ObError(Exception):
    pass


def call(func, *args):
    """调用一个 C API，自动做 ob_error** 检查。"""
    err = ctypes.c_void_p(None)
    ret = func(*args, ctypes.byref(err))
    if err.value:
        msg = ob_error_message(err)
        msg = msg.decode("utf-8", "replace") if msg else "unknown error"
        ob_delete_error(err)
        raise ObError(msg)
    return ret


def _frame_bytes(frame, size):
    ptr = call(ob_frame_data, frame)
    return ctypes.string_at(ptr, size) if ptr else None


# ---------------- 深度伪彩可视化（流画面与保存文件共用） ----------------


def _depth_vis_bgr(depth_mm: np.ndarray) -> tuple[np.ndarray, float, float]:
    """毫米深度图 → 网页同款 JET 伪彩 BGR 图，返回 (图像, 有效占比%, 中位距离mm)。

    固定 0.3–5m 量程、近亮远暗、无效像素黑色，左上角叠现场读数。
    """
    valid = depth_mm > 0
    ratio = 100.0 * float(valid.mean())
    med = float(np.median(depth_mm[valid])) if valid.any() else 0.0
    clipped = np.clip((depth_mm - DEPTH_VIS_MIN_MM)
                      / (DEPTH_VIS_MAX_MM - DEPTH_VIS_MIN_MM), 0, 1)
    d8 = ((1.0 - clipped) * 255).astype(np.uint8)  # 近亮远暗
    d8[~valid] = 0
    dimg = cv2.applyColorMap(d8, cv2.COLORMAP_JET)
    cv2.putText(dimg, f"valid {ratio:4.1f}%  med {med/1000:.2f}m",
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
    cv2.putText(dimg, f"valid {ratio:4.1f}%  med {med/1000:.2f}m",
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return dimg, ratio, med


# ---------------- 点云预览渲染（第四路画面） ----------------

CLOUD_STRIDE = 3  # 降采样步长：640x480 → 约 214x160 ≈ 3.4 万点
# 视角模式：摇摆（swing）在正面附近 ±CLOUD_SWING_RAD 弧度之间往复，兼顾立体感与稳定观察；
# 想固定视角把 CLOUD_SWING_RAD 设为 0 即可
CLOUD_SWING_RAD = 0.6   # 摇摆幅度（约 ±34°）
CLOUD_SWING_SPEED = 0.8  # 摇摆快慢（越大越快）


def _render_cloud_view(depth_u16: np.ndarray, scale: float,
                       color_bgr: np.ndarray | None, intr: dict):
    """把深度帧渲染成点云透视画面（BGR 图像，点数）。

    相机坐标系反投影 → 绕竖直轴（以场景中位距离为中心）缓慢旋转 → 透视投影回 640x480。
    对齐时逐点取彩色图颜色，否则按深度 JET 伪彩；画家算法（远先近后）保证遮挡正确。
    """
    h, w = depth_u16.shape
    s = CLOUD_STRIDE
    z_mm = depth_u16[::s, ::s].astype(np.float32) * scale
    valid = z_mm > 0
    us, vs = np.meshgrid(np.arange(0, w, s, dtype=np.float32),
                         np.arange(0, h, s, dtype=np.float32))
    z = z_mm[valid] / 1000.0
    if z.size == 0:
        canvas = np.zeros((h, w, 3), np.uint8)
        cv2.putText(canvas, "cloud: no valid depth", (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return canvas, 0
    x = (us[valid] - intr["cx"]) * z / intr["fx"]
    y = (vs[valid] - intr["cy"]) * z / intr["fy"]

    theta = CLOUD_SWING_RAD * np.sin(time.monotonic() * CLOUD_SWING_SPEED)
    zc = float(np.median(z))
    ct, st = np.cos(theta), np.sin(theta)
    xr = x * ct + (z - zc) * st
    zr = zc - x * st + (z - zc) * ct
    front = zr > 0.05  # 剔除转到相机背后的点
    xr, yr, zr = xr[front], y[front], zr[front]

    sx = xr * intr["fx"] / zr + intr["cx"]
    sy = yr * intr["fy"] / zr + intr["cy"]
    inb = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
    sx, sy, zr = sx[inb].astype(np.int32), sy[inb].astype(np.int32), zr[inb]

    colored = color_bgr is not None and color_bgr.shape[:2] == (h, w)
    if colored:
        colors = color_bgr[::s, ::s][valid][front][inb]  # BGR，与点同序
    else:
        t8 = np.clip((zr * 1000.0 - DEPTH_VIS_MIN_MM)
                     / (DEPTH_VIS_MAX_MM - DEPTH_VIS_MIN_MM), 0, 1)
        g8 = ((1.0 - t8) * 255).astype(np.uint8)
        colors = cv2.applyColorMap(g8.reshape(-1, 1), cv2.COLORMAP_JET).reshape(-1, 3)

    order = np.argsort(-zr)  # 远的先画，近的后画
    canvas = np.zeros((h, w, 3), np.uint8)
    canvas[sy[order], sx[order]] = colors[order]
    cv2.putText(canvas, f"cloud {len(zr)} pts", (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
    cv2.putText(canvas, f"cloud {len(zr)} pts", (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return canvas, len(zr)


# ---------------- 相机抓帧线程 ----------------


class CameraGrabber(threading.Thread):
    """独占 SDK pipeline 的后台线程：持续抓三路帧，编码 JPEG 供 HTTP 转发。

    相机未连接/掉线时不退出，按 RETRY_INTERVAL_S 自动重试（与 Vesc 同模式）。
    同一时刻只有一个进程能占用相机（Orbbec Viewer、camera_capture.py 等）。
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="camera-grabber")
        self._cond = threading.Condition()
        self.connected = False
        self.last_error: str | None = None
        self.seq = 0  # 每抓到一帧 frameset 自增，流 handler 据此等帧
        self.saved_pairs = 0
        # 每路：编码好的 JPEG 字节 + 分辨率 + 滚动 fps
        self.jpeg: dict[str, bytes | None] = {"color": None, "depth": None, "ir": None, "cloud": None}
        self.info: dict[str, tuple[int, int] | None] = {"color": None, "depth": None, "ir": None, "cloud": None}
        self._fps_count = {"color": 0, "depth": 0, "ir": 0, "cloud": 0}
        self.fps = {"color": 0.0, "depth": 0.0, "ir": 0.0, "cloud": 0.0}
        self._fps_t0 = time.monotonic()
        # 拍照用的原始帧（color=BGR 数组，depth/ir=uint16 数组 + 深度 scale）
        self.raw_color: np.ndarray | None = None
        self.raw_depth: np.ndarray | None = None
        self.depth_scale = 1.0
        self.raw_ir: np.ndarray | None = None
        self.depth_stats: dict | None = None
        # 点云：D2C(HW) 是否生效 + 反投影内参（对齐后深度在彩色坐标系，取 rgbIntrinsic）
        self.aligned = False
        self.intrinsics: dict | None = None
        # 点云预览画面渲染较耗 CPU，仅在有网页观众时渲染
        self.cloud_viewers = 0

    # ---- 帧解析 ----

    def _pull(self, fs) -> None:
        cf = call(ob_frameset_color_frame, fs)
        if cf:
            try:
                w = call(ob_video_frame_width, cf)
                h = call(ob_video_frame_height, cf)
                fmt = call(ob_frame_format, cf)
                raw = _frame_bytes(cf, call(ob_frame_data_size, cf))
                if raw and fmt == OB_FORMAT_MJPG:
                    arr = np.frombuffer(raw, np.uint8)
                    color_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # 拍照用
                    with self._cond:
                        self.jpeg["color"] = bytes(raw)  # MJPG 原始字节即 JPEG
                        self.info["color"] = (w, h)
                        self.raw_color = color_bgr
                        self._fps_count["color"] += 1
            finally:
                call(ob_delete_frame, cf)

        df = call(ob_frameset_depth_frame, fs)
        if df:
            try:
                w = call(ob_video_frame_width, df)
                h = call(ob_video_frame_height, df)
                fmt = call(ob_frame_format, df)
                scale = call(ob_depth_frame_get_value_scale, df)
                raw = _frame_bytes(df, call(ob_frame_data_size, df))
                if raw and fmt == OB_FORMAT_Y16:
                    depth_u16 = np.frombuffer(raw, np.uint16).reshape(h, w).copy()
                    depth_mm = depth_u16.astype(np.float32) * scale
                    # 固定量程伪彩 + 现场读数（与保存的 depthvis PNG 同一渲染函数）
                    dimg, ratio, med = _depth_vis_bgr(depth_mm)
                    ok, buf = cv2.imencode(".jpg", dimg,
                                           [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    with self._cond:
                        if ok:
                            self.jpeg["depth"] = buf.tobytes()
                        self.info["depth"] = (w, h)
                        self.raw_depth = depth_u16
                        self.depth_scale = scale
                        self.depth_stats = {"valid_pct": round(ratio, 1),
                                            "median_mm": int(med)}
                        self._fps_count["depth"] += 1
            finally:
                call(ob_delete_frame, df)

        irf = call(ob_frameset_ir_frame, fs)
        if irf:
            try:
                w = call(ob_video_frame_width, irf)
                h = call(ob_video_frame_height, irf)
                fmt = call(ob_frame_format, irf)
                raw = _frame_bytes(irf, call(ob_frame_data_size, irf))
                if raw and fmt in (OB_FORMAT_Y16, OB_FORMAT_Y8):
                    if fmt == OB_FORMAT_Y16:
                        ir16 = np.frombuffer(raw, np.uint16).reshape(h, w).copy()
                        ir8 = cv2.normalize(ir16, None, 0, 255,
                                            cv2.NORM_MINMAX).astype(np.uint8)
                    else:
                        ir16 = None
                        ir8 = np.frombuffer(raw, np.uint8).reshape(h, w).copy()
                    ok, buf = cv2.imencode(".jpg", ir8,
                                           [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    with self._cond:
                        if ok:
                            self.jpeg["ir"] = buf.tobytes()
                        self.info["ir"] = (w, h)
                        self.raw_ir = ir16 if ir16 is not None else ir8
                        self._fps_count["ir"] += 1
            finally:
                call(ob_delete_frame, irf)

        # 点云预览画面：仅在有网页观众时渲染（降采样 + 缓慢旋转，省 CPU）
        if self.cloud_viewers > 0 and self.intrinsics is not None:
            with self._cond:
                depth = None if self.raw_depth is None else self.raw_depth
                color = None if self.raw_color is None else self.raw_color
                scale = self.depth_scale
                intr = self.intrinsics
                aligned = self.aligned
            if depth is not None:
                try:
                    img, npts = _render_cloud_view(
                        depth, scale, color if aligned else None, intr)
                    ok, buf = cv2.imencode(".jpg", img,
                                           [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    with self._cond:
                        if ok:
                            self.jpeg["cloud"] = buf.tobytes()
                        self.info["cloud"] = (img.shape[1], img.shape[0])
                        self._fps_count["cloud"] += 1
                except Exception as exc:  # noqa: BLE001 - 渲染失败不影响其他流
                    if time.monotonic() - getattr(self, "_cloud_warn_t", 0) > 10:
                        self._cloud_warn_t = time.monotonic()
                        log(f"[警告] 点云渲染失败：{exc}")

    def _update_fps(self) -> None:
        now = time.monotonic()
        dt = now - self._fps_t0
        if dt >= 2.0:
            with self._cond:
                for k in self.fps:
                    self.fps[k] = round(self._fps_count[k] / dt, 1)
                    self._fps_count[k] = 0
            self._fps_t0 = now

    def _run_pipeline(self) -> None:
        pipe = call(ob_create_pipeline)
        cfg = call(ob_create_config)
        try:
            call(ob_config_enable_video_stream, cfg, OB_STREAM_COLOR, *COLOR_PROFILE)
            call(ob_config_enable_video_stream, cfg, OB_STREAM_DEPTH, *DEPTH_PROFILE)
            call(ob_config_enable_video_stream, cfg, OB_STREAM_IR, *IR_PROFILE)
            # D2C(HW)：深度对齐到彩色视角（深度帧随之变为 640x480，与彩色逐像素对应），
            # 是彩色点云的前提。老固件若拒绝则降级为普通三流，不影响其他功能。
            try:
                call(ob_config_set_align_mode, cfg, ALIGN_D2C_HW_MODE)
                aligned = True
            except ObError as exc:
                aligned = False
                log(f"[警告] D2C(HW) 设置失败，点云将退化为无色：{exc}")
            call(ob_pipeline_start_with_config, pipe, cfg)
            # 读出厂标定内参（每台设备不同，不要写死）：
            # 对齐后深度值落在彩色像素上，反投影用 rgbIntrinsic（点在彩色相机坐标系）
            intr = None
            try:
                param = call(ob_pipeline_get_camera_param, pipe)
                src = param.rgbIntrinsic if aligned else param.depthIntrinsic
                intr = {"fx": src.fx, "fy": src.fy, "cx": src.cx, "cy": src.cy,
                        "w": src.width, "h": src.height,
                        "space": "color" if aligned else "depth"}
            except Exception as exc:  # noqa: BLE001 - 老设备个别 API 有坑，失败只影响点云
                log(f"[警告] 读取相机内参失败，点云功能不可用：{exc}")
            with self._cond:
                self.connected = True
                self.last_error = None
                self.aligned = aligned
                self.intrinsics = intr
            log("相机三流已启动：color 640x480 MJPG + depth 640x400 + ir 640x400")
            log(f"D2C(HW)={'开' if aligned else '关'}，点云内参={intr}")
            t0 = time.time()
            while time.time() - t0 < WARMUP_S:  # 预热，丢曝光未稳定帧
                fs = call(ob_pipeline_wait_for_frameset, pipe, STREAM_TIMEOUT_MS)
                if fs:
                    call(ob_delete_frame, fs)
            while True:
                fs = call(ob_pipeline_wait_for_frameset, pipe, STREAM_TIMEOUT_MS)
                if fs:
                    try:
                        self._pull(fs)
                    finally:
                        call(ob_delete_frame, fs)
                    with self._cond:
                        self.seq += 1
                        self._cond.notify_all()
                self._update_fps()
        finally:
            try:
                call(ob_pipeline_stop, pipe)
            except ObError:
                pass
            call(ob_delete_pipeline, pipe)
            call(ob_delete_config, cfg)
            with self._cond:
                self.connected = False
                self.fps = {"color": 0.0, "depth": 0.0, "ir": 0.0, "cloud": 0.0}

    def run(self) -> None:
        while True:
            try:
                self._run_pipeline()
            except ObError as exc:
                with self._cond:
                    self.connected = False
                    self.last_error = str(exc)
                log(f"[警告] 相机不可用：{exc}（{RETRY_INTERVAL_S:.0f}s 后重试）")
            except Exception as exc:  # noqa: BLE001 - 任何错误都不能杀死抓帧线程
                with self._cond:
                    self.connected = False
                    self.last_error = str(exc)
                log(f"[错误] 抓帧线程：{exc}（{RETRY_INTERVAL_S:.0f}s 后重试）")
            time.sleep(RETRY_INTERVAL_S)

    # ---- 对外接口 ----

    def wait_frame(self, src: str, since_seq: int, timeout: float = 1.0) -> bytes | None:
        """等一帧新 JPEG；超时或无新帧返回 None（不重复发旧帧，也让 handler 有机会检测断连）。"""
        with self._cond:
            got_new = self._cond.wait_for(lambda: self.seq != since_seq, timeout=timeout)
            if not got_new:
                return None
            return self.jpeg[src]

    def snapshot(self, srcs: list[str] | None = None) -> dict:
        """保存当前帧。srcs 指定要保存的画面（前端按开关状态传入），缺省保存全部四路。"""
        want = set(srcs) if srcs else {"color", "depth", "ir", "cloud"}
        if not want:
            raise RuntimeError("没有指定要保存的画面")
        with self._cond:
            if not self.connected or (self.raw_color is None and self.raw_depth is None):
                raise RuntimeError("相机无可用帧（未连接或尚未出图）")
            color = None if self.raw_color is None else self.raw_color.copy()
            depth = None if self.raw_depth is None else self.raw_depth.copy()
            ir = None if self.raw_ir is None else self.raw_ir.copy()
            scale = self.depth_scale
            stats = dict(self.depth_stats) if self.depth_stats else None
            intr = dict(self.intrinsics) if self.intrinsics else None
            aligned = self.aligned
            self.saved_pairs += 1
            n = self.saved_pairs

        ts = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        files = {}
        skipped = []
        if "color" in want:
            if color is not None:
                name = f"color_{ts}.png"
                cv2.imwrite(os.path.join(CAPTURE_DIR, name), color)
                files["color"] = name
            else:
                skipped.append("color")
        if "depth" in want:
            if depth is not None:
                depth_mm = (depth.astype(np.float32) * scale)
                name = f"depth_{ts}.png"
                cv2.imwrite(os.path.join(CAPTURE_DIR, name),
                            np.clip(depth_mm, 0, 65535).astype(np.uint16))
                files["depth"] = name
                # 顺手多存一张网页同款伪彩图，方便普通看图软件直接查看
                vis, _ratio, _med = _depth_vis_bgr(depth_mm)
                vis_name = f"depthvis_{ts}.png"
                cv2.imwrite(os.path.join(CAPTURE_DIR, vis_name), vis)
                files["depthvis"] = vis_name
            else:
                skipped.append("depth")
        if "ir" in want:
            if ir is not None:
                name = f"ir_{ts}.png"
                cv2.imwrite(os.path.join(CAPTURE_DIR, name),
                            ir if ir.dtype == np.uint8 else
                            cv2.normalize(ir, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8))
                files["ir"] = name
            else:
                skipped.append("ir")
        if "cloud" in want:
            if depth is not None and intr is not None:
                try:
                    files["cloud"] = self._save_pointcloud(
                        ts, depth, scale, color if aligned else None, intr)
                except Exception as exc:  # noqa: BLE001 - 点云失败不影响图像保存结果
                    log(f"[警告] 点云生成失败：{exc}")
                    skipped.append("cloud")
            else:
                skipped.append("cloud")
        if not files:
            raise RuntimeError(f"所有画面均无可用帧：{', '.join(skipped)}")
        log(f"已保存图像对 #{n}：{', '.join(files.values())}"
            + (f"（跳过 {', '.join(skipped)}）" if skipped else ""))
        return {"seq": n, "timestamp": ts, "files": files, "depth_stats": stats,
                "skipped": skipped}

    @staticmethod
    def _save_pointcloud(ts: str, depth_u16: np.ndarray, scale: float,
                         color_bgr: np.ndarray | None, intr: dict) -> str:
        """深度帧反投影成点云，写二进制 PLY（单位：米）。

        D2C 对齐时 color_bgr 与 depth 逐像素对应，逐点取色；未对齐则只写几何。
        坐标系：相机坐标系（x 右、y 下、z 前），对齐后为彩色相机坐标系。
        """
        h, w = depth_u16.shape
        z_mm = depth_u16.astype(np.float32) * scale
        valid = z_mm > 0
        u, v = np.meshgrid(np.arange(w, dtype=np.float32),
                           np.arange(h, dtype=np.float32))
        z = z_mm[valid] / 1000.0
        x = (u[valid] - intr["cx"]) * z / intr["fx"]
        y = (v[valid] - intr["cy"]) * z / intr["fy"]
        n = int(z.shape[0])
        colored = color_bgr is not None and color_bgr.shape[:2] == (h, w)

        name = f"cloud_{ts}.ply"
        path = os.path.join(CAPTURE_DIR, name)
        if colored:
            bgr = color_bgr[valid]
            header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {n}\n"
                      "property float x\nproperty float y\nproperty float z\n"
                      "property uchar red\nproperty uchar green\nproperty uchar blue\n"
                      "end_header\n").encode()
            arr = np.empty(n, dtype=np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                              ("red", "u1"), ("green", "u1"), ("blue", "u1")]))
            arr["x"], arr["y"], arr["z"] = x, y, z
            arr["red"], arr["green"], arr["blue"] = bgr[:, 2], bgr[:, 1], bgr[:, 0]
        else:
            header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {n}\n"
                      "property float x\nproperty float y\nproperty float z\n"
                      "end_header\n").encode()
            arr = np.stack([x, y, z], axis=1).astype("<f4")
        with open(path, "wb") as f:
            f.write(header)
            f.write(arr.tobytes())
        log(f"已保存点云 {name}：{n} 点（{'彩色' if colored else '无色'}）")
        return name

    def status(self) -> dict:
        with self._cond:
            return {
                "connected": self.connected,
                "error": self.last_error,
                "fps": dict(self.fps),
                "resolution": {k: list(v) if v else None for k, v in self.info.items()},
                "saved_pairs": self.saved_pairs,
                "aligned": self.aligned,
                "intrinsics": self.intrinsics,
            }


grabber = CameraGrabber()


# ---------------- HTTP API ----------------

BOUNDARY = "mjpegframe"


def status_dict() -> dict:
    return {"ok": True, "camera": grabber.status(),
            "uptime_s": int(time.monotonic() - START_TIME)}


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotCamera/1.0"
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server 固定命名
        # 204 按规范不能带响应体：HTTP/1.1 长连接下多发 Content-Length/字节
        # 会污染同连接的下一个响应。这里只回头部，不写 body。
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/status":
            self._send_json(200, status_dict())
        elif path == "/captures":
            self._send_json(200, {"ok": True, "captures": list_captures()})
        elif path.startswith("/captures/"):
            self._send_capture_file(path[len("/captures/"):])
        elif path == "/stream":
            src = parse_qs(urlparse(self.path).query).get("src", ["color"])[0]
            if src not in ("color", "depth", "ir", "cloud"):
                self._send_json(400, {"ok": False, "error": "src 必须是 color / depth / ir / cloud"})
                return
            self._stream(src)
        else:
            self._send_json(404, {"ok": False, "error": "未知路径"})

    def _send_capture_file(self, name: str) -> None:
        """下载 captures 里已保存的文件（前端点云/图像下载入口）。

        只允许 color_/depth_/ir_/cloud_ 前缀的 .png/.ply，防目录穿越。
        """
        from urllib.parse import unquote
        name = unquote(name)
        base = os.path.basename(name)
        kind, _, _ts = base.partition("_")
        if (base != name or kind not in ("color", "depth", "ir", "cloud", "depthvis")
                or not base.endswith((".png", ".ply"))):
            self._send_json(400, {"ok": False, "error": "非法文件名"})
            return
        full = os.path.join(CAPTURE_DIR, base)
        if not os.path.isfile(full):
            self._send_json(404, {"ok": False, "error": "文件不存在"})
            return
        mime = "image/png" if base.endswith(".png") else "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{base}"')
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/snapshot":
                # 可选 JSON body：{"srcs": ["color", "depth", ...]}，缺省保存全部四路
                srcs = None
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(length))
                        if isinstance(body, dict) and isinstance(body.get("srcs"), list):
                            srcs = [s for s in body["srcs"]
                                    if s in ("color", "depth", "ir", "cloud")]
                    except (ValueError, UnicodeDecodeError):
                        srcs = None
                self._send_json(200, {"ok": True, **grabber.snapshot(srcs)})
            elif path == "/safe-stop":
                self._send_json(200, {"ok": True, "message": "相机服务正在停止"})
                # 响应送达后退出进程：server.shutdown() 结束 serve_forever，
                # main 的 finally 收尾；grabber 是 daemon 线程随进程结束
                threading.Thread(target=self._quit_soon, daemon=True, name="safe-stop-quit").start()
            elif path == "/shutdown-pi":
                self._send_json(200, {"ok": True, "message": "树莓派正在关机"})
                # 响应送达后关机整台系统：systemd 会干净停止全部服务
                #（robot-control 有 SIGTERM 清理，风机归零），随后断电
                threading.Thread(target=self._shutdown_soon, daemon=True, name="shutdown-pi").start()
            else:
                self._send_json(404, {"ok": False, "error": "未知路径"})
        except RuntimeError as exc:
            self._send_json(503, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"服务内部错误：{exc}"})

    def _stream(self, src: str) -> None:
        if not grabber.connected:
            self._send_json(503, {"ok": False, "error": f"相机未连接：{grabber.last_error}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        if src == "cloud":  # 点云渲染按观众数启停
            grabber.cloud_viewers += 1
        seq = grabber.seq
        try:
            while True:
                jpeg = grabber.wait_frame(src, seq)
                seq = grabber.seq
                if jpeg is None:
                    if not grabber.connected:  # 相机掉线且不会再有新帧，主动断开让前端重连
                        break
                    continue
                if not grabber.connected:  # 相机中途掉线
                    break
                self.wfile.write(f"--{BOUNDARY}\r\n".encode())
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:  # noqa: BLE001
            log(f"[警告] {src} 流中断：{exc}")
        finally:
            if src == "cloud":
                grabber.cloud_viewers = max(0, grabber.cloud_viewers - 1)

    def _quit_soon(self) -> None:
        time.sleep(0.5)  # 先让 HTTP 响应完整发回浏览器
        log("相机服务正在退出（安全停止）……")
        # 顺带停掉网页服务：vinext 的路由跑在 workerd 里，/api/shutdown 的
        # process.exit(0) 是空操作杀不掉它；这里用免密 sudo systemctl 代停
        try:
            subprocess.run(["sudo", "-n", "systemctl", "stop", "robot-web"],
                           timeout=10, capture_output=True)
            log("已请求停止网页服务 robot-web")
        except Exception as exc:  # noqa: BLE001
            log(f"[警告] 停止网页服务失败：{exc}")
        # 必须 os._exit(0)：正常 return 会走 Python 解释器收尾，Orbbec SDK 的
        # C++ 层在 teardown 时 SIGABRT，systemd 会判定为失败并按 on-failure 重启
        os._exit(0)

    def _shutdown_soon(self) -> None:
        time.sleep(1.0)  # 先让 HTTP 响应完整发回浏览器
        log("树莓派正在关机（/shutdown-pi）……")
        # 关机后进程被 systemd 停掉属正常流程；本机 sudoers 只给 systemctl 免密，
        # poweroff 先试免密，失败则回退到密码 sudo（与项目约定的 sudo 用法一致）
        try:
            r = subprocess.run(["sudo", "-n", "poweroff"], timeout=10, capture_output=True)
            if r.returncode == 0:
                return
            log(f"[提示] 免密 poweroff 不可用，改用密码 sudo（{r.stderr.decode(errors='replace').strip()}）")
        except Exception as exc:  # noqa: BLE001
            log(f"[提示] 免密 poweroff 异常：{exc}，改用密码 sudo")
        try:
            subprocess.run("echo '123456' | sudo -S poweroff", shell=True,
                           timeout=10, capture_output=True)
        except Exception as exc:  # noqa: BLE001
            log(f"[错误] 关机指令执行失败：{exc}，请 SSH 手动 sudo poweroff")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        msg = format % args
        if "/stream" not in msg and "/status" not in msg:  # 高频请求不刷屏
            log(f"HTTP {self.address_string()} {msg}")


def list_captures() -> list[dict]:
    """按时间戳把 color/depth/ir/cloud 归组，新的在前，最多返回 100 组。"""
    if not os.path.isdir(CAPTURE_DIR):
        return []
    groups: dict[str, dict] = {}
    for name in os.listdir(CAPTURE_DIR):
        stem, dot, _ext = name.rpartition(".")
        kind, _, ts = stem.partition("_")
        if not dot or kind not in ("color", "depth", "ir", "cloud", "depthvis") or not ts:
            continue
        full = os.path.join(CAPTURE_DIR, name)
        g = groups.setdefault(ts, {"timestamp": ts, "files": {},
                                   "mtime": os.path.getmtime(full)})
        g["files"][kind] = name
        g["mtime"] = max(g["mtime"], os.path.getmtime(full))
    return sorted(groups.values(), key=lambda g: g["timestamp"], reverse=True)[:100]


# ---------------- 启动与退出 ----------------


def main() -> int:
    signal.signal(signal.SIGINT, lambda s, f: (_ for _ in ()).throw(SystemExit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (_ for _ in ()).throw(SystemExit(0)))

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    grabber.start()
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    server.daemon_threads = True  # 流 handler 长连接不阻塞退出
    log(f"相机服务已启动：http://{HTTP_HOST}:{HTTP_PORT}（SDK: {_SDK_LIB_PATH}）")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log("相机服务已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
