
import asyncio
import json
import logging
import re

from pydantic import ValidationError

from core.groq_client import call_groq
from research.schemas import (
    MarketResearchReport,
    ResearchQueryPlan,
)

from research.web_search import search_web


logger = logging.getLogger("agentforge.market_research")


def _extract_json(text: str) -> str:
    """Remove optional Markdown fences from an LLM response."""

    text = text.strip()

    # Handle responses wrapped in ```json ... ```
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:
        text = match.group(1).strip()

    return text


async def _generate_structured_output(
    *,
    prompt: str,
    system: str,
    output_model: type,
    max_tokens: int,
):
    """
    Generate JSON using the shared gateway and validate it with Pydantic.

    Invalid JSON/schema gets one correction attempt. API errors are handled
    by the shared gateway, so this function does not duplicate its retries.
    """

    current_prompt = prompt

    for attempt in range(2):
        attempt_max_tokens = max_tokens if attempt == 0 else max_tokens + 600

        response = await call_groq(
            system=system,
            prompt=current_prompt,
            max_tokens=attempt_max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        try:
            data = json.loads(_extract_json(response))
            return output_model.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "Structured output validation failed (attempt %s/2): %s",
                attempt + 1,
                exc,
            )

            if attempt == 1:
                raise ValueError(
                    f"Could not generate valid {output_model.__name__} "
                    "after two attempts."
                ) from exc

            current_prompt = (
                prompt
                + "\n\nCORRECTION: Your previous response failed validation. "
                + "Return a complete JSON object containing EVERY required "
                + "field from the schema. Keep all fields concise. "
                + "Do not omit opportunities, risks_and_unknowns, "
                + "recommended_next_research, sources, or limitations. "
                + "Do not include Markdown fences."
            )


async def create_research_plan(
    business_idea: str,
    geography: str,
    context: str = "",
) -> ResearchQueryPlan:

    schema = json.dumps(
        ResearchQueryPlan.model_json_schema(),
        ensure_ascii=False,
    )

    prompt = f"""
Create a focused market research plan.

BUSINESS IDEA:
{business_idea}

TARGET GEOGRAPHY:
{geography}

ADDITIONAL CONTEXT:
{context or "None provided"}

Requirements:
- Identify the relevant market scope.
- Generate 3 to 8 specific research questions.
- Generate 3 to 6 distinct web search queries.
- Cover market trends, target customers, opportunities and risks
  where relevant.
- Prefer queries likely to produce verifiable evidence.
- Do not invent market statistics.
- Do not write the final research report yet.

Return ONLY a valid JSON object matching this schema:
{schema}
"""

    return await _generate_structured_output(
        prompt=prompt,
        system=(
            "You are a professional market research planner. "
            "Return accurate, concise, schema-compliant JSON."
        ),
        output_model=ResearchQueryPlan,
        max_tokens=1200,
    )


async def run_market_research(
    business_idea: str,
    geography: str = "Global",
    context: str = "",
) -> MarketResearchReport:
    """
    Research a business idea and return a validated market research report.

    All LLM requests use the shared Groq gateway.
    """

    if not business_idea.strip():
        raise ValueError("business_idea cannot be empty")

    logger.info(
        "Starting market research: idea=%s, geography=%s",
        business_idea,
        geography,
    )

    # 1. Plan the research through the shared Groq gateway.
    plan = await create_research_plan(
        business_idea=business_idea,
        geography=geography,
        context=context,
    )

    logger.info(
        "Research plan created with %s queries",
        len(plan.search_queries),
    )

    # 2. Execute web searches.
    # search_web is synchronous, so run it in a worker thread to avoid
    # blocking the asyncio event loop.
    
    search_tasks = [
        asyncio.to_thread(search_web, query=query, max_results=2)
        for query in plan.search_queries
    ]


    search_batches = await asyncio.gather(
        *search_tasks,
        return_exceptions=True,
    )

    collected_results = []
    seen_urls = set()
    search_errors = []

    for query, batch in zip(plan.search_queries, search_batches):
        if isinstance(batch, Exception):
            logger.warning(
                "Search failed for query %r: %s",
                query,
                batch,
            )
            search_errors.append(f"{query}: {batch}")
            continue

        for result in batch:
            normalized_url = result.url.rstrip("/")

            if normalized_url in seen_urls:
                continue

            seen_urls.add(normalized_url)
            collected_results.append(result)

    if not collected_results:
        details = "; ".join(search_errors[:3])

        raise RuntimeError(
            "Market research could not continue because no usable web "
            f"results were found. Check Tavily configuration. {details}"
        )

    logger.info(
        "Collected %s unique web sources",
        len(collected_results),
    )

    # 3. Prepare evidence for the report-generation call.
    
    # Keep only the most useful fields and limit snippet length.
    evidence = [
        {
            "title": result.title[:200],
            "url": result.url,
            "content": result.content[:400],
            "published_date": result.published_date,
        }
        for result in collected_results
    ]


    schema = json.dumps(
        MarketResearchReport.model_json_schema(),
        ensure_ascii=False,
    )

    report_prompt = f"""
Create a market research report for the business idea below.

BUSINESS IDEA:
{business_idea}

TARGET GEOGRAPHY:
{geography}

ADDITIONAL CONTEXT:
{context or "None provided"}

RESEARCH PLAN:
{plan.model_dump_json(indent=2)}

WEB SEARCH EVIDENCE:
{json.dumps(evidence, ensure_ascii=False, indent=2)}

SEARCH ERRORS:
{json.dumps(search_errors, ensure_ascii=False)}

REQUIREMENTS:

1. Base factual claims only on the supplied web evidence.
2. Never invent market sizes, statistics, prices or growth rates.
3. Cite exact source URLs from the supplied evidence for factual findings.
4. Use evidence_type values:
   - direct_evidence
   - inference
   - uncertain
5. Use confidence values:
   - high
   - medium
   - low
6. Search snippets are limited evidence. Do not claim that a complete
   webpage was reviewed when only a snippet was supplied.
7. Separate observed facts from recommendations.
8. Include opportunities, risks and important unknowns.
9. If evidence is weak or contradictory, say so explicitly.
10. Do not claim that market research proves customer demand.
11. Every evidence-based market finding must include at least one
    relevant source URL from the supplied evidence.
12. If the evidence does not support a conclusion, label it uncertain
    or describe the information gap.

Return ONLY a valid JSON object matching this schema:
{schema}
"""

    report = await _generate_structured_output(
        prompt=report_prompt,
        system=(
            "You are a careful market research analyst. "
            "Return one complete JSON object matching the schema. "
            "Include every required field. Keep all content concise."
        ),
        output_model=MarketResearchReport,
        max_tokens=3000,
    )

    # 4. Enforce that cited URLs actually appeared in the search results.
    allowed_urls = {
        result.url.rstrip("/"): result.url
        for result in collected_results
    }

    for finding in report.market_findings:
        verified_urls = []

        for url in finding.source_urls:
            normalized = url.rstrip("/")

            if normalized in allowed_urls:
                verified_urls.append(allowed_urls[normalized])
            else:
                logger.warning(
                    "Discarding unverified source URL: %s",
                    url,
                )

        finding.source_urls = list(dict.fromkeys(verified_urls))

    report.sources = [result.url for result in collected_results]
    report.business_idea = business_idea
    report.market_scope = plan.market_scope

    # 5. Report evidence gaps instead of silently ignoring failed searches.
    if search_errors:
        report.limitations.append(
            f"{len(search_errors)} web search query/queries failed. "
            "The report may have incomplete coverage."
        )

    # Surface findings whose citations could not be verified.
    uncited_findings = [
        finding
        for finding in report.market_findings
        if not finding.source_urls
        and finding.evidence_type != "uncertain"
    ]

    if uncited_findings:
        report.limitations.append(
            f"{len(uncited_findings)} finding(s) have no verified source "
            "URL. Review these claims before relying on them."
        )

        for finding in uncited_findings:
            finding.evidence_type = "uncertain"
            finding.confidence = "low"

    logger.info(
        "Market research completed: %s findings, %s sources",
        len(report.market_findings),
        len(report.sources),
    )

    return report
