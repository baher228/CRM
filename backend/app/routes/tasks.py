from fastapi import APIRouter, HTTPException

from app.schemas import Task, TaskCreateRequest, TaskUpdateRequest
from app.services import tasks_service


router = APIRouter()


@router.get("/tasks", response_model=list[Task])
def get_tasks(status: str | None = None) -> list[Task]:
    return tasks_service.list_tasks(status)


@router.post("/tasks", response_model=Task)
async def create_task(request: TaskCreateRequest) -> Task:
    return await tasks_service.create_task(request)


@router.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, request: TaskUpdateRequest) -> Task:
    task = tasks_service.update_task(task_id, request)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int) -> dict[str, bool]:
    deleted = tasks_service.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": True}
