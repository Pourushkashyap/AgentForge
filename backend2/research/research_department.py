
import asyncio
import logging

from research.schemas import ResearchDepartmentReport
from research.market_research import run_market_research
from research.competitor_research import run_competitor_research

logger = logging.getLogger("agentforge.research_department")


class ResearchDepartmentError(RuntimeError):
    pass


async def run_research_department(
    business_idea: str,
    market_scope: str = "Global",
    context: str = "",
) -> ResearchDepartmentReport:
    """Run market and competitor research concurrently."""

    if not business_idea or not business_idea.strip():
        raise ValueError("business_idea cannot be empty")

    if not market_scope or not market_scope.strip():
        raise ValueError("market_scope cannot be empty")

    business_idea = business_idea.strip()
    market_scope = market_scope.strip()

    results = await asyncio.gather(
        run_market_research(
            business_idea=business_idea,
            geography=market_scope,
            context=context,
        ),
        run_competitor_research(
            business_idea=business_idea,
            market_scope=market_scope,
        ),
        return_exceptions=True,
    )

    market_result, competitor_result = results

    failures = []
    if isinstance(market_result, Exception):
        failures.append(f"Market research failed: {market_result}")

    if isinstance(competitor_result, Exception):
        failures.append(f"Competitor research failed: {competitor_result}")

    if failures:
        raise ResearchDepartmentError(
            "Research department could not complete:\n"
            + "\n".join(failures)
        )

    market_report = market_result
    competitor_report = competitor_result

    opportunities = list(dict.fromkeys(
        market_report.opportunities
        + competitor_report.opportunities
    ))

    risks = list(dict.fromkeys(
        market_report.risks_and_unknowns
        + competitor_report.risks_and_unknowns
    ))

    sources = list(dict.fromkeys(
        market_report.sources + competitor_report.sources
    ))

    limitations = list(dict.fromkeys(
        market_report.limitations + competitor_report.limitations
    ))

    logger.info(
        "Research completed: %d market findings, %d competitors, %d sources",
        len(market_report.market_findings),
        len(competitor_report.competitors),
        len(sources),
    )

    return ResearchDepartmentReport(
        business_idea=business_idea,
        market_scope=market_scope,
        market_research=market_report,
        competitor_research=competitor_report,
        combined_opportunities=opportunities,
        combined_risks=risks,
        sources=sources,
        limitations=limitations,
    )
