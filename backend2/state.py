from typing import TypedDict, Optional
from pydantic import BaseModel

from typing import Literal




class ProjectState(TypedDict):
    
    project_name:str
    
    user_request: str
    
    research_report: dict | None
    
   
    
    written_files: list[str]
    
    dependencies: list[str] | None
    
    dev_dependencies:list[str] | None
    
    dockerfile: Optional[str]
    
    docker_compose: Optional[str]
    
    workspace_path: str
    
    project_path: str
    
    build_success: bool
    
    
    
    runtime_logs: Optional[str]
 