# In run_worker.py

import sys
from huey.bin.huey_consumer import consumer_main

# This script acts as the entry point for the Huey worker.
# We explicitly tell the consumer where to find the `huey` instance.

if __name__ == '__main__':
    # Add the path to the huey instance as the first argument
    # This is the fix for the "missing import path" error
    sys.argv.insert(1, 'tasks.huey')
    
    # Add the other configuration arguments
    sys.argv.extend(['--workers', '2', '--delay', '0.1', '--quiet'])
    

    consumer_main()
