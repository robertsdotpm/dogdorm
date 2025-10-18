from p2pd import UDP, TCP, V4, V6
from typing import Any, List, Optional
from pydantic import BaseModel

class ServiceData(BaseModel):
    service_type: int
    af: int
    proto: int
    ip: str
    port: int
    user: str | None
    password: str | None
    alias_id: int | None
    score: int

class InsertServicesReq(BaseModel):
    imports_list: List[List[ServiceData]]
    status_id: int

class WorkResultData(BaseModel):
    status_id: int
    is_success: int
    t: int

class WorkDoneReq(BaseModel):
    statuses: List[WorkResultData]

class AliasUpdateReq(BaseModel):
    alias_id: int
    ip: str
    current_time: int | None = None

class GetWorkReq(BaseModel):
    stack_type: int | None
    table_type: int | None
    current_time: int | None
    monitor_frequency: int | None

    