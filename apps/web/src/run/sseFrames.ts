/** Server-Sent-Events framing shared by the run streams and the public share page (which loads nothing else of
 * the app, hence no imports here). */

/** One SSE message's events: its `data:` lines, each a JSON event. A malformed line is skipped, not fatal. */
export function parseMessage<T>(message: string): T[] {
  const out: T[] = [];
  for (const line of message.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    try {
      out.push(JSON.parse(line.slice(5).trimStart()) as T);
    } catch {
      /* one bad frame must not end the stream */
    }
  }
  return out;
}

/** Feed SSE text to `onEvent`, message by message (a message ends with a blank line, `\n\n` or `\r\n\r\n`); returns
 * what is left over: an incomplete message, or the last one when the stream ends without a blank line after it. */
export function feed<T>(buffer: string, onEvent: (e: T) => void): string {
  let sep: RegExpExecArray | null;
  while ((sep = /\r?\n\r?\n/.exec(buffer))) {
    const message = buffer.slice(0, sep.index);
    buffer = buffer.slice(sep.index + sep[0].length);
    for (const e of parseMessage<T>(message)) onEvent(e);
  }
  return buffer;
}
