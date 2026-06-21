"""Shift scheduling endpoints + clock-in/out.

Mounted under `/api` by server.py.
"""
import logging
from datetime import date as _date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.db import db
from core.push import send_push
from core.security import get_current_user, require_admin
from models import (
    Shift, ShiftCreate, ShiftUpdate, UserPublic, now_iso,
)

router = APIRouter(tags=['shifts'])
logger = logging.getLogger(__name__)

WEEKDAY_MAP = {
    'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3, 'FRI': 4, 'SAT': 5, 'SUN': 6,
}


def _expand_recurring(parent: Shift) -> List[Shift]:
    """Generate one_off child shifts from a recurring parent."""
    if not parent.weekdays or not parent.recurring_until or not parent.date:
        return []
    try:
        d0 = _date.fromisoformat(parent.date)
        d1 = _date.fromisoformat(parent.recurring_until)
    except Exception:
        return []
    wanted = {WEEKDAY_MAP[w] for w in parent.weekdays if w in WEEKDAY_MAP}
    if not wanted:
        return []
    children: list[Shift] = []
    d = d0
    while d <= d1 and len(children) < 366:
        if d.weekday() in wanted:
            children.append(Shift(
                caregiver_id=parent.caregiver_id, client_id=parent.client_id,
                kind='one_off', date=d.isoformat(),
                start_time=parent.start_time, end_time=parent.end_time,
                notes=parent.notes, service_type=parent.service_type,
                created_by=parent.created_by, parent_shift_id=parent.id,
                status='scheduled',
            ))
        d += timedelta(days=1)
    return children


class ClockReq(BaseModel):
    location: Optional[str] = None


@router.post('/shifts', response_model=Shift)
async def create_shift(
    req: ShiftCreate, current: UserPublic = Depends(get_current_user)
):
    if current.role == 'caregiver':
        if req.caregiver_id != current.id:
            raise HTTPException(
                403, 'Caregivers can only schedule shifts for themselves.'
            )
        link = await db.assignments.find_one(
            {'caregiver_id': current.id, 'client_id': req.client_id}
        )
        if not link:
            raise HTTPException(403, 'You are not assigned to this client.')
    parent = Shift(**req.dict(), created_by=current.id)
    await db.shifts.insert_one(parent.dict())
    if parent.kind == 'recurring':
        children = _expand_recurring(parent)
        if children:
            await db.shifts.insert_many([c.dict() for c in children])
    if current.role == 'admin' and parent.caregiver_id != current.id:
        try:
            await send_push(
                recipients=[parent.caregiver_id],
                data={
                    'title': 'New shift scheduled',
                    'message': (
                        f"{parent.date or 'Recurring'} \u00b7 "
                        f'{parent.start_time}-{parent.end_time}'
                    ),
                    'action_url': '/schedule',
                },
            )
        except Exception as e:
            logger.warning(f'shift push failed: {e}')
    return parent


@router.get('/shifts', response_model=List[Shift])
async def list_shifts(
    client_id: Optional[str] = None,
    caregiver_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    q: dict = {}
    if client_id:
        q['client_id'] = client_id
    if caregiver_id:
        q['caregiver_id'] = caregiver_id
    if current.role == 'caregiver':
        q['caregiver_id'] = current.id
    q['$or'] = [{'kind': 'one_off'}, {'kind': {'$exists': False}}]
    if start or end:
        date_q: dict = {}
        if start:
            date_q['$gte'] = start
        if end:
            date_q['$lte'] = end
        q['date'] = date_q
    docs = (
        await db.shifts.find(q, {'_id': 0}).sort('date', 1).to_list(1000)
    )
    return [Shift(**d) for d in docs]


@router.put('/shifts/{shift_id}', response_model=Shift)
async def update_shift(
    shift_id: str, req: ShiftUpdate,
    current: UserPublic = Depends(get_current_user),
):
    d = await db.shifts.find_one({'id': shift_id}, {'_id': 0})
    if not d:
        raise HTTPException(404, 'Shift not found')
    is_admin = current.role == 'admin'
    is_owner = d.get('caregiver_id') == current.id
    if not is_admin and not is_owner:
        raise HTTPException(403, 'Not allowed')
    patch = {
        k: v for k, v in req.dict(exclude_unset=True).items() if v is not None
    }
    if not is_admin:
        allowed = {'notes', 'service_type'}
        patch = {k: v for k, v in patch.items() if k in allowed}
    if not patch:
        return Shift(**d)
    patch['updated_at'] = now_iso()
    await db.shifts.update_one({'id': shift_id}, {'$set': patch})
    d.update(patch)
    if is_admin and d.get('caregiver_id') and d['caregiver_id'] != current.id:
        try:
            await send_push(
                recipients=[d['caregiver_id']],
                data={
                    'title': 'Shift updated',
                    'message': (
                        f"{d.get('date','')} \u00b7 "
                        f"{d.get('start_time','')}-{d.get('end_time','')}"
                    ),
                    'action_url': '/schedule',
                },
            )
        except Exception as e:
            logger.warning(f'shift update push failed: {e}')
    return Shift(**d)


@router.delete('/shifts/{shift_id}')
async def delete_shift(
    shift_id: str, current: UserPublic = Depends(require_admin)
):
    d = await db.shifts.find_one({'id': shift_id}, {'_id': 0})
    if not d:
        return {'ok': True}
    await db.shifts.delete_one({'id': shift_id})
    if d.get('kind') == 'recurring':
        await db.shifts.delete_many({'parent_shift_id': d['id']})
    if d.get('caregiver_id'):
        try:
            await send_push(
                recipients=[d['caregiver_id']],
                data={
                    'title': 'Shift cancelled',
                    'message': (
                        f"{d.get('date','')} \u00b7 "
                        f"{d.get('start_time','')}-{d.get('end_time','')}"
                    ),
                    'action_url': '/schedule',
                },
            )
        except Exception as e:
            logger.warning(f'shift cancel push failed: {e}')
    return {'ok': True}


@router.post('/shifts/{shift_id}/clock-in', response_model=Shift)
async def clock_in(
    shift_id: str, req: ClockReq,
    current: UserPublic = Depends(get_current_user),
):
    d = await db.shifts.find_one({'id': shift_id}, {'_id': 0})
    if not d:
        raise HTTPException(404, 'Shift not found')
    if current.role == 'caregiver' and d['caregiver_id'] != current.id:
        raise HTTPException(403, 'Not your shift')
    update = {
        'clocked_in_at': now_iso(),
        'clock_location': req.location,
        'status': 'in_progress',
        'updated_at': now_iso(),
    }
    await db.shifts.update_one({'id': shift_id}, {'$set': update})
    d.update(update)
    return Shift(**d)


@router.post('/shifts/{shift_id}/clock-out', response_model=Shift)
async def clock_out(
    shift_id: str, req: ClockReq,
    current: UserPublic = Depends(get_current_user),
):
    d = await db.shifts.find_one({'id': shift_id}, {'_id': 0})
    if not d:
        raise HTTPException(404, 'Shift not found')
    if current.role == 'caregiver' and d['caregiver_id'] != current.id:
        raise HTTPException(403, 'Not your shift')
    update = {
        'clocked_out_at': now_iso(),
        'status': 'completed',
        'updated_at': now_iso(),
    }
    await db.shifts.update_one({'id': shift_id}, {'$set': update})
    d.update(update)
    return Shift(**d)
