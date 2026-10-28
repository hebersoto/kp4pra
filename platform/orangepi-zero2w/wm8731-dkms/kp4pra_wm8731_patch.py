#!/usr/bin/env python3
"""
KP4PRA TNC - patch the upstream WM8731 codec driver for the DRA-Pi-Zero on
the Orange Pi Zero 2W (Armbian sunxi AHUB audio).

Usage: kp4pra_wm8731_patch.py <dir containing wm8731.c and wm8731.h>

Two opt-in changes, both enabled only by device-tree properties on the codec
node (see platform/orangepi-zero2w/kp4pra-dra-orangepi.dts):

1. kp4pra,xtal-12mhz
   The DRA's WM8731 runs from its own 12.000 MHz crystal. The sunxi machine
   driver calls set_sysclk(0 Hz), which would leave the codec unclocked. With
   the property the driver uses XTAL mode at 12 MHz (USB-mode sample rates)
   and ignores machine set_sysclk requests.

2. kp4pra,ahub-fmt0-reg = <addr>
   As clock master in USB mode the WM8731 sends fs BCLKs per frame (250 at
   48 kHz, 272 at 44.1 kHz). The sunxi AHUB receiver, even as clock slave,
   only latches frames when its LRCK period matches exactly, and the AHUB
   driver can only program slot multiples (125 is unreachable). The codec's
   hw_params - which ASoC runs after the machine link hw_params and before
   the CPU DAI hw_params, which does not touch the field - writes the right
   period into AHUB I2S0 FMT0[17:8]. Odd frame lengths (32k/96k) are skipped
   with a warning.

Idempotent: re-running on patched sources is a no-op. Any anchor that does
not match exactly aborts with an error, so a changed upstream driver fails
the DKMS build loudly instead of producing a half-patched module.
"""
import os
import sys


def fail(msg):
    sys.exit(f"kp4pra-wm8731 patch: ERROR: {msg} - upstream wm8731 changed; "
             "the KP4PRA patch needs updating for this kernel.")


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        fail(f"anchor '{what}' found {n} times (expected 1)")
    return text.replace(old, new, 1)


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    h = os.path.join(d, "wm8731.h")
    c = os.path.join(d, "wm8731.c")
    H = open(h).read()
    C = open(c).read()

    if "ahub_fmt0" in H and "kp4pra,ahub-fmt0-reg" in C and "kp4pra,xtal-12mhz" in C:
        print("kp4pra-wm8731 patch: already applied")
        return

    # ---- 1. fixed 12 MHz crystal ------------------------------------------
    H = replace_once(H, "\tbool deemph;\n",
        "\tbool deemph;\n"
        "\tbool fixed_xtal;\t/* KP4PRA: crystal fixed by DT, ignore set_sysclk */\n"
        "\tvoid __iomem *ahub_fmt0;\t/* KP4PRA: sunxi AHUB I2S FMT0 (slave LRCK period) */\n",
        "wm8731.h: bool deemph")

    C = replace_once(C, "#include <linux/clk.h>\n",
        "#include <linux/clk.h>\n#include <linux/property.h>\n#include <linux/io.h>\n",
        "wm8731.c: #include <linux/clk.h>")

    C = replace_once(C,
        "\tstruct wm8731_priv *wm8731 = snd_soc_component_get_drvdata(component);\n"
        "\n\tswitch (clk_id) {\n",
        "\tstruct wm8731_priv *wm8731 = snd_soc_component_get_drvdata(component);\n"
        "\n\t/* KP4PRA: a DT-declared 12 MHz crystal is the only clock source;\n"
        "\t * ignore machine-driver requests (the sunxi AHUB card passes 0 Hz). */\n"
        "\tif (wm8731->fixed_xtal)\n\t\treturn 0;\n"
        "\n\tswitch (clk_id) {\n",
        "wm8731_set_dai_sysclk: switch (clk_id)")

    C = replace_once(C, "\tmutex_init(&wm8731->lock);\n",
        "\tmutex_init(&wm8731->lock);\n"
        "\n\tif (device_property_read_bool(dev, \"kp4pra,xtal-12mhz\")) {\n"
        "\t\twm8731->fixed_xtal = true;\n"
        "\t\twm8731->sysclk_type = WM8731_SYSCLK_XTAL;\n"
        "\t\twm8731->sysclk = 12000000;\n"
        "\t\twm8731->constraints = &wm8731_constraints_12000000;\n"
        "\t\tdev_info(dev, \"KP4PRA: fixed 12 MHz crystal (XTAL mode)\\n\");\n"
        "\t}\n"
        # ---- 2. AHUB LRCK period fixup (probe part) ------------------------
        "\n\t{\n"
        "\t\tu32 reg;\n\n"
        "\t\tif (!device_property_read_u32(dev, \"kp4pra,ahub-fmt0-reg\", &reg)) {\n"
        "\t\t\twm8731->ahub_fmt0 = devm_ioremap(dev, reg, 4);\n"
        "\t\t\tif (wm8731->ahub_fmt0)\n"
        "\t\t\t\tdev_info(dev, \"KP4PRA: AHUB LRCK period fixup at 0x%08x\\n\", reg);\n"
        "\t\t\telse\n"
        "\t\t\t\tdev_warn(dev, \"KP4PRA: ioremap 0x%08x failed\\n\", reg);\n"
        "\t\t}\n"
        "\t}\n",
        "wm8731_init: mutex_init")

    # ---- 2. AHUB LRCK period fixup (hw_params part) -------------------------
    C = replace_once(C,
        "\twm8731_set_deemph(component);\n\n"
        "\tsnd_soc_component_write(component, WM8731_IFACE, iface);\n"
        "\treturn 0;\n}\n",
        "\twm8731_set_deemph(component);\n\n"
        "\tsnd_soc_component_write(component, WM8731_IFACE, iface);\n"
        "\n"
        "\t/* KP4PRA: as clock master the WM8731 sends coeff_div[i].fs BCLKs per\n"
        "\t * frame (BCLK = MCLK in USB mode: 250 at 48k, 272 at 44.1k). The sunxi\n"
        "\t * AHUB receiver needs its LRCK period to match exactly even as a clock\n"
        "\t * slave, and its driver can only program slot multiples (125 is not\n"
        "\t * reachable). The machine link hw_params has already run; the CPU DAI\n"
        "\t * hw_params runs after this and does not touch the period field. */\n"
        "\tif (wm8731->ahub_fmt0 && (iface & 0x0040)) {\n"
        "\t\tu16 fs = coeff_div[i].fs;\n\n"
        "\t\tif (fs & 1) {\n"
        "\t\t\tdev_warn(component->dev,\n"
        "\t\t\t\t \"KP4PRA: %u BCLK/frame is odd, AHUB period not set\\n\", fs);\n"
        "\t\t} else {\n"
        "\t\t\tu32 v = readl(wm8731->ahub_fmt0);\n\n"
        "\t\t\tv = (v & ~(0x3ffu << 8)) | ((u32)(fs / 2 - 1) << 8);\n"
        "\t\t\twritel(v, wm8731->ahub_fmt0);\n"
        "\t\t\tdev_dbg(component->dev, \"KP4PRA: AHUB LRCK period %u\\n\", fs / 2);\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn 0;\n}\n",
        "wm8731_hw_params: IFACE write + return")

    open(h, "w").write(H)
    open(c, "w").write(C)
    print("kp4pra-wm8731 patch: applied (12 MHz crystal + AHUB LRCK period)")


if __name__ == "__main__":
    main()
