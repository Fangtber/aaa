# 外墙检测机器人控制台：树莓派试运行

此压缩包是前端源码包，不包含 Windows 上安装的 `node_modules`。

**控制链路已接入**：网页按钮通过 `pi/robot_control_service.py`（HTTP :8008）驱动真实风机（GPIO18/GPIO13 双路 PWM 同步，1000–1700µs）和 VESC 敲击装置（/dev/ttyAMA0）。部署控制服务请阅读 `pi/PI_CONTROL_SETUP.md`。未启动控制服务时，网页自动退化为演示模式，按钮只改界面不碰硬件。

## 准备

- 建议使用 64 位 Raspberry Pi OS。
- 树莓派需安装 Node.js 22.13 或更新版本、npm，并在安装依赖时能访问 npm 软件包仓库。
- 用 `node -v` 和 `npm -v` 检查版本。

## 复制与安装

将压缩包复制到树莓派，在目标目录解压。进入包含 `package.json` 的目录后执行：

```bash
npm ci
```

`npm ci` 会按照 `package-lock.json` 重新安装依赖。不要复制电脑上的 `node_modules`：它可能含有与树莓派 ARM/Linux 不兼容的二进制文件。

## 仅供演示的局域网预览

```bash
npm run dev -- --hostname 0.0.0.0
```

平板连接树莓派热点后，在浏览器访问 `http://<树莓派热点IP>:5173/`。可用 `hostname -I` 查看树莓派地址，再根据热点接口选择正确的 IP。若无法访问，请检查两台设备是否在同一热点网络，以及端口 5173 是否被防火墙拦截。

开发服务器不适合长期运行或开放到不受信任的网络。当前 `npm start` 为原网站构建环境的本地 Worker 预览脚本，不是树莓派硬件控制服务。正式部署前，应另行实现树莓派控制 API、身份验证、断线保护和开机自启。
