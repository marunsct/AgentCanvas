import { useEffect, useRef, useState } from "react";

/**
 * `value`, but changes show up at most once every `ms`, and the last one always does. The canvas redraws every node
 * from the run's streamed text, so one render per token made a long answer janky; a few per second looks live.
 */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0); // when `shown` last changed
  useEffect(() => {
    const wait = last.current + ms - Date.now();
    if (wait <= 0) {
      last.current = Date.now();
      setShown(value);
      return;
    }
    const timer = setTimeout(() => {
      last.current = Date.now();
      setShown(value);
    }, wait);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return shown;
}
