#!/usr/bin/env python3
"""M3 风机自转排查脚本（不需要示波器）。

用法（在树莓派上）：
    sudo python3 pi/diagnose_m3.py

脚本分三步，全部带时间戳打印：
  1) 快照：打印 pwmchip0/pwm2/pwm3 当前 sysfs 状态、GPIO18/19 引脚状态、
     是否有 robot_control 服务 / pigpiod 等进程在占用引脚。
  2) 初始化重演：按控制服务相同的顺序配置两路 PWM
     （额外显式写 polarity=normal），每写一步立即读回校验。
  3) 监视 10 秒：每 0.5 秒读回两路 duty_cycle / polarity。

判读指南（结合风机实际自转的时刻与日志时间戳对比）：
  A) 自转发生在第 2 步期间 → 启动窗口内引脚悬空被 M3 电调当成油门
     （电调输入上拉），属时序问题；严格遵守"先跑代码、后接动力电池"可规避。
  B) 自转发生在第 3 步期间，且 pwm3 读回始终是 duty=1000000 / polarity=normal
     → 树莓派输出的是正确停机信号，问题在线路（共地）或 M3 电调本体，
       下一步做"对调 M1/M3 信号线"实验。
  C) 第 1 步快照里 pwm3 的 polarity 不是 normal → 极性残留就是病根，
     本脚本第 2 步已顺手改回 normal。

安全：运行前必须拆下螺旋桨或可靠固定风机。
"""

import subprocess
import time
from pathlib import Path

PWM_CHIP = Path("/sys/class/pwm/pwmchip0")
CHANNELS = (2, 3)
PERIOD_NS = 20_000_000
STOP_NS = 1_000_000
WATCH_SECONDS = 10
WATCH_INTERVAL = 0.5


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run_cmd(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return (out.stdout + out.stderr).strip() or "<无输出>"
    except Exception as exc:  # noqa: BLE001
        return f"<执行失败：{exc}>"


def read_attr(node: Path, name: str) -> str:
    try:
        return (node / name).read_text().strip()
    except OSError as exc:
        return f"<读取失败：{exc}>"


def write_and_verify(node: Path, name: str, value) -> bool:
    """写入后立即读回校验，返回是否一致。"""
    try:
        (node / name).write_text(str(value), encoding="ascii")
    except OSError as exc:
        log(f"    {node.name} 写 {name}={value} 失败：{exc}")
        return False
    back = read_attr(node, name)
    ok = back == str(value)
    log(f"    {node.name} 写 {name}={value}，读回={back} {'OK' if ok else '!! 不一致'}")
    return ok


def dump_channel(channel: int) -> None:
    node = PWM_CHIP / f"pwm{channel}"
    if not node.exists():
        log(f"  pwm{channel}：节点不存在（尚未 export）")
        return
    log(
        f"  pwm{channel}：enable={read_attr(node, 'enable')} "
        f"period={read_attr(node, 'period')}ns "
        f"duty={read_attr(node, 'duty_cycle')}ns "
        f"polarity={read_attr(node, 'polarity')}"
    )


def step1_snapshot() -> None:
    log("—— 第 1 步：启动前快照 ——")
    if not PWM_CHIP.exists():
        log("  未找到 pwmchip0！请先执行 sudo dtoverlay pwm-2chan pin=18 pin2=19")
        return
    log(f"  npwm={read_attr(PWM_CHIP, 'npwm')}")
    for channel in CHANNELS:
        dump_channel(channel)
    log(f"  GPIO18 引脚状态：{run_cmd(['pinctrl', 'get', '18'])}")
    log(f"  GPIO19 引脚状态：{run_cmd(['pinctrl', 'get', '19'])}")
    log(f"  robot-control 服务：{run_cmd(['systemctl', 'is-active', 'robot-control'])}")
    log(f"  robot_control_service 进程：{run_cmd(['pgrep', '-a', '-f', 'robot_control_service'])}")
    log(f"  pigpiod 进程：{run_cmd(['pgrep', '-a', 'pigpiod'])}")


def step2_replay_init() -> None:
    log("—— 第 2 步：重演初始化（含 polarity=normal 加固）——")
    for channel in CHANNELS:
        node = PWM_CHIP / f"pwm{channel}"
        if not node.exists():
            try:
                (PWM_CHIP / "export").write_text(str(channel), encoding="ascii")
            except OSError as exc:
                log(f"  pwm{channel} export 失败：{exc}")
                continue
            deadline = time.monotonic() + 2.0
            while not node.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.15)
        log(f"  配置 pwm{channel}：")
        write_and_verify(node, "enable", 0)
        write_and_verify(node, "polarity", "normal")
        write_and_verify(node, "period", PERIOD_NS)
        write_and_verify(node, "duty_cycle", STOP_NS)
        write_and_verify(node, "enable", 1)
    log("  初始化完成，两路均为 50Hz / 1000us 停机信号")


def step3_watch() -> None:
    log(f"—— 第 3 步：监视 {WATCH_SECONDS} 秒（请记录风机实际自转/停止的时刻）——")
    deadline = time.monotonic() + WATCH_SECONDS
    while time.monotonic() < deadline:
        parts = []
        for channel in CHANNELS:
            node = PWM_CHIP / f"pwm{channel}"
            parts.append(
                f"pwm{channel}: duty={read_attr(node, 'duty_cycle')} "
                f"polarity={read_attr(node, 'polarity')}"
            )
        log("  " + " | ".join(parts))
        time.sleep(WATCH_INTERVAL)


def cleanup() -> None:
    for channel in CHANNELS:
        enable_file = PWM_CHIP / f"pwm{channel}" / "enable"
        if enable_file.exists():
            try:
                enable_file.write_text("0", encoding="ascii")
            except OSError:
                pass
    log("已将两路 PWM 关闭（enable=0），脚本结束")


def main() -> int:
    try:
        step1_snapshot()
        step2_replay_init()
        step3_watch()
    except KeyboardInterrupt:
        log("收到 Ctrl+C，提前结束")
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
