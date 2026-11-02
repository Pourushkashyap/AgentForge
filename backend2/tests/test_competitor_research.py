
import asyncio

from research.competitor_research import run_competitor_research


async def main():
    report = await run_competitor_research(
        business_idea=(
            "Premium artisan ice cream shop with online ordering"
        ),
        market_scope="United States",
    )

    print("\n===== COMPETITOR RESEARCH REPORT =====")
    print(report.model_dump_json(indent=2))

    print("\n===== COMPETITORS =====")
    for competitor in report.competitors:
        print(f"\nName: {competitor.name}")
        print(f"Website: {competitor.website}")
        print(f"Pricing: {competitor.pricing_status}")
        print(f"Pricing details: {competitor.pricing_details}")
        print(f"Features: {competitor.features}")
        print(f"Sources: {competitor.source_urls}")

    print("\n===== COMPETITIVE GAPS =====")
    for gap in report.competitive_gaps:
        print(f"- {gap}")

    print("\n===== LIMITATIONS =====")
    for limitation in report.limitations:
        print(f"- {limitation}")


if __name__ == "__main__":
    asyncio.run(main())
