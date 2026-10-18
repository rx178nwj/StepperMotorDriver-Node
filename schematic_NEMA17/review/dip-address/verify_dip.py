import json
import xml.etree.ElementTree as ET
from pathlib import Path
base=Path(__file__).resolve().parent
def nets(path):
    return {n.attrib['name'].removeprefix('/'): {f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node')} for n in ET.parse(path).findall('./nets/net')}
before=nets(base/'before/schematic.xml')
after=nets(base/'after/schematic.xml')
expected={'ADDR0':{'IC4.5','R59.1','SW4.1'},'ADDR1':{'IC4.6','R60.1','SW4.2'},'ADDR2':{'IC4.2','R58.1','SW4.3'}}
failures=[]
for name,pins in expected.items():
    if after.get(name)!=pins: failures.append({'target_net':name,'actual':sorted(after.get(name,set()))})
for name,pins in {'+3V3':{'SW4.4','SW4.5','SW4.6'},'GND':{'R58.2','R59.2','R60.2'}}.items():
    if not pins<=after.get(name,set()): failures.append({'missing_members':name})
def relevant(nodes):
    return {p for p in nodes if p not in {'IC4.5','IC4.6'} and p.split('.')[0] not in {'JP1','SW4','R59','R60'}}
for name in set(before)|set(after):
    if relevant(before.get(name,set())) != relevant(after.get(name,set())):
        failures.append({'unintended_net_change':name})
comps={c.attrib['ref']:c for c in ET.parse(base/'after/schematic.xml').findall('./components/comp')}
if 'JP1' in comps: failures.append({'jumper_not_removed':True})
for ref in ['R58','R59','R60']:
    if comps[ref].findtext('value')!='10k': failures.append({'pulldown_value':ref})
table=[{'ADDR2':(i>>2)&1,'ADDR1':(i>>1)&1,'ADDR0':i&1,'node_id':i} for i in range(8)]
result={'target_address_memberships':9,'preserved_other_memberships':sum(len(relevant(v)) for v in before.values()),'dip_contact_pairs':[[1,6],[2,5],[3,4]],'settings':table,'failures':failures}
(base/'after/connection-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='settings'},indent=2))
assert not failures
