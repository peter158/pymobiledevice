#!/usr/bin/env python
# -*- coding: utf8 -*-
#
# $Id$
#
# Copyright (c) 2012-2014 "dark[-at-]gotohack.org"
#
# This file is part of pymobiledevice
#
# pymobiledevice is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#

from lockdown import LockdownClient
from util.cpio import CpioArchive
from util.optparser import MultipleOption
import zlib
import gzip
from pprint import pprint
from tempfile import mkstemp
from optparse import OptionParser
from io import BytesIO
import os

SRCFILES = """Baseband
CrashReporter
MobileAsset
VARFS
HFSMeta
Lockdown
MobileBackup
MobileDelete
MobileInstallation
MobileNotes
Network
UserDatabases
WiFi
WirelessAutomation
NANDDebugInfo
SystemConfiguration
Ubiquity
tmp
WirelessAutomation"""

class FileRelayClient(object):
    def __init__(self, lockdown=None, serviceName="com.apple.mobile.file_relay"):
        if lockdown:
            self.lockdown = lockdown
        else:
            self.lockdown = LockdownClient()

        self.service = self.lockdown.startService(serviceName)
        self.packet_num = 0

    def stop_session(self):
        print "Disconecting..."
        self.service.close()

    def request_sources(self, sources=["UserDatabases"]):  
        self.service.sendPlist({"Sources": sources})
        while 1:
            res = self.service.recvPlist()
            pprint(res)
            if res:
                s = res.get("Status")
                if s == "Acknowledged":
                    z = ""
                    while True:
                        x = self.service.recv()
                        if not x:
                            break
                        z += x
                    return z
                else:
                    print res.get("Error")
        return None
       
if __name__ == "__main__":

    parser = OptionParser(option_class=MultipleOption,usage="%prog")
    parser.add_option("-s", "--sources", 
                      action="extend", 
                      dest="sources", 
                      metavar='SOURCES',
                      help="comma separated list of file relay source to dump", 
                      type="string")
    parser.add_option("-e", "--extract",dest="extractpath" , default=False,
                  help="Extract archive to specified location", type="string")
    parser.add_option("-o", "--output", dest="outputfile", default=False,
                  help="Output location", type="string")

    (options, args) = parser.parse_args()

    sources = []
    if options.sources:
        sources = options.sources
    else:
        sources = SRCFILES.split("\n")
    print "Downloading: %s" % ''.join([str(item)+" " for item in sources])

    fc = FileRelayClient()
    data = fc.request_sources(sources) 
    
    if data:
        if options.outputfile:
            path = options.outputfile
        else:
            _,path = mkstemp(prefix="fileRelay_dump_",suffix=".gz",dir=".")
        
        open(path,'wb').write(data)
        print  "Data saved to:  %s " % path
        
    if options.extractpath:   
        with open(path, 'r') as f:
            gz = gzip.GzipFile(mode='rb', fileobj=f)
            cpio = CpioArchive(fileobj=BytesIO(gz.read()))
            cpio.extract_files(files=None,outpath=options.extractpath) 
