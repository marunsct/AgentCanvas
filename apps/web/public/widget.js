/* AgentCanvas chat widget (S23). Add it to any page:
 *   <script src="https://YOUR-AGENTCANVAS/widget.js" data-share="sh_..." async></script>
 * Optional attributes: data-label (the button's name, default "Chat"), data-position ("left" or "right"),
 * data-color (the button's colour, a CSS colour).
 * It shows a round chat button; clicking it opens the share link's chat page in a small panel (an iframe). */
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
  function toggle(show) {
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
  }
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
