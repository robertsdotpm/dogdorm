from typing_extensions import TypedDict
from typing import Any, List, Union, Optional
from pydantic import BaseModel, field_validator, model_validator
from p2pd import *
from fqdn import FQDN
from ..defs import *

class UniqueIndex:
    def __init__(self, key_fields):
        """
        key_fields: list of field names, can include 'fqn_or_ip' for special logic
        """
        self.key_fields = key_fields
        self._index = {}

    def _make_key(self, obj):
        """
        Generate a tuple key from an object.
        Supports special field 'fqn_or_ip' to use obj.fqn if truthy, else obj.ip
        """
        key = []
        for f in self.key_fields:
            if f == "alias_id_or_ip":
                key.append(getattr(obj, "alias_id", None) or getattr(obj, "ip", None))
            else:
                key.append(getattr(obj, f, None))
        return tuple(key)

    def add(self, obj):
        """
        Add object to index, enforcing uniqueness.
        Raises KeyError if duplicate exists.
        """
        key = self._make_key(obj)
        if key in self._index:
            raise KeyError(f"Duplicate entry {key}")
        self._index[key] = obj

    def get(self, obj):
        """
        Retrieve object by actual object.
        """
        return self._index.get(self._make_key(obj))

    def get_key(self, key_tuple):
        """
        Retrieve object by raw key tuple.
        """
        return self._index.get(key_tuple)

    def all(self):
        """
        Return all objects in the index.
        """
        return list(self._index.values())


def add_validator(field_name: str, cls: type, func):
    setattr(
        cls, 
        f"validate_{field_name}",
        field_validator(field_name, mode="before")(func)
    )
    cls.model_rebuild()

def validate_af(cls, v):
    if v not in VALID_AFS:
        raise ValueError("af must be in valid afs if not None")
    return v

def validate_table_type(cls, v):
    if v not in TABLE_TYPES:
        raise ValueError("table types must be in table types")
    return v

def validate_ip(cls, v):
    if v not in (None, "", "0"):
        ensure_ip_is_public(v)
    return v

def validate_time(cls, v):
    if v:
        if v < 1735689600 or v > 32503680000:
            raise ValueError("invalid time field for status.")
    return v

class AliasType(BaseModel):
    id: int
    af: int
    fqn: str
    ip: str | None
    """
    group_id: int | None
    status_id: int | None
    table_type: int
    """

    group_id: Optional[int] = None
    status_id: Optional[int] = None
    table_type: int = ALIASES_TABLE_TYPE

    @field_validator("fqn")
    @classmethod
    def validate_fqn(cls, v):
        if not FQDN(v):
            raise ValueError("invalid fqn value")
        return v

class RecordType(BaseModel):
    id: int
    table_type: int
    type: int
    af: int
    proto: Optional[int] = None
    ip: str | None
    port: int
    user: Optional[str] = None
    password: Optional[str] = None
    alias_id: Optional[int] = None
    status_id: Optional[int] = None
    group_id: Optional[int] = None
    score: int = 0

    @field_validator("type")
    @classmethod
    def validate_record_type(cls, v):
        if v not in SERVICE_TYPES:
            raise ValueError("invalid record type")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not valid_port(v):
            raise ValueError("invalid port")
        return v

    @field_validator("proto")
    @classmethod
    def validate_proto(cls, v):
        if v is not None and v not in (1, 2):
            raise ValueError("proto must be 1 or 2 if not None")
        return v
    
class StatusType(BaseModel):
    id: int
    row_id: int
    table_type: int
    status: int
    last_status: int
    test_no: int
    failed_tests: int
    last_success: int
    last_uptime: int
    uptime: int
    max_uptime: int

    @field_validator("status")
    @classmethod
    def validate_status_type(cls, v):
        if v not in STATUS_TYPES:
            raise ValueError("status types must be in status types")
        return v

    @model_validator(mode="after")
    def validate_sanity(self):
        if self.max_uptime < self.uptime:
            raise ValueError("max_uptime must be >= uptime")

        if self.test_no < self.failed_tests:
            raise ValueError("test_no must be >= failed_tests")
        
        return self

class MetaGroup(BaseModel):
    id: int
    table_type: int
    af: int
    group: list[Union[RecordType, AliasType]]

for field_name in ("last_status", "last_success", "last_uptime",):
    add_validator(field_name, StatusType, validate_time)

add_validator("af", AliasType, validate_af)
add_validator("af", RecordType, validate_af)
add_validator("af", MetaGroup, validate_af)
add_validator("table_type", AliasType, validate_table_type)
add_validator("table_type", RecordType, validate_table_type)
add_validator("table_type", StatusType, validate_table_type)
add_validator("table_type", MetaGroup, validate_table_type)
add_validator("ip", AliasType, validate_ip)
add_validator("ip", RecordType, validate_ip)

# Field types.
MEM_DB_TYPES = {
    SERVICES_TABLE_TYPE: RecordType,
    ALIASES_TABLE_TYPE: AliasType,
    IMPORTS_TABLE_TYPE: RecordType,
    STATUS_TABLE_TYPE: StatusType
}

MEM_DB_ENUMS = {
    SERVICES_TABLE_TYPE: "services",
    ALIASES_TABLE_TYPE: "aliases",
    IMPORTS_TABLE_TYPE: "imports",
    STATUS_TABLE_TYPE: "status",
    GROUPS_TABLE_TYPE: "groups",
}