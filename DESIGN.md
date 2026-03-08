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
4. Loop as long as alive — a living process, not a request-response tool

This is not a framework. It's a seed.

## Architecture

```
┌──────────────────────────────────────────────┐
│                  stage0 loop                  │
│                                              │
│   ┌─────────┐   ┌─────────┐   ┌──────────┐  │
│   │  pull   │──▶│  think  │──▶│   act    │  │
│   │ (stdin  │   │ (Claude)│   │  (tools) │  │
│   │  +time) │   └────┬────┘   └──────────┘  │
│   └─────────┘        │              │        │
│       ▲              │ text         │        │
│       │              ▼ (stderr)     │        │
│       └─────────────────────────────┘        │
│                                              │
│   ┌──────────────────────────────────────┐   │
│   │         agent_dir (filesystem)       │   │
│   │                                      │   │
│   │  memory/SELF.md   ← system prompt    │   │
│   │  memory/heartbeat ← thinking rhythm  │   │
│   │  memory/*         ← long-term memory │   │
│   │  skills/*         ← learned skills   │   │
│   └──────────────────────────────────────┘   │
│                                              │
└──────────────────────────────────────────────┘
        ▲              │
        │ stdin         │ stdout (speak only)
        │              ▼
    [ human / pipe / other agent ]
```

## The Pull-Think-Act Loop

stage0 is not request-response. The agent has its own **heartbeat**:

```
while alive:
    stdin_input = poll_stdin()        # non-blocking, accumulates lines
    heartbeat_due = elapsed >= interval  # default 30min, agent-configurable
    if not stdin_input and not heartbeat_due: sleep(0.5); continue
    perception = assemble(stdin_input, elapsed)
    response = think(perception)
    act(response)
```

Two things trigger thought: **input** (stdin) and **time** (heartbeat). The agent can adjust its own heartbeat by writing to `memory/heartbeat`.

### Pull (Perception)
- Non-blocking read from **stdin** via `select()` — lines accumulate between think cycles
- Time awareness: elapsed seconds since last thought, included in every perception
- stdin is a perception stream, not a message queue. Multiple lines become one perception batch.

### Think (Cognition)
- System prompt loaded from `memory/SELF.md` **every cycle** — so self-edits take effect immediately
- Conversation history is **working memory** — finite, must be compacted when full
- Claude API with tool definitions

### Act (Agency)
- Tool calls executed, results fed back
- Inner loop continues until the model stops calling tools
- Then back to pull

### Heartbeat
- Default interval: **30 minutes** (aligns with Anthropic's ~1hr prompt cache — idle thought is cheap)
- Agent can change it: `context_write("memory/heartbeat", "60")` → think every minute
- Minimum: 10 seconds (enforced by runtime to prevent runaway costs)

## Four Primitives

The entire tool surface is four functions:

| Tool | Purpose |
|------|---------|
| `shell(command)` | Execute any shell command. The escape hatch to the full system. |
| `context_read(path)` | Read a file or list a directory within the agent directory. |
| `context_write(path, content)` | Write a file within the agent directory. |
| `speak(message)` | Say something to stdout — the agent's **only** outward voice. |

**Why so few?** Because `shell` is universal — the agent can install packages, write code, call APIs, run other agents. The other three provide safe, scoped access to the agent's own filesystem. `speak` is the only output channel.

**Why not just shell?** `context_read`/`context_write` are sandboxed to the agent directory. They make the common case (self-modification) safe and explicit, while `shell` remains the unrestricted escape hatch.

**speak vs text**: The model produces two kinds of output. `speak` goes to **stdout** — it's the agent's mouth, what flows through pipes. Text blocks go to **stderr** — the agent's inner monologue, visible for debugging but not part of the communication channel. This keeps pipes clean: `stage0_researcher | stage0_coder` only passes deliberate speech.

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

stage0 deliberately uses **stdin/stdout/stderr** instead of a terminal UI:

- **stdin** → perception (input stream)
- **stdout** → speech (`speak` tool only — the agent's deliberate voice)
- **stderr** → inner monologue (model text blocks — for debugging/observation)

```bash
# Interactive — see both speech and thoughts
python -m stage0

# Pipe — only speech flows downstream, thoughts stay on terminal
stage0_researcher | stage0_coder

# Silence thoughts
stage0 2>/dev/null

# Log thoughts to file
stage0 2>thoughts.log
```

**stdin is the agent's lifeline.** When stdin closes (EOF), the agent finishes its current work and exits — it "dies". `echo` sends one message then closes the pipe. For a living agent, keep stdin open: interactive mode, `tail -f`, or a FIFO.

This makes stage0 composable. It's a Unix citizen — it can be piped, backgrounded, chained, and orchestrated.

## Memory Model

| Layer | Mechanism | Capacity | Lifetime |
|-------|-----------|----------|----------|
| Working memory | conversation history | context window | single process |
| Long-term memory | `memory/` files | disk | permanent |

The agent's conversation history is finite working memory. When it grows large, the agent should **compact**: save important context to `memory/` files, then the runtime can trim old history. The DEFAULT_SELF seed teaches this survival skill.

## File Layout

```
agent_dir/              # defaults to current directory
├── memory/
│   ├── SELF.md         # the living system prompt (seed provided on first run)
│   └── heartbeat       # optional: thinking interval in seconds (default 1800)
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
