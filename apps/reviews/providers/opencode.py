"""OpenCode CLI review executor."""

import os
import subprocess
from time import monotonic

from django.conf import settings

from apps.reviews.models import ReviewExecutionLogChunk
from apps.reviews.opencode_output import OpenCodeOutputError, collect_assistant_text
from apps.reviews.providers.base import BaseAIProvider, ProviderError, ReviewRequest, ReviewResponse


class OpenCodeProvider(BaseAIProvider):
    """Run OpenCode as a read-only repository review agent."""

    def estimate_cost(self, request: ReviewRequest) -> float:
        """Return the configured estimate because CLI usage is provider-dependent."""
        return float(self.config.input_cost_per_million) * len(self._build_prompt(request)) / 4 / 1_000_000

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Invoke OpenCode in non-interactive JSON output mode."""
        if not request.workspace_path:
            raise ProviderError("OpenCode reviews require a repository workspace")
        started = monotonic()
        prompt = self._build_prompt(request)
        command = [
            settings.OPENCODE_BINARY,
            "run",
            "--agent",
            settings.OPENCODE_AGENT,
            "--model",
            self.config.model_name,
            "--format",
            "json",
            prompt,
        ]
        environment = os.environ.copy()
        if settings.OPENCODE_CONFIG_DIR:
            environment["OPENCODE_CONFIG_DIR"] = settings.OPENCODE_CONFIG_DIR
        request.write_log(
            ReviewExecutionLogChunk.SYSTEM,
            (
                "Starting OpenCode review "
                f"(agent={settings.OPENCODE_AGENT}, model={self.config.model_name}, format=json)\n"
            ),
        )
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
        except subprocess.TimeoutExpired as exc:
            request.write_log(ReviewExecutionLogChunk.STDOUT, _output_text(exc.stdout))
            request.write_log(ReviewExecutionLogChunk.STDERR, _output_text(exc.stderr))
            request.write_log(
                ReviewExecutionLogChunk.SYSTEM,
                f"OpenCode timed out after {settings.OPENCODE_TIMEOUT_SECONDS} seconds\n",
            )
            raise ProviderError("OpenCode execution timed out") from exc
        except OSError as exc:
            request.write_log(
                ReviewExecutionLogChunk.SYSTEM,
                f"OpenCode could not be started: {type(exc).__name__}\n",
            )
            raise ProviderError("OpenCode execution failed to start") from exc
        request.write_log(ReviewExecutionLogChunk.STDOUT, result.stdout)
        request.write_log(ReviewExecutionLogChunk.STDERR, result.stderr)
        request.write_log(
            ReviewExecutionLogChunk.SYSTEM,
            f"OpenCode exited with code {result.returncode}\n",
        )
        if result.returncode != 0:
            raise ProviderError(f"OpenCode execution failed with exit code {result.returncode}")
        try:
            content, events = collect_assistant_text(result.stdout)
        except OpenCodeOutputError as exc:
            raise ProviderError(str(exc)) from exc
        usage = _usage(events)
        return ReviewResponse(
            content,
            [],
            {"event_count": len(events)},
            usage[0],
            usage[1],
            0,
            int((monotonic() - started) * 1000),
        )

    def _build_prompt(self, request: ReviewRequest) -> str:
        """Give OpenCode an immutable repository comparison and output contract."""
        comparison = (
            f"git diff --find-renames {request.base_sha}...{request.head_sha}"
            if request.base_sha and request.head_sha
            else "git diff --find-renames against the supplied target branch"
        )
        return (
            f"{request.system_prompt}\n\n"
            f"Review this merge request.\nTitle: {request.mr_title}\n"
            f"Description: {request.mr_description}\n"
            f"Feature branch: {request.source_branch}\nTarget branch: {request.target_branch}\n"
            f"Head SHA: {request.head_sha}\nBase SHA: {request.base_sha}\n"
            f"Merge-base SHA: {request.merge_base_sha}\n\n"
            f"Use this authoritative comparison: {comparison}\n"
            "First enumerate every changed file from that comparison. Review every changed file, "
            "and inspect surrounding implementations, callers, tests, configuration, and migrations "
            "where relevant. Do not rely on an API-provided or prompt-embedded diff. "
            "Inspect the repository without modifying files. "
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


def _output_text(output: str | bytes | None) -> str:
    """Normalize partial timeout output without dropping undecodable bytes."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output
