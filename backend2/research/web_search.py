import os
import requests
from dotenv import load_dotenv

from research.schemas import SearchResult

load_dotenv()

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class WebSearchError(RuntimeError):
    pass


def search_web(query: str, max_results: int = 5) -> list[SearchResult]:
    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        raise WebSearchError(
            "TAVILY_API_KEY is missing from your .env file."
        )

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": min(max_results, 10),
        "include_answer": False,
        "include_raw_content": False,
    }

    try:
        response = requests.post(
            TAVILY_SEARCH_URL,
            json=payload,
            timeout=25,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as exc:
        raise WebSearchError(f"Web search failed: {exc}") from exc

    results = []

    for item in data.get("results", []):
        url = item.get("url")
        if not url:
            continue

        results.append(
            SearchResult(
                title=item.get("title") or "",
                url=url,
                content=item.get("content") or "",
                published_date=item.get("published_date"),
                score=item.get("score"),
            )
        )

    return results
