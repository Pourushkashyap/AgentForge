import asyncio
import os
import sys

from generation.project_pipeline import generate_project_pipeline


async def main():
    requirements = """
    Build a bakery e-commerce website named Sweet Oven.

    Features:
    - Product listing with names, images, prices, and categories
    - Product details
    - Shopping cart with quantity updates
    - Checkout flow
    - Contact page
    - Responsive design

    Generate a React frontend and FastAPI backend.
    The frontend must fetch products from the backend API.
    The backend must persist product and order data.
    All frontend API calls must match implemented backend routes.
    """

    result = await generate_project_pipeline(
        requirements=requirements,
        output_dir="generated_projects_e2e",
        overwrite_frontend=True,
        validate_connected=True,
        validate_browser="--no-browser" not in sys.argv,
    )

    print("\n=== PIPELINE RESULT ===")
    print("Project :", result["project_name"])
    print("Frontend:", result["frontend_dir"])
    print("Backend :", result["backend_dir"])
    print("Seeded  :", result["seed_summary"])
    print("Endpoints the backend serves:")
    for e in result["api_contract"]:
        print(f"  {e['method']:6} {e['path']}")

    assert result["frontend_dir"].exists(), "Frontend directory missing"
    assert (result["backend_dir"] / "main.py").exists(), "Backend main.py missing"
    assert result["contract_result"]["valid"], "API contract invalid"
    print("\nPipeline generation and contract validation succeeded.")


if __name__ == "__main__":
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not configured.")
    asyncio.run(main())