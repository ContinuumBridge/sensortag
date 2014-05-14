#!/usr/bin/env python
# sensortagadaptor5.py
# Copyright (C) ContinuumBridge Limited, 2013-2014 - All Rights Reserved
# Unauthorized copying of this file, via any medium is strictly prohibited
# Proprietary and confidential
# Written by Peter Claydon
#
ModuleName = "SensorTag"
# 2 lines below set parameters to monitor gatttool & kill thread if it has disappeared
EOF_MONITOR_INTERVAL = 1  # Interval over which to count EOFs from device (sec)
MAX_EOF_COUNT = 2         # Max EOFs allowed in that interval
INIT_TIMEOUT = 16         # Timeout when initialising SensorTag (sec)
GATT_TIMEOUT = 60         # Timeout listening to SensorTag (sec)
GATT_SLEEP_TIME = 2       # Time to sleep between killing one gatt process & starting another
MAX_NOTIFY_INTERVAL = 5   # Above this value tag will be polled rather than asked to notify (sec)

import pexpect
import sys
import time
import os
import logging
from cbcommslib import CbAdaptor
from cbconfig import *
#from threading import Thread
from twisted.internet import threads
from twisted.internet import reactor

class SimValues():
    """ Provides values in sim mode (without a real SensorTag connected). """
    def __init__(self):
        self.tick = 0

    def getSimValues(self):
        # Acceleration every 330 ms, everything else every 990 ms
        if self.tick == 0:
            # Acceleration
            raw =  ['handle', '=', '0x0030', 'value:', 'ff', 'c2', '01', 'xxx[LE]>']
        elif self.tick == 1:
            time.sleep(0.20)
            # Temperature
            raw =  ['handle', '=', '0x0027', 'value:', 'fc', 'ff', 'ec', '09', 'xxx[LE]>']
        elif self.tick == 2:
            time.sleep(0.13)
            # Acceleration
            raw = ['handle', '=', '0x0030', 'value:', 'ff', 'c2', '01', 'xxx[LE]>']
        elif self.tick == 3:
            time.sleep(0.20)
            # Rel humidity
            raw = ['handle', '=', '0x003b', 'value:', 'c0', '61', 'ae', '7e', 'xxx[LE>']
        elif self.tick == 4:
            time.sleep(0.13)
            # Acceleration
            raw = ['handle', '=', '0x0030', 'value:', 'ff', 'c2', '01', 'xxx[LE]>']
        elif self.tick == 5:
            time.sleep(0.20)
            # Gyro
            raw = ['handle', '=', '0x005a', 'value:', '28', '00', 'cc', 'ff', 'c3', 'ff', 'xxx[LE]>']
        elif self.tick == 6:
            time.sleep(0.14)
            # Acceleration
            raw = ['handle', '=', '0x0030', 'value:', 'ff', 'c2', '01', 'xxx[LE]>']
        self.tick = (self.tick + 1)%7
        return raw

class Adaptor(CbAdaptor):
    def __init__(self, argv):
        logging.basicConfig(filename=CB_LOGFILE,level=CB_LOGGING_LEVEL,format='%(asctime)s %(message)s')
        self.connected = False  # Indicates we are connected to SensorTag
        self.status = "ok"
        self.state = "stopped"
        self.badCount = 0       # Used to count errors on the BLE interface
        self.notifyApps = {"temperature": [],
                           "ir_temperature": [],
                           "acceleration": [],
                           "gyro": [],
                           "magnetometer": [],
                           "rel_humidity": [],
                           "buttons": []}
        self.pollApps =   {"temperature": [],
                           "ir_temperature": [],
                           "acceleration": [],
                           "gyro": [],
                           "magnetometer": [],
                           "rel_humidity": [],
                           "buttons": []}
        self.pollInterval = {"temperature": 1000,
                             "ir_temperature": 1000,
                             "acceleration": 1000,
                             "gyro": 1000,
                             "magnetometer": 1000,
                             "rel_humidity": 1000,
                             "buttons": 1000}
        self.pollTime =     {"temperature": 1000,
                             "ir_temperature": 1000,
                             "acceleration": 1000,
                             "gyro": 1000,
                             "magnetometer": 1000,
                             "rel_humidity": 1000,
                             "buttons": 1000}
        self.lastEOFTime = time.time()
        self.processedApps = []
        
        # Parameters for communicating with the SensorTag
        # Write 0 to turn off gyroscope, 1 to enable X axis only, 2 to
        # enable Y axis only, 3 = X and Y, 4 = Z only, 5 = X and Z, 6 =
        # Y and Z, 7 = X, Y and Z
        self.cmd = {"on": " 01",
                    "off": " 00",
                    "notify": " 0100",
                    "stop_notify": " 0000",
                    "gyro_on": " 07"
                   }
        self.primary = {"temp": 0x23,
                        "accel": 0x2E,
                        "humid": 0x39,
                        "magnet": 0x44,
                        "gyro": 0x5E,
                        "buttons": 0x69
                       }
        self.handles = {}
        self.handles["temperature"] =  {"en": str(hex(self.primary["temp"] + 6)), 
                                        "notify": str(hex(self.primary["temp"] + 3)),
                                        "data": str(format(self.primary["temp"] + 2, "#06x"))
                                       }
        self.handles["acceleration"] = {"en": str(hex(self.primary["accel"] + 6)), 
                                       "notify": str(hex(self.primary["accel"] + 3)),
                                       "period": str(hex(self.primary["accel"] + 9)), 
                                       # Period = 0x34 value x 10 ms (thought to be 0x0a)
                                       # Was running with 0x0A = 100 ms, now 0x22 = 500 ms
                                       "period_value": " 22", 
                                       "data": str(format(self.primary["accel"] + 2, "#06x"))
                                       }
        self.handles["rel_humidity"] = {"en": str(hex(self.primary["humid"] + 6)), 
                                       "notify": str(hex(self.primary["humid"] + 3)),
                                       "data": str(format(self.primary["humid"] + 2, "#06x"))
                                       }
        self.handles["magnetometer"] = {"en": str(hex(self.primary["magnet"] + 6)), 
                                        "notify": str(hex(self.primary["magnet"] + 3)),
                                        "period": str(hex(self.primary["magnet"] + 9)), 
                                        "period_value": " 66", 
                                        "data": str(format(self.primary["magnet"] + 2, "#06x"))
                                       }
        self.handles["gyro"] =  {"en": str(hex(self.primary["gyro"] + 6)), 
                                 "notify": str(hex(self.primary["gyro"] + 3)),
                                 "data": str(format(self.primary["gyro"] + 2, "#06x"))
                                }
        self.handles["buttons"] =  {"notify": str(hex(self.primary["buttons"] + 3)),
                                    "data": str(format(self.primary["buttons"] + 2, "#06x"))
                                   }

        #CbAdaprot.__init__ MUST be called
        CbAdaptor.__init__(self, argv)

    def setState(self, action):
        if self.state == "stopped":
            if action == "connected":
                self.state = "connected"
            elif action == "inUse":
                self.state = "inUse"
        elif self.state == "connected":
            if action == "inUse":
                self.state = "activate"
        elif self.state == "inUse":
            if action == "connected":
                self.state = "activate"
        if self.state == "activate":
            notifying = False
            for a in self.notifyApps:
                if self.notifyApps[a]:
                    notifying = True
                    break
            if not notifying:
                logging.info("%s %s No sensors requested in notify mode", ModuleName, self.id)
            elif self.sim == 0:
                logging.debug("%s %s Activating", ModuleName, self.id)
                status = self.switchSensors()
                logging.info("%s %s %s switchSensors status: %s", ModuleName, self.id, self.friendly_name, status)
            reactor.callInThread(self.getValues)
            pollApps = False
            for a in self.pollApps:
                if self.pollApps[a]:
                    polling = True
                    break
            if not polling:
                logging.info("%s %s No sensors requested in polling  mode", ModuleName, self.id)
            else:
                reactor.callLater(0, self.pollTag)
            self.state = "running"
        # error is only ever set from the running state, so set back to running if error is cleared
        if action == "error":
            self.state == "error"
        elif action == "clear_error":
            self.state = "running"
        logging.debug("%s %s state = %s", ModuleName, self.id, self.state)
        if self.state == "connected" or self.state == "inUse":
            external_state = "starting"
        else:
            external_state = self.state
        msg = {"id": self.id,
               "status": "state",
               "state": external_state}
        self.sendManagerMessage(msg)

    def onStop(self):
        # Mainly caters for situation where adaptor is told to stop while it is starting
        if self.connected:
            try:
                self.gatt.kill(9)
                logging.debug("%s %s %s onStop killed gatt", ModuleName, self.id, self.friendly_name)
            except:
                logging.warning("%s %s %s onStop unable to kill gatt", ModuleName, self.id, self.friendly_name)

    def initSensorTag(self):
        logging.info("%s %s %s Init", ModuleName, self.id, self.friendly_name)
        try:
            cmd = 'gatttool -i ' + self.device + ' -b ' + self.addr + \
                  ' --interactive'
            logging.debug("%s %s %s cmd: %s", ModuleName, self.id, self.friendly_name, cmd)
            self.gatt = pexpect.spawn(cmd)
        except:
            logging.error("%s %s %s Dead!", ModuleName, self.id, self.friendly_name)
            self.connected = False
            return "noConnect"
        self.gatt.expect('\[LE\]>')
        self.gatt.sendline('connect')
        index = self.gatt.expect(['successful', pexpect.TIMEOUT, pexpect.EOF], timeout=INIT_TIMEOUT)
        if index == 1 or index == 2:
            # index 2 is not actually a timeout, but something has gone wrong
            self.connected = False
            self.gatt.kill(9)
            # Wait a second just to give SensorTag time to "recover"
            time.sleep(1)
            return "timeout"
        else:
            self.connected = True
            return "ok"

    def checkAllProcessed(self, appID):
        self.processedApps.append(appID)
        found = True
        for a in self.appInstances:
            if a not in self.processedApps:
                found = False
        if found:
            self.setState("inUse")

    def writeTag(self, handle, cmd):
        # Write a command to the tag and checks it has been received
        line = 'char-write-req ' + handle + cmd
        logging.debug("%s %s %s gatt cmd: %s", ModuleName, self.id, self.friendly_name, line)
        self.gatt.sendline(line)
        index = self.gatt.expect(['successfully', pexpect.TIMEOUT, pexpect.EOF], timeout=1)
        if index == 1 or index == 2:
            logging.debug("%s char-write-req failed. index =  %s", ModuleName, index)
            self.tagOK = "not ok"

    def writeTagNoCheck(self, handle, cmd):
        # Writes a command to the tag without checking if it has been received
        # Used to write after the tag is returning values
        line = 'char-write-cmd ' + handle + cmd
        logging.debug("%s %s %s gatt cmd: %s", ModuleName, self.id, self.friendly_name, line)
        self.gatt.sendline(line)

    def readTag(self, handle):
        line = 'char-read-hnd ' + handle
        logging.debug("%s %s %s gatt cmd: %s", ModuleName, self.id, self.friendly_name, line)
        self.gatt.sendline(line)
        # The value read is caught by getValues

    def switchSensors(self):
        """ Call whenever an app updates its sensor configuration. Turns
            individual sensors in the Tag on or off.
        """
        self.tagOK = "ok"
        for a in self.notifyApps:
            if a != "ir_temperature":
                if self.notifyApps[a]:
                    if "en" in self.handles[a]:
                        self.writeTag(self.handles[a]["en"], self.cmd["on"])
                    if "notify" in self.handles[a]:
                        self.writeTag(self.handles[a]["notify"], self.cmd["notify"])
                    if "period" in self.handles[a]:
                        self.writeTag(self.handles[a]["period"], self.handles[a]["period_value"])
                else:
                    if "en" in self.handles[a]:
                        self.writeTag(self.handles[a]["en"], self.cmd["off"])
        return self.tagOK

    def pollTag(self):
        for a in self.pollApps:
            if self.pollApps[a]:
                if time.time() > self.pollTime[a]:
                    reactor.callLater(0, self.switchSensorOn, a)
                    self.pollTime[a] = time.time() + self.pollInterval[a]
        reactor.callLater(1, self.pollTag)

    def switchSensorOn(self, sensor):
        logging.debug("%s %s %s swtichSensorOn: %s", ModuleName, self.id, self.friendly_name, sensor)
        self.writeTagNoCheck(self.handles[sensor]["en"], self.cmd["on"])
        # Leave for 1 second, then read
        reactor.callLater(1, self.readSensor, sensor)

    def readSensor(self, sensor):
        self.readTag(self.handles[sensor]["data"])
        self.writeTagNoCheck(self.handles[sensor]["en"], self.cmd["off"])

    def connectSensorTag(self):
        """
        Continually attempts to connect to the device.
        Gating with doStop needed because adaptor may be stopped before
        the device is ever connected.
        """
        if self.connected == True:
            tagStatus = "Already connected" # Indicates app restarting
        elif self.sim != 0:
            # In simulation mode (no real devices) just pretend to connect
            self.connected = True
        while self.connected == False and not self.doStop and self.sim == 0:
            tagStatus = self.initSensorTag()    
            if tagStatus != "ok":
                logging.error("%s %s %s Failed to initialise", ModuleName, self.id, self.friendly_name)
        if not self.doStop:
            logging.info("%s %s %s Initialised", ModuleName, self.id, self.friendly_name)
            self.setState("connected")
        else:
            return
 
    def s16tofloat(self, s16):
        f = float.fromhex(s16)
        if f > 32767:
            f -= 65535
        return f

    def s8tofloat(self, s8):
        f = float.fromhex(s8)
        if f > 127:
            f -= 256 
        return f

    def calcTemperature(self, raw):
        # Calculate temperatures
        objT = self.s16tofloat(raw[1] + \
                            raw[0]) * 0.00000015625
        ambT = self.s16tofloat(raw[3] + raw[2]) / 128.0
        Tdie2 = ambT + 273.15
        S0 = 6.4E-14
        a1 = 1.75E-3
        a2 = -1.678E-5
        b0 = -2.94E-5
        b1 = -5.7E-7
        b2 = 4.63E-9
        c2 = 13.4
        Tref = 298.15
        S = S0 * (1 + a1 * (Tdie2 - Tref) + \
            a2 * pow((Tdie2 - Tref), 2))
        Vos = b0 + b1 * (Tdie2 - Tref) + b2 * pow((Tdie2 - Tref), 2)
        fObj = (objT - Vos) + c2 * pow((objT - Vos), 2)
        objT = pow(pow(Tdie2,4) + (fObj/S), .25)
        objT -= 273.15
        return objT, ambT

    def calcHumidity(self, raw):
        t1 = self.s16tofloat(raw[1] + raw[0])
        temp = -46.85 + 175.72/65536 * t1
        rawH = int((raw[3] + raw[2]), 16) & 0xFFFC # Clear bits [1:0] - status
        # Calculate relative humidity [%RH] 
        v = -6.0 + 125.0/65536 * float(rawH) # RH= -6 + 125 * SRH/2^16
        return v

    def calcGyro(self, raw):
        # Xalculate rotation, unit deg/s, range -250, +250
        r = self.s16tofloat(raw[1] + raw[0])
        v = (r * 1.0) / (65536/500)
        return v

    def calcMag(self, raw):
        # Calculate magnetic-field strength, unit uT, range -1000, +1000
        s = self.s16tofloat(raw[1] + raw[0])
        v = (s * 1.0) / (65536/2000)
        return v

    def getValues(self):
        """Continually updates sensor values. Run in a thread.
        """
        while not self.doStop:
            # If things appear to be going wrong, signal an error
            if self.badCount > 7:
                self.setState("error")
            if self.sim == 0:
                index = self.gatt.expect(['handle.*', pexpect.TIMEOUT, pexpect.EOF], timeout=GATT_TIMEOUT)
            else:
                index = 0
            if index == 1:
                status = ""
                logging.warning("%s %s %s gatt timeout", ModuleName, self.id, self.friendly_name)
                # First try to reconnect nicely
                self.gatt.sendline('connect')
                index = self.gatt.expect(['successful', pexpect.TIMEOUT, pexpect.EOF], timeout=INIT_TIMEOUT)
                if index == 1 or index == 2:
                    # index 2 is not actually a timeout, but something has gone wrong
                    logging.warning("%s Could not reconnect nicely. Killing", ModuleName)
                    self.badCount += 1
                    self.connected = False
                else:
                    logging.warning("%s Successful reconnection without kill", ModuleName)
                    status = self.switchSensors()
                    logging.info("%s %s %s switchSensors status: %s", ModuleName, self.id, self.friendly_name, status)
                while status != "ok" and not self.doStop:
                    self.gatt.kill(9)
                    time.sleep(GATT_SLEEP_TIME)
                    status = self.initSensorTag()   
                    logging.info("%s %s %s re-init status: %s", ModuleName, self.id, self.friendly_name, status)
                    if status == "ok":
                        # Must switch sensors on/off again after re-init
                        status = self.switchSensors()
                        logging.info("%s %s %s switchSensors status: %s", ModuleName, self.id, self.friendly_name, status)
            elif index == 2:
                # Most likely cause of EOFs is that gatt process has been killed.
                # In this case, there will be lots of them. Detect this and exit the thread.
                # Also report back to manager to allow it to take action. Eg: restart adaptor.
                if not self.doStop:
                    logging.debug("%s %s %s gatt EOF in getValues", ModuleName, self.id, self.friendly_name)
                    eofTime = time.time()
                    if eofTime - self.lastEOFTime > EOF_MONITOR_INTERVAL:
                       self.eofCount = 1
                    else:
                       self.eofCount += 1
                    self.lastEOFTime = eofTime
                    if self.eofCount > MAX_EOF_COUNT:
                        self.status = "error"
                        break
                else:
                    break
            else:
                if self.badCount > 7:
                    self.setState("reset_error")
                self.badCount = 0  # Got a value so reset
                if self.sim == 0:
                    raw = self.gatt.after.split()
                else:
                    raw = self.simValues.getSimValues()
                timeStamp = time.time()
                handles = True
                startI = 2
                while handles:
                    type = raw[startI]
                    if type.startswith(self.handles["acceleration"]["data"]): 
                        # Accelerometer descriptor
                        accel = {}
                        accel["x"] = self.s8tofloat(raw[startI+2])/63
                        accel["y"] = self.s8tofloat(raw[startI+3])/63
                        accel["z"] = self.s8tofloat(raw[startI+4])/63
                        self.sendParameter("acceleration", accel, timeStamp)
                    elif type.startswith(self.handles["buttons"]["data"]):
                        # Button press decriptor
                        buttons = {"leftButton": (int(raw[startI+2]) & 2) >> 1,
                                   "rightButton": int(raw[startI+2]) & 1}
                        self.sendParameter("buttons", buttons, timeStamp)
                    elif type.startswith(self.handles["temperature"]["data"]):
                        # Temperature descriptor
                        objT, ambT = self.calcTemperature(raw[startI+2:startI+6])
                        self.sendParameter("temperature", ambT, timeStamp)
                        self.sendParameter("ir_temperature", objT, timeStamp)
                    elif type.startswith(self.handles["rel_humidity"]["data"]):
                        relHumidity = self.calcHumidity(raw[startI+2:startI+6])
                        self.sendParameter("rel_humidity", relHumidity, timeStamp)
                    elif type.startswith("0x0057"):
                        gyro = {}
                        gyro["x"] = self.calcGyro(raw[startI+2:startI+4])
                        gyro["y"] = self.calcGyro(raw[startI+4:startI+6])
                        gyro["z"] = self.calcGyro(raw[startI+6:startI+8])
                        self.sendParameter("gyro", gyro, timeStamp)
                    elif type.startswith("0x0040"):
                        mag = {}
                        mag["x"] = self.calcMag(raw[startI+2:startI+4])
                        mag["y"] = self.calcMag(raw[startI+4:startI+6])
                        mag["z"] = self.calcMag(raw[startI+6:startI+8])
                        self.sendParameter("magnetometer", mag, timeStamp)
                    else:
                       pass
                    # There may be more than one handle in raw. Remove the
                    # first occurence & if there is another process it
                    raw.remove("handle")
                    if "handle" in raw:
                        handle = raw.index("handle")
                        startI = handle + 2
                    else:
                        handles = False
        try:
            if self.sim == 0:
                self.gatt.kill(9)
                logging.debug("%s %s %s gatt process killed", ModuleName, self.id, self.friendly_name)
        except:
            logging.error("%s %s %s Could not kill gatt process", ModuleName, self.id, self.friendly_name)

    def sendParameter(self, parameter, data, timeStamp):
        msg = {"id": self.id,
               "content": parameter,
               "data": data,
               "timeStamp": timeStamp}
        for a in self.notifyApps[parameter]:
            reactor.callFromThread(self.sendMessage, msg, a)
        for a in self.pollApps[parameter]:
            reactor.callFromThread(self.sendMessage, msg, a)

    def onAppInit(self, message):
        """
        Processes requests from apps.
        Called in a thread and so it is OK if it blocks.
        Called separately for every app that can make requests.
        """
        #logging.debug("%s %s %s onAppInit, message = %s", ModuleName, self.id, self.friendly_name, message)
        tagStatus = "ok"
        resp = {"name": self.name,
                "id": self.id,
                "status": tagStatus,
                "functions": [{"parameter": "temperature",
                               "interval": "1.0",
                               "purpose": "room"},
                              {"parameter": "ir_temperature",
                               "interval": "1.0",
                               "purpose": "ir temperature"},
                              {"parameter": "acceleration",
                               "interval": "3.0",
                               "purpose": "access door"},
                              {"parameter": "gyro",
                               "interval": "1.0",
                               "range": "-250:+250 degrees",
                               "purpose": "gyro"},
                              {"parameter": "magnetometer",
                               "interval": "1.0",
                               "range": "-1000:+1000 uT",
                               "purpose": "magnetometer"},
                              {"parameter": "rel_humidity",
                               "interval": "1.0",
                               "purpose": "room"},
                              {"parameter": "buttons",
                               "interval": "0",
                               "purpose": "user_defined"}],
                "content": "functions"}
        self.sendMessage(resp, message["id"])
        
    def onAppRequest(self, message):
        logging.debug("%s %s %s onAppRequest, message = %s", ModuleName, self.id, self.friendly_name, message)
        # Switch off anything that already exists for this app
        for a in self.notifyApps:
            if message["id"] in self.notifyApps[a]:
                self.notifyApps[a].remove(message["id"])
        for a in self.pollApps:
            if message["id"] in self.pollApps[a]:
                self.pollApps[a].remove(message["id"])
        # Now update details based on the message
        for f in message["functions"]:
            if f["interval"] < MAX_NOTIFY_INTERVAL:
                if message["id"] not in self.notifyApps[f["parameter"]]:
                    self.notifyApps[f["parameter"]].append(message["id"])
            else:
                if message["id"] not in self.pollApps[f["parameter"]]:
                    self.pollApps[f["parameter"]].append(message["id"])
                    if f["interval"] < self.pollInterval[f["parameter"]]:
                        self.pollInterval[f["parameter"]] = f["interval"]
        logging.info("%s %s %s notifyApps: %s", ModuleName, self.id, self.friendly_name, str(self.notifyApps))
        logging.info("%s %s %s pollApps: %s", ModuleName, self.id, self.friendly_name, str(self.pollApps))
        self.checkAllProcessed(message["id"])

    def onConfigureMessage(self, config):
        """Config is based on what apps are to be connected.
            May be called again if there is a new configuration, which
            could be because a new app has been added.
        """
        if not self.configured:
            if self.sim != 0:
                self.simValues = SimValues()
            self.connectSensorTag()

if __name__ == '__main__':
    adaptor = Adaptor(sys.argv)
