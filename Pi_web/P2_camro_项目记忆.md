# 项目记忆（Memory File）

> 更新日期：2026-10-01
> 用途：跨会话记忆。新会话开始时先读本文件，即可恢复项目上下文。

---

## 一、上一个项目：外墙检测爬墙机器人控制台

部署在**树莓派**上的网页控制台，供平板/手机浏览器访问，操控机器人上的**吸附风机**和 **VESC 敲击检测装置**。

### 三层结构

**1. 网页前端**
- 技术栈：Next.js 16 + React 19 + Tailwind 4 + shadcn 风格组件（vinext / Cloudflare Vite 插件构建）
- `app/page.tsx`：控制台界面——风机启停 + PWM 调速滑杆、三种敲击模式（单点 / 脉冲 / 连续）、安全停止按钮、硬件在线状态指示；界面带边框辉光特效
- `app/api/shutdown`：网页侧"安全停止"，收到请求后优雅退出 Node 进程（等效 Ctrl+C）
- 前端直连 `http://<树莓派IP>:8008`；服务未启动时自动退化为**演示模式**（只改界面、不碰硬件）

**2. 树莓派控制服务（`pi/`）**
- `pi/robot_control_service.py`：网页与硬件的桥，HTTP JSON API，端口 **8008**
- 风机：sysfs PWM，**GPIO18（M1）+ GPIO19（M3）双路同步**，50Hz，脉宽 1000–1700µs（1000µs = 停）
- 敲击：VESC 电调走 **UART `/dev/ttyAMA0` @115200**，`COMM_SET_RPM` 指令；电机 **7 对极、10:1 减速**，输出轴每转敲 1 次
- 配套：`robot-control.service` / `robot-web.service`（systemd 自启）、`99-robot-control.rules`（udev 权限）

**3. 硬件调试脚本（根目录）**
- `esc_motor_test.py`、`vesc_hammer_control.py`、`pi/diagnose_m3.py`：风机 / 电调 / 敲击电机独立测试与诊断
- 部署文档：`RASPBERRY_PI_SETUP.md`、`pi/PI_CONTROL_SETUP.md`（Node 22.13+，`npm ci`，`npm run dev -- --hostname 0.0.0.0`）

### 典型运行方式
树莓派开热点 → 平板连热点 → 浏览器打开 `http://<树莓派IP>:5173/` → 网页按钮经 :8008 控制服务驱动真实风机与敲击电机。

> 注：该项目的代码不在本工作区（P2_camro），以上为用户确认过的结构摘要。

---

## 二、当前阶段：视觉相机接入（本工作区）

### ⭐ 用户使用的相机型号（重点标注）

> **用户手上的相机 = 轮趣科技（WHEELTEC）× 奥比中光（Orbbec）Gemini Pro**
> 双目深度相机，基于奥比中光**上一代 Gemini 模组**二次开发（轮趣专利外壳）。
>
> ⚠️ **不要与 Gemini 335 / 335L 混淆**：Gemini 335 是奥比中光原厂 2024 年发布的 Gemini 330 系列新品（MX6800 芯片、主动+被动融合双目、量程 >10 m、IP65、全局快门、FOV >100°），是另一台更高端的相机，**用户没有这台**。两者都用 Orbbec SDK，代码层面大部分兼容，但给用户的参数、量程、防护等建议必须按 **Gemini Pro** 的规格来。

### 相机硬件（Gemini Pro 规格）
- 原理：主动双目结构光（红外投射 + 双目匹配）
- 深度：640×480 @30fps / 320×240 @60fps，量程约 0.5–5 m
- RGB：1920×1080 @30fps
- 接口：USB 3.0 Type-C（数据 + 供电）
- 输出数据流：深度图、RGB 图、左右红外图、点云、CameraInfo
- 特点：比 Astra Pro Plus 小巧、散热更好、可室外用、盲区更小
- 软件：Orbbec SDK / ROS 驱动（`ros_astra_camera`）

### 本工作区文件
| 路径 | 内容 |
|---|---|
| `camera.png` | Gemini Pro 相机产品图（用户提供） |
| `开发文档/` | **Orbbec SDK 完整开发包**：`SDK/`、`Doc/api`（C/C++ API HTML 文档）、`Example/`（示例）、`Driver/`（驱动）、`Script/`、`staticlib/`、`README_CN.md` |

### 推测方向（待用户确认）
爬墙机器人项目（P1）→ 当前相机项目（P2）：很可能是给爬墙机器人加视觉感知（外墙缺陷图像采集 / 深度检测），SDK 已就位，下一步可能是跑通 Orbbec SDK 示例、在树莓派或 PC 上取流。

### ✅ SDK 示例已跑通（2026-10-08，Windows 开发机实测）
- 官方 Orbbec Viewer 测试正常；SDK 示例 `Example/bin/HelloOrbbec.exe`、`QuickStart.exe` 均跑通
- 设备枚举名：**SV1301S_U3**（OpenNI2 协议设备），PID 0x0614，SN: AY2T74101EZ，固件 RD3013
- 实测取流：**Color 640×480 MJPG @30fps；Depth 640×400 Y11 @30fps**（注意：此旧模组深度是 640×400，不是 480 高）
- 限制：该设备**不支持 frame sync**（QuickStart 有 warning）；当前以 **USB2.0** 连接（建议换 USB3.0 口跑满性能）
- 注意：同一时刻只能一个进程占用相机，跑 SDK 示例前须关闭 Orbbec Viewer

### ✅ 阶段 2 完成：Python 开发环境（2026-10-08）
- **方案：ctypes 直连 SDK C API**（不用 pyorbbecsdk）。原因：PyPI 上 pyorbbecsdk v1.3.2 的所有 win_amd64 wheel 内容全是 macOS 二进制（发布事故）；v2.x 绑定正常但 **SDK v2 已不支持 OpenNI2 老设备**（实测枚举 0 台），本相机只能用 SDK v1
- 环境：用户级 Python **3.11.9**（`AppData/Local/Programs/Python/Python311`）+ workspace 内 `venv-cam`，依赖只有 opencv-python + numpy
- 成果：`camera_capture.py`（ctypes 绑定 `开发文档/SDK/lib/OrbbecSDK.dll` 的最小 C API 子集），实测采集 RGB（640×480 MJPG→BGR）+ 深度（640×400，16 位毫米值 PNG）成对保存到 `captures/`；支持 `-n N` 批量采集和 `--show` 实时预览（s 保存 / q 退出）
- 深度数据 = uint16 原始值 × value_scale，单位毫米；预览发黑是正常的（毫米值远超 255）
- 已验证：彩色图像内容正确，深度范围 569–1845 mm（桌面场景）
- ⚠️ 树莓派迁移提示：同样的 ctypes 方案在 ARM Linux 上可直接复用（换 libOrbbecSDK.so 即可），无需编译 pyorbbecsdk

### ⏭️ 阶段 3（外墙场景实测）已被用户主动跳过（2026-10-08）
- 遗留风险：**室外强光下深度可用性未验证**（主动结构光怕阳光），后续做外墙上机测试时必须补测；若深度室外不可用，应用层需按"RGB 为主、深度为辅"调整
- `--show` 预览已加现场读数（深度窗左上角显示 valid 有效占比 % 和 med 中位距离 m），补测时直接用

### 🚧 阶段 4 进行中：树莓派移植（2026-10-08 起）
- 用户采用"PC 调通 → 整体搬运"策略，PC 侧准备已全部完成：
  - `camera_capture.py` 已跨平台化（Windows 加载 `开发文档/SDK/lib/OrbbecSDK.dll`，Linux 自动改加载 `OrbbecSDK_linux/lib/libOrbbecSDK.so`）
  - 已下载 **ARM64 版 OrbbecSDK v1.10.37**（与 Windows 版同版本）：`OrbbecSDK_linux_arm64.zip`
  - 部署步骤见 `树莓派部署清单.md`（含 udev 规则、venv 依赖、常见问题表）
- 待做（需连树莓派）：确认 `uname -m` 为 aarch64（若是 armv7l 需换 arm32 包）→ 解压摆放 SDK → 装 udev 规则 → 建 venv 装依赖 → `camera_capture.py -n 3` 验证

### ✅ 阶段 5 PC 侧完成：网页控制台集成（2026-10-08）
- **三路流实测**：Color 640×480 MJPG + Depth 640×400（原生 Y11，SDK 解包为 Y16）+ IR 640×400（原生 Y10，解包 Y16）并发，USB2.0 下三路全部跑满 30fps。IR/DEPTH 必须用 `ob_config_enable_video_stream` + `ob_pipeline_start_with_config` 显式开流，格式要按设备 profile 表（Y11/Y10），写 Y8/Y16 会报 "No matched profile found!"；该设备无 IR_LEFT/IR_RIGHT 传感器（单 IR）
- **新增 `camera_service.py`**（已部署到 pi_web 的 `pi/`）：独立 HTTP 服务，端口 **8009**，与 8008 控制服务完全隔离（电机安全优先）。后台抓帧线程独占 pipeline + 三路 JPEG 预编码；`GET /status`、`GET /stream?src=color|depth|ir`（MJPEG）、`POST /snapshot`（存 color+depth 16位毫米+ir 三张到 `pi/captures/`）、`GET /captures`。相机掉线自动每 2s 重试，服务不死
- **前端 `app/page.tsx` 已加"视觉相机"卡片**：三路画面独立开关（默认只开可见光，关闭的流不占带宽）、保存按钮、状态条加相机状态、新增 `capture_image` WebMCP 工具；深度伪彩用固定量程 0.3–5m 映射（近亮远暗），左上角叠 valid%/中位距离
- **systemd**：`pi/camera.service`（venv-cam 的 python 跑 camera_service.py，Restart=always）
- PC 侧已端到端验证：四接口自测 + 双流并发 30fps + 网页 200 且含相机卡片（eslint/tsc 干净，tsc 仅剩 vite.config.ts 既有的生成文件报错）
- 树莓派待做：按《树莓派部署清单.md》阶段 5（5.1 文件摆放 → 5.2 camera.service 自启 → 5.3 curl + 平板浏览器验证）
- 注意：`camera_capture.py` 与 camera_service **不能同时占用相机**，用前者前先 `sudo systemctl stop camera`

---

## 三、环境与约定
- 工作区根目录：`C:\Users\jiaren\Desktop\K3\P2_camro`
- 用户语言：中文
- 机器人侧系统：树莓派（Raspberry Pi OS，systemd），开发机：Windows
