"""Development-only simulation endpoints.

This router is mounted **only** when ``DEV_MODE=true`` (see app/main.py), and
every handler re-checks the flag so the routes cannot be reached by a stale
mount. With DEV_MODE off the paths simply do not exist.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..schemas import SimulationRequest, SimulationStatus
from ..services.simulator import SimulationDisabledError, simulator
from ..services.websocket import manager
from .deps import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dev", tags=["development"])


def _require_dev_mode() -> None:
    if not simulator.enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        )


@router.get("/simulation", response_model=SimulationStatus)
def simulation_status(_user: CurrentUser = Depends(current_user)) -> SimulationStatus:
    _require_dev_mode()
    return simulator.status()


@router.post("/simulation/start", response_model=SimulationStatus)
async def start_simulation(
    payload: SimulationRequest,
    user: CurrentUser = Depends(current_user),
) -> SimulationStatus:
    _require_dev_mode()
    try:
        status_out = await simulator.start(payload)
    except SimulationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    logger.info("simulation_requested", extra={"by": user.username, "rooms": payload.rooms})
    await manager.broadcast(
        {"type": "simulation_status", "data": status_out.model_dump(mode="json")}
    )
    return status_out


@router.post("/simulation/stop", response_model=SimulationStatus)
async def stop_simulation(_user: CurrentUser = Depends(current_user)) -> SimulationStatus:
    _require_dev_mode()
    return await simulator.stop()
