from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Facility, FacilityCredential, User
from app.schemas import CredentialIn, FacilityIn
from app.security import encrypt_secret

router = APIRouter()


@router.get("/api/facilities")
def list_facilities(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    facilities = db.query(Facility).order_by(Facility.name).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "platform": f.platform,
            "base_url": f.base_url,
            "portal_id": f.portal_id,
            "timezone": f.timezone,
            "booking_window_days": f.booking_window_days,
        }
        for f in facilities
    ]


@router.post("/api/facilities")
def create_facility(payload: FacilityIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    facility = Facility(**payload.model_dump())
    db.add(facility)
    db.commit()
    db.refresh(facility)
    return {"id": facility.id}


@router.get("/api/credentials")
def list_credentials(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    creds = db.query(FacilityCredential).filter(FacilityCredential.user_id == user.id).all()
    return [
        {
            "id": c.id,
            "facility_id": c.facility_id,
            "facility_name": c.facility.name,
            "username": c.username,
            "member_id": c.member_id,
        }
        for c in creds
    ]


@router.post("/api/credentials")
def create_credential(payload: CredentialIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    facility = db.get(Facility, payload.facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")

    existing = (
        db.query(FacilityCredential)
        .filter(FacilityCredential.user_id == user.id, FacilityCredential.facility_id == payload.facility_id)
        .first()
    )
    if existing:
        existing.username = payload.username
        existing.encrypted_password = encrypt_secret(payload.password)
        existing.member_id = payload.member_id
        db.commit()
        return {"id": existing.id, "updated": True}

    cred = FacilityCredential(
        user_id=user.id,
        facility_id=payload.facility_id,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        member_id=payload.member_id,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return {"id": cred.id, "updated": False}


@router.delete("/api/credentials/{credential_id}")
def delete_credential(credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cred = db.get(FacilityCredential, credential_id)
    if not cred or cred.user_id != user.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    db.delete(cred)
    db.commit()
    return {"ok": True}
