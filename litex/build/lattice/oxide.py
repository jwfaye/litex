#
# This file is part of LiteX.
#
# Copyright (c) 2018-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2019-2020 David Shah <dave@ds0.me>
# Copyright (c) 2018 William D. Jones <thor0505@comcast.net>
# SPDX-License-Identifier: BSD-2-Clause

import os
import subprocess
import sys
from shutil import which

from migen.fhdl.structure import _Fragment

from litex.build.generic_platform import *
from litex.build.generic_toolchain import GenericToolchain
from litex.build import tools
from litex.build.lattice import common
from litex.build.lattice.radiant import _format_constraint, _format_ldc, _build_pdc

import math


# LatticeOxideToolchain --------------------------------------------------------------------------

class LatticeOxideToolchain(GenericToolchain):
    attr_translate = {
        "keep": ("keep", "true"),
        "syn_useioff": ("syn_useioff", 1),
    }

    special_overrides = common.lattice_NX_special_overrides_for_oxide

    def __init__(self):
        super().__init__()
        self.yosys_template   = self._yosys_template
        self.build_template   = self._build_template

    def build(self, platform, fragment,
        nowidelut      = False,
        abc9           = False,
        timingstrict   = False,
        ignoreloops    = False,
        seed           = 1,
        es_device      = False,
        **kwargs):

        self._nowidelut    = nowidelut
        self._abc9         = abc9
        self._timingstrict = timingstrict
        self._ignoreloops  = ignoreloops
        self._seed         = seed
        self._es_device    = es_device

        return GenericToolchain.build(self, platform, fragment, **kwargs)

    # Constraints (.ldc) ---------------------------------------------------------------------------

    def build_io_constraints(self):
        _build_pdc(self.named_sc, self.named_pc, self.clocks, self._vns, self._build_name)
        return (self._build_name + ".pdc", "PDC")

    # Yosys/Nextpnr Helpers/Templates --------------------------------------------------------------

    _yosys_template = [
        "verilog_defaults -push",
        "verilog_defaults -add -defer",
        "{read_files}",
        "verilog_defaults -pop",
        "attrmap -tocase keep -imap keep=\"true\" keep=1 -imap keep=\"false\" keep=0 -remove keep=0",
        "synth_nexus -flatten {nwl} {abc} -json {build_name}.json -top {build_name}",
    ]

    def _yosys_import_sources(self):
        includes = ""
        reads = []
        for path in self.platform.verilog_include_paths:
            includes += " -I" + path
        for filename, language, library, *copy in self.platform.sources:
            # yosys has no such function read_systemverilog
            if language == "systemverilog":
                language = "verilog -sv"
            reads.append("read_{}{} {}".format(
                language, includes, filename))
        return "\n".join(reads)

    def build_project(self):
        ys = []
        for l in self.yosys_template:
            ys.append(l.format(
                build_name = self._build_name,
                nwl        = "-nowidelut" if self._nowidelut else "",
                abc        = "-abc9" if self._abc9 else "",
                read_files = self._yosys_import_sources()
            ))
        tools.write_to_file(self._build_name + ".ys", "\n".join(ys))

    # Script ---------------------------------------------------------------------------------------

    _build_template = [
        "yosys -l {build_name}.rpt {build_name}.ys",
        "nextpnr-nexus --json {build_name}.json --pdc {build_name}.pdc --fasm {build_name}.fasm \
    --device {device} {timefailarg} {ignoreloops} --seed {seed}",
        "prjoxide pack {build_name}.fasm {build_name}.bit"
    ]

    def build_script(self):
        if sys.platform in ("win32", "cygwin"):
            script_ext = ".bat"
            script_contents = "@echo off\nrem Autogenerated by LiteX / git: " + tools.get_litex_git_revision() + "\n\n"
            fail_stmt = " || exit /b"
        else:
            script_ext = ".sh"
            script_contents = "# Autogenerated by LiteX / git: " + tools.get_litex_git_revision() + "\nset -e\n"
            fail_stmt = ""

        for s in self.build_template:
            s_fail = s + "{fail_stmt}\n"  # Required so Windows scripts fail early.
            script_contents += s_fail.format(
                build_name      = self._build_name,
                device          = f"{self.platform.device}{'ES' if self._es_device else ''}",
                timefailarg     = "--timing-allow-fail" if not self._timingstrict else "",
                ignoreloops     = "--ignore-loops" if self._ignoreloops else "",
                fail_stmt       = fail_stmt,
                seed            = self._seed,
            )
        script_file = "build_" + self._build_name + script_ext
        tools.write_to_file(script_file, script_contents, force_unix=False)

        return script_file

    def run_script(self, script):
        if sys.platform in ("win32", "cygwin"):
            shell = ["cmd", "/c"]
        else:
            shell = ["bash"]

        if which("yosys") is None or which("nextpnr-nexus") is None:
            msg = "Unable to find Yosys/Nextpnr toolchain, please:\n"
            msg += "- Add Yosys/Nextpnr toolchain to your $PATH."
            raise OSError(msg)

        if subprocess.call(shell + [script]) != 0:
            raise OSError("Error occured during Yosys/Nextpnr's script execution.")


def oxide_args(parser):
    toolchain_group = parser.add_argument_group(title="Toolchain options")
    toolchain_group.add_argument("--yosys-nowidelut",      action="store_true", help="Use Yosys's nowidelut mode.")
    toolchain_group.add_argument("--yosys-abc9",           action="store_true", help="Use Yosys's abc9 mode.")
    toolchain_group.add_argument("--nextpnr-timingstrict", action="store_true", help="Use strict Timing mode (Build will fail when Timings are not met).")
    toolchain_group.add_argument("--nextpnr-ignoreloops",  action="store_true", help="Ignore combinatorial loops in Timing Analysis.")
    toolchain_group.add_argument("--nextpnr-seed",         default=1, type=int, help="Set Nextpnr's seed.")
    toolchain_group.add_argument("--nexus-es-device",      action="store_true", help="Use Nexus-ES1 part.")

def oxide_argdict(args):
    return {
        "nowidelut":    args.yosys_nowidelut,
        "abc9":         args.yosys_abc9,
        "timingstrict": args.nextpnr_timingstrict,
        "ignoreloops":  args.nextpnr_ignoreloops,
        "seed":         args.nextpnr_seed,
        "es_device":    args.nexus_es_device,
    }
