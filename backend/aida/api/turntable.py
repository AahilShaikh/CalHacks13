"""Blocking HTTP routes; sync handlers run in FastAPI's thread pool."""
import os
import re
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..hardware.control import TurntableError
from ..turntable import TurntableService, service
from ..hardware.cameras import selected_camera

router = APIRouter(prefix='/api/v1/turntable', tags=['turntable'])


class RotationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    angle: float = Field(description='Degrees; normalized modulo 360. Absolute by default.')
    relative: bool = False
    tolerance_degrees: float = Field(default=1.0, gt=0, le=10)
    timeout_s: float = Field(default=30.0, ge=3, le=120)


class ZeroRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    timeout_s: float = Field(default=15.0, ge=3, le=120)
    empty_table_reference: bool = False


def get_service() -> TurntableService:
    return service


def serial_ports():
    from serial.tools.list_ports import comports
    return [{'port': p.device, 'description': p.description}
            for p in comports() if p.vid is not None]


class PortRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    port: str


@router.get('/configuration')
def configuration():
    folder = Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable'))
    saved = json.loads((folder/'hardware.json').read_text()) if (folder/'hardware.json').exists() else {}
    try:
        camera = selected_camera()
    except TurntableError:
        camera = None
    return {'port': os.environ.get('AIDA_TURNTABLE_PORT') or saved.get('port'),
            'ports': serial_ports(), 'camera_index': camera['index'] if camera else None,
            'neutral_us': int(os.environ.get('AIDA_TURNTABLE_NEUTRAL_US', '1450'))}


@router.post('/configuration')
def configure_port(request: PortRequest, controller: TurntableService = Depends(get_service)):
    with controller._exclusive():
        if request.port not in {p['port'] for p in serial_ports()}:
            raise HTTPException(409, 'Connect the Arduino over USB, refresh ports, then select its port.')
        override = os.environ.get('AIDA_TURNTABLE_PORT')
        if override and override != request.port:
            raise HTTPException(409, 'AIDA_TURNTABLE_PORT overrides this selection; change it and restart the API.')
        folder = Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable'))
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder/'hardware.tmp'
        saved = json.loads((folder/'hardware.json').read_text()) if (folder/'hardware.json').exists() else {}
        saved['port'] = request.port
        temporary.write_text(json.dumps(saved)+'\n')
        temporary.replace(folder/'hardware.json')
        return configuration()


def http_error(exc: TurntableError):
    return HTTPException(status_code=exc.http_status,
                         detail={'code': exc.code, 'message': str(exc)})


@router.post('/rotate')
def rotate(request: RotationRequest, controller: TurntableService = Depends(get_service)):
    try:
        return controller.rotate(request.angle, relative=request.relative,
                                 tolerance=request.tolerance_degrees, timeout_s=request.timeout_s,
                                 capture=True)
    except TurntableError as exc:
        raise http_error(exc) from exc


@router.post('/zero')
def zero(request: ZeroRequest = ZeroRequest(), controller: TurntableService = Depends(get_service)):
    try:
        return controller.zero(request.timeout_s, empty_table_reference=request.empty_table_reference)
    except TurntableError as exc:
        raise http_error(exc) from exc


@router.get('/captures/{filename}')
def get_capture(filename: str):
    if not re.fullmatch(r'[0-9a-f]{32}\.jpg', filename):
        raise HTTPException(status_code=404, detail='No such capture.')
    path = Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable')) / 'captures' / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail='No such capture.')
    return FileResponse(path, media_type='image/jpeg', filename=filename)
