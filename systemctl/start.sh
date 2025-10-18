#!/bin/bash

#export P2PD_DEBUG=1

n="${MONITOR_WORKER_NO:-100}"

echo "Starting dealer server"
python3 -m dogdorm.dealer --log_path="/opt/dogdorm/dealer.log" --py_dogdorm &

sleep 5 # Wait for dealer server to start.

for i in $(seq 1 $n); do
    echo "Starting worker $i"
    python3 -m dogdorm.worker \
        --log_path="/opt/dogdorm/worker_$i.log" \
        --py_dogdorm \
        >> "/opt/dogdorm/worker_$i.log" 2>&1 &
done

wait

