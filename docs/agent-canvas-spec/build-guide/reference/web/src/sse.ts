import type { RunEvent } from "./ir";

/** POST + read a Server-Sent-Events stream. (EventSource only supports GET, so we use fetch.) */
export async function postStream(url: string, body: unknown, onEvent: (e: RunEvent) => void): Promise<void> {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {       // one SSE message ends with a blank line
      const message = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of message.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as RunEvent);
      }
    }
  }
}
