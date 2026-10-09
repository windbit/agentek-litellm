"""Render the hook events recorded under a MARK label in logs/spike_events.jsonl (usage: render_marks.py "<label prefix>")."""
import sys
from lib import brief, read_jsonl, EVENTS

prefix = sys.argv[1]
events = read_jsonl(EVENTS)
for idx, event in enumerate(events):
    if event.get("event") == "MARK" and event["label"].startswith(prefix):
        print(f"=== {event['label']}")
        for later in events[idx + 1:]:
            if later.get("event") == "MARK":
                break
            print("  " + brief(later))
