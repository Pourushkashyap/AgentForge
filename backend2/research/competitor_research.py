
import asyncio
import json
import logging
import re
from typing import Any

from core.groq_client import call_groq
from research.schemas import CompetitorResearchReport
from research.web_search import SearchResult, search_web

logger = logging.getLogger(__name__)

MAX_COMPETITORS = 3
MAX_SEARCH_QUERIES = 3
SEARCH_RESULTS_PER_QUERY = 2


class CompetitorResearchError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object returned by Groq."""

    text = text.strip()
    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CompetitorResearchError(
            "Groq returned incomplete or invalid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise CompetitorResearchError(
            "Groq response must be a JSON object."
        )

    return data


def _build_search_queries(
    business_idea: str,
    market_scope: str,
) -> list[str]:
    return [
        f"premium artisan ice cream competitors {market_scope}",
        "Van Leeuwen Ice Cream official website menu shipping prices",
        "Graeter's official website online ordering shipping prices",
        "Gelato Fiasco official website menu pricing",
    ]


async def _collect_evidence(
    queries: list[str],
) -> tuple[list[SearchResult], list[str]]:
    tasks = [
        asyncio.to_thread(
            search_web,
            query=query,
            max_results=SEARCH_RESULTS_PER_QUERY,
        )
        for query in queries
    ]

    responses = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    evidence_by_url: dict[str, SearchResult] = {}
    failed_queries: list[str] = []

    for query, response in zip(queries, responses):
        if isinstance(response, Exception):
            logger.warning("Search failed for %r: %s", query, response)
            failed_queries.append(query)
            continue

        for item in response:
            url = item.url.strip()
            if url:
                evidence_by_url.setdefault(url, item)

    return list(evidence_by_url.values()), failed_queries


def _build_report_prompt(
    business_idea: str,
    market_scope: str,
    evidence: list[SearchResult],
) -> str:
    evidence_data = [
        {
            "title": item.title[:150],
            "url": item.url,
            "content": item.content[:500],
        }
        for item in evidence
    ]

    schema = CompetitorResearchReport.model_json_schema()

    return f"""
Analyze competitors for this business.

Business: {business_idea}
Market: {market_scope}

Evidence:
{json.dumps(evidence_data, ensure_ascii=False)}

Required JSON schema:
{json.dumps(schema)}

Rules:
- Return one valid JSON object matching the schema.
- Include at most {MAX_COMPETITORS} competitors.
- Use only evidence URLs in source_urls and sources.
- Prefer official websites when present in the evidence.
- Never invent websites, features, pricing, or facts.
- Use pricing_status "publicly_available" only when pricing
  is supported by evidence; otherwise use "not_found" or "unclear".
- Missing evidence does not prove a feature or price is unavailable.
- Keep every field and list concise.
- If evidence is insufficient, explain that in limitations.
- Do not claim a competitive gap is verified without evidence.
- Return compact JSON without Markdown or additional commentary.
- If a competitor's official website URL appears in the evidence,
  use that URL in its website field.
- Search-result snippets may support discovery, but do not claim
  pricing is unavailable unless the evidence establishes that fact.
""".strip()


def _validate_sources(
    report: CompetitorResearchReport,
    evidence: list[SearchResult],
) -> CompetitorResearchReport:
    allowed_urls = {item.url.strip() for item in evidence}
    valid_competitors = []

    for competitor in report.competitors:
        urls = list(dict.fromkeys(
            url for url in competitor.source_urls
            if url in allowed_urls
        ))

        if not urls:
            logger.warning(
                "Removing competitor without valid evidence: %s",
                competitor.name,
            )
            continue

        competitor.source_urls = urls
        valid_competitors.append(competitor)

    report.competitors = valid_competitors[:MAX_COMPETITORS]
    report.sources = list(dict.fromkeys(
        url for url in report.sources if url in allowed_urls
    ))

    for competitor in report.competitors:
        for url in competitor.source_urls:
            if url not in report.sources:
                report.sources.append(url)

    return report


def _complete_report(
    report: CompetitorResearchReport,
    failed_queries: list[str],
    query_count: int,
) -> CompetitorResearchReport:
    """Add concise notes without presenting assumptions as facts."""

    if not report.competitive_gaps:
        report.competitive_gaps = [
            "No competitive gap could be verified from the available evidence."
        ]

    if not report.opportunities:
        report.opportunities = [
            "Further competitor and customer research is needed to validate opportunities."
        ]

    if not report.risks_and_unknowns:
        report.risks_and_unknowns = [
            "Some competitor features or pricing may remain unverified."
        ]

    if not report.limitations:
        report.limitations = [
            "Search results do not independently verify every claim."
        ]

    if len(report.competitors) < MAX_COMPETITORS:
        report.limitations.append(
            f"Only {len(report.competitors)} competitor profiles passed source validation."
        )

    if failed_queries:
        report.limitations.append(
            f"{len(failed_queries)} of {query_count} searches failed."
        )

    for field in (
        "competitive_gaps",
        "opportunities",
        "risks_and_unknowns",
        "limitations",
        "sources",
    ):
        values = getattr(report, field)
        setattr(report, field, list(dict.fromkeys(values)))

    return report


async def run_competitor_research(
    business_idea: str,
    market_scope: str,
) -> CompetitorResearchReport:
    if not business_idea or not business_idea.strip():
        raise ValueError("business_idea must not be empty.")

    if not market_scope or not market_scope.strip():
        raise ValueError("market_scope must not be empty.")

    business_idea = business_idea.strip()
    market_scope = market_scope.strip()

    queries = _build_search_queries(business_idea, market_scope)
    evidence, failed_queries = await _collect_evidence(queries)

    if not evidence:
        return CompetitorResearchReport(
            business_idea=business_idea,
            market_scope=market_scope,
            competitors=[],
            competitive_gaps=[],
            opportunities=[],
            risks_and_unknowns=[
                "No usable competitor evidence was retrieved."
            ],
            sources=[],
            limitations=[
                "Competitor analysis requires usable web search results."
            ],
        )

    prompt = _build_report_prompt(
        business_idea,
        market_scope,
        evidence,
    )

    last_error: Exception | None = None

    for attempt in range(2):
        try:
            current_prompt = prompt

            if attempt == 1:
                current_prompt += (
                    "\nReturn a shorter, complete JSON object. "
                    "Do not omit required schema fields."
                )

            raw_response = await call_groq(
                prompt=current_prompt,
                system=(
                    "You are a careful competitor research analyst. "
                    "Use evidence only and return valid compact JSON."
                ),
                max_tokens=6000 if attempt == 0 else 6000,
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            if not raw_response or not raw_response.strip():
                raise CompetitorResearchError("Groq returned an empty response.")

            data = _extract_json(raw_response)
            report = CompetitorResearchReport.model_validate(data)

            report.business_idea = business_idea
            report.market_scope = market_scope

            report = _validate_sources(report, evidence)
            return _complete_report(
                report,
                failed_queries,
                len(queries),
            )

        except Exception as exc:
            last_error = exc
            logger.warning(
                "Competitor research attempt %d failed: %s",
                attempt + 1,
                exc,
            )

    raise CompetitorResearchError(
        "Could not generate a valid competitor report after two attempts."
    ) from last_error
