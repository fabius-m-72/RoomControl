import re
# risposta tipica: %1POWR=0|1|2|3 oppure ERRA/ERRA
resp="%1POWR=3"
m = re.search(r"POWR=(\d)", resp)
ind=int(m.group(1)) if m else None
print(ind)
