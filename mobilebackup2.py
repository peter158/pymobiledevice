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
from mobilebackup import MobileBackupClient
from optparse import OptionParser
from pprint import pprint
from util import write_file, hexdump
from biplist import writePlist, readPlist, Data
import os
import hashlib
from struct import unpack, pack
from time import mktime, gmtime, sleep, time
import datetime

CODE_SUCCESS = 0x00
CODE_ERROR_LOCAL =  0x06
CODE_ERROR_REMOTE = 0x0b
CODE_FILE_DATA = 0x0c

ERROR_ENOENT = -6
ERROR_EEXIST = -7

MBDB_SIGNATURE = 'mbdb\x05\x00'
MASK_SYMBOLIC_LINK = 0xa000
MASK_REGULAR_FILE = 0x8000
MASK_DIRECTORY = 0x4000

class MobileBackup2Client(MobileBackupClient):
    def __init__(self, lockdown = None,backupPath = None):
        if lockdown:
            self.lockdown = lockdown
        else:
            self.lockdown = LockdownClient()
        
        self.udid = lockdown.getValue("", "UniqueDeviceID")        
        self.willEncrypt = lockdown.getValue("com.apple.mobile.backup", "WillEncrypt") 
        
        self.service = self.lockdown.startService("com.apple.mobilebackup2")
        if not self.service:
            raise Exception("MobileBackup2Client init error : Could not start com.apple.mobilebackup2")
        
        if backupPath:
            self.backupPath = backupPath
        else:
            self.backupPath = "backups"
        if not os.path.isdir(self.backupPath):
            os.makedirs(self.backupPath,0o0755)  
        
        print "Starting new com.apple.mobilebackup2 service with working dir: %s" %  self.backupPath
        
        self.password = ""
        DLMessageVersionExchange = self.service.recvPlist()
        version_major = DLMessageVersionExchange[1]
        self.service.sendPlist(["DLMessageVersionExchange", "DLVersionsOk", version_major])
        DLMessageDeviceReady = self.service.recvPlist()
        if DLMessageDeviceReady and DLMessageDeviceReady[0] == "DLMessageDeviceReady":
            self.version_exchange()
        else:
            raise Exception("MobileBackup2Client init error %s" % DLMessageDeviceReady)

    def __del__(self):
        if self.service:
            self.service.sendPlist(["DLMessageDisconnect", "___EmptyParameterString___"])
         
    def internal_mobilebackup2_send_message(self, name, data):
        data["MessageName"] = name
        self.device_link_service_send_process_message(data)
    
    def internal_mobilebackup2_receive_message(self, name=None):
        res = self.device_link_service_receive_process_message()
        if res:
            if name and res["MessageName"] != name:
                print "MessageName does not match %s %s" % (name, str(res))
            return res

    def version_exchange(self):
        self.internal_mobilebackup2_send_message("Hello", {"SupportedProtocolVersions": [2.0,2.1]})
        return self.internal_mobilebackup2_receive_message("Response")
    
    def mobilebackup2_send_request(self, request, target, source, options={}):
        d = {"TargetIdentifier": target,
             "SourceIdentifier": source,
             "Options": options}
        self.internal_mobilebackup2_send_message(request, d)        
    
    def mobilebackup2_receive_message(self):
        return self.service.recvPlist()
    
    def mobilebackup2_send_status_response(self, status_code, status1="___EmptyParameterString___", status2={}):
        a = ["DLMessageStatusResponse", status_code, status1, status2]
        self.service.sendPlist(a)

    def mb2_handle_free_disk_space(self,msg):
        s = os.statvfs(self.backupPath)
        freeSpace = s.f_bsize * s.f_bavail
        a = ["DLMessageStatusResponse", 0, freeSpace]
        self.service.sendPlist(a)



    def mb2_multi_status_add_file_error(self, errplist, path, error_code, error_message):
        errplist[path] = {"DLFileErrorCode": error_code, "DLFileErrorString": error_message}
    
    def mb2_handle_copy_item(self, msg):
        src = self.check_filename(msg[1])
        dst = self.check_filename(msg[2])
        if os.path.isfile(src):
            data = self.read_file(src)
            self.write_file(dst, data)
        else:
            os.makedirs(dst)
        self.mobilebackup2_send_status_response(0)
        
    def mb2_handle_send_file(self, filename, errplist):
        self.service.send_raw(filename)
        if not filename.startswith(self.udid):
            filename = self.udid + "/" + filename

        data = self.read_file(self.check_filename(filename))
        if data != None:
            print "Sending %s to device" % filename
            self.service.send_raw(chr(CODE_FILE_DATA) + data)
            self.service.send_raw(chr(CODE_SUCCESS))
        else:
            print "File %s requested from device not found" % filename
            self.service.send_raw(chr(CODE_ERROR_LOCAL))
            self.mb2_multi_status_add_file_error(errplist, filename, ERROR_ENOENT, "Could not find the droid you were looking for ;)")
        
    def mb2_handle_send_files(self, msg):
        errplist = {}
        for f in msg[1]:
            self.mb2_handle_send_file(f, errplist)
        self.service.send("\x00\x00\x00\x00")
        if len(errplist):
            self.mobilebackup2_send_status_response(-13, "Multi status", errplist)
        else:
            self.mobilebackup2_send_status_response(0)

    def mb2_handle_list_directory(self, msg):
        path = msg[1]
        dirlist = {}
        self.mobilebackup2_send_status_response(0, status2=dirlist);

    def mb2_handle_make_directory(self, msg):
        dirname = self.check_filename(msg[1])
        print "Creating directory %s" % dirname
        if not os.path.isdir(dirname):
            os.makedirs(dirname)
        self.mobilebackup2_send_status_response(0, "")
   
    def mb2_handle_receive_files(self, msg):
        done = 0
        while not done:
            device_filename = self.service.recv_raw()
            if device_filename == "":
                break
            backup_filename = self.service.recv_raw()
            filedata = ""
            while True:
                stuff = self.service.recv_raw()
                if ord(stuff[0]) == CODE_FILE_DATA:
                    filedata += stuff[1:]
                elif ord(stuff[0]) == CODE_SUCCESS:
                    self.write_file(self.check_filename(backup_filename), filedata)
                    break
                else:
                    print "Unknown code", ord(stuff[0])
                    break
        self.mobilebackup2_send_status_response(0)

    def mb2_handle_move_files(self, msg):
        for k,v in msg[1].items():
            print "Renaming %s to %s"  % (self.check_filename(k),self.check_filename(v))
            os.rename(self.check_filename(k),self.check_filename(v))
        self.mobilebackup2_send_status_response(0)

    def mb2_handle_remove_files(self, msg):
        for filename in msg[1]:
            print "Removing ", self.check_filename(filename)
            try:
                filename = self.check_filename(filename)
                if os.path.isfile(filename):
                     os.unlink(filename)
            except Exception, e:
                print e
        self.mobilebackup2_send_status_response(0)
        
    def work_loop(self):
        while True:
            msg = self.mobilebackup2_receive_message()
            if not msg:
                break

            assert(msg[0] in ["DLMessageDownloadFiles",
                    "DLContentsOfDirectory",
                    "DLMessageCreateDirectory",
                    "DLMessageUploadFiles",
                    "DLMessageMoveFiles","DLMessageMoveItems",
                    "DLMessageRemoveFiles", "DLMessageRemoveItems",
                    "DLMessageCopyItem",
                    "DLMessageProcessMessage",
                    "DLMessageGetFreeDiskSpace",
                    "DLMessageDisconnect"])
                                
            if msg[0] == "DLMessageDownloadFiles":
                self.mb2_handle_send_files(msg)
            elif msg[0] == "DLContentsOfDirectory":
                self.mb2_handle_list_directory(msg)
            elif msg[0] == "DLMessageCreateDirectory":
                self.mb2_handle_make_directory(msg)
            elif msg[0] == "DLMessageUploadFiles":
                self.mb2_handle_receive_files(msg)
            elif msg[0] in ["DLMessageMoveFiles","DLMessageMoveItems"]:
                self.mb2_handle_move_files(msg)
            elif msg[0] in ["DLMessageRemoveFiles", "DLMessageRemoveItems"]:
                self.mb2_handle_remove_files(msg)
            elif msg[0] == "DLMessageCopyItem":
                self.mb2_handle_copy_item(msg)
            elif msg[0] == "DLMessageProcessMessage":
                errcode = msg[1].get("ErrorCode")
                if errcode == 0:
                    m =  msg[1].get("MessageName")
                    if m != "Response":
                        print m 
                if errcode == 1:
                    print msg[1].get("ErrorDescription")
                    print "Please unlock your device and retry..."
                break
            elif msg[0] == "DLMessageGetFreeDiskSpace":
                self.mb2_handle_free_disk_space(msg)
            elif msg[0] == "DLMessageDisconnect":
                break
  
    def create_status_plist(self,fullBackup=True):
        #Creating Status file for backup
        statusDict = { 'UUID': '82D108D4-521C-48A5-9C42-79C5E654B98F', #FixMe We Should USE an UUID generator uuid.uuid3(uuid.NAMESPACE_DNS, hostname)
                   'BackupState': 'new', 
                   'IsFullBackup': fullBackup, 
                   'Version': '2.4', 
                   'Date': datetime.datetime.fromtimestamp(mktime(gmtime())),
                   'SnapshotState': 'finished'
                 }
        writePlist(statusDict,self.check_filename("Status.plist"))


    def backup(self,fullBackup=True):
        print "Starting%sbackup..." % (" Encrypted " if self.willEncrypt else "") 
        options = {}
        if not os.path.isdir(os.path.join(self.backupPath,self.udid)):
            os.makedirs(os.path.join(self.backupPath,self.udid))
        
        self.create_info_plist()

        if fullBackup == True:
            options["ForceFullBackup"] = True
        self.mobilebackup2_send_request("Backup", self.udid, options)
        self.work_loop()
    

    def restore(self, options = {"RestoreSystemFiles": True,
                                "RestoreShouldReboot": False,
                                "RestoreDontCopyBackup": True,
                                "RestorePreserveSettings": True}):
        
        print "Starting restoration..."
        m = os.path.join(self.backupPath,self.udid,"Manifest.plist")
        manifest = readPlist(m)
        if manifest["IsEncrypted"]:
            print "Backup is encrypted, enter password : "
            self.password = raw_input()
            options["Password"] = self.password
        self.mobilebackup2_send_request("Restore", self.udid, self.udid, options)
        self.work_loop()


    def info(self,options={}):
        self.mobilebackup2_send_request("Info", self.udid, options)
        info = self.work_loop()
        if info:
            pprint(info['Content'])


    def list(self,options={}):
        self.mobilebackup2_send_request("List", self.udid, options)
        z = self.work_loop()
        if z:
            print z["Content"]
   

    def changepw(self,oldpw,newpw):
        options = { "OldPassword" : olpw,
                    "NewPassword" : newpw }
                    
        self.mobilebackup2_send_request("ChangePassword", self.udid, "")
        self.work_loop()

        
    
                     
if __name__ == "__main__":
    parser = OptionParser(usage="%prog")
    parser.add_option("-b", "--backup", dest="backup", action="store_true", default=True,
                  help="Backup device")
    parser.add_option("-r", "--restore", dest="restore", action="store_true", default=False,
                  help="Restore device")
    parser.add_option("-i", "--info", dest="info", action="store_true", default=False,
                  help="Show backup info")
    parser.add_option("-l", "--list", dest="list", action="store_true", default=False,
                  help="Show backup info")
    (options, args) = parser.parse_args()
    
    lockdown = LockdownClient()
    mb = MobileBackup2Client(lockdown)
    
    args
    
    if options.backup:
        mb.backup()
    elif options.restore:
        mb.restore()
    if options.info:
        mb.info()
    if options.list:
        mb.list()

