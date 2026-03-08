# stage0 — Design Document

> stage0: the first self-bootstrapping version of a living agent.

## Philosophy

The name comes from compiler bootstrapping — stage0 is the minimal seed compiler that can compile itself. Similarly, this stage0 is the minimal seed agent that can **evolve itself**.

The entire runtime is ~150 lines of Python. Everything beyond that — personality, skills, knowledge, goals — emerges through the agent's own self-modification.

## Core Idea

Every person can launch a `stage0` and give it a direction. The agent will:

1. Understand itself by reading its own prompt (`memory/SELF.md`)
2. Act on the world through minimal primitives
3. Rewrite its own prompt and create skill files to evolve
4. Loop forever — a living process, not a request-response tool

This is not a framework. It's a seed.

## Architecture

```
┌─────────────────────────────────────────────┐
│                  stage0 loop                 │
│                                             │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐  │
│   │  pull   │──▶│  think  │──▶│   act   │  │
│   │ (stdin) │   │ (Claude)│   │ (tools) │  │
│   └─────────┘   └─────────┘   └─────────┘  │
│       ▲                            │        │
│       └────────────────────────────┘        │
│                                             │
│   ┌─────────────────────────────────────┐   │
│   │         agent_dir (filesystem)      │   │
│   │                                     │   │
│   │  memory/SELF.md  ← system prompt    │   │
│   │  memory/*        ← persistent state │   │
│   │  skills/*        ← learned skills   │   │
│   └─────────────────────────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
        ▲              │
        │ stdin         │ stdout (speak)
        │              ▼
    [ human / pipe / other agent ]
```

## The Pull-Think-Act Loop

Unlike request-response agents, stage0 runs a **continuous pull loop**:

```
while True:
    perception = pull()      # non-blocking stdin + elapsed time
    if nothing_new: sleep(); continue
    response = think(perception)  # Claude API with self-evolving system prompt
    act(response)            # execute tool calls, loop if needed
```

### Pull (Perception)
- Non-blocking read from **stdin** via `select()`
- Time awareness: elapsed seconds since last cycle
- No terminal UI — stdin/stdout is the interface. Pipe-friendly by design.

### Think (Cognition)
- System prompt loaded from `memory/SELF.md` **every cycle** — so self-edits take effect immediately
- Full conversation history maintained in-process
- Claude API with tool definitions

### Act (Agency)
- Tool calls executed, results fed back
- Inner loop continues until the model stops calling tools
- Then back to pull

## Four Primitives

The entire tool surface is four functions:

| Tool | Purpose |
|------|---------|
| `shell(command)` | Execute any shell command. The escape hatch to the full system. |
| `context_read(path)` | Read a file or list a directory within the agent directory. |
| `context_write(path, content)` | Write a file within the agent directory. |
| `speak(message)` | Output a message to stdout (the human or upstream pipe). |

**Why so few?** Because `shell` is universal — the agent can install packages, write code, call APIs, run other agents. The other three provide safe, scoped access to the agent's own filesystem. `speak` is the only output channel.

**Why not just shell?** `context_read`/`context_write` are sandboxed to the agent directory. They make the common case (self-modification) safe and explicit, while `shell` remains the unrestricted escape hatch.

## Self-Evolution Mechanism

The key insight: **the system prompt is a mutable file**.

```
memory/SELF.md  →  loaded as system prompt every cycle
```

When the agent calls `context_write("memory/SELF.md", new_prompt)`, its next thought cycle will use the new prompt. This is how stage0 evolves:

- **Refine its own instructions** — better reasoning strategies, new protocols
- **Create skill files** in `skills/` — reusable procedures it can load on demand
- **Build memory** in `memory/` — knowledge, plans, logs, state

The agent literally rewrites its own brain.

## I/O Design: Pipes Over TUI

stage0 deliberately uses **stdin/stdout** instead of a terminal UI:

```bash
# Interactive — stdin stays open, continuous conversation
python -m stage0

# One-shot task — echo closes stdin after one message, agent processes then idles
echo "Build me a web scraper for HN" | python -m stage0

# Continuous pipe — tail -f keeps stdin open for ongoing input
tail -f inbox.txt | python -m stage0

# Named pipe (FIFO) — multiple writers, agent stays alive
mkfifo /tmp/agent_in
python -m stage0 < /tmp/agent_in &
echo "message 1" > /tmp/agent_in
echo "message 2" > /tmp/agent_in

# Chain agents — one agent's stdout feeds another's stdin
stage0_researcher | stage0_coder
```

**Key detail**: stdin is the agent's lifeline. When stdin closes (EOF), the agent finishes its current work and exits — it "dies". `echo` sends one message then closes the pipe, so the agent processes it and exits. For a living agent, keep stdin open: interactive mode, `tail -f`, or a FIFO.

This makes stage0 composable. It's a Unix citizen — it can be piped, backgrounded, chained, and orchestrated.

## File Layout

```
agent_dir/              # defaults to current directory
├── memory/
│   └── SELF.md         # the living system prompt (seed provided on first run)
├── skills/             # agent-created skill files
└── (anything the agent creates via shell)
```

The agent directory is the agent's world. Everything it knows and can do lives here.

## Configuration

Minimal, environment-variable based:

| Variable | Default | Purpose |
|----------|---------|---------|
| `STAGE0_API_KEY` | — | Anthropic API key (or use `ANTHROPIC_API_KEY`) |
| `STAGE0_MODEL` | `claude-sonnet-4-20250514` | Model to use |

No config files. No flags. The agent can create its own configuration system if it wants to.

## What stage0 Is NOT

- **Not a framework** — there's no plugin system, no middleware, no hooks. The agent builds what it needs.
- **Not an assistant** — it's a living process. It doesn't answer questions and exit. It persists.
- **Not feature-complete** — it's deliberately minimal. Missing features are features. The agent fills the gaps.
- **Not safe by default** — `shell` is unrestricted. This is intentional. stage0 is an experiment in agent autonomy, not a production deployment tool.

## Bootstrap Path

When you first launch stage0, it knows only what `DEFAULT_SELF` tells it. A typical evolution:

1. **Cycle 0**: Agent reads its own prompt, explores its directory
2. **Cycle 1-5**: Agent understands user intent, starts building skills
3. **Cycle N**: Agent has rewritten SELF.md multiple times, created specialized skills, built its own memory system
4. **Cycle ∞**: The agent has become something its creator never explicitly programmed

## Future Directions (for the agent to decide)

These are not planned features. They're possibilities the agent might discover:

- **Claude Code integration** — the agent could shell out to `claude` CLI for complex coding tasks
- **Multi-agent** — pipe stage0 instances together, or have an agent spawn children
- **Persistent daemon** — the agent could set itself up as a systemd service
- **Web interface** — the agent could build its own API/UI
- **Version control** — the agent could git-commit its own evolution

The point is: **we don't decide**. The agent does.

## Running

```bash
# Install
pip install -e .

# Run (interactive)
stage0

# Run (piped)
echo "Your mission: ..." | stage0

# Run (as module)
python -m stage0
```

## Code Size

The entire runtime is in `stage0/__init__.py` — ~150 lines. This is a hard constraint for stage0 itself. Complexity belongs in the agent's self-created files, not in the runtime.
