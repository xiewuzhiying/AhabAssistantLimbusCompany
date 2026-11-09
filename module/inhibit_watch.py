"""End an inhibitor when its requesting application dies."""
import os
import select
import sys

try:
    fd = os.pidfd_open(int(sys.argv[1]))
except ProcessLookupError:
    raise SystemExit(0)
try:
    select.select([fd], [], [])
finally:
    os.close(fd)
