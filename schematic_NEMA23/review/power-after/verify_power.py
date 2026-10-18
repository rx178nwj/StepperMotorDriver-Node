import json
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent

def read(path):
    root = ET.parse(path).getroot()
    nets = {}
    for net in root.findall('./nets/net'):
        name = net.attrib['name'].lstrip('/')
        nets[name] = {(n.attrib['ref'], n.attrib['pin']) for n in net.findall('node')}
    return nets

before = read(HERE.parent / 'power-before' / 'schematic.xml')
after = read(HERE / 'schematic.xml')
expected = json.loads((HERE / 'expected-nets.json').read_text(encoding='utf-8'))
failures = []
for name, pins in expected.items():
    missing = set(map(tuple, pins)) - after.get(name, set())
    if missing:
        failures.append({'net': name, 'missing': sorted(missing)})

preserved = 0
for name, pins in before.items():
    if name.startswith('unconnected-'):
        continue
    current_name = 'VM' if name == '+12V' else name
    required = pins - ({('J6', '1')} if name == '+12V' else set())
    missing = required - after.get(current_name, set())
    if missing:
        failures.append({'existing_net': name, 'missing': sorted(missing)})
    preserved += len(required)

def net_of(ref, pin):
    return next(name for name, pins in after.items() if (ref, pin) in pins)

isolated = [net_of('J6', '1'), net_of('Q1', '2'), net_of('Q1', '3'),
            net_of('U6', '3'), net_of('U6', '6'), net_of('U6', '2'), net_of('U7', '5')]
if len(set(isolated)) != len(isolated):
    failures.append({'power_rails_merged': isolated})
grounds = [net_of('NT1', '1'), net_of('NT1', '2'), net_of('NT2', '2')]
if len(set(grounds)) != 3:
    failures.append({'grounds_merged': grounds})
if after.get('DRV_VCC') != before.get('DRV_VCC'):
    failures.append({'DRV_VCC': 'membership changed'})
for ref, pin in [('U5', '4'), ('U7', '4'), ('U8', '6'), ('U8', '8'), ('U8', '11')]:
    if not net_of(ref, pin).startswith('unconnected-'):
        failures.append({'unused_pin_connected': [ref, pin]})

report = {
    'expected_pin_memberships': sum(map(len, expected.values())),
    'preserved_existing_pin_memberships': preserved,
    'power_rails_distinct': isolated,
    'grounds_distinct': grounds,
    'DRV_VCC_unchanged': after.get('DRV_VCC') == before.get('DRV_VCC'),
    'failure_count': len(failures),
    'failures': failures,
}
(HERE / 'connection-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
raise SystemExit(bool(failures))
