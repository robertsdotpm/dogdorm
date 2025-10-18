info = """
Dogdorm is a two-part system. There's a server called the dealer that hands out
work and then there are the workers who do the work. 

To start the dealer: python3 -m dogdorm.dealer
To start a worker: python3 -m dogdorm.worker

The software can also be installed as a service using systemctl (Linux-based.)
See systemctl/install.sh That script simply starts a dealer server and 100
individual worker processes.
"""

print(info)
