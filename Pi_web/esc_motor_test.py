#!/usr/bin/env python3
"""树莓派5双路BLHeli32电调启停控制程序。

接线：GPIO18(Pin 12)->M1，GPIO19(Pin 35)->M3，树莓派GND->电调GND。
首次测试必须拆下螺旋桨。（注意：GPIO13 在树莓派5上没有 PWM 功能，M3 必须用 GPIO19）
"""

import os
import signal
import sys
import time
from pathlib import Path

PWM_CHIP = Path("/sys/class/pwm/pwmchip0")
CHANNELS = (2, 3)  # GPIO18->pwm2/M1；GPIO19->pwm3/M3
PERIOD_NS = 20_000_000
STOP_PULSE_US = 1000
DEFAULT_RUN_US = 1150
MIN_RUN_US = 1050
MAX_TEST_US = 1300
configured_channels: set[int] = set()


def pwm_path(channel: int) -> Path:
    return PWM_CHIP / f"pwm{channel}"


def write_value(path: Path, value: int | str) -> None:
    try:
        path.write_text(str(value), encoding="ascii")
    except OSError as exc:
        raise RuntimeError(
            f"写入失败：{path}，数值={value}，原因={exc}"
        ) from exc


def wait_for_path(path: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise RuntimeError(f"等待PWM节点超时：{path}")


def export_channel(channel: int) -> None:
    node = pwm_path(channel)
    if not node.exists():
        write_value(PWM_CHIP / "export", channel)
        wait_for_path(node)


def configure_channel(channel: int) -> None:
    export_channel(channel)
    node = pwm_path(channel)
    try:
        write_value(node / "enable", 0)
    except RuntimeError:
        pass
    # 显式写 normal，排除 polarity 残留（sysfs 状态跨进程存活）
    write_value(node / "polarity", "normal")
    write_value(node / "period", PERIOD_NS)
    write_value(node / "duty_cycle", STOP_PULSE_US * 1000)
    write_value(node / "enable", 1)
    configured_channels.add(channel)


def setup_pwm() -> None:
    if not PWM_CHIP.exists():
        raise RuntimeError(
            "未找到pwmchip0，请先执行："
            "sudo dtoverlay pwm-2chan pin=18 pin2=19"
        )
    try:
        npwm = int((PWM_CHIP / "npwm").read_text().strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法读取pwmchip0/npwm") from exc
    if max(CHANNELS) >= npwm:
        raise RuntimeError(f"当前只有{npwm}个PWM通道，程序需要pwm2和pwm3。")
    for channel in CHANNELS:
        configure_channel(channel)


def set_both_pulses(pulse_us: int) -> None:
    for channel in CHANNELS:
        duty_file = pwm_path(channel) / "duty_cycle"
        if not duty_file.exists():
            raise RuntimeError(f"PWM通道不存在：{duty_file}")
        write_value(duty_file, pulse_us * 1000)


def stop_motors(show_message: bool = True) -> None:
    for channel in tuple(configured_channels):
        duty_file = pwm_path(channel) / "duty_cycle"
        if duty_file.exists():
            try:
                write_value(duty_file, STOP_PULSE_US * 1000)
            except RuntimeError as exc:
                print(f"[警告] {exc}", file=sys.stderr)
    if show_message and configured_channels:
        print(f"电机已停止：M1/M3 = {STOP_PULSE_US} us")


def shutdown_pwm() -> None:
    if not configured_channels:
        return
    stop_motors(show_message=False)
    time.sleep(0.3)
    for channel in tuple(configured_channels):
        enable_file = pwm_path(channel) / "enable"
        if enable_file.exists():
            try:
                write_value(enable_file, 0)
            except RuntimeError as exc:
                print(f"[警告] {exc}", file=sys.stderr)
    configured_channels.clear()


def handle_signal(signum, frame) -> None:  # noqa: ARG001
    print("\n收到终止信号，正在停止电机……")
    shutdown_pwm()
    raise SystemExit(0)


def show_status() -> None:
    names = {2: "GPIO18/M1", 3: "GPIO19/M3"}
    for channel in CHANNELS:
        node = pwm_path(channel)
        if not node.exists():
            print(f"{names[channel]}：pwm{channel}不存在")
            continue
        try:
            enabled = (node / "enable").read_text().strip()
            period = (node / "period").read_text().strip()
            duty = (node / "duty_cycle").read_text().strip()
            print(
                f"{names[channel]}：pwm{channel}，enable={enabled}，"
                f"period={period}ns，pulse={int(duty) // 1000}us"
            )
        except (OSError, ValueError) as exc:
            print(f"{names[channel]}：读取失败：{exc}")


def print_help() -> None:
    print(
        "\n命令：\n"
        "  start          两个电机以1150us运行\n"
        "  start 1100     指定1050~1300us运行\n"
        "  stop           两个电机停止\n"
        "  status         查看PWM状态\n"
        "  help           显示帮助\n"
        "  q              停止并退出\n"
    )


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[错误] 请使用sudo运行本程序。", file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    exit_code = 0
    try:
        setup_pwm()
        print("双路PWM初始化成功。")
        print("GPIO18/M1、GPIO19/M3均为50Hz、1000us最低油门。")
        print("现在可以给电调接通动力电池，等待完整解锁提示音。")
        print_help()
        while True:
            try:
                command = input("ESC> ").strip().lower().split()
            except EOFError:
                print("\n输入结束，停止并退出。")
                break
            if not command:
                continue
            if command[0] == "start":
                if len(command) > 2:
                    print("格式错误，例如：start 1150")
                    continue
                try:
                    pulse_us = int(command[1]) if len(command) == 2 else DEFAULT_RUN_US
                except ValueError:
                    print("脉宽必须为整数，例如：start 1150")
                    continue
                if not MIN_RUN_US <= pulse_us <= MAX_TEST_US:
                    print(f"测试脉宽必须在{MIN_RUN_US}~{MAX_TEST_US}us。")
                    continue
                set_both_pulses(pulse_us)
                print(f"电机运行：M1/M3={pulse_us}us；输入stop停止。")
            elif command[0] == "stop":
                stop_motors()
            elif command[0] == "status":
                show_status()
            elif command[0] == "help":
                print_help()
            elif command[0] in {"q", "quit", "exit"}:
                break
            else:
                print("未知命令，请输入start、stop、status、help或q。")
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        shutdown_pwm()
    print("PWM已关闭，程序退出。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
