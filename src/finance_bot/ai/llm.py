"""Claude Code CLI client wrapper.

Cung cấp 2 hàm chính:
  - health(): kiểm tra `claude` binary tồn tại trong PATH và `--version` chạy được.
  - chat_json(): gọi Claude qua headless CLI (`claude --print`) với prompt yêu cầu
    JSON, parse + validate kết quả về dict.

Nếu Claude CLI không khả dụng → tự nâng `LLMUnavailable` để caller fallback giữ
rule-engine draft.

Tại sao gọi qua CLI (subprocess) thay vì Anthropic SDK:
    Project dùng Claude Code subscription đã đăng nhập sẵn trên máy local; không
    cần ANTHROPIC_API_KEY riêng. CLI tự xử auth + retries + model routing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from finance_bot.logger import logger
from finance_bot.settings import get_settings


class LLMUnavailable(RuntimeError):
    """Raised when the Claude CLI is missing, exits non-zero, or times out."""


@dataclass
class LLMResponse:
    raw_text: str
    parsed: dict[str, Any] | None
    model: str


class ClaudeClient:
    """Thin wrapper around the local `claude` CLI in headless (`--print`) mode."""

    def __init__(
        self,
        binary: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        s = get_settings()
        self.binary = binary or s.claude_binary
        self.model = model or s.claude_model
        self.timeout_seconds = timeout_seconds or s.claude_timeout_seconds

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def health(self) -> bool:
        """True if the `claude` binary is on PATH and `--version` returns 0."""
        path = shutil.which(self.binary)
        if not path:
            logger.warning("Claude CLI binary {!r} not found in PATH", self.binary)
            return False
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("Claude CLI {!r} failed --version probe: {!r}", path, exc)
            return False
        if result.returncode != 0:
            logger.warning(
                "Claude CLI {!r} --version exited {}: {}",
                path, result.returncode, result.stderr.strip(),
            )
            return False
        logger.debug("Claude CLI OK: {}", result.stdout.strip())
        return True

    # ------------------------------------------------------------------
    # Chat (JSON mode)
    # ------------------------------------------------------------------
    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Call Claude with prompt-enforced JSON output. Raises LLMUnavailable on failure.

        `temperature` and `max_tokens` are accepted but NOT forwarded — the headless
        CLI does not expose them. The arbiter SYSTEM_PROMPT already constrains the
        output shape strictly enough that these knobs are not load-bearing here.
        """
        del temperature, max_tokens  # CLI doesn't expose these knobs

        path = shutil.which(self.binary)
        if not path:
            raise LLMUnavailable(f"claude binary {self.binary!r} not found in PATH")

        cmd = [
            path,
            "--print",
            "--model", self.model,
            "--append-system-prompt", system,
        ]
        try:
            result = subprocess.run(
                cmd,
                input=user,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMUnavailable(
                f"claude CLI timed out after {self.timeout_seconds}s"
            ) from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise LLMUnavailable(f"claude CLI spawn failed: {exc!r}") from exc

        if result.returncode != 0:
            raise LLMUnavailable(
                f"claude CLI exited {result.returncode}: "
                f"{result.stderr.strip()[:500]}"
            )

        text = result.stdout.strip()
        parsed = _parse_json_payload(text)
        return LLMResponse(raw_text=text, parsed=parsed, model=self.model)


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    """Parse Claude's response as JSON, tolerating ```json fences if present."""
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        stripped = _strip_code_fence(text)
        if stripped == text:
            logger.warning("Claude returned non-JSON output: {!r}", text[:200])
            return None
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Claude returned non-JSON output: {!r}", text[:200])
            return None
    return loaded if isinstance(loaded, dict) else None


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    first_nl = s.find("\n")
    if first_nl == -1:
        return s
    body = s[first_nl + 1:]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()
