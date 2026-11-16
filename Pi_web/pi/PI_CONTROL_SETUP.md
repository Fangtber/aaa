# 爬墙机器人控制链路：树莓派部署指南

本文档接续 `RASPBERRY_PI_SETUP.md`（那篇只覆盖前端演示）。本篇部署**控制服务**，让网页按钮真正驱动风机和敲击装置。

## 架构

```
平板/电脑浏览器                        树莓派
┌────────────────┐   HTTP :5173   ┌──────────────────────────┐
│ 控制台网页      │ ────────────▶ │ npm run dev（网页）       │
│                │   HTTP :8008   │                          │
│                │ ────────────▶ │ robot_control_service.py │
└────────────────┘                │   ├─ /sys/class/pwm → 风机电调 ×2
                                  │   └─ /dev/ttyAMA0  → VESC 敲击
                                  └──────────────────────────┘
```

网页从哪个主机加载，就自动连同一主机的 8008 端口，无需配置。

## 一次性系统准备

```bash
# 1. 双路 PWM overlay（与台架脚本一致）
sudo dtoverlay pwm-2chan pin=18 pin2=19
# 永久生效：把 dtoverlay=pwm-2chan,pin=18,pin2=19 写进 /boot/firmware/config.txt
# 注意：GPIO13 在树莓派5上没有 PWM 功能，M3 必须接 GPIO19（Pin 35）

# 2. UART：确认 /boot/firmware/config.txt 有 enable_uart=1，
#    且串口控制台已关闭（sudo raspi-config → Interface Options → Serial Port →
#    login shell: No, serial hardware: Yes）

# 3. pyserial
sudo apt install -y python3-serial   # 或 pip install pyserial

# 4. PWM 权限（udev 规则，免去 root）
sudo cp pi/99-robot-control.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# 5. UART 权限：把运行用户加入 dialout 组（重新登录生效）
sudo usermod -aG dialout $USER

# 6. 免密停止网页服务：网页「安全停止」由相机服务代停 robot-web，
#    没有这条规则时 sudo -n 会因要密码而失败，前端停不掉
sudo visudo -f /etc/sudoers.d/robot-web
# 写入一行：
# fangtb02 ALL=(root) NOPASSWD: /usr/bin/systemctl stop robot-web, /usr/bin/systemctl start robot-web, /usr/bin/systemctl restart robot-web
```

## 启动控制服务

前台试运行（先看日志排错）：

```bash
python3 pi/robot_control_service.py
# 正常输出：风机 PWM 初始化完成 / 控制服务已启动：http://0.0.0.0:8008
```

验证：

```bash
curl http://localhost:8008/status
# {"ok": true, "hardware": {"pwm": true, "uart": true}, ...}
```

开机自启（可选）：

```bash
# 先按实际路径编辑 pi/robot-control.service 里的 ExecStart 和 User
sudo cp pi/robot-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robot-control
```

## 启动网页

```bash
npm ci          # 首次
npm run dev -- --hostname 0.0.0.0
```

平板连树莓派热点，访问 `http://<树莓派IP>:5173/`。顶部状态条会显示真实状态：
「树莓派已连接 / 心跳正常 / PWM 就绪 · UART 就绪」。

## 网页动作与硬件对应表

| 网页操作 | 硬件动作 |
|---|---|
| 滑杆 + 启动风机 | M1/M3 两路同步输出 1000–1700µs（0%→1000，100%→1700） |
| 停止并归零 | 两路回到 1000µs |
| 单次敲击 | 电机以 60 次/分转一整圈（约 1 秒）后立即刹车 |
| 模式=脉冲敲击 + 连续敲击 | 敲一次 → 停 1 秒 → 循环，直到停止敲击 |
| 模式=单点/连续敲击 + 连续敲击 | 持续 80 次/分转动，直到停止敲击 |
| 安全停止 | 风机回 1000µs + 敲击刹车，连接保持 |

## 安全须知

- 首次联调**必须拆下螺旋桨**，敲击机构先空载验证。
- 服务退出（Ctrl+C / systemctl stop / 崩溃）会自动刹车敲击电机并把风机 PWM 归零。
- 控制端口 8008 无身份验证，只允许在树莓派自有热点内网使用；暴露到不可信网络前必须加鉴权。
- 若网页显示「PWM 不可用」，检查 dtoverlay 是否生效（`ls /sys/class/pwm/pwmchip0/`）；若显示「UART 不可用」，检查 `enable_uart`、dialout 组和接线。
