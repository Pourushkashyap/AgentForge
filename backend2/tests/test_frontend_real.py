
import asyncio

from planning.master_planner import create_master_plan
from planning.frontend_planner import create_frontend_plan
from generation.frontend_generator import generate_frontend


async def main():
    
    requirements = """
    Create a modern coffee shop website named BrewNest.

    Show the coffee menu, popular drinks, prices, and a contact section.
    Use a beautiful design with coffee colors and attractive images.
    Make it responsive and ensure all buttons and navigation work.
    """

    # Step 1: Create the master plan
    master_plan = await create_master_plan(requirements)

    print("\n--- MASTER PLAN ---")
    print("Business:", master_plan.business_name)
    print("Project:", master_plan.project_name)

    # Step 2: Create the frontend plan
    frontend_plan = await create_frontend_plan(master_plan)

    print("\n--- FRONTEND PLAN ---")
    print("Business:", frontend_plan.business_name)
    print("Project:", frontend_plan.project_name)
    print("Framework:", frontend_plan.framework)
    print("Styling:", frontend_plan.styling)

    print("\n--- PAGES ---")
    for page in frontend_plan.pages:
        print(
            f"{page.name} | {page.route} | "
            f"Auth required: {page.requires_auth}"
        )

    print("\n--- FRONTEND TASKS ---")
    for task in frontend_plan.tasks:
        print(f"{task.task_id}: {task.title}")
        print("Dependencies:", task.dependencies)

    # Step 3: Generate the new frontend
    print("\n--- GENERATING TRAVELEASE FRONTEND ---")

    project_dir = await generate_frontend(
        frontend_plan=frontend_plan,
        output_dir="generated_projects_validation_v3",
        overwrite=False,
    )

    print("\n--- GENERATION COMPLETED ---")
    print("Generated frontend:", project_dir)


if __name__ == "__main__":
    asyncio.run(main())
