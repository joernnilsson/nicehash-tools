import sys
import re
import os

import nvidia_smi
import overclock

p_xorg = """
Section "ServerLayout"
    Identifier     "Layout0"
{gpu_layout}
EndSection

Section "ServerFlags"
    Option         "AllowEmptyInput" "on"
    Option         "Xinerama"        "off"
    Option         "SELinux"         "off"
EndSection

{gpu_sections}
"""

p_gpu_layout = """    Screen      {gpu_no}  "Screen{gpu_no}"     {location}    0
""" 

p_gpu = """
Section "Screen"
    Identifier     "Screen{gpu_no}"
    Device         "VideoCard{gpu_no}"
    Monitor        "Monitor{gpu_no}"
    DefaultDepth   24
    Option         "UseDisplayDevice" "DFP-{gpu_no}"
    Option         "ConnectedMonitor" "DFP-{gpu_no}"
    Option         "CustomEDID" "DFP-{gpu_no}:{edid_path}"
    Option         "Coolbits" "29"
    SubSection "Display"
                Depth   24
                Modes   "1024x768"
    EndSubSection
EndSection

Section "Device"
	Identifier  "Videocard{gpu_no}"
	Driver      "nvidia"
        Screen      {gpu_no}
        Option      "UseDisplayDevice" "DFP-{gpu_no}"
        Option      "ConnectedMonitor" "DFP-{gpu_no}"
        Option      "CustomEDID" "DFP-{gpu_no}:{edid_path}"
        Option      "Coolbits" "29"
        BusID       "PCI:{bus_address}"
EndSection

Section "Monitor"
    Identifier      "Monitor{gpu_no}"
    Vendorname      "Dummy Display"
    Modelname       "1024x768"
EndSection
"""

dir_path = os.path.dirname(os.path.realpath(__file__))
edid_path = dir_path + "/dfp-edid.bin"

devices = nvidia_smi.devices()

ss_gpu = ""
ss_gpu_layout = ""
for d in devices:
    dev = overclock.Device(d["id"])
    dev.refresh()
    m = re.match(r"([0-9]+):([0-9]+):([0-9]+).([0-9]+)", dev.get("gpu/pci/pci_bus_id"))
    bus_address = m.group(2) + ":" + m.group(3) + ":" + m.group(4)
    ss_gpu += p_gpu.format(gpu_no=d["id"], edid_path=edid_path, bus_address=bus_address)
    ss_gpu_layout += p_gpu_layout.format(gpu_no=d["id"], location=d["id"]*1024)

conf = p_xorg.format(gpu_layout=ss_gpu_layout, gpu_sections=ss_gpu)

print(conf)
