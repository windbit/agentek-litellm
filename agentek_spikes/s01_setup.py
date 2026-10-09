import json, sys
import os
from lib import *
for name, acct in (("sub-a", "acctA"), ("sub-b", "acctB"), ("sub-c", "acctC")):
    print(name, add_subscription(name, acct))
key = new_key("spike-key", metadata={"labels": ["employee:u1", "space:7"]}, tags=None) if False else new_key("spike-key", metadata={"labels": ["employee:u1", "space:7"], "tags": ["team-x"]})
open(os.path.join(LOGS, "key.txt"), "w").write(key)
print("key ok")
