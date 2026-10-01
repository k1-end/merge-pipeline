"""OpenCode CLI review executor."""

import os
import subprocess
import tempfile
from time import monotonic
from typing import BinaryIO

from django.conf import settings

from apps.reviews.models import ReviewExecutionLogChunk
from apps.reviews.opencode_output import OpenCodeOutputError, collect_assistant_result
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
        timed_out = False
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(  # noqa: S603 - arguments are configured, never shell-evaluated
                    command,
                    cwd=request.workspace_path,
                    env=environment,
                    stdout=stdout_file,
                    stderr=stderr_file,
                )
            except OSError as exc:
                request.write_log(
                    ReviewExecutionLogChunk.SYSTEM,
                    f"OpenCode could not be started: {type(exc).__name__}\n",
                )
                raise ProviderError("OpenCode execution failed to start") from exc
            try:
                returncode = process.wait(timeout=settings.OPENCODE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                returncode = process.wait()

            _emit_file(stdout_file, ReviewExecutionLogChunk.STDOUT, request)
            _emit_file(stderr_file, ReviewExecutionLogChunk.STDERR, request)
            if timed_out:
                request.write_log(
                    ReviewExecutionLogChunk.SYSTEM,
                    f"OpenCode timed out after {settings.OPENCODE_TIMEOUT_SECONDS} seconds\n",
                )
                raise ProviderError("OpenCode execution timed out")
            request.write_log(
                ReviewExecutionLogChunk.SYSTEM,
                f"OpenCode exited with code {returncode}\n",
            )
            if returncode != 0:
                raise ProviderError(f"OpenCode execution failed with exit code {returncode}")
            stdout_file.seek(0)
            try:
                lines = (line.decode("utf-8") for line in stdout_file)
                content, event_count, input_tokens, output_tokens = collect_assistant_result(lines)
            except (OpenCodeOutputError, UnicodeDecodeError) as exc:
                raise ProviderError(str(exc)) from exc
        return ReviewResponse(
            content,
            [],
            {"event_count": event_count},
            input_tokens,
            output_tokens,
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


def _emit_file(file: BinaryIO, stream: str, request: ReviewRequest) -> None:
    """Persist a spooled process stream in bounded, lossless chunks."""
    file.seek(0)
    batch_size = ReviewExecutionLogChunk.CHUNK_SIZE * 100
    while chunk := file.read(batch_size):
        request.write_log(stream, chunk)
