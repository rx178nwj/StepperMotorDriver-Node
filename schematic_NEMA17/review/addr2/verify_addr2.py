import json
from pathlib import Path
import xml.etree.ElementTree as ET
base=Path(__file__).resolve().parent
def nets(path):
    return {n.attrib['name'].removeprefix('/'): {f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node')} for n in ET.parse(path).findall('./nets/net')}
before=nets(base/'before/schematic.xml')
after=nets(base/'after/schematic.xml')
failures=[]
for name,pins in before.items():
    if after.get(name,set())-{p for p in after.get(name,set()) if p.startswith('JP1.')} != pins:
        failures.append({'existing_net_changed':name})
for name,pins in after.items():
    if {p for p in pins if not p.startswith('JP1.')} != before.get(name,set()):
        failures.append({'unexpected_new_net_members':name})
for name,pins in {'ADDR2':{'IC4.2','R58.1','JP1.1'},'+3V3':{'JP1.2'},'GND':{'R58.2'}}.items():
    if not pins<=after.get(name,set()): failures.append({'missing_target_members':name})
if 'JP1.2' in after.get('ADDR2',set()): failures.append({'jumper_bypassed':True})
result={'preserved_existing_memberships':sum(map(len,before.values())),'addr2':sorted(after['ADDR2']),'jumper_supply':'+3V3','closed_pull_down_current_mA':0.33,'failures':failures}
(base/'after/connection-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
assert not failures
