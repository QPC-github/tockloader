import hashlib
import logging
import struct
import traceback

import Crypto
from Crypto.Signature import pkcs1_15
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA512

from .exceptions import TockLoaderException


def roundup(x, to):
    return x if x % to == 0 else x + to - x % to


class TBFTLV:
    def get_tlvid(self):
        return self.TLVID

    def get_size(self):
        return len(self.pack())


class TBFTLVUnknown(TBFTLV):
    def __init__(self, tipe, buffer):
        self.tipe = tipe
        self.buffer = buffer

    def get_tlvid(self):
        return self.tipe

    def pack(self):
        out = struct.pack("<HH", self.tipe, len(self.buffer))
        out += self.buffer

        # Need to ensure that whatever this header is that it is a multiple
        # of 4 in length.
        padding = (4 - (len(out) % 4)) % 4
        out += b"\0" * padding

        return out

    def __str__(self):
        out = "TLV: UNKNOWN ({})\n".format(self.tipe)
        out += "  buffer: {}\n".format(
            "".join(map(lambda x: "{:02x}".format(x), self.buffer))
        )
        return out


class TBFTLVMain(TBFTLV):
    TLVID = 0x01

    def __init__(self, buffer):
        self.valid = False

        if len(buffer) == 12:
            base = struct.unpack("<III", buffer)
            self.init_fn_offset = base[0]
            self.protected_size = base[1]
            self.minimum_ram_size = base[2]
            self.valid = True

    def pack(self):
        return struct.pack(
            "<HHIII",
            self.TLVID,
            12,
            self.init_fn_offset,
            self.protected_size,
            self.minimum_ram_size,
        )

    def __str__(self):
        out = "TLV: Main ({})\n".format(self.TLVID)
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "init_fn_offset", self.init_fn_offset, self.init_fn_offset
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "protected_size", self.protected_size, self.protected_size
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "minimum_ram_size", self.minimum_ram_size, self.minimum_ram_size
        )
        return out

    def object(self):
        return {
            "type": "main",
            "id": self.TLVID,
            "init_fn_offset": self.init_fn_offset,
            "protected_size": self.protected_size,
            "minimum_ram_size": self.minimum_ram_size,
        }


class TBFTLVProgram(TBFTLV):
    TLVID = 0x09

    def __init__(self, buffer):
        self.valid = False

        if len(buffer) == 20:
            base = struct.unpack("<IIIII", buffer)
            self.init_fn_offset = base[0]
            self.protected_size = base[1]
            self.minimum_ram_size = base[2]
            self.binary_end_offset = base[3]
            self.app_version = base[4]
            self.valid = True

    def pack(self):
        return struct.pack(
            "<HHIIIII",
            self.TLVID,
            20,
            self.init_fn_offset,
            self.protected_size,
            self.minimum_ram_size,
            self.binary_end_offset,
            self.app_version,
        )

    def __str__(self):
        out = "TLV: Program ({})\n".format(self.TLVID)
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "init_fn_offset", self.init_fn_offset, self.init_fn_offset
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "protected_size", self.protected_size, self.protected_size
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "minimum_ram_size", self.minimum_ram_size, self.minimum_ram_size
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "binary_end_offset", self.binary_end_offset, self.binary_end_offset
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "app_version", self.app_version, self.app_version
        )
        return out


class TBFTLVWriteableFlashRegions(TBFTLV):
    TLVID = 0x02

    def __init__(self, buffer):
        self.valid = False

        # Must be a multiple of 8 bytes
        if len(buffer) % 8 == 0:
            self.writeable_flash_regions = []
            for i in range(0, int(len(buffer) / 8)):
                base = struct.unpack("<II", buffer[i * 8 : (i + 1) * 8])
                # Add offset,length.
                self.writeable_flash_regions.append((base[0], base[1]))

    def pack(self):
        out = struct.pack("<HH", self.TLVID, len(self.writeable_flash_regions) * 8)
        for wfr in self.writeable_flash_regions:
            out += struct.pack("<II", wfr[0], wfr[1])
        return out

    def __str__(self):
        out = "TLV: Writeable Flash Regions ({})\n".format(
            self.HEADER_TYPE_WRITEABLE_FLASH_REGIONS
        )
        for i, wfr in enumerate(self.writeable_flash_regions):
            out += "  writeable flash region {}\n".format(i)
            out += "    {:<18}: {:>8} {:>#12x}\n".format("offset", wfr[0], wfr[0])
            out += "    {:<18}: {:>8} {:>#12x}\n".format("length", wfr[1], wfr[1])
        return out

    def object(self):
        out = {
            "type": "writeable_flash_regions",
            "id": self.HEADER_TYPE_WRITEABLE_FLASH_REGIONS,
            "wfrs": [],
        }

        for wfr in self.writeable_flash_regions:
            out["wfrs"].append({"offset": wfr[0], "length": wfr[1]})
        return out


class TBFTLVPackageName(TBFTLV):
    TLVID = 0x03

    def __init__(self, buffer):
        self.package_name = buffer.decode("utf-8")
        self.valid = True

    def pack(self):
        encoded_name = self.package_name.encode("utf-8")
        out = struct.pack("<HH", self.TLVID, len(encoded_name))
        out += encoded_name
        # May need to add padding.
        padding_length = roundup(len(encoded_name), 4) - len(encoded_name)
        if padding_length > 0:
            out += b"\0" * padding_length
        return out

    def __str__(self):
        out = "TLV: Package Name ({})\n".format(self.TLVID)
        out += "  {:<20}: {}\n".format("package_name", self.package_name)
        return out

    def object(self):
        return {
            "type": "name",
            "id": self.TLVID,
            "package_name": self.package_name,
        }


class TBFTLVPicOption1(TBFTLV):
    TLVID = 0x04

    def __init__(self, buffer):
        self.valid = False

        if len(buffer) == 40:
            base = struct.unpack("<IIIIIIIIII", buffer)
            self.text_offset = base[0]
            self.data_offset = base[1]
            self.data_size = base[2]
            self.bss_memory_offset = base[3]
            self.bss_size = base[4]
            self.relocation_data_offset = base[5]
            self.relocation_data_size = base[6]
            self.got_offset = base[7]
            self.got_size = base[8]
            self.minimum_stack_length = base[9]

            self.valid = True

    def pack(self):
        return struct.pack(
            "<HHIIIIIIIIII",
            self.TLVID,
            40,
            self.text_offset,
            self.data_offset,
            self.data_size,
            self.bss_memory_offset,
            self.bss_size,
            self.relocation_data_offset,
            self.relocation_data_size,
            self.got_offset,
            self.got_size,
            self.minimum_stack_length,
        )

    def __str__(self):
        out = "TLV: PIC Option 1 ({})\n".format(self.TLVID)
        out += "  {:<20}: {}\n".format("PIC", "C Style")
        return out

    def object(self):
        return {
            "type": "pic_option_1",
            "id": self.TLVID,
            "text_offset": self.text_offset,
            "data_offset": self.data_offset,
            "data_size": self.data_size,
            "bss_memory_offset": self.bss_memory_offset,
            "bss_size": self.bss_size,
            "relocation_data_offset": self.relocation_data_offset,
            "relocation_data_size": self.relocation_data_size,
            "got_offset": self.got_offset,
            "got_size": self.got_size,
            "minimum_stack_length": self.minimum_stack_length,
        }


class TBFTLVFixedAddress(TBFTLV):
    TLVID = 0x05

    def __init__(self, buffer):
        self.valid = False

        if len(buffer) == 8:
            base = struct.unpack("<II", buffer)
            self.fixed_address_ram = base[0]
            self.fixed_address_flash = base[1]
            self.valid = True

    def pack(self):
        return struct.pack(
            "<HHII", self.TLVID, 8, self.fixed_address_ram, self.fixed_address_flash
        )

    def __str__(self):
        out = "TLV: Fixed Addresses ({})\n".format(self.TLVID)
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "fixed_address_ram", self.fixed_address_ram, self.fixed_address_ram
        )
        out += "  {:<20}: {:>10} {:>#12x}\n".format(
            "fixed_address_flash", self.fixed_address_flash, self.fixed_address_flash
        )
        return out

    def object(self):
        return {
            "type": "fixed_addresses",
            "id": self.TLVID,
            "fixed_address_ram": self.fixed_address_ram,
            "fixed_address_flash": self.fixed_address_flash,
        }


class TBFTLVKernelVersion(TBFTLV):
    TLVID = 0x08

    def __init__(self, buffer):
        self.valid = False

        if len(buffer) == 4:
            base = struct.unpack("<HH", buffer)
            self.kernel_major = base[0]
            self.kernel_minor = base[1]
            self.valid = True

    def pack(self):
        return struct.pack("<HHHH", self.TLVID, 4, self.kernel_major, self.kernel_minor)

    def __str__(self):
        out = "TLV: Kernel Version ({})\n".format(self.TLVID)
        out += "  {:<20}: {}\n".format("kernel_major", self.kernel_major)
        out += "  {:<20}: {}\n".format("kernel_minor", self.kernel_minor)
        out += "  {:<20}: ^{}.{}\n".format(
            "kernel version", self.kernel_major, self.kernel_minor
        )
        return out

    def object(self):
        return {
            "type": "kernel_version",
            "id": self.TLVID,
            "kernel_major": self.kernel_major,
            "kernel_minor": self.kernel_minor,
        }


class TBFHeader:
    """
    Tock Binary Format header class. This can parse TBF encoded headers and
    return various properties of the application.
    """

    HEADER_TYPE_MAIN = 0x01
    HEADER_TYPE_WRITEABLE_FLASH_REGIONS = 0x02
    HEADER_TYPE_PACKAGE_NAME = 0x03
    HEADER_TYPE_PIC_OPTION_1 = 0x04
    HEADER_TYPE_FIXED_ADDRESSES = 0x05
    HEADER_TYPE_KERNEL_VERSION = 0x08
    HEADER_TYPE_PROGRAM = 0x09

    def __init__(self, buffer):
        # Flag that records if this TBF header is valid. This is calculated once
        # when a new TBF header is read in. Any manipulations that tockloader
        # does will not make a TBF header invalid, so we do not need to
        # re-calculate this.
        self.valid = False
        # Whether this TBF header is for an app, or is just padding (or perhaps
        # something else). Tockloader will not change this after the TBF header
        # is initially parsed, so we do not need to re-calculate this and can
        # used a flag here.
        self.app = False
        # Whether the TBF header has been modified from when it was first
        # created (by calling `__init__`). This might happen, for example, if a
        # new flag was set. We keep track of this so that we know if we need to
        # re-flash the TBF header to the board.
        self.modified = False

        # The base fields in the TBF header.
        self.fields = {}
        # A list of TLV entries.
        self.tlvs = []

        full_buffer = buffer

        # Need at least a version number
        if len(buffer) < 2:
            return

        # Get the version number
        self.version = struct.unpack("<H", buffer[0:2])[0]
        buffer = buffer[2:]

        if self.version == 1 and len(buffer) >= 74:
            checksum = self._checksum(full_buffer[0:72])
            buffer = buffer[2:]
            base = struct.unpack("<IIIIIIIIIIIIIIIIII", buffer[0:72])
            buffer = buffer[72:]
            self.fields["total_size"] = base[0]
            self.fields["entry_offset"] = base[1]
            self.fields["rel_data_offset"] = base[2]
            self.fields["rel_data_size"] = base[3]
            self.fields["text_offset"] = base[4]
            self.fields["text_size"] = base[5]
            self.fields["got_offset"] = base[6]
            self.fields["got_size"] = base[7]
            self.fields["data_offset"] = base[8]
            self.fields["data_size"] = base[9]
            self.fields["bss_mem_offset"] = base[10]
            self.fields["bss_mem_size"] = base[11]
            self.fields["min_stack_len"] = base[12]
            self.fields["min_app_heap_len"] = base[13]
            self.fields["min_kernel_heap_len"] = base[14]
            self.fields["package_name_offset"] = base[15]
            self.fields["package_name_size"] = base[16]
            self.fields["checksum"] = base[17]
            self.app = True

            if checksum == self.fields["checksum"]:
                self.valid = True

        elif self.version == 2 and len(buffer) >= 14:
            base = struct.unpack("<HIII", buffer[0:14])
            buffer = buffer[14:]
            self.fields["header_size"] = base[0]
            self.fields["total_size"] = base[1]
            self.fields["flags"] = base[2]
            self.fields["checksum"] = base[3]

            if (
                len(full_buffer) >= self.fields["header_size"]
                and self.fields["header_size"] >= 16
            ):
                # Zero out checksum for checksum calculation.
                nbuf = bytearray(self.fields["header_size"])
                nbuf[:] = full_buffer[0 : self.fields["header_size"]]
                struct.pack_into("<I", nbuf, 12, 0)
                checksum = self._checksum(nbuf)

                remaining = self.fields["header_size"] - 16

                # Now check to see if this is an app or padding.
                if remaining > 0 and len(buffer) >= remaining:
                    # This is an application. That means we need more parsing.
                    self.app = True

                    while remaining >= 4:
                        base = struct.unpack("<HH", buffer[0:4])
                        buffer = buffer[4:]
                        tipe = base[0]
                        length = base[1]

                        remaining -= 4

                        if tipe == self.HEADER_TYPE_MAIN:
                            if remaining >= 12 and length == 12:
                                self.tlvs.append(TBFTLVMain(buffer[0:12]))

                        elif tipe == self.HEADER_TYPE_PROGRAM:
                            if remaining >= 20 and length == 20:
                                self.tlvs.append(TBFTLVProgram(buffer[0:20]))

                        elif tipe == self.HEADER_TYPE_WRITEABLE_FLASH_REGIONS:
                            if remaining >= length:
                                self.tlvs.append(
                                    TBFTLVWriteableFlashRegions(buffer[0:length])
                                )

                        elif tipe == self.HEADER_TYPE_PACKAGE_NAME:
                            if remaining >= length:
                                self.tlvs.append(TBFTLVPackageName(buffer[0:length]))

                        elif tipe == self.HEADER_TYPE_PIC_OPTION_1:
                            if remaining >= 40 and length == 40:
                                self.tlvs.append(TBFTLVPicOption1(buffer[0:40]))

                        elif tipe == self.HEADER_TYPE_FIXED_ADDRESSES:
                            if remaining >= 8 and length == 8:
                                self.tlvs.append(TBFTLVFixedAddress(buffer[0:8]))

                        elif tipe == self.HEADER_TYPE_KERNEL_VERSION:
                            if remaining >= 4 and length == 4:
                                self.tlvs.append(TBFTLVKernelVersion(buffer[0:4]))

                        else:
                            logging.warning("Unknown TLV block in TBF header.")
                            logging.warning("You might want to update tockloader.")

                            # Add the unknown data to the stored state so we can
                            # put it back afterwards.
                            self.tlvs.append(TBFTLVUnknown(tipe, buffer[0:length]))

                        # All blocks are padded to four byte, so we may need to
                        # round up.
                        length = roundup(length, 4)
                        buffer = buffer[length:]
                        remaining -= length

                    if checksum == self.fields["checksum"]:
                        self.valid = True
                    else:
                        logging.error(
                            "Checksum mismatch. in packet: {:#x}, calculated: {:#x}".format(
                                self.fields["checksum"], checksum
                            )
                        )
                        self.valid = True

                else:
                    # This is just padding and not an app.
                    if checksum == self.fields["checksum"]:
                        self.valid = True

    def is_valid(self):
        """
        Whether the CRC and other checks passed for this header.
        """
        return self.valid

    def is_app(self):
        """
        Whether this is an app or padding.
        """
        return self.app

    def is_modified(self):
        """
        Whether the TBF header has been modified by Tockloader after it was
        initially read in (either from a new TAB or from the board).
        """
        return self.modified

    def is_enabled(self):
        """
        Whether the application is marked as enabled. Enabled apps start when
        the board boots, and disabled ones do not.
        """
        if not self.valid:
            return False
        elif self.version == 1:
            # Version 1 apps don't have this bit so they are just always enabled
            return True
        else:
            return self.fields["flags"] & 0x01 == 0x01

    def is_sticky(self):
        """
        Whether the app is marked sticky and won't be erase during normal app
        erases.
        """
        if not self.valid:
            return False
        elif self.version == 1:
            # No sticky bit in version 1, so they are not sticky
            return False
        else:
            return self.fields["flags"] & 0x02 == 0x02

    def set_flag(self, flag_name, flag_value):
        """
        Set a flag in the TBF header.

        Valid flag names: `enable`, `sticky`
        """
        if self.version == 1 or not self.valid:
            return

        if flag_name == "enable":
            if flag_value:
                self.fields["flags"] |= 0x01
            else:
                self.fields["flags"] &= ~0x01
            self.modified = True

        elif flag_name == "sticky":
            if flag_value:
                self.fields["flags"] |= 0x02
            else:
                self.fields["flags"] &= ~0x02
            self.modified = True

    def get_app_size(self):
        """
        Get the total size the app takes in bytes in the flash of the chip.
        """
        return self.fields["total_size"]

    def set_app_size(self, size):
        """
        Set the total size the app takes in bytes in the flash of the chip.

        Since this does not change the header size we do not need to update
        any other fields in the header.
        """
        self.fields["total_size"] = size
        self.modified = True

    def get_header_size(self):
        """
        Get the size of the header in bytes. This includes any alignment
        padding at the end of the header.
        """
        if self.version == 1:
            return 74
        else:
            return self.fields["header_size"]

    def get_size_before_app(self):
        """
        Get the number of bytes before the actual app binary in the .tbf file.
        """
        if self.version == 1:
            return 74
        else:
            header_size = self.fields["header_size"]

            binary_tlv = self._get_binary_tlv()
            protected_size = binary_tlv.protected_size

            return header_size + protected_size

    def get_app_name(self):
        """
        Return the package name if it was encoded in the header, otherwise
        return a tuple of (package_name_offset, package_name_size).
        """
        tlv = self._get_tlv(self.HEADER_TYPE_PACKAGE_NAME)
        if tlv:
            return tlv.package_name
        elif (
            "package_name_offset" in self.fields and "package_name_size" in self.fields
        ):
            return (
                self.fields["package_name_offset"],
                self.fields["package_name_size"],
            )
        else:
            return ""

    def get_app_version(self):
        """
        Return the version number of the application, if there is one.
        """
        tlv = self._get_tlv(self.HEADER_TYPE_PROGRAM)
        if tlv:
            return tlv.app_version
        else:
            return 0

    def has_fixed_addresses(self):
        """
        Return true if this TBF header includes the fixed addresses TLV.
        """
        return self._get_tlv(self.HEADER_TYPE_FIXED_ADDRESSES) != None

    def get_fixed_addresses(self):
        """
        Return (fixed_address_ram, fixed_address_flash) if there are fixed
        addresses, or None.
        """
        tlv = self._get_tlv(self.HEADER_TYPE_FIXED_ADDRESSES)
        if tlv:
            return (tlv.fixed_address_ram, tlv.fixed_address_flash)
        else:
            return None

    def has_kernel_version(self):
        """
        Return true if this TBF header includes the kernel version TLV.
        """
        return self._get_tlv(self.HEADER_TYPE_KERNEL_VERSION) != None

    def get_kernel_version(self):
        """
        Return (kernel_major, kernel_minor) if there is kernel version present,
        or None.
        """
        tlv = self._get_tlv(self.HEADER_TYPE_KERNEL_VERSION)
        if tlv:
            return (tlv.kernel_major, tlv.kernel_minor)
        else:
            return None

    def has_footer(self):
        """
        Return true if this TBF has a footer.
        """
        # For a TBF to have a footer, it must have a program header, and the
        # binary end offset must be less than the total length (leaving room for
        # a footer).
        tlv = self._get_tlv(self.HEADER_TYPE_PROGRAM)
        if tlv:
            footer_start = tlv.binary_end_offset
            if footer_start < self.fields["total_size"]:
                return True

        return False

    def get_binary_end_offset(self):
        """
        Return at what offset the application binary ends. Remaining space
        is taken up by footers.
        """
        tlv = self._get_tlv(self.HEADER_TYPE_PROGRAM)
        if tlv:
            return tlv.binary_end_offset
        else:
            return self.fields["total_size"]

    def get_footer_size(self):
        """
        Return the size in bytes of the footer. If no footer is included this
        will return 0.
        """
        footer_start = self.get_binary_end_offset()
        return self.fields["total_size"] - footer_start

    def delete_tlv(self, tlvid):
        """
        Delete a particular TLV by ID if it exists.
        """
        indices = []
        size = 0
        for i, tlv in enumerate(self.tlvs):
            if tlv.get_tlvid() == tlvid:
                # Reverse the list
                indices.insert(0, i)
                # Keep track of how much smaller we are making the header.
                size += tlv.get_size()
        # Pop starting with the last match
        for index in indices:
            logging.debug("Removing TLV at index {}".format(index))
            self.tlvs.pop(index)
            self.modified = True

        # Now update the base information since we have changed the length.
        self.fields["header_size"] -= size

        # Support both Main and Program.
        tlv_main = self._get_tlv(self.HEADER_TYPE_MAIN)
        tlv_program = self._get_tlv(self.HEADER_TYPE_PROGRAM)

        # Increase the protected size so that the actual application
        # binary hasn't moved.
        if tlv_main:
            tlv_main.protected_size += size
        if tlv_program:
            tlv_program.protected_size += size
        #####
        ##### NOTE! Based on how things are implemented in the Tock
        ##### universe, it seems we also need to increase the
        ##### `init_fn_offset`, which is calculated from the end of
        ##### the TBF header everywhere, and NOT the beginning of
        ##### the actual application binary (like the documentation
        ##### indicates it should be).
        #####
        if tlv_main:
            tlv_main.init_fn_offset += size
        if tlv_program:
            tlv_program.init_fn_offset += size

    def modify_tlv(self, tlvid, field, value):
        """
        Modify a TLV by setting a particular field in the TLV object to value.
        """
        # 0 is a special id for the root fields
        if tlvid == 0:
            self.fields[field] = value
        else:
            for tlv in self.tlvs:
                if tlv.get_tlvid() == tlvid:
                    try:
                        getattr(tlv, field)
                    except:
                        raise TockLoaderException(
                            'Field "{}" is not in TLV {}'.format(field, tlvid)
                        )
                    setattr(tlv, field, value)
                    self.modified = True

    def adjust_starting_address(self, address):
        """
        Alter this TBF header so the fixed address in flash will be correct
        if the entire TBF binary is loaded at address `address`.
        """
        # Check if we can even do anything. No fixed address means this is
        # meaningless.
        tlv_fixed_addr = self._get_tlv(self.HEADER_TYPE_FIXED_ADDRESSES)
        if tlv_fixed_addr:
            tlv_program = self._get_binary_tlv()
            # Now see if the header is already the right length.
            if (
                address + self.fields["header_size"] + tlv_program.protected_size
                != tlv_fixed_addr.fixed_address_flash
            ):
                # Make sure we need to make the header bigger
                if (
                    address + self.fields["header_size"] + tlv_program.protected_size
                    < tlv_fixed_addr.fixed_address_flash
                ):
                    # The header is too small, so we can fix it.
                    delta = tlv_fixed_addr.fixed_address_flash - (
                        address
                        + self.fields["header_size"]
                        + tlv_program.protected_size
                    )
                    # Increase the protected size to match this.
                    tlv_program.protected_size += delta

                    #####
                    ##### NOTE! Based on how things are implemented in the Tock
                    ##### universe, it seems we also need to increase the
                    ##### `init_fn_offset`, which is calculated from the end of
                    ##### the TBF header everywhere, and NOT the beginning of
                    ##### the actual application binary (like the documentation
                    ##### indicates it should be).
                    #####
                    tlv_program.init_fn_offset += delta

                else:
                    # The header actually needs to shrink, which we can't do.
                    # We should never hit this case.
                    raise TockLoaderException(
                        "Cannot shrink the header. This is a tockloader bug."
                    )

    # Return a buffer containing the header repacked as a binary buffer
    def get_binary(self):
        """
        Get the TBF header in a bytes array.
        """
        if self.version == 1:
            buf = struct.pack(
                "<IIIIIIIIIIIIIIIIIII",
                self.version,
                self.fields["total_size"],
                self.fields["entry_offset"],
                self.fields["rel_data_offset"],
                self.fields["rel_data_size"],
                self.fields["text_offset"],
                self.fields["text_size"],
                self.fields["got_offset"],
                self.fields["got_size"],
                self.fields["data_offset"],
                self.fields["data_size"],
                self.fields["bss_mem_offset"],
                self.fields["bss_mem_size"],
                self.fields["min_stack_len"],
                self.fields["min_app_heap_len"],
                self.fields["min_kernel_heap_len"],
                self.fields["package_name_offset"],
                self.fields["package_name_size"],
            )
            checksum = self._checksum(buf)
            buf += struct.pack("<I", checksum)

        elif self.version == 2:
            buf = struct.pack(
                "<HHIII",
                self.version,
                self.fields["header_size"],
                self.fields["total_size"],
                self.fields["flags"],
                0,
            )
            if self.app:
                for tlv in self.tlvs:
                    buf += tlv.pack()

            nbuf = bytearray(len(buf))
            nbuf[:] = buf
            buf = nbuf

            checksum = self._checksum(buf)
            struct.pack_into("<I", buf, 12, checksum)

            tlv_binary = self._get_binary_tlv()
            if tlv_binary and tlv_binary.protected_size > 0:
                # Add padding to this header binary to account for the
                # protected region between the header and the application
                # binary.
                buf += b"\0" * tlv_binary.protected_size

        return buf

    def _checksum(self, buffer):
        """
        Calculate the TBF header checksum.
        """
        # Add 0s to the end to make sure that we are multiple of 4.
        padding = len(buffer) % 4
        if padding != 0:
            padding = 4 - padding
            buffer += bytes([0] * padding)

        # Loop throw
        checksum = 0
        for i in range(0, len(buffer), 4):
            checksum ^= struct.unpack("<I", buffer[i : i + 4])[0]

        return checksum

    def _get_binary_tlv(self):
        """
        Get the TLV for the binary header, whether it's a program or main.
        """
        tlv = self._get_tlv(self.HEADER_TYPE_PROGRAM)
        if tlv == None:
            tlv = self._get_tlv(self.HEADER_TYPE_MAIN)
        return tlv

    def _get_tlv(self, tlvid):
        """
        Return the TLV from the self.tlvs array if it exists.
        """
        for tlv in self.tlvs:
            if tlv.get_tlvid() == tlvid:
                return tlv
        return None

    def __str__(self):
        out = ""

        if not self.valid:
            out += "INVALID!\n"

        out += "{:<22}: {}\n".format("version", self.version)

        # Special case version 1. However, at this point (May 2020), I would be
        # shocked if this ever gets run on a version 1 TBFH.
        if self.version == 1:
            for k, v in sorted(self.fields.items()):
                if k == "checksum":
                    out += "{:<22}:            {:>#12x}\n".format(k, v)
                else:
                    out += "{:<22}: {:>10} {:>#12x}\n".format(k, v, v)

                if k == "flags":
                    values = ["No", "Yes"]
                    out += "  {:<20}: {}\n".format("enabled", values[(v >> 0) & 0x01])
                    out += "  {:<20}: {}\n".format("sticky", values[(v >> 1) & 0x01])
            return out

        # Base fields that always exist.
        out += "{:<22}: {:>10} {:>#12x}\n".format(
            "header_size", self.fields["header_size"], self.fields["header_size"]
        )
        out += "{:<22}: {:>10} {:>#12x}\n".format(
            "total_size", self.fields["total_size"], self.fields["total_size"]
        )
        out += "{:<22}:            {:>#12x}\n".format(
            "checksum", self.fields["checksum"]
        )
        out += "{:<22}: {:>10} {:>#12x}\n".format(
            "flags", self.fields["flags"], self.fields["flags"]
        )
        out += "  {:<20}: {}\n".format(
            "enabled", ["No", "Yes"][(self.fields["flags"] >> 0) & 0x01]
        )
        out += "  {:<20}: {}\n".format(
            "sticky", ["No", "Yes"][(self.fields["flags"] >> 1) & 0x01]
        )

        for tlv in self.tlvs:
            out += str(tlv)

        return out

    def object(self):
        out = {"version": self.version}

        # Special case version 1. However, at this point (May 2020), I would be
        # shocked if this ever gets run on a version 1 TBFH.
        if self.version == 1:
            for k, v in sorted(self.fields.items()):
                out[k] = v
            return out

        out["header_size"] = self.fields["header_size"]
        out["total_size"] = self.fields["total_size"]
        out["checksum"] = self.fields["checksum"]
        out["flags"] = self.fields["flags"]

        out["tlvs"] = []
        for tlv in self.tlvs:
            out["tlvs"].append(tlv.object())

        return out


class TBFHeaderPadding(TBFHeader):
    """
    TBF Header that is only padding between apps. Since apps are packed as
    linked-list, this allows apps to be pushed to later addresses while
    preserving the linked-list structure.
    """

    def __init__(self, size):
        """
        Create the TBF header. All we need to know is how long the entire
        padding should be.
        """
        self.valid = True
        self.app = False
        self.modified = False
        self.fields = {}
        self.tlvs = []

        self.version = 2
        # self.fields['header_size'] = 14 # this causes interesting bugs...
        self.fields["header_size"] = 16
        self.fields["total_size"] = size
        self.fields["flags"] = 0
        self.fields["checksum"] = self._checksum(self.get_binary())


class TBFFooterTLVCredentials(TBFTLV):
    """
    Represent a Credentials TLV in the footer of a TBF.
    """

    TLVID = 0x80

    CREDENTIALS_TYPE_RESERVED = 0x00
    CREDENTIALS_TYPE_CLEARTEXTID = 0x01
    CREDENTIALS_TYPE_RSA3072KEY = 0x02
    CREDENTIALS_TYPE_RSA4096KEY = 0x03
    CREDENTIALS_TYPE_RSA3072KEYID = 0x04
    CREDENTIALS_TYPE_RSA4096KEYID = 0x05
    CREDENTIALS_TYPE_SHA256 = 0x06
    CREDENTIALS_TYPE_SHA384 = 0x07
    CREDENTIALS_TYPE_SHA512 = 0x08

    def __init__(self, buffer, integrity_blob):

        # Valid means the TLV parsed correctly.
        self.valid = False
        # Verified means tockloader was able to double check the credential and
        # verify it matches the app. Three options are:
        # - `unknown`: cannot verify either way
        # - `yes`: verified
        # - `no`: verification failed
        self.verified = "unknown"

        # This TLV requires the first field to be the credentials type. Extract
        # that, then verify the remainder of the buffer is as we expect.
        if len(buffer) >= 4:
            credentials_type = struct.unpack("<I", buffer[0:4])[0]

            # Check each credentials type.
            if credentials_type == self.CREDENTIALS_TYPE_RESERVED:
                self.credentials_type = self.CREDENTIALS_TYPE_RESERVED
                self.buffer = buffer[4:]
                # We accept any size of reserved area for future credentials.
                self.valid = True

            elif credentials_type == self.CREDENTIALS_TYPE_SHA256:
                self.credentials_type = self.CREDENTIALS_TYPE_SHA256
                self.buffer = buffer[4:]
                if len(self.buffer) == 64:
                    # SHA256 is 256 bits (64 bytes) long.
                    self.valid = True
                self.verify([], integrity_blob)

            elif credentials_type == self.CREDENTIALS_TYPE_SHA384:
                self.credentials_type = self.CREDENTIALS_TYPE_SHA384
                self.buffer = buffer[4:]
                if len(self.buffer) == 96:
                    # SHA384 is 384 bits (96 bytes) long.
                    self.valid = True
                self.verify([], integrity_blob)

            elif credentials_type == self.CREDENTIALS_TYPE_SHA512:
                self.credentials_type = self.CREDENTIALS_TYPE_SHA512
                self.buffer = buffer[4:]
                if len(self.buffer) == 128:
                    # SHA512 is 512 bits (128 bytes) long.
                    self.valid = True
                self.verify([], integrity_blob)

            elif credentials_type == self.CREDENTIALS_TYPE_RSA4096KEY:
                self.credentials_type = self.CREDENTIALS_TYPE_RSA4096KEY
                self.buffer = buffer[4:]

                if len(self.buffer) == 1024:
                    # RSA4904 public key is 512 bytes, signature is 512 bytes.
                    self.valid = True

            else:
                logging.warning("Unknown credential type in TBF footer TLV.")
                logging.warning("You might want to update tockloader.")

    def _credentials_type_to_str(self):
        names = [
            "Reserved",
            "ClearTextID",
            "RSA3072KEY",
            "RSA4096KEY",
            "RSA3072KEYID",
            "RSA4096KEYID",
            "SHA256",
            "SHA384",
            "SHA512",
        ]

        name = (
            names[self.credentials_type]
            if self.credentials_type < len(names)
            else "Unknown"
        )
        return name

    def verify(self, keys, integrity_blob):
        if integrity_blob == None:
            # If we don't have the actual binary then we can't verify any
            # credentials. This can happen if the app came from a board and we
            # didn't read the entire app binary.
            return

        if self.credentials_type == self.CREDENTIALS_TYPE_SHA256:
            hash = hashlib.sha256(integrity_blob).digest()
            if self.buffer == hash:
                self.verified = "yes"
            else:
                self.verified = "no"
                logging.warning("SHA256 hash in footer does not match binary.")

        elif self.credentials_type == self.CREDENTIALS_TYPE_SHA384:
            hash = hashlib.sha384(integrity_blob).digest()
            if self.buffer == hash:
                self.verified = "yes"
            else:
                self.verified = "no"
                logging.warning("SHA384 hash in footer does not match binary.")

        elif self.credentials_type == self.CREDENTIALS_TYPE_SHA512:
            hash = hashlib.sha512(integrity_blob).digest()
            if self.buffer == hash:
                self.verified = "yes"
            else:
                self.verified = "no"
                logging.warning("SHA512 hash in footer does not match binary.")

        elif self.credentials_type == self.CREDENTIALS_TYPE_RSA4096KEY:
            logging.debug("Verifying the RSA4096KEY credential.")

            # Unpack the credential buffer.
            pub_key_n_bytes = self.buffer[0:512]
            signature = self.buffer[512:1024]

            # Need an integer for n to compare to the public keys.
            pub_key_n = int.from_bytes(pub_key_n_bytes, "big")

            # First see if there is a key that matches. If no keys match then we
            # can't verify this credential one way or another.
            for key in keys:

                # Compare the n value in the credential to the n included in the
                # public key passed to tockloader.
                if pub_key_n == key.n:
                    # We found a key that matches. Get the hash of the main app
                    # and then check the signature.
                    hash = Crypto.Hash.SHA512.new(integrity_blob)

                    try:
                        Crypto.Signature.pkcs1_15.new(key).verify(hash, signature)
                        # Signature verified!
                        self.verified = "yes"
                    except:
                        # We were able to verify that the signature does not
                        # match.
                        self.verified = "no"

                    # Only try one matching key.
                    break

    def pack(self):
        buf = struct.pack(
            "<HHI",
            self.TLVID,
            4 + len(self.buffer),
            self.credentials_type,
        )

        return buf + self.buffer

    def __str__(self):
        verified = ""
        if self.verified == "yes":
            verified = " ✓ verified"
        elif self.verified == "no":
            verified = " ✗ NOT verified"

        out = "Footer TLV: Credentials ({})\n".format(self.TLVID)
        out += "  Type: {} ({}){}\n".format(
            self._credentials_type_to_str(), self.credentials_type, verified
        )
        out += "  Length: {}\n".format(len(self.buffer))
        # out += "  Data: "
        # out += " ".join("{:02x}".format(x) for x in self.buffer)
        # out += "\n\n"
        return out


class TBFFooter:
    """
    Represent an optional footer after the application binary in the TBF.
    """

    FOOTER_TYPE_CREDENTIALS = 0x80

    def __init__(self, tbfh, app_binary, buffer):
        # Use the same version as the header. Needed because a future version
        # of the header may define a different footer structure.
        self.version = tbfh.version
        # List of all TLVs in the footer.
        self.tlvs = []
        # Keep track if tockloader has modified the footer.
        self.modified = False

        # So we can check the credentials, create the binary blob covered by
        # integrity if it was provided to us. If the app came from a board then
        # we may not have the app binary to use.
        if app_binary != None:
            integrity_blob = tbfh.get_binary() + app_binary
        else:
            integrity_blob = None

        # Iterate all TLVs and add to list.
        position = 0
        while position < len(buffer):
            base = struct.unpack("<HH", buffer[0:4])
            buffer = buffer[4:]
            tlv_type = base[0]
            tlv_length = base[1]

            remaining = len(buffer)
            if tlv_type == self.FOOTER_TYPE_CREDENTIALS:
                if remaining >= tlv_length:
                    self.tlvs.append(
                        TBFFooterTLVCredentials(buffer[0:tlv_length], integrity_blob)
                    )

            buffer = buffer[tlv_length:]

    def delete_tlv(self, tlvid):
        """
        Delete a particular TLV by ID if it exists.
        """
        indices = []
        for i, tlv in enumerate(self.tlvs):
            if tlv.get_tlvid() == tlvid:
                # Reverse the list
                indices.insert(0, i)
        # Pop starting with the last match
        for index in indices:
            logging.debug("Removing TLV at index {}".format(index))
            self.tlvs.pop(index)
            self.modified = True

    def verify_credentials(self, public_keys, integrity_blob):
        """
        Check credential TLVs with an optional array of public keys (stored as
        binary arrays).
        """
        # Load all provided keys as Crypto objects.
        keys = []
        if public_keys:
            for public_key in public_keys:
                key = Crypto.PublicKey.RSA.importKey(public_key)
                keys.append(key)

        for tlv in self.tlvs:
            tlv.verify(keys, integrity_blob)

    def get_binary(self):
        """
        Get the TBF footer in a bytes array.
        """
        buf = bytearray()
        if self.version == 2:
            for tlv in self.tlvs:
                buf += tlv.pack()

        return buf

    def __str__(self):
        out = ""
        for tlv in self.tlvs:
            out += str(tlv)
        return out
