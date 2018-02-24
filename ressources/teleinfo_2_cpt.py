﻿#!/usr/bin/python
# -*- coding: utf-8 -*-

""" Teleinfo reader

License
=======

teleinfo_2_cpt.py is Copyright:
- (C) 2010-2012 Samuel <samuel DOT buffet AT gmail DOT com>
- (C) 2012-2017 Frédéric <fma38 AT gbiloba DOT org>
- (C) 2017 Samuel <samuel DOT buffet AT gmail DOT com>
- (C) 2015-2018 Cédric Guiné <cedric DOT guine AT gmail DOT com>
This software is governed by the CeCILL license under French law and
abiding by the rules of distribution of free software.  You can  use,
modify and/or redistribute the software under the terms of the CeCILL
license as circulated by CEA, CNRS and INRIA at the following URL
http://www.cecill.info.

As a counterpart to the access to the source code and  rights to copy,
modify and redistribute granted by the license, users are provided only
with a limited warranty  and the software's author,  the holder of the
economic rights,  and the successive licensors  have only  limited
liability.

In this respect, the user's attention is drawn to the risks associated
with loading,  using,  modifying and/or developing or reproducing the
software by the user in light of its specific status of free software,
that may mean  that it is complicated to manipulate,  and  that  also
therefore means  that it is reserved for developers  and  experienced
professionals having in-depth computer knowledge. Users are therefore
encouraged to load and test the software's suitability as regards their
requirements in conditions enabling the security of their systems and/or
data to be ensured and,  more generally, to use and operate it in the
same conditions as regards security.

The fact that you are presently reading this means that you have had
knowledge of the CeCILL license and that you accept its terms.
"""

import time
import optparse
try:
    import ftdi1 as ftdi
except ImportError:
    raise ImportError('Erreur de librairie ftdi')
import urllib2
import sys
import os
import traceback
import logging

# USB settings
USB_VENDOR = 0x0403
USB_PRODUCT = 0x6001
USB_PORT = [0x00, 0x11, 0x22]
BAUD_RATE = 1200
# Default log level
gLogLevel = logging.DEBUG

# TELEINFO settings
FRAME_LENGTH = 400  # Nb chars to read to ensure to get a least one complete raw frame

# Misc
STX = 0x02  # start of text
ETX = 0x03  # end of text
EOT = 0x04  # end of transmission

# Default output is stdout
gExternalIP = ''
gCleAPI = ''
gDebug = ''
gRealPath = ''


class MyLogger:
    """ Our own logger """
    def __init__(self):
        program_path = os.path.dirname(os.path.realpath(__file__))
        self._logger = logging.getLogger('teleinfo')
        hdlr = logging.FileHandler(program_path + '/../../../log/teleinfo_deamon')
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        hdlr.setFormatter(formatter)
        self._logger.addHandler(hdlr)
        self._logger.setLevel(gLogLevel)

    def debug(self, text):
        try:
            self._logger.debug(text)
        except NameError:
            pass

    def info(self, text):
        try:
            text = text.replace("'", "")
            self._logger.info(text)
        except NameError:
            pass

    def warning(self, text):
        try:
            text = text.replace("'", "")
            self._logger.warn(text)
        except NameError:
            pass

    def error(self, text):
        try:
            text = text.replace("'", "")
            self._logger.error(text)
        except NameError:
            pass


class FtdiError(Exception):
    """ Ftdi related errors
    """


class TeleinfoError(Exception):
    """ Teleinfo related errors
    """


class Teleinfo(object):
    """ Class for handling teleinfo stuff
    """
    def __init__(self):
        self._log = MyLogger()
        self._log.info("Initialisation de la teleinfo")
        super(Teleinfo, self).__init__()
        try:
            self.context = ftdi.new()
        except:
            pass
        ret = ftdi.usb_open(self.context, 0x0403, 0x6001)
        ftdi.set_baudrate(self.context, 1200)
        ftdi.set_line_property(self.context, ftdi.BITS_8, ftdi.EVEN, ftdi.STOP_BIT_1)

    def selectMeter(self, num):
        """ Select giver meter
        """
        err = ftdi.set_bitmode(self.context, USB_PORT[num], ftdi.BITMODE_CBUS)
        if err < 0:
            self._log.error("Can't set bitmode (%d, %s)" % (err, ftdi.get_error_string(self.context)))
            raise FtdiError("Can't set bitmode (%d, %s)" % (err, ftdi.get_error_string(self.context)))
        time.sleep(0.1)

    def readOne(self):
        """ read 1 char from usb
        """
        err, buf = ftdi.read_data(self.context, 0x1)
        if err < 0:
            self._log.error("Can't read data (%d, %s)" % (err, ftdi.get_error_string(self.context)))
            self.shutdown()
            raise FtdiError("Can't read data (%d, %s)" % (err, ftdi.get_error_string(self.context)))
        if err:
            #c = unichr(ord(buf) % 0x80)  # Clear bit 7
            c = chr(ord(buf) & 0x07f)
            return err, c
        else:
            return err, None
    def __readRawFrame(self):
        """ Read raw frame
        """

        # As the data are sent asynchronously by the USB interface, we probably don't start
        # to read at the start of a frame. So, we read enough chars to retreive a complete frame

        err = ftdi.usb_purge_buffers(self.context)
        if err < 0:
            self._log.error("Can't purge buffers (%d, %s)" % (err, ftdi.get_error_string(self.context)))
            raise FtdiError("Can't purge buffers (%d, %s)" % (err, ftdi.get_error_string(self.context)))

        raw = u""
        while len(raw) < FRAME_LENGTH:
            err, c = self.readOne()
            if c is not None and c != '\x00':
                raw += c
        return raw

    def __frameToDatas(self, frame):
        """ Split frame in datas
        """
        Content = {}
        lines = frame.split('\r')
        for line in lines:
            try:
                checksum = line[-1]
                header, value = line[:-2].split()
                data = {'header': header.encode(), 'value': value.encode(), 'checksum': checksum}
                self.__checkData(data)
                Content[header.encode()] = value.encode()
            except:
                pass
                #datas.append(data)
        return Content

    def __checkData(self, data):
        """ Check if data is ok (checksum)
        """
        # Check entry
        sum = 0x20  # Space between header and value
        for s in (data['header'], data['value']):
            for c in s:
                sum += ord(c)
        sum %= 0x40  # Checksum on 6 bits
        sum += 0x20  # Ensure printable char
        if sum != ord(data['checksum']):
            data = null
        #raise TeleinfoError("Corrupted data found (%s)" % data)


    def extractDatas(self, raw):
        """ Extract datas from raw frame
        """
        end = raw.rfind(chr(ETX)) + 1
        start = raw[:end].rfind(chr(ETX)+chr(STX))
        frame = raw[start+2:end-2]
        # Check if there is a EOT, cancel frame
        if frame.find(chr(EOT)) != -1:
            return {'Message':'EOT'}
            #raise TeleinfoError("EOT found")
        # Convert frame back to ERDF standard
        #frame = frame.replace('\n', '')     # Remove new line
        # Extract data
        datas = self.__frameToDatas(frame)
        return datas

    def readMeter(self, device, externalip, cleapi, debug, realpath):
        """ Read raw frame for giver meter
        """
        self._device = device
        self._externalip = externalip
        self._cleAPI = cleapi
        self._debug = debug
        self._realpath = realpath
        _CompteurNum = 1
        Donnees_cpt1 = {}
        Donnees_cpt2 = {}
        _Donnees_cpt1 = {}
        _Donnees_cpt2 = {}
        _RAZ = 3600
        _Separateur = " "
        _SendData = ""
        while(1):
            if(_RAZ > 1):
                _RAZ = _RAZ - 1
            else:
                _RAZ = 3600
                for cle, valeur in Donnees_cpt1.items():
                    Donnees_cpt1.pop(cle)
                    _Donnees_cpt1.pop(cle)
                for cle, valeur in Donnees_cpt2.items():
                    Donnees_cpt2.pop(cle)
                    _Donnees_cpt2.pop(cle)
            _SendData = ""
            self.selectMeter(_CompteurNum)
            raw = self.__readRawFrame()
            self.selectMeter(0)
            datas = self.extractDatas(raw)
            if(_CompteurNum == 1):
                for cle, valeur in datas.items():
                    if(cle == 'PTEC'):
                        valeur = valeur.replace(".","")
                        valeur = valeur.replace(")","")
                        Donnees_cpt1[cle] = valeur
                    else:
                        Donnees_cpt1[cle] = valeur
            elif(_CompteurNum == 2):
                for cle, valeur in datas.items():
                    if(cle == 'PTEC'):
                        valeur = valeur.replace(".","")
                        valeur = valeur.replace(")","")
                        Donnees_cpt2[cle] = valeur
                    else:
                        Donnees_cpt2[cle] = valeur
            if(self._externalip != ""):
                self.cmd = self._externalip +'/plugins/teleinfo/core/php/jeeTeleinfo.php?api=' + self._cleAPI
                _Separateur = "&"
            else:
                self.cmd = 'nice -n 19 /usr/bin/php ' + self._realpath + '/../php/jeeTeleinfo.php api=' + self._cleAPI
                _Separateur = " "
            #_SendData += _Separateur + 'ADCO='+ ADCO
            if(_CompteurNum == 1):
                for cle, valeur in Donnees_cpt1.items():
                    if(cle in _Donnees_cpt1):
                        if (Donnees_cpt1[cle] != _Donnees_cpt1[cle]):
                            _SendData += _Separateur + cle +'='+ valeur
                            _Donnees_cpt1[cle] = valeur
                    else:
                        _SendData += _Separateur + cle +'='+ valeur
                        _Donnees_cpt1[cle] = valeur
            elif(_CompteurNum == 2):
                for cle, valeur in Donnees_cpt2.items():
                    if(cle in _Donnees_cpt2):
                        if (Donnees_cpt2[cle] != _Donnees_cpt2[cle]):
                            _SendData += _Separateur + cle +'='+ valeur
                            _Donnees_cpt2[cle] = valeur
                    else:
                        _SendData += _Separateur + cle +'='+ valeur
                        _Donnees_cpt2[cle] = valeur
            if (_SendData != ""):
                if(_CompteurNum == 1):
                    if (_Donnees_cpt1.has_key("ADCO")):
                        _SendData += _Separateur + "ADCO=" + _Donnees_cpt1["ADCO"]
                elif(_CompteurNum == 2):
                    if (_Donnees_cpt2.has_key("ADCO")):
                        _SendData += _Separateur + "ADCO=" + _Donnees_cpt2["ADCO"]
                self.cmd += _SendData
                if (self._debug == '1'):
                    self._log.debug(self.cmd)
                if(self._externalip != ""):
                    try:
                        response = urllib2.urlopen(self.cmd)
                    except Exception, e:
                        errorCom = "Connection error '%s'" % e
                else:
                    try:
                        self.process = subprocess.Popen(self.cmd, shell=True)
                        self.process.communicate()
                    except Exception, e:
                        errorCom = "Connection error '%s'" % e
            if (_CompteurNum == 1):
                _CompteurNum = 2
            else:
                _CompteurNum = 1


def main():
    usage  = "%prog -r [options] -> read meters\n"
    # Common options
    parser = optparse.OptionParser(usage)
    parser.add_option("-o", "--output", dest="filename", help="append result in FILENAME")
    parser.add_option("-p", "--port", dest="port", help="port du modem")
    parser.add_option("-e", "--externalip", dest="externalip", help="ip de jeedom")
    parser.add_option("-c", "--cleapi", dest="cleapi", help="cle api de jeedom")
    parser.add_option("-d", "--debug", dest="debug", help="mode debug")
    parser.add_option("-r", "--realpath", dest="realpath", help="path usr")
    parser.add_option("-v", "--vitesse", dest="vitesse", help="vitesse")
    parser.add_option("-f", "--force", dest="force", help="forcer le lancement")
    (options, args) = parser.parse_args()

    gDeviceName = gExternalIP = gDebug = gCleAPI = gRealPath = ""
    if options.port:
        try:
            gDeviceName = options.port
        except:
            error = "Can not change port %s" % options.port
            raise TeleinfoException(error)
    if options.externalip:
        try:
            gExternalIP = options.externalip
        except:
            error = "Can not change ip %s" % options.externalip
            raise TeleinfoException(error)
    if options.debug:
        try:
            gDebug = options.debug
        except:
            error = "Can not set debug mode %s" % options.debug
            #raise TeleinfoException(error)
    if options.cleapi:
        try:
            gCleAPI = options.cleapi
        except:
            error = "Can not change ip %s" % options.cleapi
            raise TeleinfoException(error)
    if options.realpath:
        try:
            gRealPath = options.realpath
        except:
            error = "Can not get realpath %s" % options.realpath
            raise TeleinfoException(error)
    teleinfo = Teleinfo()
    pid = str(os.getpid())
    file("/tmp/teleinfo.pid", 'w').write("%s\n" % pid)

    teleinfo.readMeter(gDeviceName, gExternalIP, gCleAPI, gDebug, gRealPath)
    ftdi_.shutdown()


if __name__ == "__main__":
    main()
