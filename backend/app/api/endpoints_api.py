"""Outgoing connections of the referentials (and of the instance), grouped by host: administrators only."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..endpoints import inventory
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin/endpoints", tags=["outgoing connections"], dependencies=[Depends(require_admin)])


@router.get("", summary="Every URL contacted by the referentials, grouped by host, and the other outgoing connections")
def list_endpoints(service: Service = Depends(get_service), db: Session = Depends(get_db)):
    return inventory(service, db)


@router.get("/export", summary="Outgoing connections as CSV (one line per URL and referential), e.g. for a firewall allowlist")
def export_endpoints(hosts_only: bool = Query(False, description="One line per host"), service: Service = Depends(get_service),
                     db: Session = Depends(get_db)):
    inv = inventory(service, db)
    buf = io.StringIO()
    w = csv.writer(buf)
    if hosts_only:
        w.writerow(["host", "referentials", "urls", "calls_per_day", "authentication", "secrets", "kind"])
        for h in inv["hosts"]:
            w.writerow([h["host"], h["referential_count"], h["url_count"], h["calls_per_day"], " ".join(h["auth"]),
                        " ".join(h["secrets"]), "referentials"])
        for o in inv["others"]:
            w.writerow([o["host"], "", 1, "", "", " ".join(o.get("secrets") or []), o["kind"]])
    else:
        w.writerow(["host", "url", "referential", "role", "enabled", "schedule", "calls_per_day", "authentication", "secrets",
                    "last_contact", "status"])
        for h in inv["hosts"]:
            for u in h["urls"]:
                for r in u["referentials"]:
                    w.writerow([h["host"], u["url"], r["referential"], r["role"], r["enabled"], r["schedule"] or "",
                                r["calls_per_day"] if r["calls_per_day"] is not None else "", " ".join(r["auth"]),
                                " ".join(r["secrets"]), r["last_contact"] or "", r["health_status"] or ""])
        for o in inv["others"]:
            w.writerow([o["host"], o["url"], "", o["kind"], o["enabled"], "", "", "", " ".join(o.get("secrets") or []), "", ""])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="refexposer-{"hosts" if hosts_only else "urls"}.csv"'})
