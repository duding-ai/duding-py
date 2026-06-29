from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


# =========================
# Lead schemas
# =========================


class LeadBase(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    business: Optional[str] = None
    budget: Optional[str] = None
    link: Optional[str] = None
    role: Optional[str] = None
    cadence: Optional[str] = None
    recent: Optional[str] = None


class LeadCreate(LeadBase):
    """Payload used when creating a new lead via /api/leads."""

    pass


class LeadRead(LeadBase):
    id: int
    created_at: datetime
    status: Optional[str] = None
    run_count: int = 0
    last_run: Optional[datetime] = None

    class Config:
        from_attributes = True


# =========================
# LeadEvent schemas
# =========================


class LeadEventRead(BaseModel):
    id: int
    lead_id: int
    event_type: str
    message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
