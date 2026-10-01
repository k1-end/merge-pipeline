"""Lossless persistence for provider execution output."""

from django.db.models import Max

from apps.reviews.models import Review, ReviewExecutionLogChunk


class ReviewExecutionLogRecorder:
    """Append provider output to ordered database chunks without a total-size cap."""

    def __init__(self, review: Review):
        """Continue after any chunks already recorded for this review."""
        self.review = review
        maximum = review.execution_log_chunks.aggregate(value=Max("sequence"))["value"]
        self.next_sequence = 0 if maximum is None else maximum + 1

    def write(self, stream: str, content: str | bytes) -> None:
        """Persist all content, splitting only to avoid oversized individual rows."""
        if not content:
            return
        encoded = content.encode() if isinstance(content, str) else content
        chunks: list[ReviewExecutionLogChunk] = []
        for offset in range(0, len(encoded), ReviewExecutionLogChunk.CHUNK_SIZE):
            chunks.append(
                ReviewExecutionLogChunk(
                    review=self.review,
                    sequence=self.next_sequence,
                    stream=stream,
                    content=encoded[offset : offset + ReviewExecutionLogChunk.CHUNK_SIZE],
                )
            )
            self.next_sequence += 1
            if len(chunks) == 100:
                ReviewExecutionLogChunk.objects.bulk_create(chunks)
                chunks.clear()
        if chunks:
            ReviewExecutionLogChunk.objects.bulk_create(chunks)
