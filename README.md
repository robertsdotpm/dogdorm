# Dogdorm - custom server uptime monitor

I have a networking project called P2PD that uses public STUN, MQTT, NTP, and
TURN servers. Since public infrastructure tends to change a lot, its hard
to ensure reliability. Normally, I would manually maintain a list of servers
and periodically update hard coded IPs. But if I got lazy and didn't do this
for a while eventually most servers would end up down.

Dogdorm is a two-part system. A "dealer" server that hands out work and the
workers that do the work. The dealer consists of fastapi methods that change
an in-memory database to reflect server uptimes. Periodically, the database
is saved to disk using sqlite. I chose this approach to avoid locking issues
with many writes. 

**python3 -m dogdorm.dealer**

After the dealer server is started you can view the server list sorted by
most reliable server. Bellow is the URL for the P2PD dealer server as
an example of what the results look like.

http://ovh1.p2pd.net:8000/servers

# Install

((Software is not ready to be installed yet as it uses a few libraries with
code I haven't merged and there's last minute items to do yet.))

The software can be installed directly from this repo

**python3 -m pip install -e .**

# Workers

If you want to write modern networking code in Python its normally done using
asyncio. My experience with asyncio might be different from yours. But
in practice what I've seen is that when you try create many coroutines (that
do network tasks concurrently) it leads to many of the tasks timing out
instead of doing anything useful. This might be an error with how I'm using
asyncio, but my hypothesis is simply that a lot of code that is meant to
be non-blocking ends up subtly blocking the event loop anyway,

One might ask then, how would you scale up a Python program to handle lots
of work items? You could look at "threads" in Python. But you would quickly find
that they are not real OS threads and allow what coroutines do anyway.
Python does have processes, and "pools" of such processes. But asyncio
doesn't work nearly as well when you have to manually setup event loops in 
each process. Asyncio is complex enough that its only (somewhat)
consistent when a single event loop is run in a single process.

This is a long way to say that: I simply start 100 Python processes to do
all my networking work. The work doesn't do anything complex like try to
"concurrently" execute multiple work tasks at once. Instead, the work is done
sequently, and the result sent back to the dealer. The memory cost of every
worker is a lot... 4.5 GB for all processes (lol, maybe why Golang exists),
but the advantage is simple parallelism, vertical scaling, and the ability
to run coroutines that might be poorly optimized for asyncio.

**python3 -m dogdorm.worker**

# Adding new servers

The software will import servers to monitor from CSV files in server_lists.
There's already many servers included but more can be added here.

# Installation as a service

On Linux you can setup programs to run as a service using systemctl. This
should work well on Ubuntu and Debian. If you would like to use the software
in this way first install the Python package then run the **install.sh** script
inside the systemctl/ directory.

**sudo systemctl stop dogdorm**

**sudo systemctl start dogdorm**

**sudo systemctl status dogdorm**

The install script enables this monitor to auto start when you reboot your
server. By default the workers check for work every hour so this should
require minimal network traffic. 
