import json
import itertools
from pathlib import Path
import xml.etree.ElementTree as ET
import pymupdf

base = Path(__file__).parent
def nets(path):
    return {n.attrib['name'].lstrip('/'): {(p.attrib['ref'], p.attrib['pin']) for p in n.findall('node')} for n in ET.parse(path).findall('./nets/net')}
before = nets(base / 'before/schematic.xml')
after = nets(base / 'after/schematic.xml')
expected = {
    '+3V3': ['IC4.1','IC4.24','IC4.36','IC4.48','C1.1','C2.1','C3.1','C38.1','C40.1','J8.1','U8.1','U8.14','C41.1'],
    'GND': ['IC4.23','IC4.35','IC4.47','IC4.49','C38.2','C40.2','J8.3','U8.7','U8.4','U8.5','U8.9','U8.10','U8.12','U8.13','C41.2','R58.2'],
    'GNDA': ['IC4.8','C4.2','C5.2','C39.2','R45.2','C34.2','NT2.2'],
    'VDDA': ['IC4.9','L1.1','C4.1','C5.1','C39.1'],
    'SWDIO': ['IC4.34','J8.2'], 'SWCLK': ['IC4.37','J8.4'], 'SWO': ['IC4.39','J8.6'],
    'NRST': ['IC4.7','C6.1','SW1.1','SW1.2','J8.5'],
    'PB8_BOOT': ['IC4.44','R8.1','SW5.3','SW5.4'],
    'DRV_AGC0': ['IC4.45','IC2.1'], 'ADDR2': ['IC4.2','R58.1'],
    'VM': ['R44.1'], 'VM_DIV': ['R44.2','R45.1','U8.2'],
    'VM_SENSE_ISO': ['U8.3','R46.1'], 'VM_SENSE': ['R46.2','C34.1','IC4.10'],
}
failures = []
for name, nodes in expected.items():
    missing = {tuple(n.split('.')) for n in nodes} - after.get(name,set())
    if missing: failures.append({'net':name,'missing':sorted(missing)})
preserved = 0
for name, nodes in before.items():
    nodes = {n for n in nodes if n[0] != 'R2'}
    if not nodes: continue
    if not any(nodes <= final for final in after.values()): failures.append({'old_net_split':name})
    preserved += len(nodes)
for a,b in itertools.combinations(before.values(),2):
    a = {n for n in a if n[0] != 'R2'}
    b = {n for n in b if n[0] != 'R2'}
    if a and b and any(a <= final and b <= final for final in after.values()): failures.append({'old_nets_merged':[sorted(a),sorted(b)]})
for a,b in [('GND','GNDA'),('GNDA','GNDPWR'),('VM_DIV','VM_SENSE'),('VM_SENSE_ISO','VM_SENSE'),('VM','+3V3')]:
    if after.get(a,set()) & after.get(b,set()): failures.append({'unexpected_short':[a,b]})
result = {'checked_nets':len(expected),'checked_connections':sum(map(len,expected.values())),'preserved_existing_nodes':preserved,'intentional_removal':'R2 and unused separate VREF+ branch','failures':failures}
(base/'after/connection-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
doc = pymupdf.open(base/'after/schematic.pdf')
for name,coords in [('mcu',(235,15,475,310)),('sense',(420,195,610,325))]:
    doc[0].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),clip=pymupdf.Rect(*coords)*(72/25.4)).save(base/f'after/{name}.png')
assert not failures
