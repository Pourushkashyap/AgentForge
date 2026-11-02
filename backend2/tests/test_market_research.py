
import asyncio
import json

from research.market_research import run_market_research


async def main():
    report = await run_market_research(
        business_idea=(
            "A premium artisan ice cream shop with online ordering "
            "and local delivery"
        ),
        geography="India",
        context=(
            "Target urban customers. Research competitors, "
            "customer needs, opportunities and risks."
        ),
    )

    print("\n===== MARKET RESEARCH REPORT =====\n")

    print(
        json.dumps(
            report.model_dump(),
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\n===== QUICK SUMMARY =====")
    print(report.executive_summary)

    print("\n===== OPPORTUNITIES =====")
    for item in report.opportunities:
        print("-", item)

    print("\n===== SOURCES =====")
    for source in report.sources:
        print("-", source)


if __name__ == "__main__":
    asyncio.run(main())
