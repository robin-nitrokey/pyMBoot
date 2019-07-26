#!/usr/bin/env python

# Copyright (c) 2019 Martin Olejar
#
# SPDX-License-Identifier: BSD-3-Clause
# The BSD-3-Clause license for this file can be found in the LICENSE file included with this distribution
# or at https://spdx.org/licenses/BSD-3-Clause.html#licenseText

import os
import sys
import click
import mboot
import bincopy
import traceback


########################################################################################################################
# Helper methods
########################################################################################################################
def hexdump(data, start_address=0, compress=True, length=16, sep='.'):
    """ Return string array in hex-dump format
    :param data:          {List} The data array of {Bytes}
    :param start_address: {Int}  Absolute Start Address
    :param compress:      {Bool} Compressed output (remove duplicated content, rows)
    :param length:        {Int}  Number of Bytes for row (max 16).
    :param sep:           {Char} For the text part, {sep} will be used for non ASCII char.
    """
    msg = []

    # The max line length is 16 bytes
    if length > 16:
        length = 16

    # Create header
    header = '  ADDRESS | '
    for i in range(0, length):
        header += "{:02X} ".format(i)
    header += '| '
    for i in range(0, length):
        header += "{:X}".format(i)
    msg.append(header)
    msg.append((' ' + '-' * (13 + 4 * length)))

    # Check address align
    offset = start_address % length
    address = start_address - offset
    align = True if (offset > 0) else False

    # Print flags
    prev_line = None
    print_mark = True

    # process data
    for i in range(0, len(data) + offset, length):
        hexa = ''
        if align:
            substr = data[0: length - offset]
        else:
            substr = data[i - offset: i + length - offset]
            if compress:
                # compress output string
                if substr == prev_line:
                    if print_mark:
                        print_mark = False
                        msg.append(' *')
                    continue
                else:
                    prev_line = substr
                    print_mark = True

        if align:
            hexa += '   ' * offset

        for h in range(0, len(substr)):
            h = substr[h]
            if not isinstance(h, int):
                h = ord(h)
            hexa += "{:02X} ".format(h)

        text = ''
        if align:
            text += ' ' * offset

        for c in substr:
            if not isinstance(c, int):
                c = ord(c)
            if 0x20 <= c < 0x7F:
                text += chr(c)
            else:
                text += sep

        msg.append((' {:08X} | {:<' + str(length * 3) + 's}| {:s}').format(address + i, hexa, text))
        align = False

    msg.append((' ' + '-' * (13 + 4 * length)))
    return '\n'.join(msg)


def size_fmt(num, kibibyte=True):
    base, suffix = [(1000., 'B'), (1024., 'iB')][kibibyte]
    for x in ['B'] + [x + suffix for x in list('kMGTP')]:
        if -base < num < base:
            break
        num /= base

    return "{} {}".format(num, x) if x == 'B' else "{:3.1f} {}".format(num, x)


class UInt(click.ParamType):
    """ Custom argument type for UINT """
    name = 'unsigned int'

    def __repr__(self):
        return 'UINT'

    def convert(self, value, param, ctx):
        try:
            if isinstance(value, int):
                return value
            else:
                return int(value, 0)
        except:
            self.fail('{} is not a valid value'.format(value), param, ctx)


class BDKey(click.ParamType):
    """ Custom argument type for BackDoor Key """
    name = 'backdoor key'

    def __repr__(self):
        return 'BDKEY'

    def convert(self, value, param, ctx):
        if value[0] == 'S':
            if len(value) < 18:
                self.fail('Short key, use 16 ASCII chars !', param, ctx)
            bdoor_key = [ord(k) for k in value[2:]]
        else:
            if len(value) < 34:
                self.fail('Short key, use 32 HEX chars !', param, ctx)
            value = value[2:]
            bdoor_key = []
            try:
                for i in range(0, len(value), 2):
                    bdoor_key.append(int(value[i:i+2], 16))
            except ValueError:
                self.fail('Unsupported HEX char in Key !', param, ctx)

        return bdoor_key


class ImagePath(click.ParamType):
    """ Custom argument type for Image File """
    name = 'image path'

    def __init__(self, mode):
        self.mode = mode

    def __repr__(self):
        return 'IPATH'

    def convert(self, value, param, ctx):
        if not value.lower().endswith(('.bin', '.hex', '.ihex',  '.s19', '.srec', '.sb')):
            self.fail('Unsupported file type: *.{} !'.format(value.split('.')[-1]), param, ctx)

        if self.mode == 'open' and not os.path.lexists(value):
            self.fail('File [{}] does not exist !'.format(value), param, ctx)

        return value


# Create instances of custom argument types
UINT    = UInt()
BDKEY   = BDKey()
INFILE  = ImagePath('open')
OUTFILE = ImagePath('save')


########################################################################################################################
# KBoot tool
########################################################################################################################

# Application error code
ERROR_CODE = 1

# Application version
VERSION = mboot.__version__

# Application description
DESCRIP = (
    "NXP MCU Bootloader Command Line Interface, version: " + VERSION + " \n\n"
    "NOTE: Development version, be carefully with it usage !\n"
)


# helper method
def scan_usb(device_name):
    # Scan for connected devices
    devs = mboot.scan_usb(device_name)

    if devs:
        index = 0

        if len(devs) > 1:
            click.echo('')
            for i, dev in enumerate(devs):
                click.secho("{}) {}".format(i, dev.info()))
            click.echo('\n Select: ', nl=False)
            c = input()
            click.echo()
            index = int(c, 10)

        click.secho(" DEVICE: {}\n".format(devs[index].info()))
        return devs[index]

    else:
        click.echo("\n - Target not detected !")
        sys.exit(ERROR_CODE)


# McuBoot: base options
@click.group(context_settings=dict(help_option_names=['-?', '--help']), help=DESCRIP)
@click.option('-t', '--target', type=click.STRING, default=None, help='Select target MKL27, LPC55, ... [optional]')
@click.option('-d', "--debug", type=click.IntRange(0, 2, True), default=0, help='Debug level: 0-off, 1-info, 2-debug')
@click.version_option(VERSION, '-v', '--version')
@click.pass_context
def cli(ctx, target, debug):

    if debug > 0:
        import logging
        log_level = [logging.NOTSET, logging.INFO, logging.DEBUG]
        logging.basicConfig(level=log_level[debug])

    ctx.obj['DEBUG'] = debug
    ctx.obj['TARGET'] = target

    click.echo()


# McuBoot: MCU info command
@cli.command(short_help="Get MCU info (mboot properties)")
@click.pass_context
def info(ctx):
    # Read MBoot MCU Info (Properties collection)

    nfo = []
    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect KBoot USB device
        mb.open_usb(hid_dev)
        # Get MCU info
        nfo = mb.get_mcu_info()
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect KBoot device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    # Print KBoot MCU Info
    for key, value in nfo.items():
        m = " {}:".format(key)
        if isinstance(value, list):
            m += "".join(["\n  - {}".format(s) for s in value])
        else:
            m += "\n  = {}".format(value)
        click.echo(m)


# McuBoot: print memories list command
@cli.command(short_help="Get list of available memories")
@click.pass_context
def memlist(ctx):

    mem_list = {}
    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect KBoot USB device
        mb.open_usb(hid_dev)
        # Get MCU memory list
        mem_list = mb.get_memory_list()
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect KBoot device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    message = ''
    for key, values in mem_list.items():
        message += "{}:\n".format(key)
        if key in ('internal_ram', 'internal_flash'):
            for i, item in enumerate(values):
                message += " {}) Start Address: 0x{:08X}, Size: {}".format(i, item['address'], size_fmt(item['size']))
                if 'sector_size' in item:
                    message += ", Sector Size: {}".format(size_fmt(item['sector_size']))
                message += '\n'
        else:
            for i, attr in enumerate(values):
                message += " {}) {}:\n".format(i, attr['mem_name'])
                if 'address' in attr:
                    message += "     Start Address: 0x{:08X}\n".format(attr['address'])
                if 'size' in attr:
                    message += "     Memory Size:   {} ({} B)\n".format(size_fmt(attr['size']), attr['size'])
                if 'page_size' in attr:
                    message += "     Page Size:     {}\n".format(attr['page_size'])
                if 'sector_size' in attr:
                    message += "     Sector Size:   {}\n".format(attr['sector_size'])
                if 'block_size' in attr:
                    message += "     Block Size:    {}\n".format(attr['block_size'])
        message += '\n'

    click.echo(message)


# McuBoot: configure external memory command
@cli.command(short_help="Configure external memory")
@click.option('-a', '--address', type=UINT, default=None, help='Start address for storing memory config. inside RAM')
@click.argument('file', nargs=1, type=INFILE)
@click.pass_context
def memconf(ctx, address, file):

    err_msg = ""
    memory_id = 0
    memory_data = bytearray()

    # load memory configuration fom file
    with open(file, 'r') as f:
        # TODO: add file parser
        pass

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect KBoot USB device
        mb.open_usb(hid_dev)

        if address is None:
            # get internal memory start address and size
            memory_address = mb.get_property(mboot.PropertyTag.RAM_START_ADDRESS)[0]
            memory_size = mb.get_property(mboot.PropertyTag.RAM_SIZE)[0]
            # calculate address
            address = memory_address + memory_size - len(memory_data)
            # add additional offset 1024 Bytes
            address -= 1024

        mb.write_memory(address, memory_data)
        mb.configure_memory(memory_id, address)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect KBoot device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()


# McuBoot: receive SB file command
@cli.command(short_help="Receive SB file")
@click.argument('file', nargs=1, type=INFILE)
@click.pass_context
def sbfile(ctx, file):

    err_msg = ""

    # Load SB file
    with open(file, 'rb') as f:
        sb_data = f.read()

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect KBoot USB device
        mb.open_usb(hid_dev)
        # Write SB file data
        mb.receive_sb_file(sb_data)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect KBoot device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()


# McuBoot: memory write command
@cli.command(short_help="Write data into MCU memory")
@click.option('-a', '--address', type=UINT, default=None, help='Start Address.')
@click.option('-o', '--offset', type=UINT, default=0, show_default=True, help='Offset of input data.')
@click.option('-i', '--memid', type=UINT, default=0, show_default=True, help='Memory ID')
@click.argument('file', nargs=1, type=INFILE)
@click.pass_context
def write(ctx, address, offset, file):

    err_msg = ""
    in_data = bincopy.BinFile()

    try:
        if file.lower().endswith(('.srec', '.s19')):
            in_data.add_srec_file(file)
            if address is None:
                address = in_data.minimum_address
        elif file.lower().endswith(('.hex', '.ihex')):
            in_data.add_ihex_file(file)
            if address is None:
                address = in_data.minimum_address
        else:
            in_data.add_binary_file(file)
            if address is None:
                address = 0

        data = in_data.as_binary()
    except Exception as e:
        raise Exception('Could not read from file: {} \n [{}]'.format(file, str(e)))

    if offset < len(data):
        data = data[offset:]

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    click.echo(' Writing into MCU memory, please wait !\n')

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        # Read Flash Sector Size of connected MCU
        flash_sector_size = mb.get_property(mboot.PropertyTag.FLASH_SECTOR_SIZE)[0]

        # Align Erase Start Address and Len to Flash Sector Size
        start_address = (address & ~(flash_sector_size - 1))
        length = (len(data) & ~(flash_sector_size - 1))
        if (len(data) % flash_sector_size) > 0:
            length += flash_sector_size

        # Erase specified region in MCU Flash memory
        mb.flash_erase_region(start_address, length)

        # Write data into MCU Flash memory
        mb.write_memory(address, data)
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' - ERROR: {}'.format(str(e))

    # Disconnect MBoot device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.echo(" Wrote Successfully.")


# McuBoot: memory read command
@cli.command(short_help="Read data from MCU memory")
@click.option('-c', '--compress', is_flag=True, show_default=True, help='Compress dump output.')
@click.option('-f', '--file', type=OUTFILE, help='Output file name with ext.: *.bin, *.hex, *.ihex, *.srec or *.s19')
@click.argument('address', type=UINT)
@click.argument('length',  type=UINT, required=False)
@click.pass_context
def read(ctx, address, length, compress, file):

    data = None
    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        if ctx.obj['DEBUG']: click.echo()
        if length is None:
            size = mb.get_property(mboot.PropertyTag.FLASH_SIZE)[0]
            if address > (size - 1):
                raise Exception("LENGTH argument is required for non FLASH access !")
            length = size - address
        click.echo(" Reading from MCU memory, please wait ! \n")
        # Call MBoot flash erase all function
        data = mb.read_memory(address, length)
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if file is None:
        if ctx.obj['DEBUG']: click.echo()
        click.echo(hexdump(data, address, compress))
    else:
        try:
            if file.lower().endswith(('.srec', '.s19')):
                srec = bincopy.BinFile()
                srec.add_binary(data, address)
                srec.header = 'mboot'
                with open(file, "w") as f:
                    f.write(srec.as_srec())
            elif file.lower().endswith(('.hex', '.ihex')):
                ihex = bincopy.BinFile()
                ihex.add_binary(data, address)
                with open(file, "w") as f:
                    f.write(ihex.as_ihex())
            else:
                with open(file, "wb") as f:
                    f.write(data)
        except Exception as e:
            raise Exception('Could not write to file: {} \n [{}]'.format(file, str(e)))

        click.echo("\n Successfully saved into: {}".format(file))


# McuBoot: memory erase command
@cli.command(short_help="Erase MCU memory")
@click.option('-m/', '--mass/', is_flag=True, default=False, help='Erase complete MCU memory.')
@click.option('-a', '--address', type=UINT, help='Start Address.')
@click.option('-l', '--length',  type=UINT, help='Count of bytes aligned to flash block size.')
@click.pass_context
def erase(ctx, address, length, mass):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        if mass:
            # Connect MBoot USB device
            mb.open_usb(hid_dev)
            # Get available commands
            commands = mb.get_property(mboot.PropertyTag.AVAILABLE_COMMANDS)[0]
            # Call MBoot flash erase all function
            if mboot.is_command_available(mboot.CommandTag.FLASH_ERASE_ALL_UNSECURE, commands):
                mb.flash_erase_all_unsecure()
            elif mboot.is_command_available(mboot.CommandTag.FLASH_ERASE_ALL, commands):
                mb.flash_erase_all()
            else:
                raise Exception('Not Supported Command')
        else:
            if address is None or length is None:
                raise Exception("Argument \"-a, --address\" and \"-l, --length\" must be defined !")
            # Connect MBoot USB device
            mb.open_usb(hid_dev)
            # Call MBoot flash erase region function
            mb.flash_erase_region(address, length)
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.secho(" Erased Successfully.")


# McuBoot: eFuse read/write command
@cli.command(short_help="Read/Write eFuse from MCU")
@click.option('-l', '--length', type=UINT, default=4, show_default=True, help='Bytes count')
@click.argument('index', type=UINT)
@click.argument('value',  type=UINT, required=False)
@click.pass_context
def efuse(ctx, length, index, value):

    read_value = 0
    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)

        if value is not None:
            mb.flash_program_once(index, value, length)

        read_value = mb.flash_read_once(index, length)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.echo(" eFuse[{}] = 0x{:X}".format(index, read_value))


# McuBoot: unlock command
@cli.command(short_help="Unlock MCU")
@click.option('-k', '--key', type=BDKEY, help='Use backdoor key as ASCI = S:123...8 or HEX = X:010203...08')
@click.pass_context
def unlock(ctx, key):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)

        if key is None:
            # Call MBoot flash erase all and unsecure function
            mb.flash_erase_all_unsecure()
        else:
            # Call MBoot flash security disable function
            mb.flash_security_disable(key)
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.echo(" Unlocked Successfully.")


# McuBoot: fill memory command
@cli.command(short_help="Fill MCU memory with specified pattern")
@click.option('-p', '--pattern', type=UINT, default=0xFFFFFFFF, help='Pattern format (default: 0xFFFFFFFF).')
@click.argument('address', type=UINT)
@click.argument('length',  type=UINT)
@click.pass_context
def fill(ctx, address, length, pattern):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        # Call MBoot fill memory function
        mb.fill_memory(address, length, pattern)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.secho(" Filled Successfully.")


# McuBoot: reliable update command
@cli.command(short_help="Copy backup app from address to main app region")
@click.argument('address', type=UINT)
@click.pass_context
def update(ctx, address):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        mb.reliable_update(address)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()


# McuBoot: call command
@cli.command(short_help="Call code at address with specified argument")
@click.argument('address', type=UINT)
@click.argument('argument', type=UINT)
@click.pass_context
def call(ctx, address, argument):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        mb.call(address, argument)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()


# McuBoot: execute command
@cli.command(short_help="Execute code at address with specified argument and stackpointer")
@click.argument('address', type=UINT)
@click.argument('argument', type=UINT)
@click.argument('stackpointer', type=UINT)
@click.pass_context
def execute(ctx, address, argument, stackpointer):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create MBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)
        mb.execute(address, argument, stackpointer)

    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()


# McuBoot: reset command
@cli.command(short_help="Reset MCU")
@click.pass_context
def reset(ctx):

    err_msg = ""

    # Scan USB
    hid_dev = scan_usb(ctx.obj['TARGET'])

    # Create KBoot instance
    mb = mboot.McuBoot()

    try:
        # Connect MBoot USB device
        mb.open_usb(hid_dev)

        # Call MBoot MCU reset function
        mb.reset()
    except Exception as e:
        err_msg = '\n' + traceback.format_exc() if ctx.obj['DEBUG'] else ' ERROR: {}'.format(str(e))

    # Disconnect MBoot Device
    mb.close()

    if err_msg:
        click.echo(err_msg)
        sys.exit(ERROR_CODE)

    if ctx.obj['DEBUG']:
        click.echo()

    click.secho(" Reset OK")


def main():
    cli(obj={})


if __name__ == '__main__':
    main()
