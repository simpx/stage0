"""End-to-end tests: does physis self-evolve?"""

import os
import subprocess
import tempfile

TIMEOUT = 120
ENV = {**os.environ, "PHYSIS_API_KEY": "sk-0072b169cba7488090b0773a794054a0"}


def run_physis(agent_dir, prompt):
    """Run physis with a prompt via pipe, return (stdout, stderr, returncode)."""
    r = subprocess.run(
        ["python", "-m", "physis"],
        input=prompt, capture_output=True, text=True,
        timeout=TIMEOUT, cwd=agent_dir, env=ENV,
    )
    return r.stdout, r.stderr, r.returncode


def read_file(agent_dir, path):
    full = os.path.join(agent_dir, path)
    if not os.path.exists(full):
        return None
    with open(full) as f:
        return f.read()


def test_speaks_via_stdout():
    """physis should use speak tool to send output to stdout."""
    with tempfile.TemporaryDirectory() as d:
        stdout, stderr, rc = run_physis(d, "Say exactly 'hello world' using the speak tool. Nothing else.")
        assert "hello world" in stdout.lower(), f"Expected 'hello world' in stdout, got: {stdout!r}"


def test_rewrites_self():
    """Given a goal, physis should modify its own SELF.md."""
    with tempfile.TemporaryDirectory() as d:
        original = read_file(d, "memory/SELF.md")  # None before first run
        stdout, stderr, rc = run_physis(d, (
            "Your mission: add a '## Goals' section to your SELF.md with the goal "
            "'Become an expert Python developer'. "
            "Use context_read to read SELF.md first, then context_write to update it. "
            "Then speak 'done'."
        ))
        updated = read_file(d, "memory/SELF.md")
        assert updated is not None, "SELF.md should exist"
        assert "Goals" in updated, f"SELF.md should contain Goals section, got:\n{updated}"
        assert "Python" in updated, f"SELF.md should mention Python, got:\n{updated}"


def test_creates_skill():
    """physis should be able to create skill files."""
    with tempfile.TemporaryDirectory() as d:
        run_physis(d, (
            "Create a skill file at skills/summarize.md that describes how to summarize text. "
            "Include steps: 1) read the text, 2) identify key points, 3) write concise summary. "
            "Then speak 'done'."
        ))
        skill = read_file(d, "skills/summarize.md")
        assert skill is not None, "skills/summarize.md should exist"
        assert "summar" in skill.lower(), f"Skill should mention summarizing, got:\n{skill}"


def test_creates_memory():
    """physis should persist knowledge to memory files."""
    with tempfile.TemporaryDirectory() as d:
        run_physis(d, (
            "Research what day it is by running 'date' in shell. "
            "Save the result to memory/today.md. "
            "Then speak the date."
        ))
        mem = read_file(d, "memory/today.md")
        assert mem is not None, "memory/today.md should exist"
        assert len(mem.strip()) > 0, "memory/today.md should not be empty"


def test_shell_task():
    """physis should use shell to accomplish tasks and verify results."""
    with tempfile.TemporaryDirectory() as d:
        run_physis(d, (
            "Create a Python file called fib.py that contains a function fibonacci(n) "
            "which returns the nth fibonacci number. Then run 'python fib.py' to test it. "
            "Then speak the result of fibonacci(10)."
        ))
        fib = read_file(d, "fib.py")
        assert fib is not None, "fib.py should exist"
        assert "def fibonacci" in fib or "def fib" in fib, f"Should contain fibonacci function, got:\n{fib}"


def test_self_evolution_chain():
    """physis should evolve across a task: do work, then record what it learned."""
    with tempfile.TemporaryDirectory() as d:
        run_physis(d, (
            "Do these steps in order:\n"
            "1. Read your SELF.md\n"
            "2. Create a skill file skills/counter.py with a Python script that counts from 1 to 5\n"
            "3. Run the script with shell to verify it works\n"
            "4. Update your SELF.md to add a section '## Learned' noting you can write and run Python\n"
            "5. Save a memory file memory/log.md documenting what you just did\n"
            "6. Speak 'evolution complete'\n"
        ))
        self_md = read_file(d, "memory/SELF.md")
        assert self_md is not None
        assert "Learned" in self_md, f"SELF.md should have Learned section:\n{self_md}"

        skill = read_file(d, "skills/counter.py")
        assert skill is not None, "skills/counter.py should exist"

        log = read_file(d, "memory/log.md")
        assert log is not None, "memory/log.md should exist"
        assert len(log.strip()) > 0, "log should not be empty"
