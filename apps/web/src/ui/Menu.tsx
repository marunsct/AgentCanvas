/** A button that opens a menu below it. The menu is fixed-positioned: the top bars clip what overflows them. It closes
 * on Escape, on a click outside, and when an item calls `close`. */
import { useEffect, useRef, useState } from "react";

export function Menu({ label, title, testId, className = "", align = "right", children }: {
  label: React.ReactNode;
  title?: string;
  testId?: string;
  className?: string;
  align?: "left" | "right";
  children: (close: () => void) => React.ReactNode;
}) {
  const [at, setAt] = useState<{ top: number; left?: number; right?: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!at) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) setAt(null);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [at]);
  return (
    <div ref={box} className={`menu ${className}`}>
      <button type="button" aria-haspopup="menu" aria-expanded={Boolean(at)} data-testid={testId} title={title}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setAt(at ? null : align === "right"
            ? { top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) }
            : { top: r.bottom + 4, left: Math.max(8, r.left) });
        }}>{label}</button>
      {at && <div className="menu-list" role="menu" style={at}>{children(() => setAt(null))}</div>}
    </div>
  );
}
