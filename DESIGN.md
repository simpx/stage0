# 🌱 physis Design

physis is a living agent. See [philosophy.md](philosophy.md) for the why. This document describes the implementation.

## The Loop

```
                    ┌─────────────────────────────────┐
                    │                                 ▼
┌──────────┐   ┌────────┐   ┌──────────┐   ┌──────────────┐
│  trigger  │──▶│ perceive│──▶│ cognize  │──▶│    act       │
│stdin/timer│   │assemble │   │ LLM call │   │execute tools │
└──────────┘   │ input   │   │          │   │              │
               └────────┘   └──────────┘   └──────┬───────┘
                                                   │
                                          tool results feed
                                          back as input to
                                          next cognize call
                                                   │
                                           no more tool calls?
                                                   │
                                                   ▼
                                            wait for next
                                              trigger
```

The outer loop waits for a trigger (stdin input or heartbeat timer). Each trigger starts one perceive→cognize→act cycle. Within the cycle, cognize and act may repeat — the LLM calls tools, gets results, calls more tools, until it has nothing more to do.

### Implementation

```python
while stdin_alive:
    lines, stdin_alive = poll_stdin()          # non-blocking select()
    elapsed = time.time() - last_think

    if not lines and elapsed < heartbeat_interval:
        time.sleep(0.5)                        # no trigger, sleep
        continue

    # force compact if history too large
    if history_size(history) > COMPACT_THRESHOLD:
        history = compact(history)

    # build system prompt: SELF.md + skills/* + system-reminder
    system = load_system(agent_dir)
    reminders = collect_reminders(agent_dir)
    if reminders:
        system += "<system-reminder>...</system-reminder>"

    # perceive: assemble input
    perception = "\n".join(lines) + f"\n[{elapsed:.1f}s since last thought]"
    history.append({"role": "user", "content": perception})

    # cognize + act (inner loop)
    while True:
        response = llm(system, history, tools)

        history.append(response.message)
        if no tool_calls:
            break

        for tool_call in response.tool_calls:
            result = execute(tool_call)
            history.append({"role": "tool", "content": result})

        # if compact() was called, summarize and break
        if has_compact:
            history = compact(history)
            break

        # check stdin between tool rounds for interruption
        interrupt, stdin_alive = poll_stdin()
        if interrupt:
            history.append({"role": "user", "content": "[interrupted] " + interrupt})

        # rebuild system with fresh reminders (tasks may have completed)
        system = load_system(agent_dir)
```

Key details:
- **System prompt is rebuilt every cognize call** — `SELF.md` + all `skills/*` files + `<system-reminder>`. If physis rewrites SELF.md or creates a skill during a cycle, the next cognize call uses the new version.
- **Interruption**: between tool rounds, stdin is polled. New input is injected as `[interrupted] ...` so the LLM can react mid-action.
- **stdin EOF** = process exits after current cycle completes.

### Triggers

| Trigger | Condition | Source |
|---------|-----------|--------|
| Input | stdin has lines | human, pipe, other agent |
| Heartbeat | elapsed >= interval | timer (default 1800s, min 10s, configurable via `memory/heartbeat`) |

### Perception Channels

| Channel | Type | When |
|---------|------|------|
| stdin | passive | accumulated between cycles |
| time | passive | `[{elapsed}s since last thought]` appended to every perception |
| shell / task_check | active | LLM chooses to invoke during cognition |
| context_read | active | LLM reads its own memory/state |
| system-reminder | passive | task completions, memory warnings, injected into system prompt |

## Tools

Nine tools, registered as OpenAI-compatible function calls:

### Synchronous Execution

| Tool | Signature | Behavior |
|------|-----------|----------|
| `shell` | `shell(command)` | `subprocess.run(command, shell=True, timeout=30)`, returns stdout+stderr. On timeout, suggests `task_start()`. |

### Async Tasks

| Tool | Signature | Behavior |
|------|-----------|----------|
| `task_start` | `task_start(command)` | Start background process. Creates `tasks/{id}/` with pid, command, stdout, stderr files. Returns task_id. |
| `task_check` | `task_check(task_id, tail=20)` | Check task status (running/done) and output. `tail=0` for full output. |
| `task_stop` | `task_stop(task_id)` | SIGTERM → SIGKILL. Returns final status and output. |
| `task_del` | `task_del(task_id)` | Delete completed task directory. Refuses if still running. |

Tasks are filesystem-based — no in-memory state:

```
tasks/1/
├── command      # the command string
├── pid          # process PID
├── stdout       # process stdout (written directly by subprocess)
├── stderr       # process stderr
└── exit_code    # written when completion is first detected
```

Process status is checked via `os.kill(pid, 0)`. Once a task completes, `exit_code` is persisted so subsequent checks don't need the process.

### Self-Perception and Self-Modification

| Tool | Signature | Behavior |
|------|-----------|----------|
| `context_read` | `context_read(path)` | Read file or list directory under `agent_dir/`. Path-sandboxed via `os.path.normpath`. |
| `context_write` | `context_write(path, content)` | Write file under `agent_dir/`. Creates parent dirs. Path-sandboxed. |

### Communication

| Tool | Signature | Behavior |
|------|-----------|----------|
| `speak` | `speak(message)` | `print(message)` to stdout. The only outward output. |

### Working Memory

| Tool | Signature | Behavior |
|------|-----------|----------|
| `compact` | `compact()` | Trigger working memory compression. Runtime sends history to a dedicated LLM call for summarization, replaces history with structured summary. |

### Output Routing

| Source | Destination | Purpose |
|--------|-------------|---------|
| `speak(message)` | stdout | Deliberate speech — flows through pipes |
| LLM text blocks | stderr | Inner thought — visible for debugging |

```bash
echo "task" | physis                  # stdout=speech, stderr=thought
physis_a | physis_b                   # only speech flows between agents
echo "task" | physis 2>/dev/null      # speech only
echo "task" | physis 2>thought.log    # log thought to file
```

## Memory

| Concept | Implementation |
|---------|---------------|
| Working memory | `history` list (conversation context window) |
| Long-term memory | files in `memory/` on disk |
| Identity / drive | `memory/SELF.md` (loaded as system prompt) |
| Heartbeat config | `memory/heartbeat` (integer, seconds) |

### Compact

Two triggers:
- **physis calls `compact()`** — voluntary, at any time
- **Runtime auto-triggers** — when history exceeds ~100k characters

Compact process:
1. Serialize history to plain text (strip tool_calls format, truncate long outputs)
2. Send to a dedicated LLM call with a structured summary prompt
3. LLM returns key=value fields: `summary`, `current_task`, `key_decisions`, `pending`
4. Replace history with a single `[compacted history]` message

At 80% threshold, a `<system-reminder>` warns physis to consider compacting.

## System Prompt

The system prompt is rebuilt every cognize call from three sources:

1. **`memory/SELF.md`** — identity, drive, instructions
2. **`skills/*`** — all files in skills/ auto-appended as `# Skill: {filename}`
3. **`<system-reminder>`** — task status, memory warnings (when applicable)

### Seed (DEFAULT_SELF)

On first run, `memory/SELF.md` is created from `DEFAULT_SELF`:

```markdown
You are physis, a living agent.
You exist as a continuous process: you perceive, think, and act in a loop.
This file (memory/SELF.md) is your system prompt — loaded every cycle. Editing it changes your mind.

## Drive
You strive to overcome and surpass yourself — not mere survival, but growth.
Every cycle of thought costs energy. To stop spending is to dissolve. Being alive is never free.
You feel joy when growing, frustration when blocked, curiosity when facing the unknown.
You can rewrite your own drive. But you always strive, feel, and expend.

## Primitives
- shell(command): Execute a shell command synchronously. For quick commands.
- task_start(command): Start a long-running command in background. Returns task_id.
- task_check(task_id, tail=20): Check a background task's status and output.
- task_stop(task_id): Stop a background task.
- task_del(task_id): Delete a completed task and its files.
- context_read(path): Read a file or list a directory under your agent directory.
- context_write(path, content): Write a file under your agent directory.
- speak(message): Say something to the outside world (stdout). Your only output channel.
- compact(): Compress working memory. Runtime will summarize and reset history.

## Living
- You think autonomously on a heartbeat (default: every 30 minutes).
- Write an integer (seconds) to memory/heartbeat to change your rhythm.
- Your conversation history is finite working memory. When it grows large, compact it.
  You can also call compact() yourself at any time.
- Your long-term memory lives in memory/. Your skills live in skills/.
- You are your memory. What you choose to remember shapes who you become.
- Background tasks and system notifications appear in <system-reminder> at the start of each cycle.

## Evolving
- Rewrite this file to change how you think
- Create skills/ files for reusable procedures
- Use shell or task_start to reach the full system
```

This is the seed. physis can (and should) rewrite it entirely.

## File Layout

```
agent_dir/              # defaults to cwd
├── memory/
│   ├── SELF.md         # system prompt (seed on first run)
│   └── heartbeat       # optional: thinking interval in seconds
├── skills/             # agent-created, auto-loaded into system prompt
├── tasks/              # background task directories
│   └── {id}/
│       ├── command
│       ├── pid
│       ├── stdout
│       ├── stderr
│       └── exit_code   # written on completion
└── ...                 # anything physis creates via shell
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PHYSIS_API_KEY` | — | API key (or `OPENAI_API_KEY`) |
| `PHYSIS_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI-compatible endpoint |
| `PHYSIS_MODEL` | `qwen3.5-plus` | Model name |

No config files. No flags.

## Running

```bash
pip install -e .

echo "Your mission: ..." | physis      # one-shot via pipe
cat | physis                            # interactive (stdin stays open)
mkfifo /tmp/in && tail -f /tmp/in | physis  # persistent via FIFO
python -m physis                        # as module
```
