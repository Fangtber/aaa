#!/usr/bin/env python3
"""敲击电机慢转 demo（VESC UART）。

只保留最核心的功能：让电机以一个较低的转速缓慢转动，Ctrl+C 立即刹车退出。
协议与换算关系与 pi/robot_control_service.py 一致（电机 7 对极、10:1 减速）。

用法（树莓派上）：
    python tap_motor_demo.py            # 默认 2100 ERPM（约等于 30 次/分的敲击速度）
    python tap_motor_demo.py 3000       # 指定 ERPM
    python tap_motor_demo.py 2100 COM5  # Windows 上指定串口调试用

安全：首次测试请空载（不装敲击头/桨叶）。退出（Ctrl+C）必定刹车。
"""

import struct
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit("未安装 pyserial，请先执行：pip install pyserial")

# ---------------- 参数 ----------------

PORT = "/dev/ttyAMA0"
BAUD = 115200
COMM_SET_RPM = 8

DEFAULT_ERPM = 2100      # 慢转目标：30 次/分 × 10 减速比 × 7 对极
RAMP_STEPS = 10          # 缓启动斜坡步数
RAMP_DELAY = 0.08        # 每步间隔（秒）
REFRESH_S = 0.3          # 运转中周期重发，防 VESC 看门狗

# ---------------- VESC 协议 ----------------


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def make_packet(payload: bytes) -> bytes:
    packet = bytearray()
    if len(payload) <= 255:
        packet += bytes([2, len(payload)])
    else:
        packet += bytes([3, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF])
    packet += payload
    crc = crc16(payload)
    packet += bytes([(crc >> 8) & 0xFF, crc & 0xFF, 3])
    return bytes(packet)


def send_erpm(ser, erpm: int) -> None:
    payload = bytearray([COMM_SET_RPM])
    payload += struct.pack(">i", int(erpm))
    ser.write(make_packet(payload))
    ser.flush()


def send_erpm_multiple(ser, erpm: int, count: int = 3) -> None:
    for _ in range(count):
        send_erpm(ser, erpm)
        time.sleep(0.05)


# ---------------- 主流程 ----------------


def main() -> int:
    try:
        target_erpm = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ERPM
    except ValueError:
        print(f"ERPM 必须是整数，例如：python {Path(sys.argv[0]).name} 2100")
        return 2
    if target_erpm <= 0:
        print("ERPM 必须为正整数（0 请直接 Ctrl+C 刹车，不要用作目标转速）")
        return 2
    port = sys.argv[2] if len(sys.argv) > 2 else PORT

    print(f"打开串口 {port} @ {BAUD} ...")
    ser = serial.Serial(port, BAUD, bytesize=8,
                        parity=serial.PARITY_NONE, stopbits=1, timeout=0.1)
    time.sleep(0.5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    try:
        # 先确保处于停止状态
        send_erpm_multiple(ser, 0, count=5)
        print("串口已连接，电机已归零。")

        # 缓启动：10 步斜坡爬到目标转速
        print(f"缓启动 → {target_erpm} ERPM ...")
        for step in range(1, RAMP_STEPS + 1):
            send_erpm(ser, int(target_erpm * step / RAMP_STEPS))
            time.sleep(RAMP_DELAY)
        send_erpm_multiple(ser, target_erpm)

        print(f"电机慢转中（{target_erpm} ERPM）。按 Ctrl+C 停止。")
        while True:
            send_erpm(ser, target_erpm)  # 周期刷新，防看门狗
            time.sleep(REFRESH_S)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在刹车 ...")

    finally:
        try:
            send_erpm_multiple(ser, 0, count=10)
            print("电机已停止。")
        except Exception:
            pass
        ser.close()
        print("串口已关闭。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
