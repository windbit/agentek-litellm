from lib import *
add_solo("solo", "sub-a")          # one deployment in the group: no cooldown on 429, retries go to the same deployment
add_solo("solo2", "sub-b")
print("ok")
