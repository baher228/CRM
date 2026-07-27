from app.schemas import Task, TaskCreateRequest, TaskUpdateRequest
from app.services import crm_store


def list_tasks(status: str | None = None) -> list[Task]:
    return crm_store.list_tasks(status)


def get_task(task_id: int) -> Task | None:
    return crm_store.get_task(task_id)


async def create_task(request: TaskCreateRequest) -> Task:
    return crm_store.create_task(
        Task(
            id=0,
            title=request.title.strip(),
            due_date=request.due_date,
            related_type=request.related_type.strip(),
            related_id=request.related_id,
            related_to=request.related_to.strip(),
            priority=request.priority,
            notes=request.notes.strip(),
            sync_status="Local",
            last_sync_message="Saved in CRM Workspace",
        )
    )


def update_task(task_id: int, request: TaskUpdateRequest) -> Task | None:
    task = get_task(task_id)
    if not task:
        return None
    update = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in request.model_dump(exclude_unset=True).items()
    }
    return crm_store.save_task(task.model_copy(update=update))


def delete_task(task_id: int) -> bool:
    return crm_store.delete_task(task_id)
