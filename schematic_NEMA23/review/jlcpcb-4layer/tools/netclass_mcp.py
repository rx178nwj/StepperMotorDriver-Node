"""Extend the installed KiCad MCP with project-settings readback and schema repair."""
import json
import subprocess
import sys
import re
import shutil
from pathlib import Path
from mcp_server_kicad import pcb

@pcb.mcp.tool(annotations=pcb._ADDITIVE)
def export_netclass_settings_donor(pcb_path: str) -> dict:
    """Create an isolated settings-import PCB/project without changing the user's source."""
    source = Path(pcb_path)
    directory = source.parent / 'review/jlcpcb-4layer/settings-import'
    directory.mkdir(parents=True,exist_ok=True)
    donor = directory / 'routing-settings.kicad_pcb'
    shutil.copy2(source,donor)
    shutil.copy2(source.with_suffix('.kicad_pro'),donor.with_suffix('.kicad_pro'))
    configure_requested_netclasses(str(donor))
    assignment = assign_mcu_signal_classes(str(donor))
    verification = verify_netclasses_with_kicad(str(donor))
    if len(verification['classes'])!=8 or len(verification['nets'])!=62 or verification['failures']:
        raise RuntimeError('Donor verification failed')
    report = directory / 'verification.json'
    report.write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    return {'donor':str(donor),'project':str(donor.with_suffix('.kicad_pro')),
            'classes':8,'assigned_nets':62,'failures':verification['failures']}

@pcb.mcp.tool(annotations=pcb._DESTRUCTIVE)
def assign_mcu_signal_classes(pcb_path: str) -> dict:
    """Assign each IC4 ADC and digital signal net, keeping USB and power classes."""
    pads = pcb.get_footprint_pads(reference='IC4', pcb_path=pcb_path)
    connections = [(int(pin), net.strip()) for pin, net in re.findall(r'Pad (\d+):.*?net=([^\r\n]+)', pads)]
    if len(connections) != 49:
        raise ValueError('IC4 pad count changed; review pin classification')
    expected_adc = {10:'/VM_SENSE',16:'/TEMP',17:'/AD_IN'}
    for pin, net in expected_adc.items():
        if (pin, net) not in connections:
            raise ValueError('ADC pin/net mapping changed')
    analog = sorted(expected_adc.values())
    exclude = {'+3V3','GND','GNDA','/VDDA','/USB_PC_DP','/USB_PC_DM','/DAC_VREF'}
    digital = sorted({net for pin,net in connections if net not in exclude and pin not in expected_adc})
    if any(net.startswith('unconnected-') for net in digital):
        raise ValueError('Unconnected MCU pad needs review')
    project = Path(pcb_path).with_suffix('.kicad_pro')
    data = json.loads(project.read_text(encoding='utf-8'))
    settings = data['net_settings']
    classes = {c['name']:c for c in settings['classes']}
    template = classes['Default'].copy()
    for name,width in [('ANALOG',.2),('DIGITAL',.15)]:
        c = classes.get(name,template.copy())
        c.update(name=name,track_width=width,clearance=.15,via_diameter=.6,via_drill=.3)
        if name=='DIGITAL':
            c['priority'] = max([v.get('priority',0) for k,v in classes.items() if k!='Default']+[0])+1
        classes[name] = c
    settings['classes'] = list(classes.values())
    assignments = settings.get('netclass_assignments') or {}
    changed = set(analog+digital)
    patterns = [p for p in settings.get('netclass_patterns',[]) if p.get('pattern') not in changed]
    for name,nets in [('ANALOG',analog),('DIGITAL',digital)]:
        for net in nets:
            assignments[net] = [name]
            patterns.append({'pattern':net,'netclass':name})
    settings['netclass_assignments'] = assignments
    settings['netclass_patterns'] = patterns
    pcb._atomic_write(project,(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode('utf-8'))
    return {'project':str(project),'analog_inputs':analog,'digital_signals':digital,'mcu_connections':connections}

@pcb.mcp.tool(annotations=pcb._DESTRUCTIVE)
def configure_requested_netclasses(pcb_path: str) -> dict:
    """Set the user's seven routing classes and assignments in the paired project."""
    project = Path(pcb_path).with_suffix('.kicad_pro')
    data = json.loads(project.read_text(encoding='utf-8'))
    settings = data['net_settings']
    template = next(c for c in settings['classes'] if c['name'] == 'Default').copy()
    specs = [
        ('Default', .15, .6, .3, [], None),
        ('ANALOG', .2, .6, .3, ['/TEMP','/AD_IN','/DAC_VREF','/DRV_VREF','/VM_DIV','/VM_SENSE_ISO','/VM_SENSE'], None),
        ('USB', .25, .6, .3, ['/USB_PC_DP','/USB_PC_DM'], .25),
        ('PCA9615_DIFF', .15, .6, .3, ['/DI2C_SDA_P','/DI2C_SDA_M','/DI2C_SCL_P','/DI2C_SCL_M'], .15),
        ('POWER_1A', .5, .8, .4, ['+3V3','/+5V','/+5V_EXT','/USB_VBUS','/USB_VBUS_SRC','/BUCK_SW','/VDDA','/DRV_VCC','GND','GNDA'], None),
        ('VM_POWER', .5, 1., .5, ['/VM','GNDPWR'], None),
        ('MOTOR_PHASE', 2., 1., .5, ['/OUTA_P','/OUTA_N','/OUTB_P','/OUTB_N'], None),
    ]
    classes = {c['name']: c for c in settings['classes']}
    assignments = settings.get('netclass_assignments') or {}
    patterns = settings.get('netclass_patterns') or []
    assigned = {net for spec in specs for net in spec[4]}
    patterns = [p for p in patterns if p.get('pattern') not in assigned]
    for priority, (name, width, via, drill, nets, diff) in enumerate(specs):
        c = classes.get(name, template.copy())
        c.update(name=name, track_width=width, clearance=.15, via_diameter=via, via_drill=drill,
                 priority=2147483647 if name=='Default' else priority)
        if diff is not None:
            c.update(diff_pair_width=diff, diff_pair_gap=.15, diff_pair_via_gap=.15)
        classes[name] = c
        for net in nets:
            assignments[net] = [name]
            patterns.append({'netclass':name,'pattern':net})
    settings['classes'] = list(classes.values())
    settings['netclass_assignments'] = assignments
    settings['netclass_patterns'] = patterns
    ds = data['board']['design_settings']
    for width in [.15,.2,.25,.5,2.]:
        if width not in ds.setdefault('track_widths', [0.]):
            ds['track_widths'].append(width)
    ds['track_widths'].sort()
    for via, drill in [(.6,.3),(.8,.4),(1.,.5)]:
        dimension = {'diameter':via,'drill':drill}
        if dimension not in ds.setdefault('via_dimensions', []):
            ds['via_dimensions'].append(dimension)
    for width in [.15,.25]:
        dimension = {'width':width,'gap':.15,'via_gap':.15}
        if dimension not in ds.setdefault('diff_pair_dimensions', []):
            ds['diff_pair_dimensions'].append(dimension)
    backup = Path(pcb_path).parent / 'review/jlcpcb-4layer/project-before-netclass-repair.json'
    backup.parent.mkdir(parents=True,exist_ok=True)
    if not backup.exists():
        backup.write_text(project.read_text(encoding='utf-8'), encoding='utf-8')
    pcb._atomic_write(project, (json.dumps(data, indent=2, ensure_ascii=False)+'\n').encode('utf-8'))
    return inspect_netclass_settings(pcb_path)

@pcb.mcp.tool(annotations=pcb._READ_ONLY)
def verify_netclasses_with_kicad(pcb_path: str) -> dict:
    """Verify persisted project settings and effective net classes with KiCad 10."""
    interpreter = r'C:\Users\watan\.codex\vendor_imports\KiCAD-MCP-Server\.venv\Scripts\python.exe'
    result = subprocess.run([interpreter, str(Path(__file__).with_name('verify_netclasses.py')), pcb_path],
                            capture_output=True, text=True, encoding='utf-8', timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    return json.loads(result.stdout)

@pcb.mcp.tool(annotations=pcb._READ_ONLY)
def inspect_netclass_settings(pcb_path: str) -> dict:
    """Read the paired project's saved net classes, assignments, and presets."""
    project = Path(pcb_path).with_suffix('.kicad_pro')
    data = json.loads(project.read_text(encoding='utf-8'))
    return {'project': str(project), 'net_settings': data.get('net_settings'),
            'routing_presets': {k: data.get('board', {}).get('design_settings', {}).get(k)
                                for k in ['track_widths', 'via_dimensions', 'diff_pair_dimensions']}}

@pcb.mcp.tool(annotations=pcb._DESTRUCTIVE)
def repair_netclass_assignment_schema(pcb_path: str) -> dict:
    """Normalize saved netclass assignment values to KiCad 10 arrays, preserving classes."""
    project = Path(pcb_path).with_suffix('.kicad_pro')
    data = json.loads(project.read_text(encoding='utf-8'))
    settings = data['net_settings']
    assignments = settings.get('netclass_assignments') or {}
    for net, names in assignments.items():
        if isinstance(names, str):
            assignments[net] = [names]
    settings['netclass_assignments'] = assignments
    pcb._atomic_write(project, (json.dumps(data, indent=2, ensure_ascii=False)+'\n').encode('utf-8'))
    return inspect_netclass_settings(pcb_path)

if __name__ == '__main__':
    pcb.main()
