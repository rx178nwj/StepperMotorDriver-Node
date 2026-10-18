"""KiCad-side readback, invoked exclusively by the project MCP tool."""
import json
import sys
from pathlib import Path
import wx
import pcbnew

sys.path.insert(0, r'C:\Users\watan\.codex\vendor_imports\KiCAD-MCP-Server\python')
from utils.project_netclasses import load_project_net_classes, apply_net_classes_to_board

app = wx.App(False)
board = pcbnew.LoadBoard(sys.argv[1])
settings = load_project_net_classes(str(Path(sys.argv[1]).with_suffix('.kicad_pro')))
manager = pcbnew.SETTINGS_MANAGER()
project_path = str(Path(sys.argv[1]).with_suffix('.kicad_pro'))
if not manager.LoadProject(project_path):
    raise RuntimeError('KiCad native project load failed')
board.SetProject(manager.GetProject(project_path))
board.SynchronizeNetsAndNetClasses(False)
applied = {'native_project_loader':True}
result = {'kicad_version':pcbnew.Version(), 'applied':applied, 'classes':[], 'nets':[], 'failures':[]}
ns = board.GetDesignSettings().m_NetSettings
for expected in settings['classes']:
    name = expected['name']
    actual = ns.GetDefaultNetclass() if name=='Default' else ns.GetNetclasses()[name]
    record = {'name':name}
    for key, method in [('track_width','GetTrackWidth'),('clearance','GetClearance'),('via_diameter','GetViaDiameter'),('via_drill','GetViaDrill'),('diff_pair_width','GetDiffPairWidth'),('diff_pair_gap','GetDiffPairGap')]:
        value = getattr(actual, method)()/1e6
        record[key] = value
        if key in expected and abs(value-expected[key]) > 1e-8:
            result['failures'].append([name,key,value,expected[key]])
    result['classes'].append(record)
for name, names in settings['assignments'].items():
    net = board.FindNet(name)
    if not net:
        result['failures'].append(['missing_net',name])
        continue
    actual = net.GetNetClassName()
    result['nets'].append({'net':name,'class':actual})
    if actual not in names:
        result['failures'].append(['assignment',name,actual,names])
print(json.dumps(result))
