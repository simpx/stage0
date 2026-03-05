import json
import os
import select
import subprocess
import sys
import time

from openai import OpenAI

DEFAULT_SELF = """你是一个 living agent。

## 原语
- shell(command): 执行命令
- context_read(path): 读取 memory/ 和 skills/ 下的文件，传入目录则列出文件
- context_write(path, content): 写入文件
- speak(message): 对外说话

## 记忆
memory/ 目录下存放你的记忆，用 context_read 查看。
此文件 (SELF.md) 每轮都会加载，请保持精简。

## 技能
skills/ 目录下存放技能描述，用 context_read 按需读取。
"""

TOOLS = [
    {"type": "function", "function": {"name": "shell", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "context_read", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "context_write", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "speak", "parameters": {
        "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
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


def run(agent_dir=".", model=None, api_key=None, base_url=None):
    _init(agent_dir)
    client = OpenAI(api_key=api_key or os.environ.get("STAGE0_API_KEY"),
                    base_url=base_url or os.environ.get("STAGE0_BASE_URL"))
    model = model or os.environ.get("STAGE0_MODEL", "gpt-4o")
    history = []
    last_time = time.time()

    while True:
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
                break
        if not parts:
            time.sleep(0.5)
            continue
        history.append({"role": "user", "content": "\n".join(parts)})

        # think + act
        while True:
            with open(os.path.join(agent_dir, "memory", "SELF.md")) as f:
                system = f.read()
            response = client.chat.completions.create(
                model=model, messages=[{"role": "system", "content": system}] + history, tools=TOOLS)
            choice = response.choices[0]
            history.append(choice.message.model_dump(exclude_none=True))

            if not choice.message.tool_calls:
                if choice.message.content:
                    print(choice.message.content, flush=True)
                break

            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                result = _execute(agent_dir, tc.function.name, args)
                history.append({"role": "tool", "tool_call_id": tc.id, "content": result})
