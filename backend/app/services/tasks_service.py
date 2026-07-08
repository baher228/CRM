from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Task, TaskCreateRequest, TaskUpdateRequest
from app.services import clients_service, crm_store


def list_tasks(status: str | None = None) -> list[Task]:
    return crm_store.list_tasks(status)


def get_task(task_id: int) -> Task | None:
    return crm_store.get_task(task_id)


async def create_task(request: TaskCreateRequest) -> Task:
    task = Task(
        id=0,
        title=request.title.strip(),
        due_date=request.due_date,
        related_type=request.related_type.strip(),
        related_id=request.related_id,
        related_to=request.related_to.strip(),
        priority=request.priority,
        notes=request.notes.strip(),
    )
    if request.sync_to_attio:
        task = await _sync_task_to_attio(task)
    return crm_store.create_task(task)


def update_task(task_id: int, request: TaskUpdateRequest) -> Task | None:
    task = get_task(task_id)
    if not task:
        return None
    update = {}
    for key, value in request.model_dump(exclude_unset=True).items():
        update[key] = value.strip() if isinstance(value, str) else value
    return crm_store.save_task(task.model_copy(update=update))


def delete_task(task_id: int) -> bool:
    return crm_store.delete_task(task_id)


async def _sync_task_to_attio(task: Task) -> Task:
    if task.related_type != "client" or task.related_id is None:
        return task.model_copy(update={"last_sync_message": "Select an Attio-synced contact to create an Attio task"})
    client = clients_service.get_client(task.related_id)
    if not client or not client.attio_person_record_id:
        return task.model_copy(update={"last_sync_message": "Selected contact has no Attio person id"})

    try:
        settings = EnrichmentSettings()
        settings.require_attio_key()
        attio_client = AttioClient(settings)
        try:
            await attio_client.create_task(
                settings.attio_person_object,
                client.attio_person_record_id,
                task.title,
                task.notes or task.related_to or task.title,
            )
            return task.model_copy(
                update={
                    "attio_task_created": True,
                    "sync_status": "Synced",
                    "last_sync_message": "Attio task created",
                }
            )
        finally:
            await attio_client.close()
    except (ApiClientError, ValueError) as exc:
        return task.model_copy(update={"last_sync_message": f"Attio task failed: {exc}"})
