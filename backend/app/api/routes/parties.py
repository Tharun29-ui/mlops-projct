from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import AppSetting, Invoice
from app.repositories import dynamodb_store as store
from app.services.party_ledger import counterparty, directory, ledger, name_key, party_id

router = APIRouter(prefix="/parties", tags=["vendors and customers"])


class PartyInput(BaseModel):
    party_type: Literal["VENDOR", "CUSTOMER"]
    name: str = Field(min_length=1, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    address: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name", "phone", "email", "address", "notes")
    @classmethod
    def clean(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Enter a nonblank value or leave the optional field empty.")
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values):
        if any(not value.strip() or len(value) > 300 for value in values):
            raise ValueError("Aliases must contain 1 to 300 characters.")
        return list(dict.fromkeys(value.strip() for value in values))


def _contacts(db):
    if get_settings().business_data_table_name:
        return store.list_parties()
    return [row.value for row in db.scalars(select(AppSetting).where(AppSetting.key.like("PARTY#%"))).all()]


def _invoices(db):
    if get_settings().business_data_table_name:
        return store.list_invoices()
    fields = ("id", "filename", "invoice_number", "invoice_date", "vendor_name", "receiver_name",
              "currency", "grand_total", "amount_paid", "payment_status", "payment_history", "status", "transaction_type")
    return [{field: getattr(row, field) for field in fields} for row in db.scalars(select(Invoice)).all()]


@router.get("")
def list_parties(db: Session = Depends(get_db)):
    invoices = _invoices(db)
    return {"parties": directory(_contacts(db), invoices),
            "unassigned_invoice_count": sum(i.get("status") == "APPROVED" and not counterparty(i) for i in invoices)}


def _save(payload: PartyInput, db, identifier=None):
    contacts = _contacts(db)
    previous = next((c for c in contacts if c["id"] == identifier), None) if identifier else None
    if identifier and not previous:
        raise HTTPException(404, "Contact not found.")
    if previous and previous["party_type"] != payload.party_type:
        raise HTTPException(422, "Keep the contact type unchanged; create a separate vendor/customer contact if needed.")
    data = payload.model_dump()
    if previous and name_key(previous["name"]) != name_key(payload.name):
        data["aliases"] = list(dict.fromkeys([*data["aliases"], previous["name"]]))
    identifiers = {name_key(n) for n in [data["name"], *data["aliases"]]}
    for contact in contacts:
        if contact["id"] != identifier and contact["party_type"] == data["party_type"]:
            if identifiers & {name_key(n) for n in [contact["name"], *contact.get("aliases", [])]}:
                raise HTTPException(409, "This name or alias already belongs to another contact of this type.")
    record = {**data, "id": identifier or party_id(payload.party_type, payload.name),
              "created_at": previous["created_at"] if previous else datetime.now(timezone.utc).isoformat(),
              "updated_at": datetime.now(timezone.utc).isoformat()}
    if get_settings().business_data_table_name:
        try:
            store.save_party(record, previous=previous)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    else:
        row = db.get(AppSetting, f"PARTY#{record['id']}")
        if row:
            row.value = record
        else:
            db.add(AppSetting(key=f"PARTY#{record['id']}", value=record))
        db.commit()
    return record


@router.post("", status_code=201)
def add_party(payload: PartyInput, db: Session = Depends(get_db)):
    return _save(payload, db)


@router.put("/{identifier}")
def edit_party(identifier: str, payload: PartyInput, db: Session = Depends(get_db)):
    return _save(payload, db, identifier)


@router.get("/{identifier}/ledger")
def party_history(identifier: str, db: Session = Depends(get_db)):
    invoices = _invoices(db)
    contact = next((c for c in directory(_contacts(db), invoices) if c["id"] == identifier), None)
    if not contact:
        raise HTTPException(404, "Contact not found.")
    return ledger(contact, invoices)
