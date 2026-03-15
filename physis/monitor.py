"""physis monitor — real-time web dashboard for a physis agent."""
import json
import os
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

MONITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>physis monitor</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #1a1a2e; color: #e0e0e0; font-family: 'Menlo', 'Consolas', monospace; font-size: 13px; padding: 16px; }
h1 { color: #7fdbca; font-size: 18px; margin-bottom: 12px; }
h2 { color: #c792ea; font-size: 14px; margin: 12px 0 6px 0; border-bottom: 1px solid #333; padding-bottom: 4px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.card { background: #16213e; border: 1px solid #2a2a4a; border-radius: 6px; padding: 12px; overflow: hidden; }
.card.full { grid-column: 1 / -1; }
.stat { display: inline-block; background: #0f3460; border-radius: 4px; padding: 4px 10px; margin: 2px 4px 2px 0; }
.stat .n { color: #7fdbca; font-weight: bold; }
.stat .label { color: #888; font-size: 11px; }
.log { max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
.log .tool { color: #ffd580; }
.log .thought { color: #c3e88d; }
.log .reply { color: #89ddff; }
.log .warn { color: #ff5572; }
.log .cycle { color: #c792ea; }
.focus { white-space: pre-wrap; line-height: 1.6; max-height: 400px; overflow-y: auto; }
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.alive { background: #7fdbca; box-shadow: 0 0 6px #7fdbca; }
.stale { background: #ffd580; }
.dead { background: #ff5572; }
#last-update { color: #666; font-size: 11px; float: right; }

/* Chat */
.chat-box { display: flex; flex-direction: column; height: 300px; }
.chat-messages { flex: 1; overflow-y: auto; padding: 8px; background: #0f0f23; border-radius: 4px; margin-bottom: 8px; }
.chat-messages .msg { margin: 4px 0; }
.chat-messages .msg.you { color: #89ddff; }
.chat-messages .msg.ai { color: #c3e88d; }
.chat-messages .msg.sys { color: #666; font-style: italic; }
.chat-input { display: flex; gap: 6px; }
.chat-input input { flex: 1; background: #0f3460; border: 1px solid #2a2a4a; color: #e0e0e0; padding: 8px; border-radius: 4px; font-family: inherit; font-size: 13px; }
.chat-input button { background: #7fdbca; color: #1a1a2e; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: inherit; font-weight: bold; }
.chat-input button:hover { background: #5fc9b0; }
</style>
</head>
<body>
<h1><span class="status-dot" id="status-dot"></span>physis monitor <span id="last-update"></span></h1>

<div class="grid">
  <div class="card full" id="stats-card">
    <h2>Stats</h2>
    <div id="stats"></div>
  </div>

  <div class="card">
    <h2>FOCUS.md</h2>
    <div class="focus" id="focus"></div>
  </div>

  <div class="card">
    <h2>Chat</h2>
    <div class="chat-box">
      <div class="chat-messages" id="chat-messages">
        <div class="msg sys">Type a message to talk to physis...</div>
      </div>
      <div class="chat-input">
        <input type="text" id="chat-input" placeholder="Say something..." onkeydown="if(event.key==='Enter')sendChat()">
        <button onclick="sendChat()">Send</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Recent Thoughts</h2>
    <div class="log" id="thoughts"></div>
  </div>

  <div class="card full">
    <h2>Runtime Log (last 30 lines)</h2>
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
  const el = document.getElementById('stats');
  const items = [
    ['heartbeats', s.heartbeats], ['breaks', s.breaks], ['molts', s.molts],
    ['POSTs', s.posts], ['uptime', s.uptime], ['log lines', s.log_lines],
  ];
  el.innerHTML = items.map(([l,n]) => `<span class="stat"><span class="n">${n}</span> <span class="label">${l}</span></span>`).join('');
}

function renderFocus(d) {
  document.getElementById('focus').textContent = d.focus || '(no FOCUS.md)';
}

function renderThoughts(d) {
  const el = document.getElementById('thoughts');
  el.innerHTML = (d.thoughts || []).map(l => colorLine(l)).join('\\n');
  el.scrollTop = el.scrollHeight;
}

function renderRuntime(d) {
  const el = document.getElementById('runtime');
  el.innerHTML = (d.runtime || []).map(l => colorLine(l)).join('\\n');
  el.scrollTop = el.scrollHeight;
}

function renderStatus(d) {
  const dot = document.getElementById('status-dot');
  const age = d.stats.last_log_age || 999;
  if (age < 10) { dot.className = 'status-dot alive'; }
  else if (age < 60) { dot.className = 'status-dot stale'; }
  else { dot.className = 'status-dot dead'; }
  document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
}

async function poll() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    renderStats(d);
    renderFocus(d);
    renderThoughts(d);
    renderRuntime(d);
    renderStatus(d);
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
    await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg}),
    });
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
    for (let i = lastChatLen; i < msgs.length; i++) {
      appendChat('ai', msgs[i]);
    }
    lastChatLen = msgs.length;
  } catch(e) {}
}

poll();
setInterval(poll, 3000);
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


class ChatBridge:
    """TCP bridge to physis for chat, with /resume support."""
    def __init__(self, physis_host, physis_port, session_id="web:monitor"):
        self.host = physis_host
        self.port = physis_port
        self.sock = None
        self.session_id = session_id
        self.messages = []
        self.lock = threading.Lock()
        self.reader_thread = None

    def _ensure_connected(self):
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.sock.sendall(f"/resume {self.session_id}\n".encode())
            self.reader_thread = threading.Thread(target=self._reader, daemon=True)
            self.reader_thread.start()
            return True
        except Exception as e:
            self.sock = None
            with self.lock:
                self.messages.append(f"(connection failed: {e})")
            return False

    def _reader(self):
        buf = ""
        while True:
            try:
                data = self.sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    with self.lock:
                        self.messages.append("(disconnected)")
                    self.sock = None
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    with self.lock:
                        self.messages.append(line)
            except Exception:
                with self.lock:
                    self.messages.append("(connection lost)")
                self.sock = None
                break

    def send(self, message):
        if not self._ensure_connected():
            return
        try:
            self.sock.sendall((message + "\n").encode())
        except Exception as e:
            with self.lock:
                self.messages.append(f"(send failed: {e})")
            self.sock = None

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
                last_lines = _tail(runtime_log, 1)
                if last_lines:
                    try:
                        ts = last_lines[0][:19]
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
                    "focus": _read_file(focus_path),
                    "thoughts": _tail(thought_log, 20),
                    "runtime": _tail(runtime_log, 30),
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
    parser.add_argument("--physis-host", default="127.0.0.1", help="Physis TCP host (default: 127.0.0.1)")
    parser.add_argument("--physis-port", type=int, default=4242, help="Physis TCP port (default: 4242)")
    args = parser.parse_args()

    agent_dir = os.path.abspath(args.dir)
    if not os.path.exists(os.path.join(agent_dir, "runtime.log")):
        print(f"No runtime.log found in {agent_dir}", file=sys.stderr)
        sys.exit(1)

    chat = ChatBridge(args.physis_host, args.physis_port)
    handler = make_handler(agent_dir, chat)
    server = HTTPServer((args.host, args.port), handler)
    print(f"physis monitor: http://{args.host}:{args.port} (watching {agent_dir}, chat via {args.physis_host}:{args.physis_port})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
