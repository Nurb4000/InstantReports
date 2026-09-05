from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# HTTP statuses that indicate a transient upstream problem worth retrying: the
# request may have been dropped (408) or the model service is overloaded/rate
# limited (429) or errored internally (5xx). Client 4xx (auth, bad request) are
# not retried — repeating them will only fail again.
_TRANSIENT_STATUS = {408, 429, *[code for code in range(500, 600)]}


class AILLMResponseError(ValueError):
    """Raised when the model returns output that cannot be used (e.g. invalid
    JSON for a structured request).

    Deliberately distinct from transient transport errors so callers can tell
    "the network hiccapped, retry now" apart from "the model produced unusable
    output, notify the user and let them retry".
    """


def classify_ai_error(exc: Exception, operation: str = "request") -> tuple[int, str]:
    """Map an AI failure to ``(http_status, detail)`` for route layers.

    Kept in this (fastapi-free) module so every route maps AI errors the same
    way without duplicating the logic or importing the client's internals:

    - ``AILLMResponseError`` (model ran but returned unusable output) -> 502.
    - Transient transport/HTTP errors (after retries exhausted) -> 503.
    - Anything else -> 500.
    """
    if isinstance(exc, AILLMResponseError):
        return 502, f"The AI {operation} did not return a usable response. Please try again."
    if isinstance(
        exc, (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)
    ):
        return 503, f"The AI service is temporarily unavailable while {operation}. Please try again later."
    return 500, f"AI {operation} failed: {exc}"


def _extract_json_response(text: str) -> Any:
    """Parse a JSON object/array out of an AI response.

    Local and smaller models frequently wrap JSON in markdown code fences
    (```` ```json ... ``` ````) or surround it with prose even when asked for raw
    JSON. A bare ``json.loads`` chokes on the fences, so this strips an optional
    ```` ```lang ```` wrapper first and, as a fallback, extracts the outermost
    balanced ``{...}`` or ``[...]`` block. Raises ``json.JSONDecodeError`` when no
    valid JSON can be recovered.
    """
    stripped = text.strip()
    candidate = re.sub(r"\s*```[a-zA-Z0-9]*\s*", "", stripped)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in ("{", "}"), ("[", "]"):
        start = candidate.find(open_c)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise json.JSONDecodeError("No valid JSON found in AI response", candidate, 0)


class AIClient:
    """OpenAI-compatible AI client for llama.cpp and OpenAI."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "none",
        model: str = "local-model",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        retries: int = 2,
        retry_backoff: float = 0.5,
        timeout: float = 60.0,
    ) -> str:
        """Send a chat completion request to the AI backend.

        Transient upstream failures (timeouts, connection resets, and 408/429/5xx)
        are retried up to ``retries`` times with exponential backoff; non-transient
        errors (e.g. 401/400) raise immediately.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens in response
            response_format: Optional JSON schema for structured output
            retries: Number of additional attempts on transient failures
            retry_backoff: Base delay in seconds between retries (exponential)
            timeout: Per-request timeout in seconds

        Returns:
            Response text from the AI
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            payload["response_format"] = response_format

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key and self.api_key != "none":
            headers["Authorization"] = f"Bearer {self.api_key}"

        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout) as http_client:
                    response = await http_client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in _TRANSIENT_STATUS and attempt < retries:
                    await _sleep_before_retry(attempt, retries, retry_backoff, e)
                    attempt += 1
                    continue
                logger.error(f"AI API error: {e.response.status_code} - {e.response.text}")
                raise
            except httpx.HTTPError as e:
                # ConnectError / NetworkError / TimeoutException / etc.
                if attempt < retries:
                    await _sleep_before_retry(attempt, retries, retry_backoff, e)
                    attempt += 1
                    continue
                logger.error(f"AI transport error: {e}")
                raise
            else:
                data = response.json()
                message = data["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                if not content:
                    # Reasoning models (DeepSeek-R1, SmolLM3 reasoning, ...) return
                    # their output in ``reasoning_content`` and leave ``content``
                    # empty. Fall back so both instruct and reasoning endpoints work.
                    content = (message.get("reasoning_content") or "").strip()
                return content


async def _sleep_before_retry(
    attempt: int, retries: int, retry_backoff: float, exc: httpx.HTTPError
) -> None:
    """Log a transient failure and sleep before the next attempt.

    Backoff is exponential (`retry_backoff * 2**attempt`) so retries don't
    hammer an overloaded model server.
    """
    delay = retry_backoff * (2 ** attempt)
    logger.warning(
        "Transient AI error (%s); retry %d/%d in %.2fs",
        type(exc).__name__,
        attempt + 1,
        retries,
        delay,
    )
    await asyncio.sleep(delay)


class AIReportGenerator:
    """Generate report definitions from natural language."""

    def __init__(self, client: AIClient):
        self.client = client

    async def generate_report(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        """Generate a report definition from natural language.

        Args:
            prompt: Natural language description of the report
            schema: Optional data source schema for context

        Returns:
            Report definition dictionary
        """
        system_prompt = """You are an expert report designer. Generate a valid InstantReports JSON definition based on the user's description.

The report definition should include:
- name: Report title
- description: Brief description
- data_sources: Array of data source definitions
- parameters: Array of parameter definitions (if needed)
- layout: Object with page settings and sections array

Each section should have:
- type: header, detail, summary, or footer
- elements: Array of elements (text, table, chart, crosstab, subreport)

Return ONLY valid JSON, no markdown formatting."""

        user_prompt = f"Create a report for: {prompt}"

        if schema:
            user_prompt += f"\n\nAvailable data sources:\n{json.dumps(schema, indent=2)}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.client.chat_completion(
            messages=messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        try:
            return _extract_json_response(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse AI response as JSON: {response!r}")
            raise AILLMResponseError("Invalid report definition generated by AI")


class AISQLGenerator:
    """Generate SQL queries from natural language."""

    def __init__(self, client: AIClient):
        self.client = client

    async def generate_sql(
        self,
        prompt: str,
        schema: dict[str, Any],
        connector_type: str = "postgresql",
    ) -> str:
        """Generate a SQL query from natural language.

        Args:
            prompt: Natural language description of the data needed
            schema: Data source schema with tables and columns
            connector_type: Database type for syntax (postgresql, mysql, etc.)

        Returns:
            SQL query string
        """
        system_prompt = f"""You are an expert SQL developer. Generate a valid {connector_type} SQL query based on the user's description and the provided schema.

Rules:
- Use proper {connector_type} syntax
- Include all necessary JOINs
- Use parameterized queries where appropriate (use $1, $2, etc. for PostgreSQL, %s for MySQL)
- Return ONLY the SQL query, no explanations or markdown formatting"""

        user_prompt = f"Generate SQL for: {prompt}\n\nSchema:\n{json.dumps(schema, indent=2)}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.client.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
        )

        # Clean up the response
        sql = response.strip()
        sql = sql.removeprefix("```sql")
        sql = sql.removesuffix("```")
        sql = sql.strip()

        return sql


class AILayoutAssistant:
    """Suggest optimal report layouts based on data schema."""

    def __init__(self, client: AIClient):
        self.client = client

    async def suggest_layout(
        self,
        data_schema: dict[str, Any],
        report_type: str = "summary",
    ) -> dict[str, Any]:
        """Suggest a report layout based on data schema.

        Args:
            data_schema: Schema with tables and columns
            report_type: Type of report (summary, detail, comparison, trend)

        Returns:
            Layout suggestion with sections and elements
        """
        system_prompt = """You are an expert report designer. Suggest an optimal layout for a report based on the provided data schema.

Consider:
- Data types and relationships
- Appropriate chart types for different data
- Logical grouping and aggregation
- Readability and visual hierarchy

Return a JSON object with:
- sections: Array of section definitions (header, detail, summary)
- Each section has elements with type, columns, charts, etc."""

        user_prompt = f"Suggest a {report_type} report layout for this schema:\n{json.dumps(data_schema, indent=2)}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.client.chat_completion(
            messages=messages,
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        try:
            return _extract_json_response(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse layout suggestion as JSON: {response!r}")
            raise AILLMResponseError("Invalid layout suggestion from AI")


class AIDataInsights:
    """Generate natural language insights from data."""

    def __init__(self, client: AIClient):
        self.client = client

    async def generate_insights(
        self,
        data: list[dict[str, Any]],
        context: str = "",
    ) -> str:
        """Generate insights from report data.

        Args:
            data: Sample data rows (limited to 50 for context)
            context: Additional context about the report

        Returns:
            Natural language insights summary
        """
        system_prompt = """You are a data analyst. Analyze the provided data and generate key insights.

Focus on:
- Trends and patterns
- Notable highs and lows
- Correlations between fields
- Actionable recommendations

Keep the summary concise (2-3 paragraphs) and business-focused."""

        sample_data = data[:50] if len(data) > 50 else data

        user_prompt = f"Analyze this data{f' in the context of: {context}' if context else ''}:\n\n{json.dumps(sample_data, indent=2)}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.client.chat_completion(
            messages=messages,
            temperature=0.5,
            max_tokens=1024,
        )

        return response.strip()
