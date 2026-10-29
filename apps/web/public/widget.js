/* AgentCanvas chat widget (S23). Add it to any page:
 *   <script src="https://YOUR-AGENTCANVAS/widget.js" data-share="sh_..." async></script>
 * Optional attributes: data-label (the button's name, default "Chat"), data-position ("left" or "right"),
 * data-color (the button's colour, a CSS colour).
 * It shows a round chat button; clicking it opens the share link's chat page in a small panel (an iframe).
 *
 * postMessage API (the page's own script talks to the widget with window.postMessage; same window, same origin):
 *   window.postMessage({ agentcanvas: 1, type: "open", text: "optional text for the message box" }, location.origin)
 *   window.postMessage({ agentcanvas: 1, type: "close" }, location.origin)
 *   window.postMessage({ agentcanvas: 1, type: "prefill", text: "..." }, location.origin)   (opens the chat too)
 * and the widget posts events back the same way: "ready" (the chat has loaded), "reply" (an answer arrived, with
 * text, runId, threadId), "opened" and "closed". Listen with window.addEventListener("message", ...) and check
 * event.data.agentcanvas === 1. The chat page itself only takes commands from a site its link allows. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var token = script.getAttribute("data-share") || "";
  if (!/^[A-Za-z0-9_-]{4,200}$/.test(token)) {
    if (window.console) console.warn("AgentCanvas widget: data-share must be a share link token (sh_...)");
    return;
  }
  var origin = new URL(script.src, location.href).origin;
  var label = script.getAttribute("data-label") || "Chat";
  var side = script.getAttribute("data-position") === "left" ? "left" : "right";
  var color = script.getAttribute("data-color") || "#6c47ff";
  var SVG = "http://www.w3.org/2000/svg";

  function icon(path) {
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "26");
    svg.setAttribute("height", "26");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(SVG, "path");
    p.setAttribute("d", path);
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "2");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    svg.appendChild(p);
    return svg;
  }
  var CHAT = "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z";
  var CLOSE = "M6 6l12 12M18 6L6 18";

  function css(el, styles) {
    for (var k in styles) el.style[k] = styles[k];
  }

  var root = document.createElement("div");
  root.setAttribute("data-agentcanvas-widget", "");
  css(root, { position: "fixed", bottom: "20px", zIndex: "2147483000" });
  root.style[side] = "20px";

  var panel = document.createElement("div");
  panel.id = "agentcanvas-chat-" + Math.random().toString(36).slice(2);
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", label);
  css(panel, {
    display: "none", position: "absolute", bottom: "72px", width: "min(380px, calc(100vw - 32px))",
    height: "min(600px, calc(100vh - 110px))", borderRadius: "14px", overflow: "hidden", background: "#fff",
    boxShadow: "0 18px 48px rgba(16,24,40,.22), 0 4px 12px rgba(16,24,40,.1)", border: "1px solid rgba(0,0,0,.08)",
  });
  panel.style[side] = "0";

  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Open " + label);
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", panel.id);
  css(button, {
    width: "56px", height: "56px", borderRadius: "50%", border: "0", cursor: "pointer", background: color,
    color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", padding: "0",
    boxShadow: "0 6px 18px rgba(16,24,40,.25)",
  });
  button.appendChild(icon(CHAT));

  var frame = null;
  var open = false;
  var ready = false; // the chat page has loaded and listens for commands
  var queued = null; // text to put in the message box once it has
  function emit(event) { // to the page that added the widget
    window.postMessage(Object.assign({ agentcanvas: 1 }, event), location.origin);
  }
  function prefill(text) {
    if (ready && frame) frame.contentWindow.postMessage({ agentcanvas: 1, type: "prefill", text: text }, origin);
    else queued = text;
  }
  function toggle(show) {
    var changed = open !== show;
    open = show;
    if (show && !frame) { // the chat page loads on the first open, not with the host page
      frame = document.createElement("iframe");
      frame.src = origin + "/s/" + encodeURIComponent(token) + "?embed=1";
      frame.title = label;
      frame.setAttribute("allow", "clipboard-write");
      css(frame, { width: "100%", height: "100%", border: "0", display: "block" });
      panel.appendChild(frame);
    }
    panel.style.display = show ? "block" : "none";
    button.setAttribute("aria-expanded", show ? "true" : "false");
    button.setAttribute("aria-label", (show ? "Close " : "Open ") + label);
    button.replaceChild(icon(show ? CLOSE : CHAT), button.firstChild);
    if (show && frame) frame.focus();
    if (changed) emit({ type: show ? "opened" : "closed" });
  }
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || typeof d !== "object" || d.agentcanvas !== 1) return;
    if (e.source === window && e.origin === location.origin) { // the page's own commands, not another frame's
      if (d.type === "open") { toggle(true); if (typeof d.text === "string") prefill(d.text); }
      else if (d.type === "close") toggle(false);
      else if (d.type === "prefill" && typeof d.text === "string") { toggle(true); prefill(d.text); }
    } else if (frame && e.source === frame.contentWindow && e.origin === origin) { // the chat page's events
      if (d.type === "ready") {
        ready = true;
        if (queued !== null) { prefill(queued); queued = null; }
        emit({ type: "ready" });
      } else if (d.type === "reply") {
        emit({ type: "reply", text: String(d.text || ""), runId: d.runId, threadId: d.threadId, question: !!d.question });
      }
    }
  });
  button.addEventListener("click", function () { toggle(!open); });
  document.addEventListener("keydown", function (e) {
    if (open && e.key === "Escape") { toggle(false); button.focus(); }
  });

  root.appendChild(panel);
  root.appendChild(button);
  function mount() { document.body.appendChild(root); }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
