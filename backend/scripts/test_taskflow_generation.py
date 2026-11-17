from pathlib import Path
from planning.backend_schemas import BackendPlan
from generation.backend_generator import generate_backend_project

plan = BackendPlan(
    business_name="TaskFlow",
    project_name="TaskFlow API",
    framework="FastAPI",
    architecture="FastAPI with JSON file storage",
    database_models=[
        {
            "name": "User",
            "description": "TaskFlow users",
            "fields": [
                {"name": "id", "data_type": "string", "required": False},
                {"name": "name", "data_type": "string", "required": True},
                {"name": "email", "data_type": "email", "required": True},
                {"name": "is_active", "data_type": "boolean", "required": True},
            ],
        },
        {
            "name": "Task",
            "description": "Tasks assigned to users",
            "fields": [
                {"name": "id", "data_type": "string", "required": False},
                {"name": "title", "data_type": "string", "required": True},
                {"name": "description", "data_type": "text", "required": False},
                {"name": "status", "data_type": "string", "required": True},
                {"name": "priority", "data_type": "integer", "required": True},
                {"name": "completed", "data_type": "boolean", "required": True},
                {"name": "assignee_id", "data_type": "string", "required": False},
            ],
        },
        {
            "name": "Comment",
            "description": "Comments on tasks",
            "fields": [
                {"name": "id", "data_type": "string", "required": False},
                {"name": "task_id", "data_type": "string", "required": True},
                {"name": "author", "data_type": "string", "required": True},
                {"name": "message", "data_type": "text", "required": True},
            ],
        },
    ],
    tasks=[
        {
            "task_id": "TF1",
            "title": "Generate TaskFlow backend",
            "description": "Generate a multi-model CRUD API",
            "dependencies": [],
        }
    ],
)

output = Path("generated_projects/taskflow_api").resolve()
generate_backend_project(plan, output)

print(f"Generated project: {output}")
print(f"Models: {', '.join(model.name for model in plan.database_models)}")
print(f"main.py exists: {(output / 'main.py').is_file()}")
print(f"Requirements exist: {(output / 'requirements.txt').is_file()}")
