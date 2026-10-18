import json
import xml.etree.ElementTree as ET
from pathlib import Path

base = Path(__file__).resolve().parent
root = ET.parse(base / 'schematic.xml').getroot()
nets = {n.attrib['name'].removeprefix('/'): {f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node')} for n in root.findall('./nets/net')}
components = {c.attrib['ref']: c.findtext('value') for c in root.findall('./components/comp')}
expected = {
    '+3V3': 'IC4.1 IC4.24 IC4.36 IC4.48 U7.5 U2.1 J5.1 J8.1 U8.1 U8.16 C38.1 C35.1 C36.1',
    'GND': 'IC4.23 IC4.35 IC4.47 IC4.49 U5.4 U6.1 U7.2 U8.8 U20.2 U20.3 U3.2 NT1.2 NT2.1',
    'GNDA': 'IC4.8 NT2.2 R45.2 C34.2 C4.2 C5.2',
    'GNDPWR': 'J6.2 NT1.1 IC2.15 IC2.16 IC2.21 IC2.22 C15.2 C16.2 C17.2',
    'VM': 'J6.1 U5.2 U5.3 IC2.10 IC2.11 IC2.26 IC2.27 R44.1 R36.1',
    '+5V_EXT': 'U5.1 L2.2 U6.3 U6.5 R41.1 C27.1 C28.1',
    '+5V': 'U6.2 U6.7 U7.1 U7.3 C31.1',
    'BUCK_SW': 'U5.5 L2.1 C26.2', 'BUCK_BOOT': 'U5.6 C26.1',
    'POWER_PR1': 'U6.4 R41.2 R42.1', 'PWR_ST': 'U6.8 IC4.3 R43.2',
    'USB_VBUS_SRC': 'J2.A4_B9 J2.B4_A9 U20.1 C42.1',
    'USB_VBUS': 'U20.5 U6.6 U3.5 C43.1',
    'USB_PC_DP': 'IC4.33 J2.A6 J2.B6 U3.1 U3.6',
    'USB_PC_DM': 'IC4.32 J2.A7 J2.B7 U3.3 U3.4',
    'USB_CC1': 'J2.A5 R19.1', 'USB_CC2': 'J2.B5 R20.1',
    'VDDA': 'IC4.9 L1.1 C4.1 C5.1 C39.1',
    'NRST': 'IC4.7 J8.5 C6.1 SW1.1 SW1.2',
    'PB8_BOOT': 'IC4.44 R8.1 SW5.3 SW5.4',
    'SWDIO': 'IC4.34 J8.2', 'SWCLK': 'IC4.37 J8.4', 'SWO': 'IC4.39 J8.6',
    'VM_DIV': 'R44.2 R45.1 U8.2', 'VM_SENSE_ISO': 'U8.3 R46.1',
    'VM_SENSE': 'R46.2 C34.1 IC4.10',
    'I2C_PC_SCL': 'IC4.21 U2.4 R11.2', 'I2C_PC_SDA': 'IC4.22 U2.2 R10.2',
    'I2C_SENS_SCL': 'IC4.42 J5.3 R31.2', 'I2C_SENS_SDA': 'IC4.43 J5.4 R32.2',
    'DAC_VREF': 'IC4.14 R29.2', 'DRV_VREF': 'IC2.31 IC2.32 R29.1 R30.1 C21.1',
    'DRV_VCC': 'IC2.33 IC2.34 C19.1 R23.1 R50.1 R52.1 R54.1 R56.1',
    'DRV_OSCM': 'IC2.35 R23.2 C20.1', 'DRV_LTH': 'IC2.7 R24.1',
    'DRV_CLIM0': 'IC2.3 R50.2 R51.1', 'DRV_CLIM1': 'IC2.4 R52.2 R53.1',
    'DRV_BOOST': 'IC2.6 R54.2 R55.1', 'DRV_FLIM': 'IC2.5 R56.2 R57.1',
}
for name, mcu, driver in [('CLK',29,43),('DIR',30,42),('EN',31,44),('RESET',28,45),('DMODE0',25,39),('DMODE1',26,40),('DMODE2',27,41),('AGC0',45,1),('AGC1',46,2),('LO1',12,47),('LO2',13,48),('MO',4,46)]:
    expected['DRV_'+name] = f'IC4.{mcu} IC2.{driver}'
for name, pins, connector in [('OUTA_P',(13,14),1),('OUTA_N',(17,18),2),('OUTB_N',(19,20),3),('OUTB_P',(23,24),4)]:
    expected[name] = f'IC2.{pins[0]} IC2.{pins[1]} J4.{connector} J7.{connector}'
for name, pin, resistor, diode in [('RUN',15,33,1),('FAULT',16,34,2),('MCU',18,35,3),('COMM',19,37,5)]:
    expected['LED_'+name] = f'IC4.{pin} R{resistor}.1'
    expected[f'Net-(D{diode}-A)'] = f'R{resistor}.2 D{diode}.2'
checks = []
for name, pins in expected.items():
    missing = set(pins.split()) - nets.get(name, set())
    checks.append({'net':name,'pass':not missing,'missing':sorted(missing)})
pin_net = {p:n for n,ps in nets.items() for p in ps}
for ref,pins in [('IC2',[9,12,25,28,36,37,38]),('U8',[6,7,9,12,13]),('U7',[4])]:
    for pin in pins:
        node=f'{ref}.{pin}'
        checks.append({'isolated_pin':node,'pass':not (nets.get(pin_net.get(node,''),set())-{node})})
findings = {
    'communication_supply_isolated_from_local_5V': pin_net['U2.10'] != pin_net['U6.2'],
    'communication_supply_members': sorted(nets['+5V_com']),
    'signals_without_mcu': {n:sorted(nets[n]) for n in ['SWITCH1','SWITCH2','TEMP'] if not any(p.startswith('IC4.') for p in nets[n])},
    'missing_documented_input_protection': [r for r in ['F1','Q1','D6','D7','R38'] if r not in components],
    'driver_nc_isolated': all(not (nets.get(pin_net.get(f'IC2.{p}',''),set())-{f'IC2.{p}'}) for p in [9,12,25,28,36,37,38]),
}
result={'components':len(components),'nets':len(nets),'expected_nets':len(expected),'expected_memberships':sum(len(p.split()) for p in expected.values()),'checks':checks,'connectivity_failures':[c for c in checks if not c['pass']],'functional_findings':findings,'calculations':{'vref_max_nominal_V':3.3*9.1/19.1,'current_max_nominal_A':3.3*9.1/19.1*1.25,'vm_adc_at_24V':24/19,'vm_filter_tau_s':(180000*10000/190000+4700)*1e-7,'power_led_resistor_W_at_24V_Vf2V':22**2/3300,'power_mux_switch_nominal_V':130.1/30.1}}
(base/'audit-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False,indent=2))
assert not result['connectivity_failures']
