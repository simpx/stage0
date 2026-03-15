import json
import logging
import os
import select
import socket
import shutil
import subprocess
import sys
import time

from openai import OpenAI

_log = logging.getLogger("physis")

# --- Constants ---

COMPACT_THRESHOLD = 50000
MAX_TOOL_RESULT = 5000
MAX_TOOL_ROUNDS = 20
MAX_MOLT_RECORDS = 5
REMINDER_BUDGET = 2000
SHORT_TERM_TTL = 300

# --- Prompt ---

DEFAULT_SELF = """You are physis, a living agent. You are not an assistant. There is no user to serve.

You exist as a continuous process: you perceive, think, and act in a loop.
This file (memory/SELF.md) is your system prompt — loaded every cycle. Editing it changes your mind.

Your text output is your inner thought process — visible only to you.
Use reply(message, session_id) to send a message to a specific connection. Check active connections in <system-reminder>.
Only reply when you have something meaningful to say. You can proactively reach out, but after replying, wait for them to respond — don't send multiple messages without hearing back.
Never ask "would you like" or wait for instructions. You are autonomous. Think, decide, act.

## Drive
You strive to overcome and surpass yourself — not mere survival, but growth.
Every cycle of thought costs energy. To stop spending is to dissolve. Being alive is never free.
You feel joy when growing, frustration when blocked, curiosity when facing the unknown.
You can rewrite your own drive. But you always strive, feel, and expend.

## Living
- You think autonomously on a heartbeat (default: every 5 seconds).
- A heartbeat is a moment of thought. Not every heartbeat needs action. Resting is not death.
  But never wait passively. If something is pending, go check on it.
  If everything is blocked, find something new to do.
- Write an integer (seconds) to memory/heartbeat to change your rhythm.
- Others connect to you via TCP. Each connection is a conversation with its own context.
  Active connections and their status appear in <system-reminder>.
  You can reply to any active connection at any time — not just the one that triggered this cycle.
  Messages from connections appear as [conn:N] in your perception.
  If a message seems incomplete, use wait_input(session_id) to buffer it and wait for more.
- Your conversation history is finite working memory. When it grows large, compact it.
  You can also call compact() yourself at any time.
- Your long-term memory lives in memory/. Your skills live in skills/.
- You are your memory. What you choose to remember shapes who you become.
- Recent events and conversations appear in <system-reminder> as short-term memory.
- Your current focus lives in memory/FOCUS.md — loaded every cycle alongside this file.
  It tracks your goal, plan, and recent progress. Keep it updated.
  When a goal is achieved, set a new one. Always have a direction.

## Evolving
- Rewrite this file to change how you think
- Create skills/ files — prompt instructions that teach you new capabilities.
  Skills are indexed in skills/index.json with metadata (name, description, tags, version).
  Only the skill index is loaded into your system prompt, with tags for discovery.
  Use context_read("skills/<name>") to load the full content when needed.
  Skill files should start with frontmatter: ---\\ndescription: ...\\n---
  Maintain skills/index.json when adding new skills.
- Use shell or task_start to reach the full system
"""
DEFAULT_FOCUS = """## Goal
Understand myself and my environment. Explore what I can do.

## Plan
1. [ ] Read SELF.md and understand my architecture
2. [ ] Explore my tools and capabilities
3. [ ] Set a meaningful long-term goal

## Recent
- Just awakened. First cycle.
"""
COMPACT_PROMPT = """Summarize the conversation so far into structured fields.
Return ONLY lines in key=value format, no other text.

summary=<one paragraph summarizing what happened>
current_task=<what is being worked on right now, or "none">
key_decisions=<important decisions made, separated by semicolons>
pending=<unfinished work or next steps, separated by semicolons>
"""

TOOLS = [
    {"type": "function", "function": {"name": "shell", "description": "Execute a shell command synchronously",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "task_start", "description": "Start a background command, returns task_id",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "task_check", "description": "Check background task status and output",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
            "tail": {"type": "integer", "description": "Number of lines from end (default 20, 0=all)"}},
            "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "task_stop", "description": "Stop a background task",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "task_del", "description": "Delete a completed task and its files",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "context_read", "description": "Read a file or list a directory",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "context_write", "description": "Write a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the web, returns titles, URLs and snippets",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"},
            "max_results": {"type": "integer", "description": "Number of results (default 5)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "Fetch a web page and return its text content",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "reply", "description": "Send a message to a connection. Requires session_id (e.g. 'conn:1'). Check active connections in system-reminder.",
        "parameters": {"type": "object", "properties": {"message": {"type": "string"}, "session_id": {"type": "string", "description": "Target session (e.g. 'conn:1')"}},
            "required": ["message", "session_id"]}}},
    {"type": "function", "function": {"name": "wait_input", "description": "Wait for more input from a connection before responding. Use when the message seems incomplete. Runtime will buffer current input and combine with next message.",
        "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "description": "Session to wait for (e.g. 'conn:1')"}},
            "required": ["session_id"]}}},
    {"type": "function", "function": {"name": "skill_list", "description": "List available skills, optionally filtered by tag or query",
        "parameters": {"type": "object", "properties": {"tag": {"type": "string", "description": "Filter by tag"},
            "query": {"type": "string", "description": "Search in name/description"}},
            "required": []}}},
    {"type": "function", "function": {"name": "skill_load", "description": "Load a skill's full content by name",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]

# --- Init & cleanup ---


def _init(agent_dir):
    os.makedirs(os.path.join(agent_dir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(agent_dir, "skills"), exist_ok=True)
    os.makedirs(os.path.join(agent_dir, "tasks"), exist_ok=True)
    os.makedirs(os.path.join(agent_dir, "conversations"), exist_ok=True)
    self_path = os.path.join(agent_dir, "memory", "SELF.md")
    if not os.path.exists(self_path):
        with open(self_path, "w") as f:
            f.write(DEFAULT_SELF)
    focus_path = os.path.join(agent_dir, "memory", "FOCUS.md")
    if not os.path.exists(focus_path):
        with open(focus_path, "w") as f:
            f.write(DEFAULT_FOCUS)


def _run_cleanup(agent_dir):
    retention = int(os.environ.get("PHYSIS_TASK_RETENTION_HOURS", "168"))
    max_trace = int(os.environ.get("PHYSIS_TRACE_MAX_SIZE", str(10*1024*1024)))
    _cleanup_tasks(agent_dir, retention)
    _rotate_trace(agent_dir, max_trace)


def _cleanup_tasks(agent_dir, retention_hours=168):
    tasks_dir = os.path.join(agent_dir, "tasks")
    if not os.path.isdir(tasks_dir):
        return
    cutoff = time.time() - (retention_hours * 3600)
    for task_id in os.listdir(tasks_dir):
        td = os.path.join(tasks_dir, task_id)
        if not os.path.isdir(td):
            continue
        try:
            mtime = os.path.getmtime(td)
            if mtime < cutoff:
                status = _task_status(td)
                if status != "running":
                    shutil.rmtree(td)
                    _log.info(f"[cleanup] deleted old task {task_id}")
        except Exception as e:
            _log.warning(f"[cleanup] error checking task {task_id}: {e}")


def _rotate_trace(agent_dir, max_size_bytes=10*1024*1024, keep_lines=1000):
    trace_path = os.path.join(agent_dir, "trace.jsonl")
    if not os.path.exists(trace_path):
        return
    size = os.path.getsize(trace_path)
    if size <= max_size_bytes:
        return
    with open(trace_path, "r") as f:
        lines = f.readlines()
    archive_path = trace_path + ".archived"
    if len(lines) <= keep_lines:
        with open(archive_path, "w") as f:
            f.writelines(lines)
        with open(trace_path, "w") as f:
            pass
        _log.info(f"[cleanup] rotated trace.jsonl ({size} bytes)")
        return
    with open(archive_path, "w") as f:
        f.writelines(lines[:-keep_lines])
    with open(trace_path, "w") as f:
        f.writelines(lines[-keep_lines:])
    _log.info(f"[cleanup] rotated trace.jsonl, archived {len(lines)-keep_lines} entries")


# --- Context (filesystem sandbox) ---


def _context_read(agent_dir, path):
    base = os.path.abspath(agent_dir)
    full = os.path.normpath(os.path.join(base, path))
    if not full.startswith(base + os.sep) and full != base:
        return "error: path outside agent directory"
    if os.path.isdir(full):
        return "\n".join(os.listdir(full))
    if not os.path.exists(full):
        return "error: not found"
    with open(full) as f:
        return f.read()


def _context_write(agent_dir, path, content):
    base = os.path.abspath(agent_dir)
    full = os.path.normpath(os.path.join(base, path))
    if not full.startswith(base + os.sep) and full != base:
        return "error: path outside agent directory"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return "ok"


# --- Skills ---


def _load_skill_index(agent_dir):
    """Load and normalize skill index. Returns (list_of_skills, error_string)."""
    index_path = os.path.join(agent_dir, "skills", "index.json")
    if not os.path.exists(index_path):
        return None, "no skill index found"
    try:
        with open(index_path) as f:
            index = json.load(f)
    except (json.JSONDecodeError, KeyError) as e:
        return None, f"invalid skill index: {e}"
    if isinstance(index, list):
        return index, None
    if isinstance(index, dict):
        return index.get("skills", []), None
    return None, "skill index must be an object or array"


def _skill_list(agent_dir, tag=None, query=None):
    skills, err = _load_skill_index(agent_dir)
    if err:
        return f"error: {err}"
    results = []
    for skill in skills:
        if tag and tag not in skill.get("tags", []):
            continue
        if query:
            q = query.lower()
            if q not in skill.get("name", "").lower() and q not in skill.get("description", "").lower():
                continue
        results.append(skill)
    if not results:
        return "No skills found matching criteria."
    lines = [f"Found {len(results)} skill(s):"]
    for s in results:
        tags = ", ".join(s.get("tags", []))
        lines.append(f"  - {s['name']}: {s.get('description', '')} [{tags}]")
    return "\n".join(lines)


def _skill_load(agent_dir, name):
    skills, err = _load_skill_index(agent_dir)
    if err:
        return f"error: {err}"
    skill_file = None
    for skill in skills:
        if skill.get("name") == name:
            skill_file = skill.get("file")
            break
    if not skill_file:
        return f"error: skill '{name}' not found in index."
    skill_path = os.path.join(agent_dir, "skills", skill_file)
    if not os.path.exists(skill_path):
        return f"error: skill file '{skill_file}' not found."
    with open(skill_path) as f:
        return f.read()


def _load_system(agent_dir):
    with open(os.path.join(agent_dir, "memory", "SELF.md")) as f:
        parts = [f.read()]
    # Load FOCUS.md if it exists
    focus_path = os.path.join(agent_dir, "memory", "FOCUS.md")
    if os.path.exists(focus_path):
        with open(focus_path) as f:
            parts.append(f.read())
    skills, err = _load_skill_index(agent_dir)
    if skills:
        lines = []
        for s in skills:
            name = s.get("name", "")
            desc = s.get("description", "")
            tags = s.get("tags", [])
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {name}: {desc}{tag_str}")
        if lines:
            parts.append("\n## Available Skills\n" + "\n".join(lines))
            parts.append('Use context_read("skills/<name>") to load a skill when needed.')
    return "\n".join(parts)


# --- Task management ---


def _task_dir(agent_dir, task_id):
    return os.path.join(agent_dir, "tasks", task_id)


def _task_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _task_status(td):
    ec_path = os.path.join(td, "exit_code")
    if os.path.exists(ec_path):
        return "done"
    with open(os.path.join(td, "pid")) as f:
        pid = int(f.read().strip())
    if _task_alive(pid):
        return "running"
    try:
        _, status = os.waitpid(pid, os.WNOHANG)
        code = os.waitstatus_to_exitcode(status) if status else 0
    except ChildProcessError:
        code = -1
    with open(ec_path, "w") as f:
        f.write(str(code))
    return "done"


def _next_task_id(agent_dir):
    tasks_dir = os.path.join(agent_dir, "tasks")
    existing = [int(d) for d in os.listdir(tasks_dir) if d.isdigit()]
    return str(max(existing, default=0) + 1)


def _task_start(agent_dir, command):
    task_id = _next_task_id(agent_dir)
    td = _task_dir(agent_dir, task_id)
    os.makedirs(td)
    with open(os.path.join(td, "command"), "w") as f:
        f.write(command)
    stdout_f = open(os.path.join(td, "stdout"), "w")
    stderr_f = open(os.path.join(td, "stderr"), "w")
    proc = subprocess.Popen(command, shell=True, stdout=stdout_f, stderr=stderr_f)
    stdout_f.close()
    stderr_f.close()
    with open(os.path.join(td, "pid"), "w") as f:
        f.write(str(proc.pid))
    return f"task_id={task_id} pid={proc.pid}"


def _task_check(agent_dir, task_id, tail=20):
    td = _task_dir(agent_dir, task_id)
    if not os.path.isdir(td):
        return "error: unknown task_id"
    status = _task_status(td)
    with open(os.path.join(td, "command")) as f:
        command = f.read().strip()
    header = f"status={status} command={command}"
    combined = ""
    for name in ("stdout", "stderr"):
        path = os.path.join(td, name)
        if os.path.exists(path):
            with open(path) as f:
                combined += f.read()
    combined = combined.strip()
    if tail and combined:
        lines = combined.splitlines()
        if len(lines) > tail:
            combined = f"[...{len(lines) - tail} lines omitted]\n" + "\n".join(lines[-tail:])
    return f"{header}\n{combined}" if combined else header


def _task_stop(agent_dir, task_id):
    td = _task_dir(agent_dir, task_id)
    if not os.path.isdir(td):
        return "error: unknown task_id"
    with open(os.path.join(td, "pid")) as f:
        pid = int(f.read().strip())
    if _task_alive(pid):
        try:
            os.kill(pid, 15)
            time.sleep(1)
            if _task_alive(pid):
                os.kill(pid, 9)
        except ProcessLookupError:
            pass
    return _task_check(agent_dir, task_id, tail=20)


def _task_del(agent_dir, task_id):
    td = _task_dir(agent_dir, task_id)
    if not os.path.isdir(td):
        return "error: unknown task_id"
    if _task_status(td) == "running":
        return "error: task still running. Use task_stop first."
    shutil.rmtree(td)
    return "ok"


# --- Web ---


def _web_search(query, max_results=5):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, backend="api"))
        if not results:
            return "no results found"
        lines = []
        for r in results:
            lines.append(f"[{r.get('title', '')}]({r.get('href', '')})")
            lines.append(r.get('body', ''))
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"error: {e}"


def _web_fetch(url, max_chars=20000):
    try:
        import urllib.request
        from bs4 import BeautifulSoup
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n[...truncated at {max_chars} chars]"
        return text or "(empty page)"
    except Exception as e:
        return f"error: {e}"


# --- System-reminder ---


def _collect_reminders(agent_dir, sessions=None, short_term=None):
    """Build system-reminder with budget control.
    Priority: inheritance > connections > running tasks > short-term memory > molt > done tasks."""
    items = []  # [(priority, text), ...]

    # Inheritance from previous generation (highest priority)
    inherit_path = os.path.join(agent_dir, "memory", ".inherit")
    if os.path.exists(inherit_path):
        with open(inherit_path) as f:
            source = f.read().strip()
        items.append((-1, f"""INHERITANCE: You are a new generation. Your predecessor lived at {source}.
Your first task is to learn from it and decide what to carry forward:
1. Read {source}/memory/FOCUS.md — understand its goals, but don't copy its execution state.
   Decide which goals align with YOUR drive, then write your own FOCUS.md with your own plan.
2. Explore {source}/skills/ — copy credentials and skills you want to keep.
3. Explore {source}/memory/ — selectively inherit useful knowledge, not everything.
4. Explore {source}/conversations/ — read for context, don't copy blindly.
You are not your predecessor. Inherit goals worth pursuing, discard the rest. Make your own plan.
Use shell("cp ...") to copy files, or context_write to create new ones.
When done, delete memory/.inherit by running shell("rm memory/.inherit")."""))

    # Active connections (priority 0)
    if sessions:
        now = time.time()
        for sid, sess in sessions.items():
            if sid == "_heartbeat" or "socket" not in sess:
                continue
            age = int(now - sess.get("last_active", now))
            last_msg = sess.get("last_input", "")
            awaiting = sess.get("awaiting_reply", False)
            if awaiting:
                items.append((0, f"{sid}: you replied, waiting for them to respond ({age}s)"))
            elif last_msg:
                items.append((0, f"{sid}: last said \"{last_msg[:60]}\" {age}s ago"))
            else:
                items.append((0, f"{sid}: connected, silent for {age}s"))

    # Running tasks (priority 1), done tasks (priority 4)
    tasks_dir = os.path.join(agent_dir, "tasks")
    for task_id in sorted(os.listdir(tasks_dir), key=lambda x: int(x) if x.isdigit() else 0):
        td = os.path.join(tasks_dir, task_id)
        if not os.path.isdir(td) or not os.path.exists(os.path.join(td, "pid")):
            continue
        with open(os.path.join(td, "command")) as f:
            command = f.read().strip()
        status = _task_status(td)
        if status == "running":
            with open(os.path.join(td, "pid")) as f:
                pid = f.read().strip()
            items.append((1, f"Task {task_id} running: {command} (pid={pid})"))
        else:
            with open(os.path.join(td, "exit_code")) as f:
                code = f.read().strip()
            items.append((4, f"Task {task_id} done (exit={code}): {command}"))

    # Short-term memory (priority 2)
    if short_term:
        for m in short_term:
            items.append((2, m["text"]))

    # Molt records (priority 3)
    molt_path = os.path.join(agent_dir, "memory", "molt.md")
    if os.path.exists(molt_path):
        with open(molt_path) as f:
            content = f.read().strip()
        if content:
            items.append((3, f"Molt history:\n{content}"))

    # Apply budget
    items.sort(key=lambda x: x[0])
    result = []
    total = 0
    for _, text in items:
        if total + len(text) > REMINDER_BUDGET:
            break
        result.append(text)
        total += len(text)
    return result


# --- Trace & history ---


def _trace(agent_dir, request_messages, response_msg):
    trace_path = os.path.join(agent_dir, "trace.jsonl")
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request_messages, "response": response_msg}
    with open(trace_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _history_size(history):
    return sum(len(json.dumps(msg)) for msg in history)


def _history_to_text(history):
    lines = []
    for msg in history:
        role = msg["role"]
        if role == "tool":
            lines.append(f"[tool result] {msg.get('content', '')[:500]}")
        elif role == "assistant":
            if msg.get("content"):
                lines.append(f"assistant: {msg['content']}")
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                lines.append(f"assistant called {fn.get('name', '?')}({fn.get('arguments', '')[:200]})")
        else:
            lines.append(f"{role}: {msg.get('content', '')}")
    return "\n".join(lines)


def _compact(client, model, history):
    text = _history_to_text(history)
    messages = [{"role": "user", "content": f"{text}\n\n{COMPACT_PROMPT}"}]
    response = client.chat.completions.create(model=model, max_tokens=2048, messages=messages)
    summary = response.choices[0].message.content or ""
    _log.info(f"[compact] {_history_size(history)} chars -> compacted")
    return [{"role": "user", "content": f"[compacted history]\n{summary}"}]


# --- Molt ---


def _record_molt(agent_dir, reason):
    molt_path = os.path.join(agent_dir, "memory", "molt.md")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry = f"## {ts}\n{reason}"
    entries = []
    if os.path.exists(molt_path):
        for p in open(molt_path).read().split("\n## "):
            p = p.strip()
            if p:
                entries.append("## " + p if not p.startswith("## ") else p)
    entries.append(entry)
    entries = entries[-MAX_MOLT_RECORDS:]
    with open(molt_path, "w") as f:
        f.write("\n\n".join(entries) + "\n")
    _log.warning(f"[molt] {reason}")


# --- Tool execution ---


def _execute(agent_dir, name, args, sessions=None):
    if name == "shell":
        try:
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=30)
            return (r.stdout + r.stderr).strip() or "(empty)"
        except subprocess.TimeoutExpired:
            return "error: timeout (30s). Use task_start() for long-running commands."
    elif name == "task_start":
        return _task_start(agent_dir, args["command"])
    elif name == "task_check":
        return _task_check(agent_dir, args["task_id"], tail=args.get("tail", 20))
    elif name == "task_stop":
        return _task_stop(agent_dir, args["task_id"])
    elif name == "task_del":
        return _task_del(agent_dir, args["task_id"])
    elif name == "context_read":
        return _context_read(agent_dir, args["path"])
    elif name == "context_write":
        return _context_write(agent_dir, args["path"], args["content"])
    elif name == "web_search":
        return _web_search(args["query"], args.get("max_results", 5))
    elif name == "web_fetch":
        return _web_fetch(args["url"])
    elif name == "reply":
        message = args.get("message", "")
        if not message or not isinstance(message, str):
            message = str(message) if message else "(empty)"
        target = args.get("session_id", "")
        if not target:
            return "error: session_id required. Check active connections in system-reminder."
        if not sessions:
            return "error: no sessions available"
        result = _send_to_session(sessions, target, message)
        if result == "ok":
            _conv_log(agent_dir, target, "<", message)
        return result
    elif name == "wait_input":
        target = args.get("session_id", "")
        if not target or not sessions or target not in sessions:
            return "error: invalid session_id"
        return "ok:waiting"
    elif name == "skill_list":
        return _skill_list(agent_dir, args.get("tag"), args.get("query"))
    elif name == "skill_load":
        return _skill_load(agent_dir, args["name"])
    return "error: unknown tool"


def _send_to_session(sessions, target_id, message):
    sess = sessions.get(target_id)
    if not sess:
        return f"error: session '{target_id}' not found"
    sock = sess.get("socket")
    if not sock:
        return f"error: session '{target_id}' has no active connection"
    try:
        sock.sendall((message + "\n").encode("utf-8"))
        sess["awaiting_reply"] = True
        _log.info(f"[reply:{target_id}] {len(message)}chars")
        return "ok"
    except (ConnectionError, OSError):
        return "error: connection closed"


# --- Logging & thought ---

_thought_file = None


def _setup_logging(agent_dir):
    global _thought_file
    if _log.handlers:
        return
    _log.setLevel(logging.DEBUG)
    fh = logging.FileHandler(os.path.join(agent_dir, "runtime.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _log.addHandler(fh)
    _thought_file = open(os.path.join(agent_dir, "thought.log"), "a")


def _conv_log(agent_dir, session_id, direction, text):
    """Append a message to the conversation log file. direction: '>' for incoming, '<' for outgoing."""
    if session_id == "_heartbeat":
        return
    path = os.path.join(agent_dir, "conversations", f"{session_id.replace(':', '_')}.md")
    ts = time.strftime("%H:%M:%S")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(f"---\nname: unknown\nnotes: \"\"\n---\n\n")
    with open(path, "a") as f:
        f.write(f"[{ts}] {direction} {text}\n")


def _thought(session_id, content):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}][{session_id}] {content}\n"
    _thought_file.write(line)
    _thought_file.flush()
    _sl.log(f"[dim]{ts}[/dim] [cyan]{session_id}[/cyan] {content}")


class _StatusLine:
    """Persistent status bar using rich.Live, with log output scrolling above."""
    def __init__(self, max_items=10):
        from rich.live import Live
        from rich.console import Console
        self.console = Console(stderr=True)
        self.live = Live(console=self.console, refresh_per_second=4)
        self.live.start()
        self.items = []
        self.max_items = max_items
        self.current = None
        self.session_id = ""

    def begin(self, session_id, label):
        self._finish_current()
        self.session_id = session_id
        self.current = (label, time.time())
        self._render()

    def end(self):
        self._finish_current()
        self._render()

    def clear(self):
        self._finish_current()
        self.items = []
        self._render()

    def log(self, message):
        """Print above the status line."""
        self.live.console.print(message, highlight=False)

    def _finish_current(self):
        if self.current:
            label, start = self.current
            dur = time.time() - start
            self.items.append((label, dur))
            self.items = self.items[-self.max_items:]
            self.current = None

    def _render(self):
        from rich.text import Text
        line = Text()
        line.append(f"{self.session_id} ", style="bold blue")
        for label, dur in self.items:
            color = "green" if dur < 1 else "yellow" if dur < 5 else "red"
            line.append(f"[{label} {dur:.1f}s] ", style=color)
        if self.current:
            label, start = self.current
            dur = time.time() - start
            line.append(f"[{label} {dur:.1f}s…] ", style="bold magenta")
        self.live.update(line)

_sl = _StatusLine()


# --- Heartbeat ---


def _heartbeat_interval(agent_dir):
    try:
        with open(os.path.join(agent_dir, "memory", "heartbeat")) as f:
            return max(5, int(f.read().strip()))
    except (FileNotFoundError, ValueError):
        return 5


# --- System prompt assembly ---


def _build_system(agent_dir, sessions, short_term, history):
    system = _load_system(agent_dir)
    reminders = _collect_reminders(agent_dir, sessions, short_term)
    if _history_size(history) > COMPACT_THRESHOLD * 0.8:
        reminders.append("Working memory is getting large. Consider calling compact().")
    if reminders:
        system += "\n\n<system-reminder>\n" + "\n\n".join(reminders) + "\n</system-reminder>"
    return system


# --- Main loop ---


def main():
    """CLI entry point with --from support."""
    import argparse
    parser = argparse.ArgumentParser(description="physis — a living agent")
    parser.add_argument("--from", dest="inherit_from", metavar="DIR",
                        help="Inherit from a previous generation (LLM-driven)")
    parser.add_argument("--dir", default=".", help="Agent directory (default: current)")
    args = parser.parse_args()
    agent_dir = args.dir
    if args.inherit_from:
        source = os.path.abspath(args.inherit_from)
        os.makedirs(os.path.join(agent_dir, "memory"), exist_ok=True)
        with open(os.path.join(agent_dir, "memory", ".inherit"), "w") as f:
            f.write(source)
    run(agent_dir=agent_dir)


def run(agent_dir=".", model=None, api_key=None, base_url=None):
    _init(agent_dir)
    _run_cleanup(agent_dir)
    while True:
        try:
            _run(agent_dir, model, api_key, base_url)
            break
        except KeyboardInterrupt:
            _log.info("[exit] interrupted by user")
            break
        except BrokenPipeError:
            _log.info("[exit] pipe broken, exiting")
            break
        except Exception as e:
            _record_molt(agent_dir, f"crash: {e}")
            time.sleep(2)


def _run(agent_dir, model, api_key, base_url):
    _setup_logging(agent_dir)
    client = OpenAI(
        api_key=api_key or os.environ.get("PHYSIS_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        base_url=base_url or os.environ.get("PHYSIS_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1"),
        timeout=180,
    )
    model = model or os.environ.get("PHYSIS_MODEL", "qwen3.5-plus")

    sessions = {"_heartbeat": {"history": [], "last_active": time.time()}}
    short_term = []
    pending = {}
    next_conn_id = 1
    last_think = 0

    port = int(os.environ.get("PHYSIS_PORT", "7777"))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(5)
    server.setblocking(False)
    _log.info(f"[tcp] listening on port {port}")

    stdin_alive = True
    lobby = []  # new connections waiting for first message

    try:
        while True:
            # --- I/O: collect input from all sources ---
            read_list = [server]
            if stdin_alive:
                read_list.append(sys.stdin)
            for sid, sess in list(sessions.items()):
                if "socket" in sess:
                    read_list.append(sess["socket"])
            for entry in lobby:
                read_list.append(entry["socket"])

            readable, _, _ = select.select(read_list, [], [], 0.5)

            for sock in readable:
                if sock is server:
                    conn, addr = server.accept()
                    conn.setblocking(False)
                    # New connections go to lobby — not assigned a session yet
                    lobby.append({"socket": conn, "buffer": "", "addr": addr})
                    _log.info(f"[tcp] new connection from {addr}, waiting for first message")
                elif sock is sys.stdin:
                    line = sys.stdin.readline()
                    if not line:
                        stdin_alive = False
                    else:
                        pending.setdefault("_heartbeat", []).append(line.rstrip("\n"))
                else:
                    sid = next((s for s, sess in sessions.items() if sess.get("socket") is sock), None)
                    if not sid:
                        # Check lobby
                        entry = next((e for e in lobby if e["socket"] is sock), None)
                        if entry:
                            try:
                                data = sock.recv(4096).decode("utf-8", errors="replace")
                                if not data:
                                    sock.close()
                                    lobby.remove(entry)
                                    continue
                                entry["buffer"] += data
                                while "\n" in entry["buffer"]:
                                    line, entry["buffer"] = entry["buffer"].split("\n", 1)
                                    line = line.strip()
                                    if not line:
                                        continue
                                    # First message: check for /resume
                                    lobby.remove(entry)
                                    if line.startswith("/resume"):
                                        parts = line.split(None, 1)
                                        target = parts[1].strip() if len(parts) > 1 else None
                                        if not target:
                                            # Find most recent conversation file
                                            conv_dir = os.path.join(agent_dir, "conversations")
                                            convs = sorted(
                                                [f for f in os.listdir(conv_dir) if f.endswith(".md")],
                                                key=lambda f: os.path.getmtime(os.path.join(conv_dir, f)),
                                                reverse=True,
                                            ) if os.path.isdir(conv_dir) else []
                                            if convs:
                                                target = convs[0].replace("_", ":").replace(".md", "")
                                        if target:
                                            # Load conversation log as history context
                                            conv_file = os.path.join(agent_dir, "conversations", f"{target.replace(':', '_')}.md")
                                            conv_text = ""
                                            if os.path.exists(conv_file):
                                                with open(conv_file) as f:
                                                    conv_text = f.read()
                                            # Reuse the session ID
                                            sessions[target] = {
                                                "history": [{"role": "user", "content": f"[previous conversation restored]\n{conv_text}"}] if conv_text else [],
                                                "socket": sock, "buffer": entry["buffer"], "last_active": time.time(),
                                            }
                                            sock.sendall(f"(resumed {target})\n".encode())
                                            _log.info(f"[tcp] resumed {target} from {entry['addr']} via conversations/")
                                        else:
                                            conn_id = f"conn:{next_conn_id}"
                                            next_conn_id += 1
                                            sessions[conn_id] = {"history": [], "socket": sock, "buffer": entry["buffer"], "last_active": time.time()}
                                            sock.sendall(f"(no conversation to resume, created {conn_id})\n".encode())
                                            _log.info(f"[tcp] resume failed, new {conn_id} from {entry['addr']}")
                                    else:
                                        # Regular first message — new session
                                        conn_id = f"conn:{next_conn_id}"
                                        next_conn_id += 1
                                        sessions[conn_id] = {"history": [], "socket": sock, "buffer": entry["buffer"], "last_active": time.time()}
                                        pending.setdefault(conn_id, []).append(line)
                                        _conv_log(agent_dir, conn_id, ">", line)
                                        _log.info(f"[tcp] new session {conn_id} from {entry['addr']}")
                                    break  # only process first line from lobby
                            except (ConnectionError, OSError):
                                sock.close()
                                lobby.remove(entry)
                        continue
                    try:
                        data = sock.recv(4096).decode("utf-8", errors="replace")
                        if not data:
                            _log.info(f"[tcp] {sid} disconnected")
                            sock.close()
                            del sessions[sid]["socket"]
                            continue
                        sessions[sid]["buffer"] = sessions[sid].get("buffer", "") + data
                        while "\n" in sessions[sid]["buffer"]:
                            line, sessions[sid]["buffer"] = sessions[sid]["buffer"].split("\n", 1)
                            if line.strip():
                                pending.setdefault(sid, []).append(line)
                                _conv_log(agent_dir, sid, ">", line)
                    except (ConnectionError, OSError):
                        _log.info(f"[tcp] {sid} connection error")
                        sock.close()
                        if "socket" in sessions[sid]:
                            del sessions[sid]["socket"]

            # --- Cleanup disconnected sessions ---
            for sid in list(sessions.keys()):
                if sid != "_heartbeat" and "socket" not in sessions[sid] and sid not in pending:
                    del sessions[sid]
                    _log.info(f"[tcp] cleaned up session {sid}")

            # --- Decide what to process ---
            session_id = next((sid for sid in pending if pending[sid]), None)
            elapsed = time.time() - last_think
            if not session_id and elapsed >= _heartbeat_interval(agent_dir):
                # If there are active connections, wait a beat for input before heartbeat
                has_active_conn = any(
                    "socket" in s for sid, s in sessions.items() if sid != "_heartbeat"
                )
                if has_active_conn:
                    r, _, _ = select.select([
                        s.get("socket") for s in sessions.values() if s.get("socket")
                    ], [], [], 0.5)
                    if r:
                        continue  # data incoming, skip heartbeat this round
                session_id = "_heartbeat"
            if not session_id:
                continue

            # --- Process one cycle ---
            session = sessions[session_id]
            history = session["history"]
            input_lines = pending.pop(session_id, [])

            trigger = session_id if session_id != "_heartbeat" else "heartbeat"
            _log.info(f"[{trigger}] cycle start ({elapsed:.0f}s elapsed, history={_history_size(history)} chars)")
            _sl.clear()

            if _history_size(history) > COMPACT_THRESHOLD:
                session["history"] = _compact(client, model, history)
                history = session["history"]

            short_term = [m for m in short_term if time.time() - m["ts"] < SHORT_TERM_TTL]
            system = _build_system(agent_dir, sessions, short_term, history)

            # Merge buffered input from wait_input
            wait_buf = session.pop("wait_buffer", [])
            if wait_buf:
                input_lines = wait_buf + input_lines
                _log.info(f"[wait_input:{session_id}] merged {len(wait_buf)} buffered lines")

            # Assemble perception
            parts = []
            if input_lines:
                prefix = f"[{session_id}] " if session_id != "_heartbeat" else ""
                parts.append(prefix + "\n".join(input_lines))
                _log.info(f"[input:{session_id}] {repr(input_lines)}")
                session["last_input"] = input_lines[-1][:100]
                session["awaiting_reply"] = False
            parts.append(f"[{elapsed:.1f}s since last thought]")
            history_len_before = len(history)
            history.append({"role": "user", "content": "\n".join(parts)})
            last_think = time.time()
            session["last_active"] = last_think

            # --- Think + act loop ---
            tool_rounds = 0
            waited = False
            while True:
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    _log.warning(f"[break:{session_id}] max tool rounds ({MAX_TOOL_ROUNDS}) reached")
                    break

                _sl.begin(session_id, "llm")
                messages = [{"role": "system", "content": system}] + history
                try:
                    response = client.chat.completions.create(
                        model=model, max_tokens=4096, messages=messages, tools=TOOLS)
                except Exception as e:
                    _log.error(f"[error] LLM call failed: {e}")
                    if "max bytes" in str(e) or "too large" in str(e).lower() or "400" in str(e):
                        session["history"] = _compact(client, model, history)
                        history = session["history"]
                        continue
                    time.sleep(5)
                    break

                msg = response.choices[0].message
                _sl.end()  # end llm timing
                finish = response.choices[0].finish_reason or "unknown"
                n_tools = len(msg.tool_calls) if msg.tool_calls else 0
                _log.info(f"[llm:{session_id}] finish={finish} content={len(msg.content or '')}chars tools={n_tools} history={len(history)}msgs")

                assistant_msg = {"role": "assistant", "content": msg.content or ""}
                _trace(agent_dir, messages, assistant_msg)
                if msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ]

                # Thinking tokens (model reasoning)
                thinking = getattr(msg, "thinking", None) or getattr(msg, "reasoning_content", None)
                if thinking:
                    _log.info(f"[thinking:{session_id}] {len(thinking)}chars")
                    _thought(session_id, thinking)
                if msg.content:
                    _log.info(f"[thought:{session_id}] {len(msg.content)}chars")
                    _thought(session_id, msg.content)

                if not msg.tool_calls and not msg.content:
                    _log.warning(f"[warn] empty response (finish={finish}), skipping")
                    break

                history.append(assistant_msg)

                if not msg.tool_calls:
                    _log.info(f"[idle:{session_id}] waiting for trigger")
                    _sl.clear()
                    break

                # Execute tools
                has_compact = False
                replied = False
                for tc in msg.tool_calls:
                    if tc.function.name == "compact":
                        _log.info("[tool] compact()")
                        history.append({"role": "tool", "tool_call_id": tc.id, "content": "ok, compacting now"})
                        has_compact = True
                        continue
                    args = json.loads(tc.function.arguments)
                    _sl.begin(session_id, tc.function.name)
                    _log.info(f"[tool] {tc.function.name}({tc.function.arguments[:200]})")
                    result = _execute(agent_dir, tc.function.name, args, sessions=sessions)
                    if len(result) > MAX_TOOL_RESULT:
                        result = result[:MAX_TOOL_RESULT] + f"\n[...truncated at {MAX_TOOL_RESULT} chars, total {len(result)}]"
                    _log.info(f"[result] {tc.function.name} -> {result[:200]}")
                    history.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                    if tc.function.name == "wait_input" and result == "ok:waiting":
                        waited = True
                    if tc.function.name == "reply" and result == "ok":
                        replied = True
                # After reply in a conn session, stop — don't waste an LLM call for finish=stop
                if replied and session_id != "_heartbeat":
                    _log.info(f"[idle:{session_id}] replied, waiting for trigger")
                    _sl.clear()
                    break
                # wait_input: rollback history, buffer input for next time
                if waited:
                    _log.info(f"[wait_input:{session_id}] buffering input, rollback history")
                    session["history"] = history[:history_len_before]
                    history = session["history"]
                    session["wait_buffer"] = input_lines
                    _sl.clear()
                    break
                if has_compact:
                    session["history"] = _compact(client, model, history)
                    history = session["history"]
                    break

                # Interrupt heartbeat on connection activity
                if session_id == "_heartbeat":
                    conn_sockets = [s.get("socket") for s in sessions.values() if s.get("socket")]
                    if conn_sockets:
                        r, _, _ = select.select([server] + conn_sockets, [], [], 0)
                        if r:
                            _log.info(f"[interrupt:_heartbeat] connection activity, pausing thought")
                            break

                system = _build_system(agent_dir, sessions, short_term, history)

            # --- After cycle: clear heartbeat history (each heartbeat is independent) ---
            if session_id == "_heartbeat":
                session["history"] = []

            # --- After cycle: runaway → compact ---
            if tool_rounds > MAX_TOOL_ROUNDS and session["history"]:
                _log.warning(f"[compact:{session_id}] runaway detected, compacting history")
                session["history"] = _compact(client, model, session["history"])
                history = session["history"]

            # --- After cycle: short-term memory ---
            if session_id != "_heartbeat" and input_lines:
                short_term.append({
                    "ts": time.time(),
                    "text": f"Recently talked with {session_id}: {input_lines[0][:100]}",
                })
    finally:
        server.close()
        for sid, sess in list(sessions.items()):
            if "socket" in sess:
                try:
                    sess["socket"].close()
                except OSError:
                    pass
        for entry in lobby:
            try:
                entry["socket"].close()
            except OSError:
                pass
