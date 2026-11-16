#!/usr/bin/env bash
# ============================================================
# deploy_to_pi.sh — 一键把本项目的代码上传到树莓派并重启服务
#
# 用法（在 Windows 的 Git Bash 里运行）：
#   ./deploy_to_pi.sh                  # 用默认地址
#   ./deploy_to_pi.sh fangtb02@192.168.x.x   # 指定树莓派地址
#
# 做了什么：
#   1. 打包代码（自动排除 node_modules / 构建缓存 / 日志 / 图片等）
#   2. 通过 ssh 传到树莓派解压覆盖（旧 camera_service.py 自动备份）
#   3. 依赖有变化才跑 npm ci
#   4. 重启 camera / robot-web 服务；robot_control_service.py 有改动才重启 robot-control
#   5. 自动健康检查三个端口
#
# 提示：脚本会多次 ssh，每次都要输密码。配一次免密就省事了：
#   ssh-keygen -t ed25519
#   ssh-copy-id fangtb02@<树莓派IP>
# ============================================================

set -euo pipefail

# ---------- 配置（按实际情况改这里） ----------
PI="${1:-fangtb02@raspberrypi.local}"   # 树莓派地址，可用参数覆盖
PI_DIR="/home/fangtb02/Pi_web"          # 树莓派上的项目目录（与 systemd 服务里的路径一致）
# ----------------------------------------------

cd "$(dirname "$0")"

echo "==> 目标: $PI:$PI_DIR"

# ---------- 1. 记录远端旧文件的指纹（用于判断要不要 npm ci / 重启控制服务） ----------
echo "==> 读取树莓派上现有文件状态..."
REMOTE_LOCK=$(ssh "$PI" "sha256sum '$PI_DIR/package-lock.json' 2>/dev/null | cut -d' ' -f1" || true)
REMOTE_CTRL=$(ssh "$PI" "sha256sum '$PI_DIR/pi/robot_control_service.py' 2>/dev/null | cut -d' ' -f1" || true)
LOCAL_LOCK=$(sha256sum package-lock.json | cut -d' ' -f1)
LOCAL_CTRL=$(sha256sum pi/robot_control_service.py | cut -d' ' -f1)

# ---------- 2. 备份 + 打包上传 ----------
echo "==> 备份旧版 camera_service.py 并上传代码..."
ssh "$PI" "mkdir -p '$PI_DIR' && cd '$PI_DIR' && \
  [ -f pi/camera_service.py ] && \
  cp pi/camera_service.py 'pi/camera_service.py.bak.$(date +%Y%m%d-%H%M%S)' || true"

# 需要上传的内容：前端源码 + pi 服务 + 配置 + 根目录调试脚本 + 文档
# 排除：node_modules / 构建缓存 / 日志 / 已保存的图像 / 参考图片 / pycache
tar czf - \
  --exclude='node_modules' \
  --exclude='.next' \
  --exclude='.vinext' \
  --exclude='.wrangler' \
  --exclude='__pycache__' \
  --exclude='captures' \
  --exclude='Log' \
  --exclude='refs' \
  --exclude='sdk_ref' \
  --exclude='*.png' \
  --exclude='tsconfig.tsbuildinfo' \
  app pi lib components hooks db drizzle public vendor scripts \
  package.json package-lock.json .npmrc \
  next.config.ts tsconfig.json vite.config.ts postcss.config.mjs \
  eslint.config.mjs components.json drizzle.config.ts \
  esc_motor_test.py vesc_hammer_control.py \
  ./*.md \
| ssh "$PI" "tar xzf - -C '$PI_DIR'"

echo "==> 上传完成。"

# ---------- 3. 依赖变了才 npm ci ----------
if [ "$LOCAL_LOCK" != "$REMOTE_LOCK" ]; then
  echo "==> package-lock.json 有变化，树莓派上执行 npm ci（可能要几分钟）..."
  ssh "$PI" "cd '$PI_DIR' && npm ci"
else
  echo "==> 依赖未变化，跳过 npm ci。"
fi

# ---------- 4. 重启服务 ----------
echo "==> 重启 camera 和 robot-web 服务..."
ssh "$PI" "sudo systemctl restart camera robot-web"

if [ "$LOCAL_CTRL" != "$REMOTE_CTRL" ]; then
  echo "==> robot_control_service.py 有改动，重启 robot-control（风机 PWM 会瞬时归零，属安全动作）..."
  ssh "$PI" "sudo systemctl restart robot-control"
else
  echo "==> 控制服务未改动，robot-control 保持运行不重启。"
fi

# ---------- 5. 健康检查 ----------
echo "==> 等服务起来（约 8 秒）..."
sleep 8

echo "==> 健康检查："
ssh "$PI" "
  curl -sf http://127.0.0.1:8009/status >/dev/null \
    && echo '  相机服务 8009: OK' || echo '  相机服务 8009: 未响应（journalctl -u camera -f 查日志）'
  curl -sf http://127.0.0.1:8008/status >/dev/null \
    && echo '  控制服务 8008: OK' || echo '  控制服务 8008: 未响应（journalctl -u robot-control -f 查日志）'
  curl -sf -o /dev/null http://127.0.0.1:5173/ \
    && echo '  网页 5173: OK' || echo '  网页 5173: 未响应（journalctl -u robot-web -f 查日志）'
"

echo ""
echo "==> 完成！平板浏览器打开 http://<树莓派IP>:5173/ 验证。"
echo "    相机服务日志出现「相机三流已启动」和「D2C(HW)=开」即新版生效。"
