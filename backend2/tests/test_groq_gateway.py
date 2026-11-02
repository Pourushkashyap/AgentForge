
import asyncio

from core.groq_client import call_groq


async def main():
    print("Sending request through shared Groq gateway...")

    answer = await call_groq(
        system="You are a concise AI assistant.",
        prompt="Explain what an AI research agent does in 3 bullet points.",
        max_tokens=300,
        temperature=0.2,
    )

    print("\nResponse:\n")
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
