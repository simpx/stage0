"""physis monitor — real-time web dashboard for a physis agent."""
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

MONITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>physis monitor</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #1a1a2e; color: #e0e0e0; font-family: 'Menlo', 'Consolas', monospace; font-size: 13px; padding: 12px; display: flex; flex-direction: column; height: 100vh; }
h2 { color: #c792ea; font-size: 12px; margin: 0 0 6px 0; text-transform: uppercase; letter-spacing: 1px; }
.row { display: flex; gap: 10px; }
.card { background: #16213e; border: 1px solid #2a2a4a; border-radius: 6px; padding: 10px; overflow: hidden; flex: 1; }

/* Row 1: Stats */
#row-stats { margin-bottom: 8px; }
#row-stats .card { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.stat { display: inline-block; background: #0f3460; border-radius: 4px; padding: 3px 8px; margin: 1px 2px; }
.stat .n { color: #7fdbca; font-weight: bold; }
.stat .label { color: #888; font-size: 11px; }
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
.alive { background: #7fdbca; box-shadow: 0 0 6px #7fdbca; }
.stale { background: #ffd580; }
.dead { background: #ff5572; }

/* Row 2: Timeline */
#row-timeline { margin-bottom: 8px; }
#timeline { display: flex; align-items: center; gap: 0; flex-wrap: wrap; overflow-x: auto; font-size: 12px; }
.tl-session { color: #82aaff; font-weight: bold; margin-right: 6px; }
.tl-block { display: inline-block; padding: 2px 6px; margin: 1px; border-radius: 3px; white-space: nowrap; }
.tl-fast { background: #1b3a2a; color: #7fdbca; }
.tl-mid { background: #3a2e1b; color: #ffd580; }
.tl-slow { background: #3a1b1b; color: #ff5572; }
.tl-active { background: #2e1b3a; color: #c792ea; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }

/* Row 3: Chat + Thoughts */
#row-main { flex: 3; margin-bottom: 8px; min-height: 400px; }
#row-main .card { display: flex; flex-direction: column; min-height: 0; }
.log { flex: 1; overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.5; min-height: 0; }
.log .tool { color: #ffd580; }
.log .thought { color: #c3e88d; }
.log .reply { color: #89ddff; }
.log .warn { color: #ff5572; }
.log .cycle { color: #c792ea; }
.chat-messages { flex: 1; overflow-y: auto; padding: 6px; background: #0f0f23; border-radius: 4px; margin-bottom: 6px; min-height: 0; }
.chat-messages .msg { margin: 3px 0; }
.chat-messages .msg.you { color: #89ddff; }
.chat-messages .msg.ai { color: #c3e88d; }
.chat-messages .msg.sys { color: #666; font-style: italic; }
.chat-input { display: flex; gap: 6px; }
.chat-input input { flex: 1; background: #0f3460; border: 1px solid #2a2a4a; color: #e0e0e0; padding: 6px 8px; border-radius: 4px; font-family: inherit; font-size: 13px; }
.chat-input button { background: #7fdbca; color: #1a1a2e; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-family: inherit; font-weight: bold; }

/* Row 4: Focus */
#row-focus { margin-bottom: 8px; flex: 0 0 auto; }
.focus { white-space: pre-wrap; line-height: 1.5; max-height: 160px; overflow-y: auto; }

/* Row 5: Runtime */
#row-runtime { flex: 0 0 auto; }
#row-runtime .log { max-height: 150px; }
</style>
</head>
<body>

<!-- Row 1: Stats -->
<div class="row" id="row-stats">
  <div class="card">
    <span class="status-dot" id="status-dot"></span>
    <div id="stats" style="display:inline"></div>
    <span id="last-update" style="color:#666;font-size:11px;margin-left:auto"></span>
  </div>
</div>

<!-- Row 2: Timeline -->
<div class="row" id="row-timeline">
  <div class="card">
    <div id="timeline"></div>
  </div>
</div>

<!-- Row 3: Chat + Thoughts -->
<div class="row" id="row-main">
  <div class="card" style="flex:1">
    <h2>Chat</h2>
    <div class="chat-messages" id="chat-messages">
      <div class="msg sys">Type a message to talk to physis...</div>
    </div>
    <div class="chat-input">
      <input type="text" id="chat-input" placeholder="Say something..." onkeydown="if(event.key==='Enter')sendChat()">
      <button onclick="sendChat()">Send</button>
    </div>
  </div>
  <div class="card" style="flex:1">
    <h2>Thoughts</h2>
    <div class="log" id="thoughts"></div>
  </div>
</div>

<!-- Row 4: Focus -->
<div class="row" id="row-focus">
  <div class="card">
    <h2>FOCUS.md</h2>
    <div class="focus" id="focus"></div>
  </div>
</div>

<!-- Row 5: Runtime -->
<div class="row" id="row-runtime">
  <div class="card">
    <h2>Runtime Log</h2>
    <div class="log" id="runtime"></div>
  </div>
</div>

<script>
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function colorLine(line) {
  const e = escHtml(line);
  if (e.includes('[tool]')) return '<span class="tool">' + e + '</span>';
  if (e.includes('[thought:') || e.includes('[thinking:')) return '<span class="thought">' + e + '</span>';
  if (e.includes('[reply:')) return '<span class="reply">' + e + '</span>';
  if (e.includes('[warn]') || e.includes('[molt]') || e.includes('[break:')) return '<span class="warn">' + e + '</span>';
  if (e.includes('cycle start')) return '<span class="cycle">' + e + '</span>';
  return e;
}

function renderStats(d) {
  const s = d.stats;
  const items = [
    ['heartbeats', s.heartbeats], ['breaks', s.breaks], ['molts', s.molts],
    ['POSTs', s.posts], ['uptime', s.uptime], ['lines', s.log_lines],
  ];
  document.getElementById('stats').innerHTML = items.map(([l,n]) =>
    `<span class="stat"><span class="n">${n}</span> <span class="label">${l}</span></span>`).join('');
  const dot = document.getElementById('status-dot');
  const age = s.last_log_age || 999;
  dot.className = 'status-dot ' + (age < 10 ? 'alive' : age < 60 ? 'stale' : 'dead');
  document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
}

function renderTimeline(d) {
  const tl = d.timeline || [];
  const el = document.getElementById('timeline');
  if (!tl.length) { el.innerHTML = '<span style="color:#666">waiting...</span>'; return; }
  let html = '';
  for (const item of tl) {
    const dur = item.duration;
    let cls = 'tl-fast';
    if (item.active) cls = 'tl-active';
    else if (dur >= 5) cls = 'tl-slow';
    else if (dur >= 1) cls = 'tl-mid';
    const label = item.active ? `${item.name} ${dur.toFixed(1)}s…` : `${item.name} ${dur.toFixed(1)}s`;
    html += `<span class="tl-block ${cls}">${escHtml(label)}</span>`;
  }
  el.innerHTML = html;
}

function renderFocus(d) { document.getElementById('focus').textContent = d.focus || '(no FOCUS.md)'; }

function renderThoughts(d) {
  const el = document.getElementById('thoughts');
  el.innerHTML = (d.thoughts || []).map(l => colorLine(l)).join('\n');
  el.scrollTop = el.scrollHeight;
}

function renderRuntime(d) {
  const el = document.getElementById('runtime');
  el.innerHTML = (d.runtime || []).map(l => colorLine(l)).join('\n');
  el.scrollTop = el.scrollHeight;
}

async function poll() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    renderStats(d);
    renderTimeline(d);
    renderFocus(d);
    renderThoughts(d);
    renderRuntime(d);
  } catch(e) { console.error(e); }
}

// Chat
let lastChatLen = 0;
async function sendChat() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendChat('you', msg);
  try {
    await fetch('/api/chat', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({message:msg}) });
  } catch(e) { appendChat('sys', 'Send failed: ' + e); }
}
function appendChat(cls, text) {
  const el = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = (cls === 'you' ? '> ' : '') + text;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}
async function pollChat() {
  try {
    const r = await fetch('/api/chat');
    const d = await r.json();
    const msgs = d.messages || [];
    // Reset if server restarted (fewer messages than we tracked)
    if (msgs.length < lastChatLen) lastChatLen = 0;
    for (let i = lastChatLen; i < msgs.length; i++) appendChat('ai', msgs[i]);
    lastChatLen = msgs.length;
  } catch(e) {}
}

poll();
setInterval(poll, 2000);
setInterval(pollChat, 1000);
</script>
</body>
</html>"""


def _tail(path, n=30):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 65536)
            f.seek(size - read_size)
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-n:]
    except Exception:
        return []


def _count(path, pattern):
    if not os.path.exists(path):
        return 0
    try:
        count = 0
        with open(path, "r", errors="replace") as f:
            for line in f:
                if pattern in line:
                    count += 1
        return count
    except Exception:
        return 0


def _read_file(path, max_chars=3000):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def _parse_timeline(runtime_lines):
    """Parse recent tool/llm operations from runtime log lines into a timeline."""
    timeline = []
    current_session = ""
    last_ts = None
    # Patterns: [llm:X], [tool] name(...), [idle:X], [break:X], [heartbeat] cycle start
    ts_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+')
    for line in runtime_lines:
        m = ts_re.match(line)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue

        if "cycle start" in line:
            # Extract session from [heartbeat] or [conn:N]
            if "[heartbeat]" in line:
                current_session = "_heartbeat"
            else:
                sm = re.search(r'\[(conn:\S+)\]', line)
                if sm:
                    current_session = sm.group(1)
            # Close previous if exists
            if timeline and timeline[-1].get("active"):
                timeline[-1]["duration"] = ts - timeline[-1]["_start"]
                timeline[-1]["active"] = False
            last_ts = ts

        elif "[llm:" in line:
            # LLM call completed
            if timeline and timeline[-1].get("active"):
                timeline[-1]["duration"] = ts - timeline[-1]["_start"]
                timeline[-1]["active"] = False
            last_ts = ts

        elif "[tool]" in line:
            # Close previous
            if timeline and timeline[-1].get("active"):
                timeline[-1]["duration"] = ts - timeline[-1]["_start"]
                timeline[-1]["active"] = False
            # Extract tool name
            tm = re.search(r'\[tool\] (\w+)\(', line)
            name = tm.group(1) if tm else "tool"
            timeline.append({
                "session": current_session, "name": name,
                "duration": 0, "active": True, "_start": ts,
            })
            last_ts = ts

        elif "[result]" in line:
            if timeline and timeline[-1].get("active"):
                timeline[-1]["duration"] = ts - timeline[-1]["_start"]
                timeline[-1]["active"] = False
            # Add llm block (waiting for next LLM response)
            timeline.append({
                "session": current_session, "name": "llm",
                "duration": 0, "active": True, "_start": ts,
            })
            last_ts = ts

        elif "[idle:" in line or "[break:" in line:
            if timeline and timeline[-1].get("active"):
                timeline[-1]["duration"] = ts - timeline[-1]["_start"]
                timeline[-1]["active"] = False
            last_ts = ts

    # Update active item duration to now
    if timeline and timeline[-1].get("active"):
        timeline[-1]["duration"] = time.time() - timeline[-1]["_start"]

    # Clean up internal field, keep last 15
    for item in timeline:
        item.pop("_start", None)
    return timeline[-15:]


class ChatBridge:
    """TCP bridge to physis for chat, with /resume support."""
    MAX_MESSAGES = 200

    def __init__(self, physis_host, physis_port, session_id="web:monitor"):
        self.host = physis_host
        self.port = physis_port
        self._sock = None
        self.session_id = session_id
        self.messages = []
        self.lock = threading.Lock()

    def _ensure_connected(self):
        if self._sock:
            return True
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((self.host, self.port))
            s.sendall(f"/resume {self.session_id}\n".encode())
            self._sock = s
            threading.Thread(target=self._reader, args=(s,), daemon=True).start()
            return True
        except Exception as e:
            s.close()
            with self.lock:
                self.messages.append(f"(connection failed: {e})")
            return False

    def _reader(self, sock):
        """Read from a specific socket. Exits when socket is closed or replaced."""
        buf = ""
        while True:
            try:
                data = sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    with self.lock:
                        self.messages.append("(disconnected)")
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    with self.lock:
                        self.messages.append(line)
                        if len(self.messages) > self.MAX_MESSAGES:
                            self.messages = self.messages[-self.MAX_MESSAGES:]
            except Exception:
                with self.lock:
                    self.messages.append("(connection lost)")
                break
        # Only clear _sock if it's still this socket (not already reconnected)
        if self._sock is sock:
            self._sock = None
        try:
            sock.close()
        except OSError:
            pass

    def send(self, message):
        if not self._ensure_connected():
            return
        try:
            self._sock.sendall((message + "\n").encode())
        except Exception as e:
            with self.lock:
                self.messages.append(f"(send failed: {e})")
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def get_messages(self):
        with self.lock:
            return list(self.messages)


def make_handler(agent_dir, chat_bridge):
    runtime_log = os.path.join(agent_dir, "runtime.log")
    thought_log = os.path.join(agent_dir, "thought.log")
    focus_path = os.path.join(agent_dir, "memory", "FOCUS.md")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/status":
                first_lines = []
                try:
                    with open(runtime_log, "r", errors="replace") as f:
                        first_lines = [f.readline()]
                except Exception:
                    pass
                uptime = "?"
                if first_lines and first_lines[0]:
                    try:
                        ts = first_lines[0][:19]
                        start = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                        secs = int(time.time() - start)
                        if secs >= 3600:
                            uptime = f"{secs // 3600}h{(secs % 3600) // 60}m"
                        elif secs >= 60:
                            uptime = f"{secs // 60}m{secs % 60}s"
                        else:
                            uptime = f"{secs}s"
                    except Exception:
                        pass

                last_log_age = 999
                runtime_lines = _tail(runtime_log, 60)
                if runtime_lines:
                    try:
                        ts = runtime_lines[-1][:19]
                        last_t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                        last_log_age = int(time.time() - last_t)
                    except Exception:
                        pass

                log_lines = 0
                try:
                    with open(runtime_log, "r", errors="replace") as f:
                        for _ in f:
                            log_lines += 1
                except Exception:
                    pass

                data = {
                    "stats": {
                        "heartbeats": _count(runtime_log, "[heartbeat] cycle start"),
                        "breaks": _count(runtime_log, "[break:"),
                        "molts": _count(runtime_log, "[molt]"),
                        "posts": _count(runtime_log, "-X POST"),
                        "uptime": uptime,
                        "log_lines": log_lines,
                        "last_log_age": last_log_age,
                    },
                    "timeline": _parse_timeline(runtime_lines),
                    "focus": _read_file(focus_path),
                    "thoughts": _tail(thought_log, 20),
                    "runtime": runtime_lines[-30:],
                }
                self._json_response(data)

            elif self.path == "/api/chat":
                self._json_response({"messages": chat_bridge.get_messages()})

            elif self.path == "/":
                body = MONITOR_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/api/chat":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    msg = data.get("message", "").strip()
                    if msg:
                        chat_bridge.send(msg)
                    self._json_response({"ok": True})
                except Exception as e:
                    self._json_response({"error": str(e)}, 400)
            else:
                self.send_error(404)

        def _json_response(self, data, code=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    import argparse
    parser = argparse.ArgumentParser(description="physis monitor — web dashboard")
    parser.add_argument("--dir", default=".", help="Agent directory to monitor")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--physis-host", default="127.0.0.1", help="Physis TCP host")
    parser.add_argument("--physis-port", type=int, default=4242, help="Physis TCP port")
    args = parser.parse_args()

    agent_dir = os.path.abspath(args.dir)
    if not os.path.exists(os.path.join(agent_dir, "runtime.log")):
        print(f"No runtime.log found in {agent_dir}", file=sys.stderr)
        sys.exit(1)

    chat = ChatBridge(args.physis_host, args.physis_port)
    handler = make_handler(agent_dir, chat)
    server = HTTPServer((args.host, args.port), handler)
    print(f"physis monitor: http://{args.host}:{args.port} (watching {agent_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
