import random
import re
from collections import Counter
from dataclasses import asdict, fields, is_dataclass
from collections import OrderedDict
import aiosqlite
import sqlite3
from ..defs import *
from ..worker.work_queue import *
from .mem_db_defs import *
from p2pd import *

"""
Dynamically exports a dataclass to an sqlite table.
Uses schema lookups to only insert the fields that overlap.
Used for export to sqlite.
"""
# The schema doesn't change while we run, and the export walks thousands of
# rows a minute -- asking sqlite once per row was most of the work.
_SCHEMA_CACHE = {}

async def table_columns(db, table):
    if table not in _SCHEMA_CACHE:
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            _SCHEMA_CACHE[table] = {row[1] async for row in cursor}

    return _SCHEMA_CACHE[table]

async def insert_object(db, table, obj):
    # Load the tables schema.
    columns = await table_columns(db, table)

    # Create key: value mappings for only the keys that match the schema.
    data = asdict(obj) if hasattr(obj, "__dataclass_fields__") else vars(obj)
    valid = {k: v for k, v in data.items() if k in columns}
    if not valid:
        return

    # Dynamically generate an insert statement (parametized for safety.)
    cols = ", ".join(valid.keys())
    placeholders = ", ".join("?" for _ in valid)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    await db.execute(sql, tuple(valid.values()))

"""
Dynamically loads a row from a table in sqlite based on
the fields that match the schema of the table in a
given dataclass definition.
"""
async def load_objects(db, table, cls, where_clause: str = None, where_args: tuple = ()):
    # Load the table's schema.
    db_cols = await table_columns(db, table)

    # Get fields within the cls describing the import data.
    if is_dataclass(cls):
        class_fields = [f.name for f in fields(cls)]
    elif hasattr(cls, "model_fields"):  # Pydantic v2
        class_fields = list(cls.model_fields.keys())
    elif hasattr(cls, "__fields__"):  # Pydantic v1
        class_fields = list(cls.__fields__.keys())
    else:
        raise TypeError(f"Cannot introspect fields of {cls}")

    # Filter returned data based on whether the column is a valid field name.
    select_cols = [c for c in class_fields if c in db_cols]
    if not select_cols:
        return []

    # Generate a select that only selects such fields.
    sql = f"SELECT {', '.join(select_cols)} FROM {table}"
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += " ORDER BY id ASC"

    # Execute the select query to pull chosen cols.
    # Fetchall returns tuples so col index is used to index cols in the tuple
    # and then build a dictionary of key-value pairs.
    async with db.execute(sql, where_args) as cursor:
        rows = await cursor.fetchall()
        col_index = {desc[0]: i for i, desc in enumerate(cursor.description)}

    # Build the key-value pairs based on the cols index positions.
    objs = []
    for row in rows:
        kwargs = {col: row[col_index[col]] for col in select_cols}
        objs.append(cls(**kwargs))

    return objs
"""
The SQLite DB has uniqueness constraints on service tuples:
(af, ip or fqn, port, type, proto) and
it will throw integrity errors if a duplicate exists.
That is fine and expected though.
Currently, the software exports every minute as a checkpoint.
"""
async def sqlite_export(mem_db, sqlite_db):
    # A row that will not save is a row lost at the next restart, so say how
    # many and give an example rather than skipping them without a word.
    failed = Counter()
    example = {}
    for table_type in mem_db.tables:
        for record_id in mem_db.tables[table_type]:
            entry = mem_db.tables[table_type][record_id]
            table_name = MEM_DB_ENUMS[table_type]
            try:
                await insert_object(sqlite_db, table_name, entry)
            except sqlite3.IntegrityError as e:
                failed[table_name] += 1
                example.setdefault(table_name, "id %s: %s" % (record_id, e))
            except:
                log_exception()

    if failed:
        log("checkpoint did not save %s -- %s" % (dict(failed), example))

"""
The checkpoint's services and imports tables carried UNIQUE constraints on
(type, af, [proto,] ip, port). The memory DB deliberately keys a server found
through a DNS name by that alias instead of its address, so the key survives
the address changing -- which means two names resolving to the same ip:port
are two records in memory but one collision on disk.

The export skipped the collision silently, so those records were never
written, vanished at every restart, and were re-created under reused ids
while their old status rows lingered: on the P2PD monitor, 131 services and
148 imports held only in memory, 3667 orphaned status rows, and 323 services
carrying more than one. Uniqueness is the memory DB's job, enforced when a
record is inserted; the checkpoint's job is to hold exactly what memory holds.
So the constraints come off, by rebuilding the table without them.
"""
_CHECKPOINT_UNIQUES = re.compile(
    r',\s*UNIQUE\("type","af",(?:"proto",)?"ip","port"\)'
    r'|UNIQUE\("type","af",(?:"proto",)?"ip","port"\)\s*,'
)

async def migrate_checkpoint(sqlite_db):
    for table in ("services", "imports"):
        async with sqlite_db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            continue

        old_sql = row[0]
        new_sql = _CHECKPOINT_UNIQUES.sub("", old_sql)
        if new_sql == old_sql:
            continue

        new_sql = new_sql.replace('"%s"' % table, '"%s_rebuild"' % table, 1)
        await sqlite_db.execute("BEGIN")
        try:
            await sqlite_db.execute('DROP TABLE IF EXISTS "%s_rebuild"' % table)
            await sqlite_db.execute(new_sql)
            await sqlite_db.execute('INSERT INTO "%s_rebuild" SELECT * FROM "%s"' % (table, table))
            await sqlite_db.execute('DROP TABLE "%s"' % table)
            await sqlite_db.execute('ALTER TABLE "%s_rebuild" RENAME TO "%s"' % (table, table))
        except Exception:
            await sqlite_db.rollback()
            raise
        else:
            await sqlite_db.commit()
            log("checkpoint: dropped the %s UNIQUE constraint that silently lost records" % table)

"""
The software manually manages IDs for objects. To keep things simple,
the next new ID to hand out is based on the max id of all objects seen + 1.
The functions bellow use + 1 on top of that in case the last insert was missed.
It's not perfect but avoiding auto increment and foreign key constraints
makes it easier to manage the mem DB for import / export.
Currently, the software imports at every restart of the service.
"""
async def sqlite_import(mem_db):
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        await migrate_checkpoint(sqlite_db)

        # 1. Load all StatusType rows in batch
        all_statuses = await load_objects(sqlite_db, "status", StatusType)
        for status in all_statuses:
            mem_db.add_id(STATUS_TABLE_TYPE, status.id + 1)
            mem_db.statuses[status.id] = status

        # Used to rebuild groups table.
        group_maps = OrderedDict({
            ALIASES_TABLE_TYPE: {},
            IMPORTS_TABLE_TYPE: {},
            SERVICES_TABLE_TYPE: {}
        })

        # 2. Load main tables.
        for table_type in group_maps:
            cls = MEM_DB_TYPES[table_type]
            table_name = MEM_DB_ENUMS[table_type]
            objs = await load_objects(sqlite_db, table_name, cls)
            for obj in objs:
                # Insert into main table dict
                mem_db.add_id(table_type, obj.id + 1)
                mem_db.tables[table_type][obj.id] = obj

                # Try increase max group_id.
                mem_db.add_id(GROUPS_TABLE_TYPE, obj.group_id + 1)
                if obj.group_id not in group_maps[table_type]:
                    group_maps[table_type][obj.group_id] = []
                group_maps[table_type][obj.group_id].append(obj)

                # Rebuild unique indexes
                mem_db.uniques[table_type].add(obj)
                if table_name == "aliases":
                    mem_db.records_by_aliases[obj.id] = []
                    mem_db.add_alias_by_ip(obj)
                else:
                    if obj.alias_id is not None:
                        mem_db.records_by_aliases[obj.alias_id].append(obj)

    # After loading all tables
    for status in mem_db.statuses.values():
        table_type = status.table_type
        row_id = status.row_id

        # Fetch the corresponding record
        record = mem_db.records[table_type].get(row_id)
        if record:
            record.status_id = status.id

    """
    A record takes the last status row that names it -- statuses load in id
    order, so that is the newest, the one its checks have been updating. Any
    other row naming it is left over from an earlier record that held the
    same id, and a row naming nothing is left over from a record that is
    gone. Neither is read by anything, but both used to be written back on
    every checkpoint forever, so drop them here.
    """
    linked = set()
    for table_type in (ALIASES_TABLE_TYPE, IMPORTS_TABLE_TYPE, SERVICES_TABLE_TYPE):
        for record in mem_db.records[table_type].values():
            if record.status_id is not None:
                linked.add(record.status_id)

    stale = [status_id for status_id in mem_db.statuses if status_id not in linked]
    for status_id in stale:
        del mem_db.statuses[status_id]

    if stale:
        log("checkpoint: dropped %d status rows no record uses" % len(stale))

    # Rebuild meta_group structure for services.
    for table_type in group_maps:
        """
        Work used to go back on the INIT queue no matter what it was doing
        when we checkpointed, and INIT is handed out with no time check at
        all -- so every restart re-probed every server at once, ignoring
        MONITOR_FREQUENCY, and imports that had been retired to DISABLED came
        back to be retried again.

        allocate_work also walks each queue oldest-first and stops at the
        first item too recent to re-run, so the queues have to be rebuilt in
        the order the work was last touched rather than by row id.
        """
        restored = []
        for group_id in group_maps[table_type]:
            group = group_maps[table_type][group_id]
            status = mem_db.statuses.get(group[0].status_id)

            # No status row means it has never been checked: let it run now.
            if status is None:
                restored.append((0, group_id, group, STATUS_INIT))
                continue

            queue = status.status

            # Whoever held dealt work did not survive the restart, so put it
            # back up for grabs rather than waiting out the worker timeout.
            if queue == STATUS_DEALT:
                queue = STATUS_AVAILABLE

            # A restart re-draws the offset, so a fleet that went quiet
            # together comes back spread out rather than all due at once.
            last = status.last_status or 0
            if queue == STATUS_AVAILABLE and last:
                last = int(last + random.uniform(-SCHEDULE_JITTER, SCHEDULE_JITTER) * MONITOR_FREQUENCY)

            restored.append((last, group_id, group, queue))

        restored.sort(key=lambda item: item[0])
        for last_touched, group_id, group, queue in restored:
            mem_db.add_work(
                group[0].af,
                table_type,
                group,
                group_id,
                queue,
                t=last_touched or None
            )
