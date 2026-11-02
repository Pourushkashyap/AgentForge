
import asyncio

from planning.master_planner import create_master_plan
from planning.backend_planner import create_backend_plan


async def main():
    requirements = """
    Build an e-commerce website for my bakery named Sweet Oven.
    Features: product listings, product details, shopping cart,
    checkout, user authentication, and admin product management.
    Use FastAPI for the backend, PostgreSQL for the database,
    and Docker for deployment.
    """

    master_plan = await create_master_plan(requirements)
    backend_plan = await create_backend_plan(master_plan)

    print("\n--- MASTER PLAN ---")
    print("Business:", master_plan.business_name)
    print("Project:", master_plan.project_name)

    print("\n--- BACKEND PLAN ---")
    print("Business:", backend_plan.business_name)
    print("Project:", backend_plan.project_name)
    print("Framework:", backend_plan.framework)
    print("Architecture:", backend_plan.architecture)

    print("\n--- API ENDPOINTS ---")
    for endpoint in backend_plan.endpoints:
        print(
            f"{endpoint.method} {endpoint.path} | "
            f"{endpoint.name} | "
            f"Auth: {endpoint.authentication_required}"
        )

    print("\n--- DATABASE MODELS ---")
    for model in backend_plan.database_models:
        print(f"\n{model.name}: {model.description}")
        for field in model.fields:
            print(f"  {field.name}: {field.data_type}")

    print("\n--- BACKEND TASKS ---")
    for task in backend_plan.tasks:
        print(f"{task.task_id}: {task.title}")
        print("Dependencies:", task.dependencies)

    print("\n--- ASSUMPTIONS ---")
    for assumption in backend_plan.assumptions:
        print("-", assumption)


if __name__ == "__main__":
    asyncio.run(main())
