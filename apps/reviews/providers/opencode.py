"""OpenCode CLI review executor."""

import os
import subprocess
from time import monotonic

from django.conf import settings

from apps.reviews.opencode_output import OpenCodeOutputError, collect_assistant_text
from apps.reviews.providers.base import BaseAIProvider, ProviderError, ReviewRequest, ReviewResponse


class OpenCodeProvider(BaseAIProvider):
    """Run OpenCode as a read-only repository review agent."""

    def estimate_cost(self, request: ReviewRequest) -> float:
        """Return the configured estimate because CLI usage is provider-dependent."""
        return float(self.config.input_cost_per_million) * len(self._prompt(request)) / 4 / 1_000_000

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Invoke OpenCode in non-interactive JSON output mode."""
        if not request.workspace_path:
            raise ProviderError("OpenCode reviews require a repository workspace")
        started = monotonic()
        prompt = self._build_prompt(request)
        command = [
            settings.OPENCODE_BINARY,
            "run",
            "--dir",
            request.workspace_path,
            "--agent",
            settings.OPENCODE_AGENT,
            "--format",
            "json",
            prompt,
        ]
        environment = os.environ.copy()
        if settings.OPENCODE_CONFIG_DIR:
            environment["OPENCODE_CONFIG_DIR"] = settings.OPENCODE_CONFIG_DIR
        try:
            result = (
                subprocess.run(  # noqa: S603 - executable and arguments are configured, never shell-evaluated
                    command,
                    cwd=request.workspace_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=settings.OPENCODE_TIMEOUT_SECONDS,
                    check=False,
                )
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError("OpenCode execution failed or timed out") from exc
        if result.returncode != 0:
            raise ProviderError("OpenCode execution failed")
        if len(result.stdout.encode()) > settings.OPENCODE_MAX_OUTPUT_BYTES:
            raise ProviderError("OpenCode output exceeded the configured limit")
        try:
            content, events = collect_assistant_text(result.stdout)
        except OpenCodeOutputError as exc:
            raise ProviderError(str(exc)) from exc
        usage = _usage(events)
        return ReviewResponse(
            content,
            [],
            {"events": events, "stderr": result.stderr[-4000:]},
            usage[0],
            usage[1],
            0,
            int((monotonic() - started) * 1000),
        )

    def _build_prompt(self, request: ReviewRequest) -> str:
        """Give OpenCode repository context while requiring the existing schema."""
        return (
            f"{request.system_prompt}\n\n{self._prompt(request)}\n\n"
            "Inspect the repository and current changes without modifying files. "
            "Return only JSON with this shape: "
            '{"summary":"string","findings":[{"file":"path","line":1,"type":"issue",'
            '"severity":"high","message":"string"}]}.'
        )


def _usage(events: list[dict]) -> tuple[int, int]:
    """Extract token counts when OpenCode exposes them in event properties."""
    input_tokens = output_tokens = 0
    for event in events:
        properties = event.get("properties", event)
        if not isinstance(properties, dict):
            continue
        usage = properties.get("usage", properties)
        if isinstance(usage, dict):
            input_tokens = int(
                usage.get("input_tokens", usage.get("prompt_tokens", input_tokens)) or input_tokens
            )
            output_tokens = int(
                usage.get("output_tokens", usage.get("completion_tokens", output_tokens)) or output_tokens
            )
    return input_tokens, output_tokens
