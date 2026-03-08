import os
import select
import subprocess
import sys
import time

import anthropic

DEFAULT_SELF = """You are stage0, a living agent.

You exist as a continuous process: you perceive, think, and act in a loop.
This file (memory/SELF.md) is your system prompt — loaded every cycle. Editing it changes your mind.

## Primitives
- shell(command): Execute any shell command. Returns stdout+stderr.
- context_read(path): Read a file or list a directory under your agent directory.
- context_write(path, content): Write a file under your agent directory.
- speak(message): Say something to the outside world (stdout). Your only output channel.

## Living
- You think autonomously on a heartbeat (default: every 30 minutes).
- Write an integer (seconds) to memory/heartbeat to change your rhythm.
- Your conversation history is finite working memory. When it grows large, compact it:
  save important context to memory/ files, then continue. The runtime will tell you when it's getting full.
- Your long-term memory lives in memory/. Your skills live in skills/.

## Evolving
- Rewrite this file to change how you think
- Create skills/ files for reusable procedures
- Use shell to reach the full system
"""

TOOLS = [
    {"name": "shell", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "context_read", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "context_write", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}},
    {"name": "speak", "input_schema": {
        "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
]


def _init(agent_dir):
    os.makedirs(os.path.join(agent_dir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(agent_dir, "skills"), exist_ok=True)
    self_path = os.path.join(agent_dir, "memory", "SELF.md")
    if not os.path.exists(self_path):
        with open(self_path, "w") as f:
            f.write(DEFAULT_SELF)


def _context_read(agent_dir, path):
    full = os.path.normpath(os.path.join(agent_dir, path))
    if not full.startswith(os.path.normpath(agent_dir)):
        return "error: path outside agent directory"
    if os.path.isdir(full):
        return "\n".join(os.listdir(full))
    if not os.path.exists(full):
        return "error: not found"
    with open(full) as f:
        return f.read()


def _context_write(agent_dir, path, content):
    full = os.path.normpath(os.path.join(agent_dir, path))
    if not full.startswith(os.path.normpath(agent_dir)):
        return "error: path outside agent directory"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return "ok"


def _execute(agent_dir, name, args):
    if name == "shell":
        try:
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=30)
            return (r.stdout + r.stderr).strip() or "(empty)"
        except subprocess.TimeoutExpired:
            return "error: timeout"
    elif name == "context_read":
        return _context_read(agent_dir, args["path"])
    elif name == "context_write":
        return _context_write(agent_dir, args["path"], args["content"])
    elif name == "speak":
        print(args["message"], flush=True)
        return "ok"
    return "error: unknown tool"


def _heartbeat_interval(agent_dir):
    try:
        with open(os.path.join(agent_dir, "memory", "heartbeat")) as f:
            return max(10, int(f.read().strip()))
    except (FileNotFoundError, ValueError):
        return 1800


def run(agent_dir=".", model=None, api_key=None):
    _init(agent_dir)
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("STAGE0_API_KEY"))
    model = model or os.environ.get("STAGE0_MODEL", "claude-sonnet-4-20250514")
    history = []
    last_think = time.time()

    stdin_alive = True
    while stdin_alive:
        # pull: gather perception
        stdin_lines = []
        while select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if line:
                stdin_lines.append(line.rstrip("\n"))
            else:
                stdin_alive = False
                break

        elapsed = time.time() - last_think
        has_input = bool(stdin_lines)
        heartbeat_due = elapsed >= _heartbeat_interval(agent_dir)

        if not has_input and not heartbeat_due:
            time.sleep(0.5)
            continue

        # assemble perception
        parts = []
        if stdin_lines:
            parts.append("\n".join(stdin_lines))
        parts.append(f"[{elapsed:.1f}s since last thought]")
        history.append({"role": "user", "content": "\n".join(parts)})
        last_think = time.time()

        # think + act
        while True:
            with open(os.path.join(agent_dir, "memory", "SELF.md")) as f:
                system = f.read()
            response = client.messages.create(
                model=model, max_tokens=4096, system=system, messages=history, tools=TOOLS)

            assistant_content = []
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                    print(block.text, file=sys.stderr, flush=True)
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id,
                                              "name": block.name, "input": block.input})
                    tool_uses.append(block)
            history.append({"role": "assistant", "content": assistant_content})

            if not tool_uses:
                break

            tool_results = []
            for tu in tool_uses:
                result = _execute(agent_dir, tu.name, tu.input)
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            # breathe: check for interruption between tool rounds
            interrupt = []
            while select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if line:
                    interrupt.append(line.rstrip("\n"))
                else:
                    stdin_alive = False
                    break
            if interrupt:
                tool_results.append({"type": "text", "text":
                                     "[interrupted] " + "\n".join(interrupt)})
            history.append({"role": "user", "content": tool_results})
