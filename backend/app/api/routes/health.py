from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check; does not expose secrets or depend on AWS services."""
    return {"status": "ok"}
