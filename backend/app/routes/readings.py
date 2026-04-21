"""
Route handlers for sensor readings ingestion and retrieval.

Manages the `/api/ingest` endpoint consumed by ESP32 firmware and the
per-room reading history, latest snapshot, and 24-hour summary endpoints.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import ComfortThreshold, Room, SensorReading, get_db
from app.models import ReadingResponse, SensorPayload
from app.transforms import run_all_transforms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["readings"])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/ingest", response_model=ReadingResponse, status_code=status.HTTP_201_CREATED)
async def ingest(payload: SensorPayload, db: DB):
    try:
        room_result = await db.execute(select(Room).where(Room.id == payload.room_id))
        if room_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Room not registered. Register this room in the dashboard before flashing.",
            )

        threshold_result = await db.execute(select(ComfortThreshold).limit(1))
        thresholds = threshold_result.scalar_one()

        transformed = run_all_transforms(payload, thresholds)

        reading = SensorReading(
            room_id=payload.room_id,
            timestamp=payload.timestamp,
            temperature=payload.temperature,
            humidity=payload.humidity,
            motion_count=payload.motion_count,
            light_raw=payload.light_raw,
            sound_rms=payload.sound_rms,
            **transformed,
        )
        db.add(reading)
        await db.commit()
        await db.refresh(reading)
        return reading

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error during ingest for room_id=%s", payload.room_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process sensor reading. Please try again.",
        )


@router.get("/rooms/{room_id}/readings", response_model=list[ReadingResponse])
async def get_readings(
    room_id: str,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    if room_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    result = await db.execute(
        select(SensorReading)
        .where(SensorReading.room_id == room_id)
        .order_by(SensorReading.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/rooms/{room_id}/latest", response_model=ReadingResponse)
async def get_latest(room_id: str, db: DB):
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    if room_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    result = await db.execute(
        select(SensorReading)
        .where(SensorReading.room_id == room_id)
        .order_by(SensorReading.timestamp.desc())
        .limit(1)
    )
    reading = result.scalar_one_or_none()
    if reading is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No readings yet for this room",
        )
    return reading


@router.get("/rooms/{room_id}/summary")
async def get_summary(room_id: str, db: DB):
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    if room_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    since = datetime.now(timezone.utc) - timedelta(hours=24)

    metrics = {
        "temperature": ("°C", SensorReading.temperature),
        "humidity": ("%", SensorReading.humidity),
        "sound_db": ("dB", SensorReading.sound_db),
        "light_lux": ("lux", SensorReading.light_lux),
        "movements_per_min": ("mov/min", SensorReading.movements_per_min),
        "comfort_score": ("/100", SensorReading.comfort_score),
    }

    summary = {}
    for name, (unit, col) in metrics.items():
        result = await db.execute(
            select(
                func.avg(col).label("avg"),
                func.min(col).label("min"),
                func.max(col).label("max"),
            ).where(
                SensorReading.room_id == room_id,
                SensorReading.timestamp >= since,
            )
        )
        row = result.one()
        if row.avg is None:
            summary[name] = {"avg": None, "min": None, "max": None, "unit": unit}
        else:
            summary[name] = {
                "avg": round(row.avg, 1),
                "min": round(row.min, 1),
                "max": round(row.max, 1),
                "unit": unit,
            }

    return summary
