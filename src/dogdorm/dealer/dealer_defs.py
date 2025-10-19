"""
This file details the data types accepted by the main
service monitoring API.
"""
from p2pd import UDP, TCP, V4, V6
from typing import Any, List, Optional
from pydantic import BaseModel

# A service to monitor
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

# /insert --> insert a new service to monitor
class InsertServicesReq(BaseModel):
    imports_list: List[List[ServiceData]]
    status_id: int

# Particular status result
class WorkResultData(BaseModel):
    status_id: int
    is_success: int
    t: int

 #/complete --> update status
class WorkDoneReq(BaseModel):
    statuses: List[WorkResultData]

# /alias --> update IP of a DNS name
class AliasUpdateReq(BaseModel):
    alias_id: int
    ip: str
    current_time: int | None = None

# /work --> get work
class GetWorkReq(BaseModel):
    stack_type: int | None
    table_type: int | None
    current_time: int | None
    monitor_frequency: int | None

    