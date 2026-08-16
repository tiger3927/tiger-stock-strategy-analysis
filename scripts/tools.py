import datetime
import json
import os
import time

def load_json(filename: str):
    s = "{}"
    with open(filename, "r", encoding="utf8") as f:
        s = f.read()
        f.close()
    # print(s)
    return json.loads(s)

