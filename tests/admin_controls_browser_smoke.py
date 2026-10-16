"""Run from repository root, open http://127.0.0.1:8765/list or /card.
Uses real templates/CSS and the list UI function, without auth, DB or Telegram.
Query: ?theme=dark&tab=frozen&role=agent. Geometry results: #smokeResults.
"""
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/static/"):
            target = ROOT / "api/app" / path.lstrip("/")
            if not target.is_file() or not target.resolve().is_relative_to(ROOT / "api/app/static"):
                self.send_error(404)
                return
            content = target.read_bytes()
            mime = "text/css" if target.suffix == ".css" else "application/octet-stream"
        else:
            is_list = path != "/card"
            name = "tickets" if is_list else "ticket"
            html = (ROOT / f"api/app/templates/{name}.html").read_text(encoding="utf-8")
            html = re.sub(r"<script[\s\S]*?</script>", "", html)
            source = (ROOT / "api/app/static/list.js").read_text(encoding="utf-8")
            ui = source[source.index("function updateBulkAssignUi()"):source.index("async function assignTickets")]
            script = """
const params = new URLSearchParams(location.search);
document.body.dataset.sidebarReady = "1";
document.body.dataset.theme = params.get("theme") || "light";
const state = {role: params.get("role") || "admin", tab: params.get("tab") || "active", selectedTicketIds: new Set(), visibleTicketIds: []};
document.querySelectorAll(".admin-only").forEach(n => n.classList.toggle("hidden", state.role !== "admin"));
const el = Object.fromEntries([...document.querySelectorAll("[id]")].map(n => [n.id, n]));
const results = [];
function check(ok, name) { results.push({name, pass: Boolean(ok)}); }
function rect(id) {const r = el[id].getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};}
"""
            if is_list:
                script += ui + """
let baseline;
for (const count of [0,1,9,10,99]) {
 state.selectedTicketIds = new Set(Array.from({length:count},(_,i)=>String(i)));
 updateBulkAssignUi();
 const geometry = ["bulkAssignBtn", state.tab === "frozen" ? "bulkUnfreezeBtn" : "bulkFreezeBtn", "bulkAdminActions", "bulkCloseBtn", "bulkDeleteBtn"].map(rect);
 if (state.role === "admin") {
  if (!baseline) baseline = JSON.stringify(geometry);
  check(JSON.stringify(geometry) === baseline, `stable geometry ${count}`);
  check(el.bulkAssignBtn.scrollWidth <= el.bulkAssignBtn.clientWidth, `full assignment label ${count}`);
  check(el.bulkAssignBtn.disabled === (count===0) && el.bulkCloseBtn.disabled === (count===0) && el.bulkDeleteBtn.disabled === (count===0), `disabled ${count}`);
  const [assign,freeze,group,close,del] = geometry;
  check(assign.x < freeze.x && freeze.x < group.x && close.x < del.x, `order ${count}`);
  check(Math.abs(close.y-del.y)<1 && Math.abs(close.y+close.h/2-(assign.y+assign.h/2))<1, `horizontal ${count}`);
 }
}
if (state.role !== "admin") check(getComputedStyle(el.bulkAdminActions).display === "none", "admin role gate");
const toolbar=document.querySelector(".toolbar");
check(getComputedStyle(toolbar).overflowX === "auto", "local scrolling");
check(document.documentElement.scrollWidth <= innerWidth, "no page overflow");
state.selectedTicketIds.clear(); updateBulkAssignUi();
"""
            else:
                script += """
const nodes = ["adminSkipClientNotification","adminForceCloseBtn","adminDeleteBtn","markSpecialBtn"].map(id => el[id].closest("label") || el[id]);
if (state.role === "admin") {
 const r=nodes.map(n=>n.getBoundingClientRect());
 check(r.every((v,i)=>i===0 || v.top>=r[i-1].bottom), "vertical admin controls");
 check(!el.adminSkipClientNotification.checked, "checkbox defaults off");
} else check(getComputedStyle(document.querySelector(".admin-actions-box")).display === "none", "admin role gate");
"""
            script += """
const report=document.createElement("pre"); report.id="smokeResults";
report.style.cssText="position:fixed;bottom:4px;left:4px;z-index:9999;font-size:10px;background:var(--surface);color:var(--text);max-height:125px;overflow:auto;border:1px solid var(--border);padding:4px";
report.textContent=JSON.stringify(results,null,2);document.body.append(report);
"""
            content = html.replace("</body>", "<script>" + script + "</script></body>").encode("utf-8")
            mime = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    print("Isolated template smoke: http://127.0.0.1:8765/list", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
