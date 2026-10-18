import json
from pathlib import Path
import xml.etree.ElementTree as ET
base=Path(__file__).resolve().parent
def nets(path):
    return {n.attrib['name']:{(p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')} for n in ET.parse(path).findall('./nets/net')}
before=nets(base/'before/schematic.xml')
after=nets(base/'after/schematic.xml')
assert before==after, 'Net membership changed'
part=next(c for c in ET.parse(base/'after/schematic.xml').findall('./components/comp') if c.attrib['ref']=='SW4')
assert part.findtext('value')=='KSD32'
assert part.findtext('footprint')=='Button_Switch_THT:SW_DIP_SPSTx03_Slide_9.78x9.8mm_W7.62mm_P2.54mm'
result={'netlist_unchanged':True,'preserved_memberships':sum(map(len,before.values())),'value':part.findtext('value'),'footprint':part.findtext('footprint'),'failures':[]}
(base/'after/connection-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
