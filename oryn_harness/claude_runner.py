"""Wrapper subprocess autour de la CLI Claude Code.

Utilise --output-format stream-json pour :
1. Streamer les events en temps réel dans la console
2. Parser le résultat final structuré

Ref CLI : https://docs.claude.com/en/docs/claude-code/cli-reference
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()


@dataclass
class ClaudeResult:
    """Résultat d'un appel à `claude` CLI."""

    text: str
    raw_stdout: str
    raw_stderr: str
    cost_usd: float
    duration_ms: int
    num_turns: int
    session_id: str | None
    success: bool


class ClaudeRunner:
    """Lance des sessions Claude Code isolées en sous-processus.

    Chaque agent reçoit sa propre instance avec son system prompt et son cwd.
    La sortie est streamée en temps réel dans la console.
    """

    def __init__(
        self,
        cwd: Path,
        model: str,
        system_prompt: str,
        permission_mode: str = "auto",
        timeout: int = 1800,
        allowed_tools: list[str] | None = None,
        verbose: bool = True,
    ):
        self.cwd = Path(cwd).resolve()
        self.model = model
        self.system_prompt = system_prompt
        self.permission_mode = permission_mode
        self.timeout = timeout
        self.allowed_tools = allowed_tools
        self.verbose = verbose

    def _build_command(self, user_prompt: str) -> list[str]:
        cmd = [
            "claude",
            "-p", user_prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--model", self.model,
            "--append-system-prompt", self.system_prompt,
        ]

        if self.permission_mode == "dangerous":
            cmd += ["--dangerously-skip-permissions"]
        else:
            cmd += ["--permission-mode", self.permission_mode]

        if self.allowed_tools:
            cmd += ["--allowed-tools", ",".join(self.allowed_tools)]

        return cmd

    def run(self, user_prompt: str) -> ClaudeResult:
        """Lance un appel claude avec streaming en temps réel."""
        cmd = self._build_command(user_prompt)

        console.print(f"[dim]→ claude (model={self.model}, cwd={self.cwd})[/dim]")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            console.print("[red]claude CLI non trouvée. Installe avec : npm install -g @anthropic-ai/claude-code[/red]")
            return ClaudeResult(
                text="", raw_stdout="", raw_stderr="claude not found",
                cost_usd=0.0, duration_ms=0, num_turns=0,
                session_id=None, success=False,
            )

        lines: list[str] = []
        result_data: dict | None = None

        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                lines.append(line)

                # Parse chaque event JSON
                try:
                    event = json.loads(line)
                    self._handle_event(event)

                    # Capturer le result final
                    if isinstance(event, dict) and event.get("type") == "result":
                        result_data = event
                except json.JSONDecodeError:
                    # Ligne non-JSON, afficher telle quelle
                    if self.verbose:
                        console.print(f"[dim]{line}[/dim]")

            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            return ClaudeResult(
                text="", raw_stdout="\n".join(lines),
                raw_stderr=f"TIMEOUT après {self.timeout}s",
                cost_usd=0.0, duration_ms=self.timeout * 1000, num_turns=0,
                session_id=None, success=False,
            )

        stderr = proc.stderr.read() if proc.stderr else ""
        raw_stdout = "\n".join(lines)

        return self._parse_result(result_data, raw_stdout, stderr, proc.returncode)

    def _handle_event(self, event: dict) -> None:
        """Affiche un event stream-json en temps réel."""
        if not self.verbose:
            return

        event_type = event.get("type", "")

        if event_type == "assistant":
            # Message de l'assistant — afficher le contenu texte
            content = event.get("message", {}).get("content", [])
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            console.print(f"[cyan]{text}[/cyan]")
                    elif block.get("type") == "tool_use":
                        tool = block.get("name", "?")
                        inp = block.get("input", {})
                        # Afficher un résumé concis du tool call
                        if tool == "Bash":
                            cmd = inp.get("command", "")
                            console.print(f"  [yellow]$ {cmd[:120]}[/yellow]")
                        elif tool == "Write":
                            path = inp.get("file_path", "")
                            console.print(f"  [green]✎ {path}[/green]")
                        elif tool == "Edit":
                            path = inp.get("file_path", "")
                            console.print(f"  [green]✏ {path}[/green]")
                        elif tool == "Read":
                            path = inp.get("file_path", "")
                            console.print(f"  [dim]📖 {path}[/dim]")
                        elif tool in ("Glob", "Grep"):
                            pattern = inp.get("pattern", "")
                            console.print(f"  [dim]🔍 {tool} {pattern}[/dim]")
                        else:
                            console.print(f"  [blue]🔧 {tool}[/blue]")

        elif event_type == "result":
            cost = event.get("total_cost_usd", 0)
            duration = event.get("duration_ms", 0)
            turns = event.get("num_turns", 0)
            console.print(
                f"[dim]← done ({turns} turns, ${cost:.4f}, {duration/1000:.1f}s)[/dim]"
            )

    def _parse_result(
        self,
        result_data: dict | None,
        raw_stdout: str,
        stderr: str,
        returncode: int,
    ) -> ClaudeResult:
        """Parse le result event final."""
        text = ""
        cost = 0.0
        duration = 0
        turns = 0
        session_id = None
        success = returncode == 0

        if result_data:
            text = result_data.get("result", "") or result_data.get("text", "")
            cost = float(result_data.get("total_cost_usd", 0.0))
            duration = int(result_data.get("duration_ms", 0))
            turns = int(result_data.get("num_turns", 0))
            session_id = result_data.get("session_id")
            if result_data.get("is_error"):
                success = False
        elif raw_stdout:
            # Fallback : essayer de parser le dernier JSON
            for line in reversed(raw_stdout.split("\n")):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and data.get("type") == "result":
                        text = data.get("result", "")
                        cost = float(data.get("total_cost_usd", 0.0))
                        duration = int(data.get("duration_ms", 0))
                        turns = int(data.get("num_turns", 0))
                        session_id = data.get("session_id")
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
            if not text:
                text = raw_stdout

        return ClaudeResult(
            text=text,
            raw_stdout=raw_stdout,
            raw_stderr=stderr,
            cost_usd=cost,
            duration_ms=duration,
            num_turns=turns,
            session_id=session_id,
            success=success,
        )
