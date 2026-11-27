import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

window.events = [];
async function call(name, args) {
  const r = await fetch("/call", { method: "POST", body: JSON.stringify({ name, arguments: args }) });
  return await r.json();
}
async function main() {
  const params = new URLSearchParams(location.search);
  const tool = params.get("tool"), args = JSON.parse(params.get("args") || "{}"), theme = params.get("theme") || "light";
  const html = await (await fetch("/app.html")).text();
  const iframe = document.getElementById("app");
  iframe.srcdoc = html;
  await new Promise(resolve => iframe.addEventListener("load", resolve, { once: true }));
  const bridge = new AppBridge(null, { name: "TestHost", version: "1.0.0" }, { openLinks: {}, serverTools: {} },
    { hostContext: { theme, displayMode: "inline", containerDimensions: { maxHeight: 4000, width: 760 } } });
  bridge.oncalltool = async (p) => { window.events.push({ type: "tools/call", ...p }); return await call(p.name, p.arguments); };
  bridge.onopenlink = async ({ url }) => { window.events.push({ type: "open-link", url }); return {}; };
  bridge.onmessage = async (p) => { window.events.push({ type: "message", ...p }); return {}; };
  bridge.onsizechange = ({ width, height }) => { window.events.push({ type: "size", width, height }); if (height) iframe.style.height = `${height}px`; };
  bridge.oninitialized = async () => {
    window.events.push({ type: "initialized" });
    bridge.sendToolInput({ arguments: args });
    const result = await call(tool, args);
    window.events.push({ type: "result-sent" });
    bridge.sendToolResult(result);
  };
  window.bridge = bridge;  // check.js sends its own results
  await bridge.connect(new PostMessageTransport(iframe.contentWindow, iframe.contentWindow));
}
main().catch(e => { window.events.push({ type: "error", message: String(e) }); console.error(e); });
