import json
import xml.etree.ElementTree as ET
from pathlib import Path

base=Path(__file__).resolve().parent
def nets(path):
    return {n.attrib['name'].removeprefix('/'): {f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node')} for n in ET.parse(path).findall('./nets/net')}
before=nets(base/'before/schematic.xml')
after=nets(base/'after/schematic.xml')
expected={
    'SWITCH1':{'IC4.40','SW2.1','SW2.2','R4.2','C7.1'},
    'SWITCH2':{'IC4.41','SW3.1','SW3.2','R5.2','C8.1'},
    'TEMP':{'IC4.16','TH1.1','R7.2','C9.1'},
    'LED_FAULT':{'IC4.20','R34.1'},
}
failures=[]
for name,pins in expected.items():
    if after.get(name)!=pins: failures.append({'net':name,'missing':sorted(pins-after.get(name,set())),'extra':sorted(after.get(name,set())-pins)})
changed={'IC4.16','IC4.20','IC4.40','IC4.41'}
preserved=0
for name,pins in before.items():
    pins=pins-changed
    if not pins: continue
    if not pins<=after.get(name,set()): failures.append({'unexpected_change':name})
    preserved+=len(pins)
for name,pins in after.items():
    old=before.get(name,set())-changed
    if pins-changed != old: failures.append({'unintended_membership_delta':name})
result={'target_nets':4,'target_connections':16,'preserved_other_pin_memberships':preserved,'failures':failures}
(base/'after/connection-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
assert not failures
