# -*- coding: utf-8 -*-
"""
NMEA (National Marine Electronics Association) parser
Ublox, Septentrio, Trimble, and assorted ICDs were used as references
parsedMessage structure: {talkerId : {messageName: {messageDataHere}}}
Created by and property of Uncompromising Sensor Support LLC
"""

import functools, operator, re, time
from functools import reduce

class Nmea:
    """NMEA Parsing class"""
    

    def __init__(self):
        """Initializes Nmea class with sentenceFormatter and labels dictionaries."""

        self.systemIds = {
            '1' : 'GPS-SBAS',
            '2' : 'GLONASS',
            '3' : 'Galileo',
            '4' : 'Beidou',
            '5' : 'QZSS',
        }

        # PUBX03 and GSV requrire using the talker ID to assign a system/signal of interest when overwriting
        self.talkerIdSystemId = {
            'GP': '1',
            'GL': '2',
            'GA': '3',
            'GB': '4',                               
            'GQ': '5',
        }

        # self.gnssIds = {
        #     '0' : 'GPS',
        #     '1' : 'SBAS',
        #     '2' : 'Galileo',
        #     '3' : 'Beidou',
        #     '4' : 'IMES',
        #     '5' : 'QZSS',
        #     '6' : 'GLONASS',
        #     '7' : 'IMES',
        # }

        self.signalIds = {
            '1' : {'1': 'L1C/A',
                   '6': 'L2 CL',
                   '5': 'L2 CM',
                   '7': 'L5 I',
                   '8': 'GPS L5Q',
                   },

            '2' : {'1': 'L1 OF',
                   '2': 'L2 OF'},
            '3' : {'1' : 'E5 al-E5 aQ',
                   '2' : 'E5 bl-E5 bQ',
                   '7' : 'E1 C-E1 B'},
            '4' : {'1' : 'B1ID1-B1ID2',
                   'B' : 'B2ID1-B2ID2',
                   '3' : 'B1IC',
                   '5' : 'B2a'},
            '5' : {'1' : 'L1C/A',
                   '4' : 'L1S',
                   '5' : 'L2 CM',
                   '6' : 'L2 CL',
                   '7' : 'L5 I',
                   '8' : 'L5 Q',
                   },
            '6' : {'1' : 'L5 A'}
        } 

        # Map sentenceFormatter to tuple of message name (internal), labels                                                                                                                   # https://www.tronico.fi/OH6NT/docs/NMEA0183.pdf 
        self.labels = {
            'ALM' : ('GPS Almanac', ['numMsg', 'msgNum', 'svid', 'week','health','e','toc', 'i0', 'OmegaDot', 'sqrtA', 'omega', 'omega0', 'M0', 'f0', 'f1']),
            'DTM' : ('Datum Reference', ['datum', 'subDatum', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'alt (m)', 'refDatum']),
            'GAQ' : ('Poll a Standard message', ['msgId']),
            'GBQ' : ('Poll a Standard message', ['msgId']),
            'GFA' : ('GNSS Fix Accuracy', ['utcTime', 'horizProtectLevel (m)', 'vertProtectLevel (m)', 'stdX (m)', 'stdY (m)', 'theta (deg)', 'stdH (m)', 'sal (m)', 'integrityStatus']),     # https://www.deere.com/assets/pdfs/navcom/User%20Manuals/96-312007-3001RevN_Sapphire%20TRM.PDF
            'GGA' : ('GPS Fix', ['time', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'quality', 'numSV', 'HDOP', 'alt (m)', 'altUnit', 'sep (m)', 'sepUnit', 'diffAge (s)', 'diffStation']),
            'GGQ' : ('GNSS Position and Position Quality', ['utcTime', 'utcDate', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'quality', 'numSV', 'coordQaulity (m)', 'HAE (m)', 'haeUnit']), # http://help.t4d.trimble.com/documentation/manual/version4.6/server/NMEARecv_GEN_GGQ_Description.htm
            'GLL' : ('GPS Latitude, Longitude, and Time', ['lat', 'NS', 'lon', 'EW', 'time', 'status', 'posMode']),
            'GLQ' : ('Poll a Standard message', ['msgId']),
            'GMP' : ('GNSS Map Projection', ['utcTime', 'mapProjection', 'zone', 'x', 'y', 'mode', 'numSv', 'HDOP', 'msl (m)', 'sep (m)', 'diffAge (s)', 'diffStation']),                     # https://www.plaisance-pratique.com/IMG/pdf/NMEA0183-2.pdf
            'GNQ' : ('Poll a Standard message', ['msgId']),
            'GNS' : ('GNSS Fix', ['time', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'posMode', 'numSV', 'HDOP', 'alt (m)', 'sep (m)', 'diffAge (s)', 'diffStation', 'navStatus']),
            'GPQ' : ('Poll a Standard message', ['msgId']),
            'GQQ' : ('Poll a Standard message', ['msgId']),
            'GST' : ('GNSS Psuedorange Errors', ['time', 'ramgeRms (m)', 'stdMajor (m)', 'stdMinor (m)', 'orient (deg)', 'stdLat (m)', 'stdLong (m)', 'stdAlt (m)']),
            'HDT' : ('Heading Direction True', ['head (deg)', 'relTrueNorth (deg)']),                                                                                                         # https://receiverhelp.trimble.com/alloy-gnss/en-us/NMEA-0183messages_HDT.html 
            'LLK' : ('Leica Local Position and GDOP', ['utcTime', 'utcDate', 'gridEasting (m)', 'gridEastingUnit', 'quality', 'numSv', 'GDOP', 'HAE (m)', 'haeUnit']),                        # https://geomar.com/trms/tp-trm601/TrackMaker601.pdf
            'LLQ' : ('Leica Local Position Quality', ['utcTime', 'utcDate', 'gridEastingm', 'm', 'gridNorthingm', 'm', 'quality', 'numSv', 'posQualirtm', 'HAEm', 'm']),                      # https://geomar.com/trms/tp-trm601/TrackMaker601.pdf
            'RLM' : ('Return Link Message', ['beacon', 'time', 'code', 'body']),
            'RMC' : ('Recommended Minimum Data', ['time', 'status', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'spd (knots)', 'cog (deg)', 'date', 'mv (deg)', 'mvEW', 'posMode', 'navStatus']),
            'ROT' : ('Rate and Direction of Turn', ['rateDegMin (degMin)', 'valid']),                                                                                                         # https://receiverhelp.trimble.com/alloy-gnss/en-us/NMEA-0183messages_ROT.html
            'THS' : ('True Heading and Status', ['headt (deg)', 'mi']),
            'TXT' : ('Text', ['numMsg', 'msgNum', 'msgType', 'text']),
            'TXTBase' : ('Text', ['numMsg', 'msgNum', 'validText', 'text']),
            'VLW' : ('Dual Ground/Water Distance', ['twd (nmi)', 'twdUnit', 'wd (nmi)', 'wdUnit', 'tgd (nmi)', 'tgdUnit', 'gd (nmi)', 'gdUnit']),
            'VTG' : ('Course Over Ground and Speed', ['cogt (deg)', 'cogtUnit', 'cogm (deg)', 'cogmUnit', 'sogn (knots)', 'sognUnit', 'sogk (km/h)','sogkUnit', 'posMode']),
            'ZDA' : ('Time and Date', ['time', 'day', 'month', 'year', 'ltzh', 'ltzn']),
                                        
        }
                
        # Define proprietary messages
        self.pubxLabels = {
            '41' : ('Set Protocols and Baud Rate', ['portId', 'inProto', 'outProto', 'baudrate (bit/s)', 'autobauding']),
            '00' : ('Latitude and Longitude Position', ['time', 'lat (degMin)', 'NS', 'long (degMin)', 'EW', 'altRef (m)', 'navStat',
                        'hAcc (m)', 'vAcc (m)', 'SOG (km/hr)', 'COG (deg)', 'vVel (m/s)', 'diffAge', 'HDOP', 'VDOP', 'TDOP', 'numSv', 'reserved', 'DR']),
            '40' : ('Set NMEA Message Output Rate', ['rddc (cycles)' ,'rus1 (cycles)', 'rus2 (cycles)', 'rusb (cycles)', 'rspi (cycles)', 'reserved']),
            '04' : ('Time of Day and Clock Information', ['time', 'date', 'utcTow (s)', 'utcWk', 'leapSec (s)', 'clkBias (ns)', 
                    'clkDrift (ns/s)', 'tpGran (ns)']),
        }
        

        self.ptnlLabels = {
            'AVR' : ('Tilt, Yaw, Roll, Range for Moving Baseline RTK', ['utcTime', 'yaw (deg)', 'yaw', 'tilt (deg)', 'tilt', 'antDist (m)', 'gpsQuality', 'PDOP', 'numSv']),                # https://receiverhelp.trimble.com/alloy-gnss/en-us/NMEA-0183messages_PTNL_AVR.html
            'GGK' : ('GNSS Time, Position, Position Type and DOP', ['utcTime', 'utcDate', 'lat (degMin)', 'NS', 'lon (degMin)', 'EW', 'quality', 'numSV', 'DOP', 'HAE (m)', 'haeUnit'])     # https://receiverhelp.trimble.com/alloy-gnss/en-us/NMEA-0183messages_PTNL_GGK.html
        }

        self.pssnLabels = {
            'HRP': ('Heading, Roll, and Pitch', ['utcTime', 'utcDate', 'head (deg)', 'roll (deg)', 'pitch (deg)', 'headStd (deg)', 'rollStd (deg)', 'pitchStd (deg)']),
            'RBD': ('Rover-Base Direction', ['utcTime', 'utcDate', 'az (deg)', 'el (deg)', 'quality', 'baseMotion', 'corrAge (s)', 'roverSn', 'baseId']),
            'RBP': ('Rover-Base Position', ['utcTime', 'utcDate', 'north (m)', 'east (m)', 'up (m)','numSv', 'quality', 'baseMotion', 'corrAge (s)', 'roverSn', 'baseId']),
            'RBV': ('Rover-Base Velocity',['utcTime', 'utcDate', 'delNorth (m/s)', 'delEast (m/s)', 'delUp (m/s)', 'quality', 'baseMotion', 'corrAge (s)', 'roverSn', 'baseId']),
            'TFM' : ('Used RTCM Coordinate Transformation', ['utcTime', 'height', 'msgUsed1', 'msgUsed2', 'msgUsed3']),
        }

        self.dynamicData = {
        'GBS' :('GNSS Base Station', self.GBS),
        'GRS': ('GNSS Range Residuals', self.GRS),
        'GSA': ('GNSS DOP and Active Satellites', self.GSA),
        'GSV': ('GNSS Satellites in View', self.GSV),
        '03' : ('Sattelite Status', self.pubx03),
        'SNC' : ('NTRIP Client Status', self.SNC)
    }

        # Talker Ids defined after label dictionaries (for access) mapping {talkerId:(name, labelsDictionary)}
        self.talkerIds = {
            'GP': ('GPS', self.labels),
            'GL': ('GLONASS', self.labels),
            'GA': ('Galileo', self.labels),
            'GB': ('BeiDou', self.labels),
            'GI' : ('NavIC', self.labels),                                       
            'GQ': ('QZSS', self.labels),
            'GN': ('GNSS', self.labels),
            'PUBX' : ('ProprietaryUblox', self.pubxLabels),      
            'PTNL' : ('ProprietaryTrimble', self.ptnlLabels),
            'PSSN' : ('ProprietarySeptentrio', self.pssnLabels)
        }


    def checksum(self, message:bytes) -> int:
        """Returns checksum (int) for checksum portion of nmea message (between $ and *)"""
        message = message.decode('ASCII')
        checksum = reduce(operator.xor, (ord(s) for s in message), 0)
        return checksum
            

    def parse(self, raw:bytes) -> dict:
        """Split a given NMEA message for parse function, and error check the result,
        returning a mapping {talkerId: {messageName: {parsedMessageHere}}}"""

        try:
            # Ensure valid message format - strict NMEA with checksum
            # $TALKER_ID(2 chars) + MSG_TYPE(3 chars) + data + *CHECKSUM(2 hex)\r\n
            if message:= re.search(rb'\$[A-Z]{2}[A-Z]{3}[A-Z0-9,. *\-]*\*[0-9A-F]{2}\r\n', raw):
                message = message.group().decode('ASCII')
            else:
                return {"noMessage" : {}}
            
            # Ensure valid checksum
            calculatedChecksum = reduce(operator.xor, (ord(s) for s in message[1:-5]), 0)
            rawChecksum = int(raw[-4:-2], base = 16)
            if rawChecksum != calculatedChecksum:
                return {"unknownMessage" : {"info" : {"passedChecksum" : False, "raw" : message}}}
        
        except (UnicodeDecodeError, ValueError):
            # Not valid NMEA - binary data or malformed
            return {"noMessage" : {}}
        
        # Get message information
        csv = message[1:-5].split(',')                                                       # Comma seperated values
        if csv[0][:2] in self.talkerIds:                                                     # Handle regular NMEA messages
            talkerId = csv[0][:2] 
            sentenceFormatter = csv[0][2:]
            fields = csv[1:]  
        else:
            talkerId = csv[0]                                                                # Handle proprietary NMEA messages
            sentenceFormatter = csv[1]
            fields = csv[2:]  
        
        talkerIdName, labelDictionary = self.talkerIds.get(talkerId, (f'unknownTalkerId: {talkerId}', self.labels))   # Pull long form talkerId, and talkerId label dictionary            
        messageName, labels = labelDictionary.get(sentenceFormatter, (f'unrecognizedSentenceFormatter: {sentenceFormatter}', ['unknown']*len(fields)))
   
        # Overwrite dynamic labels
        if sentenceFormatter in self.dynamicData:                                                                              # Overwrite if a dynamic message
            messageName, dataFunction = self.dynamicData.get(sentenceFormatter, (f'unrecognizedSentenceFormatter: {sentenceFormatter}', ['unknown']*len(fields)))
            data = dataFunction(fields, talkerId)
        else:
            data = {key:value for key,value in zip(labels, fields)}
        data['talkerId'] = talkerId
        data['talkerIdName'] = talkerIdName
        data['messageName'] = messageName
        return {sentenceFormatter :  data}


    def splitAll(self, bytesBin:bytes) -> tuple:
        """Takes bytes, returns a tuple of bytes that were not used in found messages
        and a list of raw messages (bytes)"""
        messages = []
        while message := re.search(rb'\$.*\\r\\n', bytesBin):
                start = message.start()
                end = message.end()
                messages.append(bytesBin[start:end])
                bytesBin = bytesBin[:start] + bytesBin[end:]
        return bytesBin, messages


    def parseAll(self, bytesBin:bytes) -> tuple:
        """Takes bytes, returns a tuple of bytes that were not used in parsed messages
        and a list of parsed messages (dict)"""
        messages = []
        
        # Strict NMEA format: $TALKER_ID(2 chars) + MSG_TYPE(3 chars) + data + *CHECKSUM(2 hex)\r\n
        # Character class: allows uppercase letters, digits, comma, dot, asterisk, minus, space
        # This prevents matching binary SBF data that happens to contain '$'
        while message := re.search(rb'\$[A-Z]{2}[A-Z]{3}[A-Z0-9,. *\-]*\*[0-9A-F]{2}\r\n', bytesBin):
                start = message.start()
                end = message.end()
                
                # Additional validation: must be printable ASCII (NMEA requirement)
                msg_bytes = bytesBin[start:end]
                try:
                    # Try to decode as ASCII - if this fails, it's not NMEA
                    msg_bytes.decode('ASCII')
                    messages.append(self.parse(msg_bytes))
                    bytesBin = bytesBin[:start] + bytesBin[end:]
                except UnicodeDecodeError:
                    # Not valid NMEA, skip this match and continue searching
                    bytesBin = bytesBin[:start] + bytesBin[end:]
                    
        return bytesBin, messages
        

    ################ Dynamic label functions -- allow for multidimensional data structures ############################
    def mapSignal(self, data):
        """Lookup systemId and signalId, and add system and signal to the data dictionary"""
        systemId = data.get('systemId', False)
        signalId = data.get('signalId', False)
        if not systemId and signalId:
            return data
        system = self.systemIds.get(systemId, 'unknownSystemId')
        signal = self.signalIds.setdefault(systemId, {}).get(data.get('signalId',False), 'unknownSignalId')
        data['system'] = system
        data['signal'] = signal
        return data


    def GBS(self, fields, *args):
        labels = ['time', 'errLat (m)', 'errLon (m)',  'errAlt (m)', 'svid', 'prob', 'bias (m)', 'stddev (m)', 'systemId', 'signalId']
        # print(f'GBS got {fields}')
        return self.mapSignal({key:value for key,value in zip(labels, fields)})


    def GRS(self, fields, *args):
        data = {'time': fields[0], 'mode' : fields[1], 'systemId' : fields[-2], 'signalId' : fields[-1]}
        data['residuals (m)'] = [residual for residual in fields[2:-2]]
        return self.mapSignal(data)
    

    def GSA(self, fields, *args):
        data = {'opMode' : fields[0], 'navMode' : fields[1]}
        if systemId := fields[-1] in self.systemIds:
            data['svids'] = [svid for svid in fields[2:len(fields) - 6]]
            data['system'] = self.systemIds.get(systemId, 'unknownSystemId')
            data = data | {'PDOP' : fields[-4], 'HDOP' : fields [-3], 'VDOP' : fields[-2]}
        else: 
            data['svids'] = [svid for svid in fields[2:len(fields) - 5]]
            data = data | {'PDOP' : fields[-3], 'HDOP' : fields[-2], 'VDOP' : fields[-2]}
        return data
    

    def GSV(self, fields, *args):
        data = {'numMsg' : fields[0], 'msgNum' : fields[1], 'numSV' : fields[2]}
        data['systemId'] = self.talkerIdSystemId.get(args[0], 'unknownSystemId')        # Added to allow signalId to be looked up with mapSignal
        const = {}
        subLabels = ['elv (deg)', 'az (deg)', 'cno (dBHz)']
        index = 3
        for _ in range(1, int((len(fields) - 6)/4)):
            const[fields[index]] = {label:fields[index + _ + 1] for _,label in enumerate(subLabels)}
            index += 4
        data[self.systemIds.get(data['systemId'], 'unknownSystemId')] = const
        if signalId := fields[-1] in self.signalIds:
            data['signalId'] = signalId
        return self.mapSignal(data)


    def pubx03(self, fields, *args):
        data = {'n' : fields[0]}
        subLabels = ['s', 'az (deg)', 'el (deg)', 'cno (dBHz)', 'lck (s)']
        index = 1
        for _ in range(1, int((len(fields) - 1)/6)):
            data[f'svid {fields[index]}'] = {label:fields[index + _] for _,label in enumerate(subLabels)}
            index += 6
        return data
    

    def SNC(self, fields, *args):
        data = {label:field for label, field in zip(['', 'msgRev', 'time', 'week'], fields[:4])}
        index = 5        
        for count,sncSub in enumerate(range(1, int((len(fields) - 5)/6))):
            snc = {label:fields[index + _] for _,label in enumerate(['CDIndex', 'Status', 'ErrorCode', 'Info'])}
            data[f'sncSub{count}'] = snc
            index += 7                                # Skip the brackets
        data.pop('')
        return data


    ##################### Integration Functions #####################################################################
    def splitMessages(self, bytesBin:bytes) -> tuple:
        """Takes bytes, returns a tuple of bytes that were not used in found messages
        and a list of found messages (bytes)"""
        messages = []
        while message := re.search(rb'\$.*\\r\\n', bytesBin):
                start = message.start()
                end = message.end()
                messages.append(bytesBin[start:end])
                bytesBin = bytesBin[:start] + bytesBin[end:]
        return bytesBin, messages
    
    def getRx(self, comPort):
        import serial, time                                                        
        usb = serial.Serial(comPort)
        usb.read(usb.inWaiting())
        for message in self.enableUBXNMEA():
            usb.write(message)
            time.sleep(0.075)
        time.sleep(0.125)
        response = usb.read(usb.inWaiting())
        if re.search(rb'\$.*\\r\\n', response):
            return usb
        usb.close()
        return False
    
    def testRx(self, usb):
        usb.baudrate = 9600
        for message in self.enableUBXNMEA():
            usb.write(message)
            time.sleep(0.075)
        time.sleep(0.125)
        response = usb.read(usb.inWaiting())
        if re.search(rb'\$.*\\r\\n', response):
            return True
        return False


# Main loop 
if __name__ == '__main__':
    nmea = Nmea()

    # Setup serial
    import serial
    conn = serial.Serial('COM36')
    bytesBin = b''