import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent
tree = ET.parse(root / 'schematic.xml')
expected = json.loads((root / 'expected-connections.json').read_text(encoding='utf-8'))
nets = {}
pin_net = {}
for net in tree.findall('.//nets/net'):
    name = net.attrib['name'].removeprefix('/')
    members = {(node.attrib['ref'], node.attrib['pin']) for node in net.findall('node')}
    nets[name] = members
    for member in members:
        pin_net[member] = name
failures = []
for name, pins in expected.items():
    wanted = {(p['reference'], p['pin']) for p in pins}
    actual = nets.get(name, set())
    missing = wanted - actual
    extra = actual - wanted if name != 'GND' else set()
    if missing or extra:
        failures.append({'net': name, 'missing': sorted(missing), 'extra': sorted(extra)})
nc = {9, 12, 25, 28, 36, 37, 38}
for number in nc:
    members = nets.get(pin_net.get(('IC2', str(number)), ''), set())
    if members - {('IC2', str(number))}:
        failures.append({'nc_pin': number, 'members': sorted(members)})
for number in set(range(1, 50)) - nc:
    if ('IC2', str(number)) not in pin_net:
        failures.append({'unconnected_driver_pin': number})
if pin_net.get(('R29', '1')) == pin_net.get(('R29', '2')):
    failures.append({'vref_series_resistor_short': True})
if pin_net.get(('NT1', '1')) == pin_net.get(('NT1', '2')):
    failures.append({'net_tie_bypassed': True})
result = {'checked_nets': len(expected), 'checked_connected_pins': sum(map(len, expected.values())), 'driver_pins': 49, 'nc_pins': sorted(nc), 'failures': failures}
(root / 'connection-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
raise SystemExit(bool(failures))
