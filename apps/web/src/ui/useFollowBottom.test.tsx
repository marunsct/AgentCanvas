// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nearBottom, useFollowBottom } from "./useFollowBottom";

afterEach(cleanup);

let follow: () => void = () => {};
function Log({ n }: { n: number }) {
  const f = useFollowBottom([n]);
  follow = f.follow;
  return <div data-testid="log" onScroll={f.onScroll}>{Array.from({ length: n }, (_, i) => <p key={i}>{i}</p>)}<div ref={f.end} /></div>;
}

const scrollTo = (el: HTMLElement, top: number) => {
  Object.defineProperties(el, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200, configurable: true },
    scrollTop: { value: top, configurable: true, writable: true } });
  fireEvent.scroll(el);
};

describe("a log that follows its end", () => {
  it("scrolls down as it grows only while the reader is at the end", () => {
    const into = vi.fn();
    Element.prototype.scrollIntoView = into;
    const { rerender } = render(<Log n={1} />);
    expect(into).toHaveBeenCalledTimes(1);
    scrollTo(screen.getByTestId("log"), 100); // scrolled up to read something earlier
    rerender(<Log n={2} />);
    expect(into).toHaveBeenCalledTimes(1); // it used to be pulled back down on every word
    scrollTo(screen.getByTestId("log"), 790); // back at the end
    rerender(<Log n={3} />);
    expect(into).toHaveBeenCalledTimes(2);
    scrollTo(screen.getByTestId("log"), 0);
    follow(); // Send
    rerender(<Log n={4} />);
    expect(into).toHaveBeenCalledTimes(3);
    expect(nearBottom({ scrollHeight: 1000, scrollTop: 760, clientHeight: 200 })).toBe(true);
  });
});
