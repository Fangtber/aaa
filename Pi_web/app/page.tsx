"use client";

import { useEffect, useRef, useState } from "react";
import { Aperture, Check, Fan, Gauge, Hammer, Power, PowerOff, Radio, RotateCcw, ShieldCheck, Wifi } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Toaster } from "@/components/ui/sonner";
import { BorderGlow } from "@/components/border-glow";

const TAP_GEARS = [1, 2, 3, 4, 5] as const;

type HardwareStatus = { pwm: boolean; uart: boolean };
type ServerStatus = {
  hardware: HardwareStatus;
  fan: { running: boolean; pwm: number; pulse_us: number };
  tap: { state: "idle" | "running"; gear: number | null; hits_per_min: number | null };
};

type ApiResult =
  | { kind: "ok"; state: ServerStatus }
  | { kind: "error"; message: string }
  | { kind: "offline" };

// ---------------- 相机服务（8009，独立于 8008 控制服务） ----------------

type CamSrc = "color" | "depth" | "ir" | "cloud";
const camSrcLabels: Record<CamSrc, string> = { color: "可见光", depth: "深度", ir: "红外", cloud: "点云" };
// 保存结果里的文件名 → 中文名（depthvis 是深度附带的伪彩预览图，不是一路流）
const savedFileLabels: Record<string, string> = { color: "可见光", depth: "深度原始图", depthvis: "深度伪彩图", ir: "红外", cloud: "点云PLY" };

type CameraStatus = {
  connected: boolean;
  error: string | null;
  fps: Record<CamSrc, number>;
  resolution: Record<CamSrc, [number, number] | null>;
  saved_pairs: number;
  aligned?: boolean;
};

type CamSnapshotResult =
  | { kind: "ok"; timestamp: string; files: Record<string, string>; depthStats: { valid_pct: number; median_mm: number } | null }
  | { kind: "error"; message: string }
  | { kind: "offline" };

function camBase(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.protocol}//${window.location.hostname}:8009`;
}

async function camStatus(): Promise<CameraStatus | null> {
  try {
    const res = await fetch(`${camBase()}/status`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return null;
    const data = (await res.json()) as { camera?: CameraStatus };
    return data.camera ?? null;
  } catch {
    return null;
  }
}

async function camSnapshot(srcs?: CamSrc[]): Promise<CamSnapshotResult> {
  try {
    const res = await fetch(`${camBase()}/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(srcs && srcs.length ? { srcs } : {}),
      signal: AbortSignal.timeout(10000),
    });
    const data = (await res.json().catch(() => ({}))) as {
      ok?: boolean; error?: string; timestamp?: string;
      files?: Record<string, string>; depth_stats?: { valid_pct: number; median_mm: number } | null;
    };
    if (!res.ok || data.ok === false || !data.timestamp) {
      return { kind: "error", message: data.error ?? `相机服务返回 HTTP ${res.status}` };
    }
    return { kind: "ok", timestamp: data.timestamp, files: data.files ?? {}, depthStats: data.depth_stats ?? null };
  } catch {
    return { kind: "offline" };
  }
}

function apiBase(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.protocol}//${window.location.hostname}:8008`;
}

async function apiPost(path: string, body?: Record<string, unknown>): Promise<ApiResult> {
  try {
    const res = await fetch(`${apiBase()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(3000),
    });
    const data = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      error?: string;
      state?: ServerStatus;
    };
    if (!res.ok || data.ok === false || !data.state) {
      return { kind: "error", message: data.error ?? `控制服务返回 HTTP ${res.status}` };
    }
    return { kind: "ok", state: data.state };
  } catch {
    return { kind: "offline" };
  }
}

async function apiStatus(): Promise<ServerStatus | null> {
  try {
    const res = await fetch(`${apiBase()}/status`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<ServerStatus>;
    return data && data.fan && data.tap && data.hardware ? (data as ServerStatus) : null;
  } catch {
    return null;
  }
}

export default function Home() {
  const [pwm, setPwm] = useState(62);
  const [fanRunning, setFanRunning] = useState(false);
  const [tapGear, setTapGear] = useState<number | null>(null); // null = 未运行；1~5 = 当前挡位
  const [connected, setConnected] = useState(false);
  const [hw, setHw] = useState<HardwareStatus | null>(null);
  const [ready, setReady] = useState(false);
  // 相机：四路画面独立开关，默认只开可见光
  const [cam, setCam] = useState<CameraStatus | null>(null);
  const [camView, setCamView] = useState<Record<CamSrc, boolean>>({ color: true, depth: false, ir: false, cloud: false });
  const [camNonce, setCamNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  // 最近一次保存的彩色点云文件名（用于下载链接）
  const [lastCloud, setLastCloud] = useState<string | null>(null);
  // 拖动滑块期间不接受心跳的 pwm 覆盖，否则风机运行中滑块会被拍回服务端值
  const pwmEditing = useRef(false);

  useEffect(() => {
    // localStorage 只在客户端可用，必须在挂载后恢复，避免 SSR 水合不一致
    /* eslint-disable react-hooks/set-state-in-effect */
    const storedPwm = Number(localStorage.getItem("robot:pwm"));
    const storedCam = localStorage.getItem("robot:cam-view");
    if (Number.isFinite(storedPwm) && storedPwm >= 0 && storedPwm <= 100) setPwm(storedPwm);
    if (storedCam) {
      try {
        const parsed = JSON.parse(storedCam) as Record<CamSrc, boolean>;
        if (parsed && typeof parsed === "object" && "color" in parsed) setCamView((v) => ({ ...v, ...parsed }));
      } catch { /* 忽略损坏的缓存 */ }
    }
    setReady(true);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  useEffect(() => {
    if (!ready) return;
    localStorage.setItem("robot:pwm", String(pwm));
    localStorage.setItem("robot:cam-view", JSON.stringify(camView));
  }, [pwm, camView, ready]);

  // 心跳：每 2 秒拉一次控制服务 + 相机服务状态，连接成功时以服务端状态为准
  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const [s, c] = await Promise.all([apiStatus(), camStatus()]);
      if (cancelled) return;
      setCam(c);
      if (!s) {
        setConnected(false);
        setHw(null);
        return;
      }
      setConnected(true);
      setHw(s.hardware);
      setFanRunning(s.fan.running);
      if (s.fan.running && !pwmEditing.current) setPwm(s.fan.pwm);
      setTapGear(s.tap.state === "running" ? s.tap.gear : null);
    }
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const tapText = tapGear === null ? "已连接 · 未运行" : `运行中 · ${tapGear} 档`;

  const systemText = fanRunning
    ? `风机运行中 · PWM ${pwm}%`
    : tapGear !== null
      ? `敲击${tapText}`
      : "系统正常，等待操作";

  async function startFan() {
    if (pwm === 0) return void toast.error("PWM 为 0，请先调高转速");
    const r = await apiPost("/fan/start", { pwm });
    if (r.kind === "ok") {
      setFanRunning(true);
      toast.success(`风机已启动，PWM ${pwm}%（脉宽 ${r.state.fan.pulse_us} µs）`);
    } else if (r.kind === "offline") {
      setFanRunning(true);
      toast.warning("未连接树莓派控制服务，按演示模式运行");
    } else {
      toast.error(`风机启动失败：${r.message}`);
    }
  }

  async function stopFan() {
    const r = await apiPost("/fan/stop");
    setFanRunning(false);
    setPwm(0);
    if (r.kind === "ok") toast.info("风机已停止并归零");
    else if (r.kind === "offline") toast.warning("未连接控制服务，演示模式：风机已停止");
    else toast.error(`风机停止指令未送达：${r.message}，请立即检查设备`);
  }

  async function setGear(gear: number) {
    const r = await apiPost("/tap/gear", { gear });
    if (r.kind === "ok") {
      setTapGear(gear);
      toast.success(`敲击装置已切换到 ${gear} 档`);
    } else if (r.kind === "offline") {
      setTapGear(gear);
      toast.warning("未连接控制服务，演示模式");
    } else {
      toast.error(`换挡失败：${r.message}`);
    }
  }

  async function stopTap() {
    const r = await apiPost("/tap/stop");
    setTapGear(null);
    if (r.kind === "ok") toast.info("敲击装置已停止");
    else if (r.kind === "offline") toast.warning("未连接控制服务，演示模式：已停止");
    else toast.error(`敲击停止指令未送达：${r.message}，请立即检查设备`);
  }

  function toggleCam(src: CamSrc) {
    setCamView((v) => {
      const next = { ...v, [src]: !v[src] };
      toast.info(`${camSrcLabels[src]}画面已${next[src] ? "开启" : "关闭"}`);
      return next;
    });
  }

  function camImgError() {
    // 流断开（相机服务重启/掉线）时 3 秒后换 URL 重试
    window.setTimeout(() => setCamNonce((n) => n + 1), 3000);
  }

  async function saveImages() {
    const srcs = (Object.keys(camView) as CamSrc[]).filter((s) => camView[s]);
    if (srcs.length === 0) {
      toast.warning("四路画面都已关闭，没有可保存的内容");
      return;
    }
    setSaving(true);
    try {
      const r = await camSnapshot(srcs);
      if (r.kind === "ok") {
        const d = r.depthStats;
        const depthNote = d && r.files.depth ? `，深度有效 ${d.valid_pct}% · 中位 ${(d.median_mm / 1000).toFixed(2)} m` : "";
        const cloud = r.files.cloud;
        setLastCloud(cloud ?? null);
        const savedNames = Object.keys(r.files).map((k) => savedFileLabels[k] ?? k).join("、");
        toast.success(`已保存：${savedNames}（${r.timestamp}）${depthNote}`);
      } else if (r.kind === "offline") {
        toast.warning("未连接相机服务，演示模式：未实际保存");
      } else {
        toast.error(`保存失败：${r.message}`);
      }
    } finally {
      setSaving(false);
    }
  }

  async function safeStop() {
    // 1) 先关控制服务：风机归零 + 敲击刹车，随后进程自退出（等效终端 Ctrl+C）
    const r = await apiPost("/safe-stop");
    setFanRunning(false);
    setPwm(0);
    setTapGear(null);
    if (r.kind === "ok") toast.warning("控制服务已安全停机并退出（风机归零 + 敲击停止）");
    else if (r.kind === "offline") toast.warning("控制服务本就不在线，仅关闭网页服务");
    else toast.error(`安全停止未送达：${r.message}，请立即断电检查`);
    // 1.5) 再关相机服务：它会顺带用 systemctl 停掉网页服务，然后自身退出
    try {
      await fetch(`${camBase()}/safe-stop`, { method: "POST", signal: AbortSignal.timeout(2000) });
    } catch { /* 相机服务本就不在线，继续走兜底 */ }
    // 2) 兜底再关网页服务自身（workerd 下 process.exit 可能是空操作，真正停靠的是上一步）
    try {
      await fetch("/api/shutdown", { method: "POST", signal: AbortSignal.timeout(2000) });
      toast.info("网页服务已关闭。重启：SSH 到树莓派执行 sudo systemctl start robot-control camera robot-web");
    } catch {
      toast.error("网页服务关闭指令未送达，请手动到终端按 Ctrl+C");
    }
  }

  async function shutdownPi() {
    if (!window.confirm("确定要关闭整台树莓派吗？\n\n风机和敲击会先急停归零，随后系统 poweroff 断电，网页随即断开。重新上电后三个服务会自动恢复。")) return;
    // 1) 先急停动力硬件：风机瞬时归零 + 敲击刹车（控制服务随后自退出）
    const r = await apiPost("/safe-stop");
    setFanRunning(false);
    setPwm(0);
    setTapGear(null);
    if (r.kind === "error") toast.error(`风机急停未送达：${r.message}，请立即断电检查`);
    // 2) 再请相机服务执行系统关机（整台 poweroff，三个服务随系统干净停止）
    try {
      const res = await fetch(`${camBase()}/shutdown-pi`, { method: "POST", signal: AbortSignal.timeout(3000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.warning("树莓派正在关机：风机已归零，系统即将断电，页面会断开。重新上电后服务自动恢复。");
    } catch {
      toast.error("关机指令未送达（相机服务不在线）。请 SSH 到树莓派执行 sudo poweroff");
    }
  }

  useEffect(() => {
    type WebTool = { name: string; title: string; description: string; inputSchema: object; annotations: { readOnlyHint: boolean; untrustedContentHint: boolean }; execute: (input: Record<string, unknown>) => unknown };
    type ModelContext = { registerTool: (tool: WebTool, options?: { signal?: AbortSignal }) => void | Promise<void> };
    const context = (document as Document & { modelContext?: ModelContext }).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    const register = (tool: WebTool) => void Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(() => undefined);
    register({
      name: "set_fan_pwm", title: "设置风机 PWM", description: "设置双螺旋桨风机的目标 PWM，占空比范围为 0–100。",
      inputSchema: { type: "object", properties: { value: { type: "number", minimum: 0, maximum: 100 } }, required: ["value"], additionalProperties: false },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input) { const value = Number(input.value); if (!Number.isFinite(value) || value < 0 || value > 100) throw new Error("PWM 必须在 0–100 之间"); const next = Math.round(value); setPwm(next); return { pwm: next }; },
    });
    register({
      name: "start_fan", title: "启动风机", description: "按当前 PWM 启动双螺旋桨风机（真实硬件，脉宽 1000–1700µs）。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false },
      async execute() {
        if (pwm === 0) throw new Error("PWM 为 0，无法启动风机");
        const r = await apiPost("/fan/start", { pwm });
        if (r.kind === "error") throw new Error(`风机启动失败：${r.message}`);
        setFanRunning(true);
        return { running: true, pwm, live: r.kind === "ok" };
      },
    });
    register({
      name: "capture_image", title: "保存相机图像", description: "按当前画面开关保存相机画面：开启的可见光/深度/红外各存一张 PNG，点云开启时另存彩色点云 PLY，存到树莓派 captures 目录。返回的 files.cloud 是点云文件名，可用 GET /captures/<文件名> 下载。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false },
      async execute() {
        // 工具注册时机早于开关变化，从 localStorage 读最新开关状态
        let srcs: CamSrc[] = ["color", "depth", "ir", "cloud"];
        try {
          const stored = localStorage.getItem("robot:cam-view");
          if (stored) {
            const parsed = JSON.parse(stored) as Record<string, boolean>;
            srcs = (["color", "depth", "ir", "cloud"] as CamSrc[]).filter((s) => parsed[s]);
          }
        } catch { /* 缓存损坏时默认全保存 */ }
        if (srcs.length === 0) throw new Error("四路画面都已关闭，没有可保存的内容");
        const r = await camSnapshot(srcs);
        if (r.kind === "offline") throw new Error("相机服务未连接");
        if (r.kind === "error") throw new Error(`保存失败：${r.message}`);
        return { saved: true, timestamp: r.timestamp, files: r.files, depthStats: r.depthStats };
      },
    });
    register({
      name: "safe_stop_robot", title: "安全停止机器人", description: "立即停止风机和敲击装置，并关闭控制服务、相机服务与网页服务（三个服务都会真正停住，不会自动重启），之后需 SSH 到树莓派执行 sudo systemctl start robot-control camera robot-web 恢复。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false },
      async execute() {
        const r = await apiPost("/safe-stop");
        setFanRunning(false); setPwm(0); setTapGear(null);
        let cameraExited = false;
        try {
          await fetch(`${camBase()}/safe-stop`, { method: "POST", signal: AbortSignal.timeout(2000) });
          cameraExited = true;
        } catch { /* 相机服务本就不在线 */ }
        let webShutdown = false;
        try {
          await fetch("/api/shutdown", { method: "POST", signal: AbortSignal.timeout(2000) });
          webShutdown = true;
        } catch { /* 网页服务关闭指令未送达 */ }
        return { fanRunning: false, pwm: 0, tapGear: null, controlServiceExited: r.kind === "ok", cameraServiceExited: cameraExited, webServiceExited: webShutdown };
      },
    });
    register({
      name: "shutdown_raspberry_pi", title: "关机树莓派", description: "安全关闭整台树莓派：先急停风机和敲击装置（风机瞬时归零），再执行系统 poweroff，三个服务随系统干净停止并断电。与「安全停止」不同：安全停止只停三个服务，树莓派仍开机可 SSH 恢复；关机后整台断电，必须重新上电才能使用（上电后服务自动恢复）。仅在用户明确要关机/断电时使用。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false },
      async execute() {
        const r = await apiPost("/safe-stop");
        setFanRunning(false); setPwm(0); setTapGear(null);
        let shutdownSent = false;
        try {
          const res = await fetch(`${camBase()}/shutdown-pi`, { method: "POST", signal: AbortSignal.timeout(3000) });
          shutdownSent = res.ok;
        } catch { /* 相机服务不在线，关机指令未送达 */ }
        if (!shutdownSent) throw new Error("关机指令未送达（相机服务不在线），请 SSH 到树莓派执行 sudo poweroff");
        return { fanRunning: false, pwm: 0, tapGear: null, fanEmergencyStopped: r.kind === "ok", shutdownSent: true };
      },
    });
    return () => lifecycle.abort();
  }, [pwm]);

  const activeCamSrcs = (Object.keys(camSrcLabels) as CamSrc[]).filter((s) => camView[s]);
  const camActive = cam?.connected === true && activeCamSrcs.length > 0;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true"><ShieldCheck /></div>
        <div><p className="eyebrow">ROBOT CONTROL / 01</p><h1>外墙检测机器人控制台</h1></div>
        <div className="live-badge"><span />实时控制</div>
      </header>

      <section className="status-strip" aria-label="设备连接状态">
        <div className="status-chip"><Check />{connected ? "树莓派已连接" : "树莓派未连接"}</div>
        <div className="status-chip"><Radio />{connected ? "心跳正常" : "心跳中断"}</div>
        <div className="status-chip">
          <Wifi />
          {connected && hw
            ? <>风机 {hw.pwm ? "PWM 就绪" : "PWM 不可用"} · 敲击 {hw.uart ? "UART 就绪" : "UART 不可用"}</>
            : "硬件状态未知"}
        </div>
        <div className="status-chip">
          <Aperture />
          {cam === null ? "相机服务未连接" : cam.connected ? `相机在线 · 已存 ${cam.saved_pairs} 组` : "相机掉线，重试中"}
        </div>
      </section>

      <section className="control-grid">
        <BorderGlow active={fanRunning} className="glow-shell" glowIntensity={fanRunning ? 0.9 : 0.55}>
        <article className={`control-card fan-card ${fanRunning ? "is-active" : ""}`}>
          <div className="card-heading">
            <div className="card-icon"><Fan className={fanRunning ? "spin" : ""} /></div>
            <div><h2>双螺旋桨风机</h2><p>通过 PWM 调节双螺旋桨转速</p></div>
            <span className="device-state">{fanRunning ? "运行中" : "已停止"}</span>
          </div>
          <div className="primary-readout"><span>当前 PWM</span><strong>{pwm}<small>%</small></strong></div>
          <div className="slider-block">
            <Slider aria-label="风机 PWM" value={[pwm]} min={0} max={100} step={1} onValueChange={(v) => { pwmEditing.current = true; setPwm(v[0]); }} onValueCommit={(v) => { pwmEditing.current = false; toast.info(`PWM 已设为 ${v[0]}%`); }} />
            <div className="scale"><span>0</span><span>25</span><span>50</span><span>75</span><span>100%</span></div>
          </div>
          <div className="connected-actions two-actions">
            <Button onClick={startFan}><Power />启动风机</Button>
            <Button className="tonal" onClick={stopFan}><RotateCcw />停止并归零</Button>
          </div>
        </article>
        </BorderGlow>

        <BorderGlow active={tapGear !== null} className="glow-shell" glowIntensity={tapGear !== null ? 0.9 : 0.55}>
        <article className={`control-card tap-card ${tapGear !== null ? "is-active" : ""}`}>
          <div className="card-heading">
            <div className="card-icon"><Hammer /></div>
            <div><h2>敲击装置</h2><p>选择挡位持续敲击，随时换挡或停止</p></div>
            <span className="device-state">{tapText}</span>
          </div>
          <div className="tap-content">
            <div className="state-readout"><span>当前状态</span><strong>{tapText}</strong></div>
            <div className="gear-field"><span>敲击挡位</span>
              <div className="gear-row" role="group" aria-label="敲击挡位">
                {TAP_GEARS.map((g) => (
                  <button
                    key={g}
                    type="button"
                    className={`gear-btn ${tapGear === g ? "on" : ""}`}
                    aria-pressed={tapGear === g}
                    onClick={() => setGear(g)}
                  >
                    {g}档
                  </button>
                ))}
              </div>
              <small>点击后立即以该挡位持续敲击，运行中可直接换挡</small>
            </div>
          </div>
          <div className="connected-actions one-action">
            <Button className="tonal" onClick={stopTap}>停止敲击</Button>
          </div>
        </article>
        </BorderGlow>

        <BorderGlow active={camActive} className="glow-shell camera-glow" glowIntensity={camActive ? 0.9 : 0.55}>
        <article className={`control-card camera-card ${camActive ? "is-active" : ""}`}>
          <div className="card-heading">
            <div className="card-icon"><Aperture /></div>
            <div><h2>视觉相机</h2><p>Gemini Pro · 可见光 / 深度 / 红外 / 点云，开什么画面就保存什么{cam?.aligned ? "（D2C 已对齐）" : ""}</p></div>
            <span className="device-state">{cam?.connected ? "已连接" : "未连接"}</span>
          </div>
          <div className="cam-toggles" role="group" aria-label="画面开关">
            {(Object.keys(camSrcLabels) as CamSrc[]).map((s) => (
              <button
                key={s}
                type="button"
                className={`cam-toggle ${camView[s] ? "on" : ""}`}
                aria-pressed={camView[s]}
                onClick={() => toggleCam(s)}
              >
                {camSrcLabels[s]}
                {cam?.connected ? <small>{cam.fps[s] ?? 0} fps</small> : null}
              </button>
            ))}
          </div>
          {activeCamSrcs.length === 0 ? (
            <div className="cam-hint">四路画面都已关闭，点上方开关重新开启</div>
          ) : (
            <div className="cam-windows">
              {activeCamSrcs.map((s) => (
                <div className="cam-window" key={`${s}-${camNonce}`}>
                  <span className="cam-label">{camSrcLabels[s]}</span>
                  {cam?.connected ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`${camBase()}/stream?src=${s}&n=${camNonce}`}
                      alt={`${camSrcLabels[s]}实时画面`}
                      onError={camImgError}
                    />
                  ) : (
                    <div className="cam-offline">相机服务未连接<br /><small>演示模式：无实时画面</small></div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="connected-actions">
            <Button onClick={saveImages} disabled={saving}>
              <Aperture />{saving ? "保存中…" : "保存图像"}
            </Button>
            {lastCloud ? (
              <a
                className="cam-download"
                href={`${camBase()}/captures/${lastCloud}`}
                download={lastCloud}
              >
                下载彩色点云 PLY（{lastCloud}）
              </a>
            ) : null}
          </div>
        </article>
        </BorderGlow>
      </section>

      <footer className="system-bar">
        <div className="system-message"><span className="system-icon"><Gauge /></span><div><small>系统状态</small><strong>{systemText}</strong></div></div>
        <div className="connection-note"><span />{connected ? "树莓派连接保持" : "等待树莓派连接"}</div>
        <Button className="safe-stop" onClick={safeStop}><Power />安全停止</Button>
        <Button className="pi-shutdown" onClick={shutdownPi}><PowerOff />关机树莓派</Button>
      </footer>
      <Toaster position="top-center" richColors closeButton />
    </main>
  );
}
