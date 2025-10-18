class Node:
    __slots__ = ("value", "prev", "next")
    
    def __init__(self, value):
        self.value = value
        self.prev = None
        self.next = None


class LinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.count = 0

    def prepend(self, value):
        """Add value to start of the linked list and return its node."""
        node = Node(value)
        if not self.head:
            self.head = self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node

        self.count += 1
        return node

    def append(self, value):
        """Append value to the end and return its node."""
        node = Node(value)
        if not self.head:
            self.head = self.tail = node
        else:
            self.tail.next = node
            node.prev = self.tail
            self.tail = node

        self.count += 1
        return node

    def remove(self, node):
        """Remove a node in O(1). Raises if node is invalid."""
        if not isinstance(node, Node):
            raise TypeError("remove expects a Node instance")
        if self.count == 0:
            raise ValueError("Cannot remove from empty list")

        # Update links
        if node.prev:
            node.prev.next = node.next
        else:
            if self.head is not node:
                raise ValueError("Node not in list")
            self.head = node.next

        if node.next:
            node.next.prev = node.prev
        else:
            if self.tail is not node:
                raise ValueError("Node not in list")
            self.tail = node.prev

        # Detach completely
        node.prev = node.next = None
        self.count -= 1

    def popleft(self):
        """Pop from the front. Raises IndexError if empty."""
        if not self.head:
            raise IndexError("popleft from empty list")
        node = self.head
        self.remove(node)
        return node

    def __iter__(self):
        cur = self.head
        while cur:
            nxt = cur.next
            yield cur.value
            cur = nxt

    def __bool__(self):
        return self.head is not None

    def __len__(self):
        return self.count
