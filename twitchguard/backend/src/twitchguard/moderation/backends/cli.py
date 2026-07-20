"""CLI backends: local claude / gemini / codex binaries (FR-44, FR-46, IR-25)."""
from __future__ import annotations

import asyncio
import shutil

from ...config import ClassifierConfig
from .base import BackendUnavailable, CompletionResult, ModelBackend

# The prompt is appended as the last argument.
CLI_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "-p"],
    "codex": ["codex", "exec"],
}


class CLIBackend(ModelBackend):
    kind = "cli"

    def __init__(self, cfg: ClassifierConfig, tool: str) -> None:
        super().__init__(cfg)
        if tool not in CLI_COMMANDS:
            raise BackendUnavailable("cli_unsupported", f"CLI tool '{tool}' is not supported")
        self.tool = tool

    async def validate(self) -> None:
        """FR-46: a missing binary is detected when the setting is saved."""
        if shutil.which(self.tool) is None:
            raise BackendUnavailable(
                "cli_not_found", f"CLI tool '{self.tool}' was not found in PATH on the server"
            )

    async def complete(self, prompt: str) -> CompletionResult:
        if shutil.which(self.tool) is None:
            raise BackendUnavailable("cli_not_found", f"CLI tool '{self.tool}' not found in PATH")
        proc = await asyncio.create_subprocess_exec(
            *CLI_COMMANDS[self.tool],
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.cfg.cli_timeout_s
            )
        except TimeoutError:
            proc.kill()
            raise BackendUnavailable(
                "cli_timeout", f"CLI tool '{self.tool}' timed out after {self.cfg.cli_timeout_s}s"
            ) from None
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", "replace")[-200:]
            raise BackendUnavailable("cli_error", f"CLI tool exited with error: {detail}")
        return CompletionResult(text=stdout.decode("utf-8", "replace"), tokens=0)
