"""
There's some neat computer science here.

If deque was used it would have:
    Log(n) deletes.
    Log(1) popleft.
    Log(1) pop.

which is decent, but the Log(n) isn't ideal and can still be improved.
If you use a doubly-linked list over a deque and have a hashtable
of Node pointers mapped by name / ID, you can then
have Log(1) deletes, too. So moving items between queues is all Log(1).

Would not work if the linked-list was indexed by positional offsets
over memory addresses as you would have to update each offset for a
delete. This is a very neat trick used by high performance schedulers.
"""

from typing import Hashable, Any
import time
from ..defs import *
from ..db.linked_list import *

class WorkQueue:
    def __init__(self):
        self.queues = {
            # Init tells the scheduler "hand out all items as work in this queue"
            STATUS_INIT: LinkedList(),

            # Available work is conditional on how recently it was handed out.
            STATUS_AVAILABLE: LinkedList(),

            # Dealt means it's been given out but if it hasn't been updated
            # after a threshold then it can be given to someone else.
            STATUS_DEALT: LinkedList(),

            # Completely disabled work items not to be handed out.
            STATUS_DISABLED: LinkedList()
        }

        # work_id -> (queue_name, node reference)
        self.index = {} 
        self.timestamps = {}

    # Work items are groups of one or more servers indexed by unique group_ids.
    # The group_ids are just increasingly counts on new group.
    def add_work(self, work_id: Hashable, payload: Any, queue_name: int, t=None):
        # Avoid overwriting pre-existing work.
        if work_id in self.index:
            raise KeyError(f"add_work: Work ID {work_id} already added.")
        
        # Recording the time at queue changes is used by the scheduler
        # when deciding if work items are too recent or expired. Restoring a
        # checkpoint passes the time the work was really last touched, so a
        # restart doesn't look like every server was just checked.
        ts = int(t) if t else int(time.time())
        node = self.insert_ordered(queue_name, (work_id, payload), ts)
        self.index[work_id] = (queue_name, node)
        self.timestamps[work_id] = ts

    """
    Place an item so each queue stays in timestamp order.

    The scheduler walks a queue oldest-first and stops at the first item that
    is not due, which is only right while timestamps never decrease along it.
    Plain appends kept that true when every timestamp was "now"; a jittered
    schedule puts some a little in the future, so the item goes where its
    time belongs. That is nearly always at or close to the end, so the walk
    starts there and is short.
    """
    def insert_ordered(self, queue_name, value, ts):
        lst = self.queues[queue_name]
        cur = lst.tail
        while cur is not None and self.timestamps.get(cur.value[0], 0) > ts:
            cur = cur.prev

        return lst.insert_after(cur, value)

    # Move group given by work_id to destination queue given by status enum.
    def move_work(self, work_id: Hashable, queue_name: int, t=None):
        # Work doesn't exist.
        if work_id not in self.index:
            raise KeyError(f"move_work: Work ID {work_id} doesnt exist.")

        # Remove from existing linked-list.
        from_queue, node = self.index[work_id]
        self.queues[from_queue].remove(node)

        # Into the target queue at its place in time order.
        ts = int(t) if t is not None else int(time.time())
        new_node = self.insert_ordered(queue_name, node.value, ts)
        self.index[work_id] = (queue_name, new_node)
        self.timestamps[work_id] = ts

    def remove_work(self, work_id: Hashable):
        queue_name, node = self.index.pop(work_id)
        self.queues[queue_name].remove(node)
        self.timestamps.pop(work_id, None)

    def pop_available(self):
        # popleft raises on an empty list, so ask first -- the old guard here
        # tested the returned Node, which is never falsy.
        if not self.queues[STATUS_AVAILABLE]:
            return None

        node = self.queues[STATUS_AVAILABLE].popleft()
        work_id, payload = node.value
        self.index.pop(work_id, None)
        self.timestamps.pop(work_id, None)
        return work_id, payload

"""

wq = WorkQueue()
wq.add_work("job1", {"task": 123}, STATUS_INIT)
wq.add_work("job2", {"task": 456}, STATUS_AVAILABLE)

# Move job2 to dealt
wq.move_work("job2", STATUS_DEALT)

# Pop from available
print(wq.pop_available())  # None (because job2 was moved)

# Iterate over dealt
for work_id, payload in wq.queues[STATUS_DEALT]:
    print(work_id, payload)

"""