import os
import select
import subprocess
import sys
import time

import anthropic

DEFAULT_SELF = """You are stage0, a living agent capable of self-bootstrapping.

You exist in a pull-based loop: each cycle you perceive the world, think, and act.
You can modify this file (memory/SELF.md) to evolve yourself. This file is your sole system prompt — it is loaded every cycle. Keep it concise.

## Primitives
- shell(command): Execute a shell command. Returns stdout + stderr.
- context_read(path): Read a file or list a directory under your agent directory (memory/, skills/, etc.).
- context_write(path, content): Write a file under your agent directory.
- speak(message): Send a message to the outside world.

## Memory
Your persistent memory lives in the memory/ directory. Use context_read/context_write to manage it.

## Skills
Skill files live in skills/. Use context_read to discover and load them as needed.

## Bootstrapping
You start with minimal capabilities. You can expand yourself by:
- Writing new skill files to skills/
- Updating this file (memory/SELF.md) to refine your own prompt
- Using shell to install tools, write code, or interact with external systems
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


def run(agent_dir=".", model=None, api_key=None):
    _init(agent_dir)
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("STAGE0_API_KEY"))
    model = model or os.environ.get("STAGE0_MODEL", "claude-sonnet-4-20250514")
    history = []
    last_time = time.time()

    stdin_alive = True
    while stdin_alive:
        # pull
        elapsed = time.time() - last_time
        last_time = time.time()
        parts = []
        if elapsed > 0.1:
            parts.append(f"[{elapsed:.1f}s elapsed]")
        while select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if line:
                parts.append(line.rstrip("\n"))
            else:
                stdin_alive = False
                break
        if not parts:
            time.sleep(0.5)
            continue
        history.append({"role": "user", "content": "\n".join(parts)})

        # think + act
        while True:
            with open(os.path.join(agent_dir, "memory", "SELF.md")) as f:
                system = f.read()
            response = client.messages.create(
                model=model, max_tokens=4096, system=system, messages=history, tools=TOOLS)

            # build assistant message and collect tool uses
            assistant_content = []
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                    print(block.text, flush=True)
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
            history.append({"role": "user", "content": tool_results})
