"use client";

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

type BorderGlowProps = {
  children: ReactNode;
  className?: string;
  active?: boolean;
  animated?: boolean;
  borderRadius?: number;
  edgeSensitivity?: number;
  glowRadius?: number;
  glowIntensity?: number;
  coneSpread?: number;
};

export function BorderGlow({ children, className = "", active = false, animated = true, borderRadius = 26, edgeSensitivity = 30, glowRadius = 18, glowIntensity = 0.65, coneSpread = 26 }: BorderGlowProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<number | null>(null);
  const [hovered, setHovered] = useState(false);
  const [intro, setIntro] = useState(false);
  const [angle, setAngle] = useState(110);
  const [proximity, setProximity] = useState(0);

  const updateFromPoint = useCallback((clientX: number, clientY: number) => {
    const root = rootRef.current;
    if (!root) return;
    const rect = root.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top, cx = rect.width / 2, cy = rect.height / 2;
    const dx = x - cx, dy = y - cy;
    const kx = dx === 0 ? Infinity : cx / Math.abs(dx), ky = dy === 0 ? Infinity : cy / Math.abs(dy);
    setProximity(Math.min(Math.max(1 / Math.min(kx, ky), 0), 1));
    let degrees = Math.atan2(dy, dx) * (180 / Math.PI) + 90;
    if (degrees < 0) degrees += 360;
    setAngle(degrees);
  }, []);

  useEffect(() => {
    if (!animated || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const startedAt = performance.now();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 入场动画首帧需在 effect 里同步置位
    setIntro(true);
    const tick = (now: number) => {
      const progress = Math.min((now - startedAt) / 2100, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAngle(110 + 355 * eased);
      setProximity(Math.sin(progress * Math.PI) * 0.96);
      if (progress < 1) frameRef.current = requestAnimationFrame(tick);
      else setIntro(false);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => { if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); };
  }, [animated]);

  const visible = hovered || intro || active;
  const effectiveProximity = active && !hovered && !intro ? 0.94 : proximity;
  const threshold = edgeSensitivity / 100;
  const glowOpacity = visible ? Math.max(0, (effectiveProximity - threshold) / (1 - threshold)) : 0;
  const borderOpacity = visible ? Math.max(0, (effectiveProximity - threshold - 0.12) / (0.88 - threshold)) : 0;
  const angleValue = `${angle.toFixed(2)}deg`;

  return (
    <div ref={rootRef} className={`border-glow ${active ? "border-glow-active" : ""} ${className}`} style={{ borderRadius }} onPointerMove={(event) => updateFromPoint(event.clientX, event.clientY)} onPointerEnter={() => setHovered(true)} onPointerLeave={() => { setHovered(false); setProximity(active ? 0.94 : 0); }}>
      <div aria-hidden="true" className="border-glow-mesh" style={{ opacity: borderOpacity, background: "radial-gradient(at 80% 55%, var(--glow-a) 0, transparent 50%), radial-gradient(at 12% 10%, var(--glow-b) 0, transparent 48%), radial-gradient(at 82% 18%, var(--glow-c) 0, transparent 46%), linear-gradient(var(--surface-highest) 0 100%) padding-box", maskImage: `conic-gradient(from ${angleValue} at center, black ${coneSpread}%, transparent ${coneSpread + 15}%, transparent ${100 - coneSpread - 15}%, black ${100 - coneSpread}%)`, WebkitMaskImage: `conic-gradient(from ${angleValue} at center, black ${coneSpread}%, transparent ${coneSpread + 15}%, transparent ${100 - coneSpread - 15}%, black ${100 - coneSpread}%)` }} />
      <span aria-hidden="true" className="border-glow-halo" style={{ inset: -glowRadius, opacity: glowOpacity, maskImage: `conic-gradient(from ${angleValue} at center, black 3%, transparent 13%, transparent 87%, black 97%)`, WebkitMaskImage: `conic-gradient(from ${angleValue} at center, black 3%, transparent 13%, transparent 87%, black 97%)` }}>
        <span style={{ inset: glowRadius, boxShadow: `0 0 3px hsl(var(--glow-hsl) / ${55 * glowIntensity}%), 0 0 12px 1px hsl(var(--glow-hsl) / ${32 * glowIntensity}%), 0 0 28px 2px hsl(var(--glow-hsl) / ${18 * glowIntensity}%)` }} />
      </span>
      <div className="border-glow-content">{children}</div>
    </div>
  );
}
