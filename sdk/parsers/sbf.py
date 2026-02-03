# -*- coding: utf-8 -*-
"""
SBF (Septentrio Binary Format) parser, as defined by the 
Mosaic-X5 Firmware v4.14.4 Referece Guide
https://www.septentrio.com/en/products/gps/gnss-receiver-modules/mosaic-x5

Created by and property of Uncompromising Sensor Support LLC
"""

# Imports
import struct, re, time, os
from collections import defaultdict

# Class
class Sbf:
    """SBF parser, defines SBF commands and returns messages using
    self.parse(message) method, which parses bytes and returns a python
    dictionary."""
    
    def __init__(self, units = True):
        
        # Set flag to determine if units should be converted
        self.units = True

        # Define SBF fields (takes byte, returns python value)
        self.fmts = {
            'u1' : lambda x: struct.unpack('<B',x)[0],                         # Unsigned 1-byte int
            'u2' : lambda x: struct.unpack('<H',x)[0],                         # Unsigned 2-byte int
            'u4' : lambda x: struct.unpack('<I',x)[0],                         # Unsigned 4-byte int
            'u8' : lambda x: struct.unpack('<Q',x)[0],                         # Unsigned 8-byte int
            'i1' : lambda x: struct.unpack('b', x)[0] ,                        # -struct.unpack('<b',x)[0] # Signed 1-byte int, twos complement
            'i2' : lambda x: -struct.unpack('<h',x)[0],                        # Signed 2-byte int, twos complement
            'i4' : lambda x: -struct.unpack('<i',x)[0],                        # Signed 4-byte int, twos complement
            'i8' : lambda x: -struct.unpack('<q',x)[0],                        # Signed 8-byte int, twos complement
            'f4' : lambda x: struct.unpack('<f',x)[0],                         # IEEE float on 4 bytes
            'f8' : lambda x: struct.unpack('<d',x)[0],                         # IEEE float on 8 bytes
            'bitlist' : lambda x, bits: [int(_) for _ in list("{0:b}".format(x).zfill(bits))],                           # Big endian list of 1s and 0s to represent given value
            'bitint' : lambda x,start,stop: int("{0:b}".format(x).zfill(8)[::-1][start:stop][::-1], base = 2),           # Little endian integer that uses a slice of a bitslist (based on most requiring little endian)
            'bitint2' : lambda x,size, start,stop: int("{0:b}".format(x).zfill(size)[::-1][start:stop][::-1], base = 2)  # Little endian bitint with adjustable bit size (ex: 2 bytes = 16bit chunk)  
            }
        self.fmtLengths = {'u1': 1,
                            'u2': 2,
                            'u4': 4,
                            'u8': 8,
                            'i1': 1,
                            'i2': 2,
                            'i4': 4,
                            'i8': 8,
                            'f4': 4,
                            'f8': 8}
        
        # Define SVIDS as {sbfSvidNumber: (constellation, svidNumber)}
        numbers = list(range(1,62)) + list(range(63,69)) +  list(range(71,188)) + list(range(191,246))
        add = lambda constellation, start, stop, offset: [(constellation, _- offset) for _ in range(start, stop)]
        ids = add('GPS', 1, 38, 0) + add('GLONASS', 38, 62, 37) + add('GLONASS', 63,69,38)
        ids += add('Galileo', 71, 107, 70) + add('L Band', 107, 120,0) + add('SBAS', 120, 141, 100)
        ids += add('BeiDou', 141, 181, 140) + add('QZSS', 181, 188, 180) + add('NAVIC', 191, 198,190)
        ids += add('SBAS', 198, 216, 57) + add('NAVIC', 216, 223, 208) + add('BeiDou', 223, 246, 182)
        self.svids = {number:svid for number, svid in zip(numbers, ids)}
        self.svids[62] = ('GLONASS', 0)
        
        # Setup self.signals as a better mapping signalNumber : (signalType, constellation, frequencyBand, rinexCode)
        self.signals = {0 : ('L1CA', 'GPS', 'L1', '1C'),
                        1 : ('L1P', 'GPS', 'L1', '1W'),
                        2 : ('L2P', 'GPS', 'L2', '2W'),
                        3 : ('L2C', 'GPS', 'L2', '2L'),
                        4 : ('L5', 'GPS', 'L5', '5Q'),
                        5 : ('L1C', 'GPS', 'L1', '1L'),
                        6 : ('L1CA', 'QZSS', 'L1', '1C'),
                        7 : ('L2C', 'QZSS', 'L2', '2L'),
                        8 : ('L1CA', 'GLONASS', 'L1', '1C'),
                        9 : ('L1P', 'GLONASS', 'L1', '1P'),
                        10 : ('L2P', 'GLONASS', 'L2', '2P'),
                        11 : ('L2CA', 'GLONASS', 'L2', '2C'),
                        12 : ('L3', 'GLONASS', 'L3', '3Q'),
                        13 : ('B1C', 'BeiDou', 'L1', '1P'),
                        14 : ('B2a', 'BeiDou', 'L2', '5P'),
                        15 : ('L5', 'NAVIC', 'L5', '5A'),
                        16 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        17 : ('E1', 'Galileo', 'L1', '1C'),
                        18 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        19 : ('E6', 'Galileo', 'L2', '6C'),                            # Also referred to as 6B, pending GALE6BC used
                        20 : ('E5a', 'Galileo', 'L5', '5Q'),
                        21 : ('E5b', 'Galileo', 'L5', '7Q'),
                        22 : ('E5 AltBOC', 'Galileo', 'L5', '8Q'),
                        23 : ('LBand', 'MSS', 'L1', 'NA'),
                        24 : ('L1CA', 'SBAS', 'L1', '1C'),
                        25 : ('L5', 'SBAS', 'L5', '5I'),
                        26 : ('L5', 'QZSS', 'L5', '5Q'),
                        27: ('L6', 'QZSS', 'L6', None),
                        28 : ('B1I', 'BeiDou', 'L1', '2I'),
                        29 : ('B2I', 'BeiDou', 'L2', '7I'),
                        30 : ('B3I', 'BeiDou', 'L3', '6I'),
                        31 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        32 : ('L1C', 'QZSS', 'L1', '1L'),
                        33 : ('L1S', 'QZSS', 'L1', '1Z'),
                        34 : ('B2b', 'BeiDou', 'L2', '7D'),
                        35 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        36 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        37 : ('Reserved', 'Reserved', 'Reserved', 'Reserved'),
                        38 : ('L1CB (Tentative!)', 'QZSS', 'L1', '1E'),
                        39 : ('L5S', 'QZSS', 'L5', '5P'),
                        99 : ('badValue', 'badValue', 'badValue', 'badValue')}
        self.signalFrequency = {f'{value[1]} {value[0]}' : value[2] for value in self.signals.values()}  

        # Set GNSS tracking modes to be used in multiple messages
        modes = ['No GNSS PVT Availible', 'Stand-Alone PVT', 'Differential PVT',
                 'Fixed location', 'RTK with fixed ambiguities','RTK with float ambiguities',
                 'SBAS aided PVT', 'moving-base RTK with fixed ambiguities',
                 'moving-base RTK with float ambiguities', 'Reserved', 'Precise Pointing Positioning'
                 'Reserved']
        self.modes = {index: meaning for index, meaning in enumerate(modes)}
        
        # Define GNSS errors
        errors = ['No Error', 'Not enough measurements', 'Not enough ephemerides availible',
                  'DOP too large', 'Sum of squared residuals too large', 'No convergence',
                  'Not enough measurements after outlier rejection', 
                  'Position output prohibited due to export laws', 
                  'Not enough differential corrections availible',
                  'Base station coordinates unavailible', 'Ambiguities not fixed and user requsted\
                   to only output RTX-fixed positions', 'Datum transformation parameters unknown']
        self.GNSSerrors = {index: meaning for index, meaning in enumerate(errors)}
           
        # Define field, format, bitmask, badVal, scale, and unit for every SBF block
        self.startBlock =  (['CRC', 'ID','Length', 'TOW', 'WNc'],
                             ['u2', 'u2', 'u2', 'u4', 'u2'], 
                             {'ID' : {'Block Number' : 0x1fff,
                                      'Block Reveision' : 0xe000}},
                             {'TOW' : 4294967295, 'WNc' : 65535},
                             {'TOW' : 0.001},
                             {'TOW' : 's', 'WNc' : 'weeks'})
                             
            
        # Determine implemented messages
        self.implemented = {4027: ('MeasEpoch', self.MeasEpoch),
                            4007: ('PVTGeodetic', self.PVTGeodetic),
                            4244: ('ExtEventPVTGeodetic', self.ExtEventPVTGeodetic),
                            5891: ('GPSNav', self.GPSNav),
                            4004: ('GLONav', self.GLONav),
                            4081: ('BDSNav', self.BDSNav),
                            4002: ('GALNav', self.GALNav),
                            5902: ('ReceiverSetup', self.ReceiverSetup),
                            4014: ('ReceiverStatus', self.ReceiverStatus),
                            4036: ('GLOTime', self.GLOTime),
                            5894: ('GPSUtc', self.GPSUtc),
                            4052: ('PosLocal', self.PosLocal),
                            5906: ('PosCovGeodetic', self.PosCovGeodetic),
                            4028: ('BaseVectorGeod', self.BaseVectorGeod),
                            5908: ('VelCovGeodetic', self.VelCovGeodetic),
                            4040: ('BBSamples', self.BBSamples),
                            4013: ('ChannelStatus', self.ChannelStatus),
            }
        
        self.pvtTrackingVals = ['Stand-Alone', 'Differential PVT',
                                'Fixed location', 'RTK with fixed ambiguities','RTK with float ambiguities',
                                'SBAS aided PVT', 'moving-base RTK with fixed ambiguities',
                                'moving-base RTK with float ambiguities', 'Precise Pointing Positioning']
    
    def splitAll(self, bytesBin):
        """Takes bytes, returns a tuple of bytes that were not used in parsed messages
        and a list of parsed messages (dict)"""

        messages = []
        
        # Match response commands
        while message:= re.search(rb'\$R:(.*\\r\\n){2}USB[0-9]>', bytesBin):
            start, end = message.start(), message.end()
            messages.append({'ack' : bytesBin[start:end]})
            bytesBin = bytesBin[:start] + bytesBin[end:]

        # Match SBF commands
        while message:= re.search(rb'\$\@', bytesBin):
            start = message.start()
            if len(bytesBin) > start + 8:
                end = start + struct.unpack('<H',bytesBin[start+6:start+8])[0]
                if len(bytesBin) >= end:
                        messages.append(bytesBin[start:end])
                        bytesBin = bytesBin[:start] + bytesBin[end:]
                else:
                    break
            else:
                break

        return bytesBin, messages
    
    
    def parse(self, message):
        """Parse message (bytes) into a parsed dictionary. 
        Calls implemented parsers and handles bad values, scales, units, and bitmasks."""
        
        # Parse start block
        fields, fmts, bitmasks, badVals, scales, units = self.startBlock            # Get block start
        ret = self.parseBlock(message, fields, fmts, start = 2)                     # Parse block start 
        ret = self.badVal(ret, badVals)                                                      # Handle do not use values (rewrites bad value key to "key {(DO NOT USE)}"
        ret = self.scale(ret, scales)                                               # Apply scale factors 
        ret = self.bitmask(ret, bitmasks)                                           # Apply bitmasks 
        if self.units:                                                              # Apply units    
            ret = self.unit(ret, units)
        
        # Parse main block
        messageNumber = ret.get('Block Number', 0)
        if messageNumber in self.implemented:                                       # Parse implmented messages
            name, parser = self.implemented[messageNumber]
            data = parser(message)
        else:
            name, data = 'unknown', {'raw': str(message[14:])}
        
        # Return dictionary with ret from start block added
        return {name: data|ret}
    
    
    def parseAll(self, bytesBin):
        """Takes bytes, returns a tuple of bytes that were not used in parsed messages
        and a list of parsed messages (dict)"""

        messages = []
        
        # CRITICAL: Remove ACK response commands from byte stream (don't add to messages - they cause buffer growth)
        # Format: $R:...<any text>...\r\n\r\nUSB[0-9]>
        # Use non-greedy match to handle multiple ACKs
        while message:= re.search(rb'\$R:.*?\r\n\r\nUSB[0-9]>', bytesBin, re.DOTALL):
            start, end = message.start(), message.end()
            # Remove from stream completely - these are just acknowledgements
            bytesBin = bytesBin[:start] + bytesBin[end:]

        # Match SBF commands - robust extraction
        while message:= re.search(rb'\$\@', bytesBin):
            start = message.start()
            
            # Need at least 8 bytes for header (sync + CRC + length + ID)
            if len(bytesBin) < start + 8:
                # Not enough data for header - keep from sync marker forward
                bytesBin = bytesBin[start:]
                break
            
            # Extract message length from header (bytes 6-8)
            messageLength = struct.unpack('<H', bytesBin[start+6:start+8])[0]
            end = start + messageLength
            
            # Validate message length is reasonable
            if messageLength < 8 or messageLength > 100000:
                # Corrupt header - skip this sync marker and continue searching
                bytesBin = bytesBin[start+2:]
                continue
            
            # Check if we have the complete message
            if len(bytesBin) >= end:
                # Parse and add complete message
                messages.append(self.parse(bytesBin[start:end]))
                bytesBin = bytesBin[end:]
            else:
                # Incomplete message - keep from start of message forward
                bytesBin = bytesBin[start:]
                break

        return bytesBin, messages
        
    
    def parseBlock(self, message:bytes, fields:list, formats:list, start:int = 14) -> dict:
        """Creates ret, preliminary parsed result dictionary"""
        ret = {}
        for field, fmt in zip(fields, formats):
            stop = start + self.fmtLengths[fmt]
            ret[field] = self.fmts[fmt](message[start:stop])
            start = stop
        return ret
    

    def badVal(self, ret, badVals):
        """Takes ret, badVals {label: val} and returns ret with badVals overwritten to 'label (DO NOT USE)'"""
        for label, val in badVals.items():
            if ret.get(label, 255) == val:
                # ret.pop(label)
                ret[f'{label} (DO NOT USE)'] = val
        return ret


    def bitmask(self, ret, bitmasks, bitmaskFunc = False):
        """Takes ret, bitmasks {label: func/dict} and returns ret with bitmasks applied
        such that label values are now {label: {newLabel: val}}"""
        if bitmaskFunc:                                                # Overwrite bitmask with custom functions
            for label, function in bitmasks.items():
                field = ret.get(label, 1)
                ret = function(field) | ret
                # ret[f'{label} values'] = function(val)               # bitmask labels must be accessable from main ret
        else:                                                          # Overwrite bitmask with mask
            for label, fieldDict in bitmasks.items():
                labelDict = {}
                for newLabel, mask in fieldDict.items():
                    val = ret[label] & mask
                    valbits = "{0:b}".format(mask)
                    n = len(valbits) - valbits.rfind('1')
                    val = val if n == 1 else val/(2**(n+1))            # Handle edge case where n = 1 (keep original val)
                    labelDict[newLabel]  = val
                ret = labelDict | ret
                # ret[f'{label} values'] = labelDict
        return ret
     
    
    def scale(self, ret, scales):
        """Tales ret, scales and applies scales to ret with unfound values set to 0"""
        for label, scale in scales.items():
            ret[label] = ret.get(label, 0)*scale
        return ret
    
    
    def unit(self, ret, units):
        """Takes ret, units and applies units to ret with unfound values set to 0"""
        for label, val in units.items():
            ret[f'{label} ({val})'] = ret.get(label, 0)
            ret.pop(label)
        return ret
        

    ############## Indivudal message parsers #################################
    def MeasEpoch(self, message):
        """Uses helper functions (below) to iteratively solve for, and parse the measEpoch message"""
        
        def getSignal(ret):
            """Find and return signalId, constellation, frequency, and rinexType
            from the ret dictionary"""
            SigIdxLo = ret['SigIdxLo']
            if SigIdxLo == 31:
                    rawObsInfo = int(struct.pack('<B',ret['ObsInfo']).hex())
                    offset = int(format(rawObsInfo, '#010b')[2:][3:7],2)
                    return self.signals.get(32 + offset,(f'{32 + offset} not in self.signals', None, None, None))    
            elif SigIdxLo in [8,9,10,11]:
                ret['GLO frequency slot'] = self.fmts['bitint'](ret['ObsInfo'],3,8) - 8
            return self.signals.get(SigIdxLo, (f'{SigIdxLo} not in self.signals', None, None, None))
        
        def commonFlags(value):
            """Helper function returns {field:Val} for the bitmask from 
            a given value. Used for common flags in main block."""
            commonFlags = {0: 'Multipath mitigation', 1: 'Code smoothing', 
                            2: 'Reserved', 3: 'Clock Sterring', 4: 'NA', 
                            5: 'High Dynamics', 6: 'E6B signal used', 
                            7: 'Scrambling'}
            ret = {}
            for index, bit in enumerate(self.fmts['bitlist'](value, 8)):
                if index in commonFlags:
                    ret[commonFlags[index]] = bool(bit)
            return ret

        # Setup convinient additional mappings 
        signals = {}                                                               # Map {Constellation: {SVID : {SIGID : [CN0, LockTime]}}}
        cn0s = {}                                                                  # Map {Frequency Band: {const SVID : {SigId : CN0}}}     
        trackingConstellation = {}                                                 # Map {constellation: [SVIDS]}
        trackingSignals = {}                                                       # Map {constellation : {signalType : [SVIDS]}}
                
        # Setup parameters for the mainSbfBlock   
        fields = ['N1', 'SB1Length' ,'SB2Length', 'CommonFlags', 'CumClkJumps', 'Reserved1']
        formats = ['u1']*6
        scales = {'CumClkJumps' : 0.001}
        units = {'CumClkJumps' : 's'}
        bitmasks = {'CommonFlags':commonFlags}
        
        # Params for block 1
        b1Fields = ['RxChannel', 'Type', 'SVID', 'Misc', 'CodeLSB', 'Doppler',
                    'CarrierLSB', 'CarrierMSB', 'CN0', 'LockTime', 'ObsInfo',
                    'N2']
        b1Formats = ['u1']*4 + ['u4', 'i4', 'u2', 'i1', 'u1', 'u2', 'u1', 'u1']
        b1Scales = {'CodeLSB' : 0.001, 'Doppler' : 0.0001, 'CarrierLSB' : 0.0001, 
                     'CarrierMSB' : 65.536, 'CN0' : 0.25, 'MSB of psuedorange': 4294967.2}
        b1Units = {'CodeLSB' : 'm', 'Doppler' : 'Hz', 'CarrierLSB' : 'cycles', 
                    'CarrierMSB' : 'cycles', 'CN0' : 'dB-Hz', 'MSB of psuedorange' : 'm'}
        b1BadVals = {'Misc' : 0, 'CodeLSB' : 0, 'Doppler' : -2147483648,
                       'CarrierLSB' : 0, 'CarrierMSB' : -128, 'CN0' : 255,
                       'LockTime' : 65535}
        b1Bitmasks = {
            'Type' : lambda x: {'SigIdxLo':  self.fmts['bitint'](x,0,5), 
                       'Antenna ID': x}, 
            
            'Misc': lambda x: {'MSB of psuedorange':
                               self.fmts['bitint'](x,0,3),
                               'Reserved' : self.fmts['bitlist'](x, 8)[4:8]}
            }
            
        # Params for block 2
        b2Fields = ['Type', 'LockTime', 'CN0', 'OffsetsMSB', 
                       'CarrierMSB', 'ObsInfo','CodeOffsetLSB', 'CarrierLSB',
                       'DopplerOffsetLSB' ]
        b2Formats = ['u1']*4 + ['i1', 'u1'] + ['u2']*3
        b2Scales = {'CN0' : 0.25, 'CarrierMSB' : 65.536, 'CodeOffsetMSB' : 65.536,
                       'DopplerOffsetMSB': 6.5536}
        b2Units = {'LockTime': 's', 'CN0' : 'dB-Hz', 'CarrierMSB' :  'cycles',
                      'CodeOffsetMSB': 'm', 'DopplerOffsetMSB': 'Hz'}
        b2Bitmasks = {'Type' : b1Bitmasks['Type'], 
                         'OffsetsMSB' : lambda x: {
                                        'CodeOffsetMSB' : self.fmts['bitint'](x, 0 ,3)*-1,
                                        'DopplerOffsetMSB': self.fmts['bitint'](x, 3,8)*-1},
                         }
        b2BadVals = {'LockTime' : 254, 'CN0' : 255, 'CodeOffsetMSB' : -4,
                         'DopplerOffsetMSB' : -16, 'CarrierMSB' : -128, 
                         'CodeOffsetLSB' : 0, 'CarrierLSB' : 0,
                         'DopplerOffsetLSB' : 0}
        
        
        # Start ret as parsed main block
        ret = self.parseBlock(message, fields, formats)
        ret = self.bitmask(ret, bitmasks, bitmaskFunc = True)
        ret = self.scale(ret, scales)
        if self.units:
            ret = self.unit(ret, units)
        
        # Iteratively solve message starting with  block1                                   
        start = 20

        # Parse block 1
        for b1 in range(ret['N1']):
            ret1 = self.parseBlock(message, b1Fields, b1Formats, start = start)
            ret1 = self.bitmask(ret1, b1Bitmasks, bitmaskFunc = True)
            ret1 = self.badVal(ret1, b1BadVals)
            ret1 = self.scale(ret1, b1Scales)
            cn0 = ret1['CN0']                                 # Get info before adjusting units
            lockTime = ret1['LockTime']                       # Get info before adjusting units
            if self.units:
                ret1 = self.unit(ret1, b1Units)
            start += ret['SB1Length'] 
            
            # Add information to convenient additional mappings
            signalId, constellation, frequency, rinexType = getSignal(ret1)
            constellation, svid = self.svids.get(ret1['SVID'], (None, None))

            # Handle new information set
            signals.setdefault(constellation, {})[svid] = {signalId: (cn0, lockTime)}
            cn0s.setdefault(frequency, {})[f'{constellation} {svid}'] = cn0 # {signalId: (cn0, lockTime)}
            trackingConstellation.setdefault(constellation, []).append(svid)
            trackingSignals.setdefault(f'{constellation} {signalId}', []).append(svid)

            # Add svid and signalId to ret
            ret[svid] = {signalId:ret1}
            
            # Iteratively solve for and add type2 (MeasEpochChannelType2 sub-block)
            for b2 in range(ret1['N2']):
                ret2 = self.parseBlock(message, b2Fields, b2Formats, start = start)
                ret2 = self.bitmask(ret2, b2Bitmasks, bitmaskFunc = True)
                ret2 = self.badVal(ret2, b2BadVals)
                ret2 = self.scale(ret2, b2Scales)
                cn0 = ret2['CN0']                                 # Get info before adjusting units
                lockTime = ret2['LockTime']                       # Get info before adjusting units
                if self.units:
                    ret2 = self.unit(ret2, b2Units)
                start += ret['SB2Length'] 
                
                # Add information to convenient additional mappings
                signalId, constellation, frequency, rinexType = getSignal(ret2)
                if constellation  not in signals:                 # Handle None, 'Reserved' case
                    continue
                signals[constellation][svid][signalId] =  (cn0, lockTime)
                trackingConstellation[constellation].append(svid)
                trackingSignals.setdefault(f'{constellation} {signalId}', []).append(svid)
                cn0s.setdefault(frequency, {})[f'{constellation} {svid}'] = cn0 # {signalId: (cn0, lockTime)}

        # Add convenient additional mappings to ret and return
        ret['cn0s'] = cn0s     
        ret['signals'] = signals              
        ret['trackingConstellation'] = trackingConstellation                
        ret['trackingSignals'] = trackingSignals       
        return ret
    

    def PVTGeodetic(self, message): 
        fields = ['Mode', 'Error', 'Latitude', 'Longitude', 'Height', 
                  'Undulation', 'Vn', 'Ve', 'Vu', 'COG', 'RxClkBias',
                  'RxClkDrift', 'TimeSystem', 'Datum', 'NrSV', 'WACorrInfo', 
                  'ReferenceID', 'MeanCorrAge', 'SignalInfo', 'AlertFlag',
                  'NrBases', 'PPPInfo', 'Latency', 'HAccuracy', 'VAccuracy',
                  'Misc']
        formats = ['u1']*2 + ['f8']*3 + ['f4']*5 + ['f8','f4'] + ['u1']*4 
        formats += ['u2']*2 + ['u4', 'u1', 'u1'] + ['u2']*4 + ['u1']
        scales = {'MeanCorrAge' : 0.01, 'Latency' : 0.0001, 'HAccuracy' : 0.01, 
                  'VAccuracy' : 0.01}
        units = {field:unit for field, unit in zip(fields[2:12] + fields[21:25], ['rad', 'rad',
                'm', 'm', 'm/s', 'm/s', 'm/s', 'deg', 'ms', 'ppm', 's', 's', 'm', 'm'])}
        units['MeanCorrAge']  = 's'

        # Create ret
        ret = self.parseBlock(message, fields, formats)

        # Overwrite bitmasks
        bitmasks = {'Mode': {'Type of PVT Solution' : 0xf,
                             'setPVTMode commanded and determining position' : 0x40,
                             '2D/3D flag: set in 2D mode' : 0x80},
                
                    'WACorrInfo': {
                              'Orbit and SV clock correction info is used' : 1,
                              'Range correction information is used': 2,
                              'Ionispheric information is used': 4,
                              'Orbit accuracy information is used': 8,
                              'DO229 Precision Approach mode is active': 16,
                              'Type of RTK corrections': 96,
                              'Reserved': 128},
         
                    'AlertFlag' : {'RAIM integrity' : 0x3,
                                    'Integrity has failed as per Galileo HPCA' : 0x4,
                                    'Galileo Ionospheric storm flag' : 0x8
                        },
                    
                    'PPPInfo' : {'Age of last seed (s), clipped to 4091' : 0x7ff,
                                 'Reserved' : 0x1000,
                                 'Type of last seed' : 0xe000
                                 },
                    
                    'Misc' : {'Baseline points' : 0xe000,
                              'Phase center compensated' : 0x2,
                              'ARP' : 0x3}
                    }
        ret = self.bitmask(ret, bitmasks)
        
        # Overwrite dictionaries
        pvtSolutionType = {0:'No GNSS PVT', 1 : 'Stand-Alone', 
                2: 'Differential', 3: 'Fixed location', 
                4 : 'RTK with fixed ambiguities', 5 : 'RTK with float ambiguities', 
                6 : 'SBAS aided PVT', 7: 'Moving base RTK with fixed ambiguities', 
                8 : 'Moving base RTK with float ambiguities', 9: 'Reserved',
                10 : 'Precise Point Positioning', 12 : 'Reserved'
            }
        pvtErrorCode = {0 : 'No error', 1 : 'Not enough measurements', 
                          2 : 'Not enough ephemerides availible', 
                          3 : 'DOP too large', 4 : 'Sum of squared residuals too large', 
                          5 : 'No convergence', 6 : 'Not enough measurements for outlier rejection',
                          7 : 'Position output prohibited due to export laws', 
                          8 : 'Not enough differential corrections available',
                          9 : 'Base station coordinates unavailible',
                          10 : 'Ambiguities not fixed and user requested to only ouput RTK-fixed positions'
                          }
        timeSystem = {0 : 'GPS time', 1 : 'Galileo time', 3 : 'GLONASS time',
                       4 : 'BeiDou time', 5: 'QZSS time', 100 : 'Fugro AtomiChron time',
                       255: 'Do-Not-Use'
                       }
        datum = {0 : 'WGS84/ITRS', 19 : 'Datum same as DGNSS/RTK', 30 : 'ETRS89',
                 31 : 'NAD83(2011)', 32 : 'NAD83(PA11)', 33 : 'NAD83(MA11)',
                 34 : 'GDA94(2010)', 35 : 'GDA2020', 36 : 'JGD2011', 
                 250 : 'First user-defined datum', 251 : 'Second user-defined datum',
                 255: 'Do-Not-Use'
                 }
        raimIntegrity = {0 : 'RAIM not active', 1 : 'RAIM integrity test successful', 
                          2 : 'RAIM integrity test failed', 3: 'Reserved'
                          }
        arp_marker = {0 : 'Unknown', 1 : 'ARP-to-marker offset is zero',
                      2 : 'ARP-to-marker offset is not zero'
                      }
                   
        # Overwrite values
        ret['Type of PVT Solution'] = pvtSolutionType.get(ret['Type of PVT Solution'], 'PVT Solution Type not implemented in parser')
        ret['Error'] = pvtErrorCode.get(ret['Error'], 'PVT Error Code not implemented in parser')
        ret['TimeSystem'] = timeSystem.get(ret['TimeSystem'],'Time System not implemented in parser')
        ret['Datum'] = datum.get(ret['Datum'],'Datum not implemented in parser')
        ret['RAIM integrity'] = raimIntegrity.get(ret['RAIM integrity'], 'RAIM integrity not implemented in parser')
        ret['ARP'] = arp_marker.get(ret['ARP'], 'ARP not implemented in parser')
        ret = self.scale(ret, scales)
        if self.units:
            ret = self.unit(ret, units)
        return ret


    def ExtEventPVTGeodetic(self, message):
        """Parse ExtEventPVTGeodetic message - PVT at external event (PPS) time"""
        # First 4 bytes after header contain event-specific fields
        fields = ['RxCLKBias', 'RxClkDrift', 'TimeSystem', 'Datum', 'NrSV']
        formats = ['f8', 'f4', 'u1', 'u1', 'u1']
        
        # Parse event timing (RxCLKBias is the key field - receiver clock offset at PPS edge)
        ret = self.parseBlock(message, fields, formats, start=14)
        
        # Apply units
        units = {'RxCLKBias': 'ms', 'RxClkDrift': 'ppm'}
        if self.units:
            ret = self.unit(ret, units)
        
        # TimeSystem dictionary
        timeSystem = {0: 'GPS time', 1: 'Galileo time', 3: 'GLONASS time',
                     4: 'BeiDou time', 5: 'QZSS time', 255: 'Do-Not-Use'}
        ret['TimeSystem'] = timeSystem.get(ret.get('TimeSystem', 255), 'Unknown')
        
        return ret

    
    def GPSNav(self, message):
        fields = ['PRN', 'Reserved', 'WN', 'CAorPonL2', 'URA', 'health', 'L2DataFlag',
                  'IODC', 'IODE2', 'IODE3', 'FitIntFlg', 'Reserved2', 'T_gd', 't_oc',
                  'a_f2', 'a_f1', 'a_f0', 'C_rs', 'DEL_N', 'M_0', 'C_uc', 'e', 'C_us',
                  'SQRT_A', 't_oe', 'C_ic', 'OMEGA_0', 'C_is', 'i_0', 'C_rc', 'omega', 
                  'OMEGADOT', 'IDOT', 'WNt_oc', 'WNt_oe']
        formats = [f'u{_}' for _ in (1,1,2,1,1,1,1,2,1,1,1,1)] + ['f4', 'u4']
        formats += ['f4']*5 + ['f8', 'f4', 'f8', 'f4', 'f8', 'u4', 'f4', 'f8', 'f4']
        formats += ['f8', 'f4', 'f8', 'f4', 'f4', 'u2', 'u2']
        units = ['s', 's', 's/s^2', 's/s', 's', 'm', 'semi-circle/s', 'semi-circle',
                 'rad', '', 'rad', 'm^(1/2)', 's', 'rad', 'semi-circle', 'rad',
                 'semi-circle', 'm', 'semi-circle', 'semi-circle/s', 'semi-circle/s',
                 'week', 'week']
        units = {field:unit for field, unit in zip(fields[12:], units)}
        units['WN']  = 'week'
        badVals = {'WN' : 65535}
    
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)
       
        # Get SV Number
        constellation, svNumber = self.svids.get(ret['PRN'], ('Unknown', 0))
        ret['svNumber'] = svNumber
    
        return ret
    
    
    def GLONav(self, message):
        fields = ['SVID', 'FreqNr', 'X', 'Y', 'Z', 'Dx', 'Dy', 'Dz', 'Ddx',
                  'Ddy', 'Ddz', 'gamma', 'tau', 'dtau', 't_oe', 'WN_toe', 'P1',
                  'P2', 'E', 'B', 'tb', 'M', 'P', 'l', 'p4', 'N_T', 'F_T', 'C']
        formats = ['u1']*2 + ['f8']*3 + ['f4']*9 + [f'u{_}' for _ in (4,2,1,1,
                  1,1,2,1,1,1,1,2,2,1)]
        scales = {field:1000 for field in fields[3:11]}
        scales['F_T'] =  0.01
        units = ['m']*3 + ['m/s']*3 + ['m/s^2']*3 + ['Hz/Hz'] + ['s']*3 + ['week','minute']
        units = {field:unit for field, unit in zip(fields[3:17], units)}
        for field, unit in zip(['E', 'tb', 'N_T', 'F_T'], ['day', 'minute', 'day', 'm']):
            units[field] = unit
        badVals = {'C' : 255}
    
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        ret = self.scale(ret, scales)
        if self.units:
            ret = self.unit(ret, units)
        
        # Get SV Number
        if ret['SVID'] == 'GLO Unknown':
            svNumber = 0
        else:
            constellation, svNumber = self.svids.get(ret['SVID'], ('Unknown', 0))
        ret['svNumber'] = svNumber
     
        return ret
    

    def BDSNav(self, message):
        fields = ['PRN', 'Reserved', 'WN', 'URA', 'SatH1', 'IODC', 'IODE', 
                  'Reserved2', 'T_GD1', 'T_GD2', 't_oc','a_f2', 'a_f1', 'a_f0',
                  'C_rs', 'DEL_N', 'M_0', 'C_uc', 'e', 'C_us', 'SQRT_A', 't_oe',
                  'C_ic', 'OMEGA_0', 'C_is', 'i_0', 'C_rc', 'omega', 'OMEGADOT', 
                  'IDOT', 'WNt_oc', 'WNt_oe']
        formats = [f'u{_}' for _ in (1,1,2,1,1,1,1,2)] + ['f4', 'f4', 'u4']
        formats += ['f4']*5 + ['f8', 'f4', 'f8', 'f4', 'f8', 'u4', 'f4', 'f8', 'f4']
        formats += ['f8', 'f4', 'f8', 'f4', 'f4', 'u2', 'u2']
        units = ['s', 's', 's', 's/s^2', 's/s', 's', 'm', 'semi-circle/s',
                 'semi-circle', 'rad', '', 'rad', 'm^(1/2)', 's', 'rad', 
                 'semi-circle', 'rad', 'semi-circle', 'm', 'semi-circle', 
                 'semi-circle/s', 'semi-circle/s','week', 'week']
        units = {field:unit for field, unit in zip(fields[8:], units)}
        units['WN']  = 'week'
        badVals = {'T_GD2' : -2e10}
    
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)
            
        # Add svNumber for program
        constellation, svNumber = self.svids.get(ret['PRN'], ('Unknown', 0))
        if svNumber == 0:
            print(f'PRN {ret["PRN"]} not found in SV IDs')
        ret['svNumber'] = svNumber
            
        return ret
    
    
    def GALNav(self, message):
        fields = ['SVID', 'Source', 'SQRT_A', 'M_0', 'e', 'i_0','omega', 
                  'OMEGA_0', 'OMEGADOT', 'IDOT', 'DEL_N', 'C_uc', 'C_us',
                  'C_rc', 'C_rs', 'C_ic', 'C_is', 't_oe', 't_oc', 'a_f2', 'a_f1', 'a_f0',
                  'WNt_oe', 'WNt_oc', 'IODnav', 'Health_OSSOL', 'Health_PRS',
                  'SISA_L1E5a', 'SISA_L1E5b', 'SISA_L1AE6A', 'BGD_L1e5a',
                  'BGD_L1e5b', 'BGD_L1AE6A','CNAVenc']
        formats = ['u1']*2 + ['f8']*6 + ['f4']*9 + ['u4']*2 + ['f4']*2
        formats += ['f8'] + ['u2']*4 + ['u1']*4 + ['f4']*3 + ['u1']
        units = ['m^(1/2)','semi-circle','','semi-circle','semi-circle','semi-circle',
                 'semi-circle/s','semi-circle/s','semi-circle/s', 'rad', 'rad',
                 'm','m','rad','rad','s','s','s/s^2', 's/s','s','week','week']
        units = {field:unit for field, unit in zip(fields[2:24], units)}
        for field in fields[30:33]:
            units[field] = 's'
            
        badVals = {'SISA_L1E5a' : 255, 'SISA_L1E5b' : 255, 'SISA_L1AE6A':255,
            'BGD_L1e5a' : -2e10, 'BGD_L1e5b' :-2e10, 'BGD_L1AE6A':-2e10,
            'CNAVenc' : 255}
    
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)
        
        # Get svNumber for program
        constellation, svNumber = self.svids.get(ret['SVID'], ('Unknown', 0))
        ret['svNumber'] = svNumber
        return ret
    
    
    def GLOTime(self, message):
        fields = ['SVID', 'FreqNr', 'N_4', 'KP', 'N', 'tau_GPS','tau_c', 
                  'B1', 'B2']
        formats = ['u1']*4 + ['u2', 'f4','f8','f4','f4']
        units = ['day', 'ns', 'ns', 's', 's/msd']
        units = {field:unit for field, unit in zip(fields[4:], units)}
        scales = {'tau_GPS' : 1e9, 'tau_c' : 1e9}
            
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        self.scale(ret, scales)
        if self.units:
            ret = self.unit(ret, units)
        return ret
    
    
    def GPSUtc(self, message):
        fields = ['PRN', 'Reserved 1', 'A_1', 'A_0', 't_ot', 'WN_t', 'DEL_t_LS',
                  'WN_LSF', 'DN', 'DEL_t_LSF']
        formats = ['u1']*2 + ['f4','f8','u4','u1', 'i1', 'u1','u1', 'i1']
        units = ['s/s', 's', 's', 'week', 's', 'week', 'day', 's']
        units = {field:unit for field, unit in zip(fields[2:], units)}
        
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        if self.units:
            ret = self.unit(ret, units)
        return ret
    
        
    def ReceiverSetup(self, message):
        # Parse string fields
        stringFields = ['MarkerName', 'MarkerNumber', 'Observer', 'Agency', 'RxSerialNumber', 
                        'RxName', 'RxVersion', 'AntSerialNbr', 'AntType','MarkerType', 
                        'GNSSFWVersion', 'ProductName', 'StationCode', 'CountryCode']
        stringStarts = [2,62,82,102,142,162,182,202,222,254,274,314] # + [334, 347]
        stringStops = [62,82,102,142,162,182,202,222,242,274,314,354] # + [344, 350]
        stringStarts = [_ + 14 for _ in stringStarts]
        stringStops = [_ + 14 for _ in stringStops]
        
        # Decode ASCII strings
        ret = {}
        for field, start, stop in zip(stringFields, stringStarts, stringStops):
            ret[field] = message[start:stop].rstrip(b'\x00').decode('ASCII')
        
        # Cut message down to what is remaining
        offset = 0
        for start, stop in zip (stringStarts, stringStops):
            message = message[:start + offset] + message[stop+offset:]
            offset -= (stop - start)
        
        # Parse remaining message
        fields = ['Reserved', 'Reserved2', 'deltaH', 'deltaE', 'deltaN', 'Latitude', 'Longitude','Height', 
                  'MonumentIdx','ReceiverIdx','CountryCode','Reserved3']
        formats = ['u1']*2 + ['f4']*3 + ['f8','f8','f4','u1', 'u1']
        units = ['m']*3 + ['rad','rad','HAE m']
        units = {field:unit for field, unit in zip(fields[2:8], units)}            
        badVals = {field: -2e10 for field in ('Latitude', 'Longitude','Height')}
    
        # Create ret
        newret = self.parseBlock(message, fields, formats)
        newret = self.badVal(newret, badVals)
        if self.units:
            newret = self.unit(newret, units)
        
        for field, value in newret.items():
            ret[field] = value
            
        return ret
    
    def ReceiverStatus(self, message):
        # Main block fields and formats (from PDF)
        fields = [ 'CPULoad', 'ExtError', 'UpTime','RxState', 'RxError', 'N', 'SBLength', 'CmdCount', 'Temperature']
        formats = [ 'u1', 'u1', 'u4', 'u4', 'u4', 'u1', 'u1', 'u1', 'u1']
        units = {'UpTime': 's', 'Temperature' : 'C'}
        badVals = {'CPULoad' : 255, 'CmdCount' : 0, 'Temperature' : 0}

        # Parse main block
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)

        # Parse AGC state sub-blocks
        agcFields = ['FrontEndId', 'Gain', 'SampleVar', 'Blankingstat']
        agcFormats = ['u1', 'i1', 'u1', 'u1']
        agcUnit = {'Gain' : 'dB', 'Blankingstat' : '%'}
        agcBadVals = {'Gain' : -128, 'SampleVar' : 0}
        agcFrontEndIds =  { 0: "GPSL1/E1",
                            1: "GLOL1",
                            2: "E6",
                            3: "GPSL2",
                            4: "GLOL2",
                            5: "L5/E5a/B2a",
                            6: "E5b/B2b",
                            7: "E5(a+b)",
                            8: "GPS/GLONASS/SBAS/Galileo L1",
                            9: "GPS/GLONASS L2",
                            10: "MSS/L-band",
                            11: "B1",
                            12: "B3",
                            13: "S-band",
                            14: "B3/E6"
                        }
        # Calculate start of AGC sub-blocks: 14 bytes header + sum of main block field lengths
        start = 14 + sum(self.fmtLengths[f] for f in formats)
        for _ in range(ret['N']):
            
            # Check if there is enough data for the next AGC block
            if start + ret['SBLength'] > len(message):
                break
            
            # Parse AGC block
            agcBlock = self.parseBlock(message, agcFields, agcFormats, start=start)
            agcBlock = self.badVal(agcBlock, agcBadVals)
            if self.units:
                agcBlock = self.unit(agcBlock, agcUnit)
            agcBlock['FrontEndId'] = agcFrontEndIds.get(agcBlock['FrontEndId'], 'Unknown')
            
            # Add AGC block to ret
            ret.setdefault('AgcBlocks',{})[agcBlock['FrontEndId']] = agcBlock
            
            # Update start for next AGC block
            start += ret['SBLength']

        return ret
    
    
    def PosLocal(self, message):
        fields = ['Mode', 'Error', 'Lat', 'Lon', 'Alt', 'Datum', 'Padding']
        formats = ['u1']*2 + ['f8']*3 + ['u1']
        units = {'Lat' : 'rad', 'Lon' : 'rad', 'Alt' : 'm'}
        badVals = {'Lat' : -2e10, 'Lon' : -2e10, 'Alt' : -2e10}
        
        # Setup bitfields
        bitmask = {'Mode' : lambda x: {'Mode': self.modes.get(self.fmts['bitint'](x,0,3), 'Not in parser'),
                   'SetPVTMode Static Auto and figuring position and still figuring position': bool(self.fmts['bitint'](x,6,7)),
                   '2D Mode': bool(self.fmts['bitint'](x,7,8))}}
        
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)

        ret = self.bitmask(ret, bitmask, bitmaskFunc = True)
        ret['Error'] = self.GNSSerrors.get(ret['Error'], 'Not in SBF parser PosLocal')
        return ret
    
    
    def PosCovGeodetic(self, message):
        fields = ['Mode', 'Error', 'Cov_latlat', 'Cov_lonlon', 'Cov_hgthgt', 'Cov_bb',
                  'Cov_latlon', 'Cov_lathgt', 'Cov_latb', 'Cov_lonhgt', 'Cov_lonb',
                  'Cov_hb']
        formats = ['u1']*2 + ['f4']*10
        units = {field:'m^2' for field in fields[2:]}
        badVals = {field:-2e10 for field in fields[2:]}
        
        # Setup bitfields
        bitmask = {'Mode' : lambda x: {'Mode': self.modes.get(self.fmts['bitint'](x,0,3), 'Not in parser'),
                   'SetPVTMode Static Auto and figuring position and still figuring position': bool(self.fmts['bitint'](x,6,7)),
                   '2D Mode': bool(self.fmts['bitint'](x,7,8))}}
            
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)

        ret = self.bitmask(ret, bitmask, bitmaskFunc = True)
        ret['Error'] = self.GNSSerrors.get(ret['Error'], 'Not in SBF parser PosLocal')
        return ret
    
    
    def BaseVectorGeod(self, message):
        # Params for main SBF block       
        fields = ['N', 'SBLength']
        formats = ['u1']*2
        
        # Params for block
        bFields = ['NrSV', 'Error', 'Mode', 'Misc', 'DeltaEast', 'DeltaNorth',
                    'DeltaUp', 'DeltaVe', 'DeltaVn', 'DeltaVu', 'Azimuth', 
                    'Elevation', 'ReferenceID', 'CorrAge', 'SignalInfo']
        bFormats = ['u1']*4 + ['f8']*3 + ['f4']*3 + ['u2', 'i2', 'u2', 'u2', 'u4']
        
        bScales = {'Azimuth': 0.01, 'Elevation': 0.01, 'CorrAge': 0.01}
        
        units = ['m','m','m','m/s','m/s','m/s','deg','deg']
        bUnits = {field:unit for field, unit in zip(bFields[4:12], units)}
        bUnits['CorrAge'] = 's'
        
        badVals = [-2e10]*6 + [65535,-32768]
        bBadVals = {field:bv for field, bv in zip(bFields[4:12], badVals)}
        bBadVals['CorrAge'] = 65535
        bBadVals['SignalInfo'] = 65535
        
        bBitmasks = {
                'Mode' : lambda x: {'Mode': self.modes.get(self.fmts['bitint'](x,0,3), 'Not in parser'),
                            'SetPVTMode Static Auto and figuring position and still figuring position': bool(self.fmts['bitint'](x,6,7)),
                            '2D Mode': bool(self.fmts['bitint'](x,7,8))},
                'Misc' : lambda x: {'Baseline is basestation ARP' : bool(self.fmts['bitint'](x,0,1)),
                                    'Phase center is compensated for at rover' : bool(self.fmts['bitint'](x,1,2))},
                
                'SignalInfo': lambda x: {'Signals with differential corrections': [f'{self.signals(index)[0]} {self.signals(index)[1]}' 
                                        for index, bit in enumerate(self.fmts['bitlist'](x,32)) if bit == '1']}
                }
    
        # Start ret as parsed main block
        ret = self.parseBlock(message, fields, formats)
        
        # Iteratively solve message                                                    # Byte at which to start the next sub-block
        start = 2
        for block in range(ret['N']):
            retBlock = self.parseBlock(message, bFields, bFormats, start = start)
            retBlock = self.bitmask(retBlock, bBitmasks, bitmaskFunc = True)
            retBlock = self.scale(retBlock, bScales)
            if self.units:
                retBlock = self.unit(retBlock, bUnits)
            ret[f'Block {block}'] = retBlock
            start += ret['SBLength'] 
        return ret
    
    
    def VelCovGeodetic(self, message):
        fields = ['Mode', 'Error', 'Cov_VnVn', 'Cov_VeVe', 'Cov_VuVu', 'Cov_DtDt',
                  'Cov_VnVe', 'Cov_VnVu', 'Cov_VnDt', 'Cov_VeVu', 'Cov_VeDt',
                  'Cov_VuDt']
        formats = ['u1']*2 + ['f4']*10
        units = {field:'m^2/s^2' for field in fields[2:]}
        badVals = {field:-2e10 for field in fields[2:]}
        
        # Setup bitfields
        bitmask = {'Mode' : lambda x: {'Mode': self.modes.get(self.fmts['bitint'](x,0,3), 'Not in parser'),
                   'SetPVTMode Static Auto and figuring position and still figuring position': bool(self.fmts['bitint'](x,6,7)),
                   '2D Mode': bool(self.fmts['bitint'](x,7,8))}}
            
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        ret = self.badVal(ret, badVals)
        if self.units:
            ret = self.unit(ret, units)

        ret = self.bitmask(ret, bitmask, bitmaskFunc = True)
        ret['Error'] = self.GNSSerrors.get(ret['Error'], 'Not in SBF parser PosLocal')
        return ret
    

    def BBSamples(self, message):
        fields = ['N', 'Info'] + [f'Reserved{_}' for _ in range(3)] 
        fields += ['SampleFreq', 'LOFreq']
        formats = ['u2'] + ['u1']*4 + ['u4']*2 + ['u2']
        units = {'SampleFreq': 'Hz', 'LOFreq': 'Hz'}
        
        # Create ret
        ret = self.parseBlock(message, fields, formats)
        if self.units:
            ret = self.unit(ret, units)
        
        # Parse out the samples of interest
        I, Q = [], []
        for start in range(28,28+ret['N']*2,2):
            sampleBytes = message[start:start+2]
            I.append(-1*sampleBytes[0])
            Q.append(-1*sampleBytes[1])
        ret['I'] = I
        ret['Q'] = Q
        return ret
    

    def ChannelStatus(self, message):
        # Setup convinient additional mappings 
        signals = {}                                                                                                     # Map {Constellation: {SVID : {SIGID : [trackingStatus, PVTStatus]}}}
        used = {}

        # Parse main block
        fields = ['N','SB1Length', 'SB2Length']
        formats = ['u1']*3
        ret = self.parseBlock(message, fields, formats)

        # Setup ChannelSatInfo sub-block information
        satFields = ['SVID', 'FreqNr', 'Reserved', 'Azimuth/RiseSet', 'HealthStatus',
                    'Elevation', 'N2', 'RxChannel']
        satFormats = ['u1']*2 + ['u2']*3 + ['i1', 'u1', 'u1']
        satUnits = {'Azimuth' : 'deg', 'Elevation' : 'deg'}
        riseSet = {0 : 'Sattelite setting', 1 : 'Sattelite rising', 3 : 'Eleveation rate unknown'}
        healthStatus = {0 : 'health unknown, or not applicable', 1 : 'healthy', 3 : 'unhealthy'}
        satBitmasks = {
            'Azimuth/RiseSet' : lambda x: {'Azimuth': self.fmts['bitint2'](x,16,0,8),
                                           'Rise/Set' : riseSet.get(self.fmts['bitint2'](x,16,14,15), 'Not in parser')},
            'HealthStatus' : lambda x : {'Healthy' : healthStatus.get(self.fmts['bitint2'](x,16,1,3), 'Not in parser')}
        }
        
        # Setup ChannelStateInfo
        chanFields = ['Antenna', 'Reserved', 'TrackingStatus', 'PVTStatus', 'PVTInfo']
        chanFormats = ['u1','u1','u2','u2', 'u2']                             
        signalOrder = {'GPS' : ['','','L1C', 'L5', 'L2C', 'L2P', 'L1P', 'L1CA'],                       # 'P1(Y)', 'P2(Y)' changed to L1P and L2P for self.signals compatibility
                'GLONASS' : ['','','','L3', 'L2CA', 'L2P', 'L1P', 'L1CA'],
                'Galileo' : ['','E5 AltBOC', 'E5b', 'E5a', 'E6', '', 'E1', ''],                    # E6BC changed to E6 for self.signals compatibility E5 AltBOC and E1BC adjusted too
                'SBAS' : ['','','','','','L5','L1CA'],                                                         # L1 changed to L1CA for self.signals compatibility
                'BeiDou' : ['','','B2b', 'B2a', 'B1C', 'B3I', 'B2I', 'B1I'],
                'QZSS' : ['L5S', 'L1CB', 'L1S', 'L1C', 'L6', 'L5', 'L2C', 'L1CA'],
                'NAVIC' : ['','','','','','','','L5']}
        ts = {0: 'idle or not applicable',
              1 : 'Search',
              2 : 'Sync',
              3:  'Tracking'}
        ps = {0: 'not used',
              1 : 'waiting for ephemeris',
              2 : 'used',
              3 : 'rejected'}     

        # Parse satBlocks
        start = 20
        for block in range(ret['N']):
            satBlock = self.parseBlock(message, satFields, satFormats, start = start)
            satBlock = self.bitmask(satBlock, satBitmasks, bitmaskFunc = True)
            if self.units:
                satBlock = self.unit(satBlock, satUnits)
            start += ret['SB1Length']

            # Record data appropriately
            constellation, svNumber = self.svids.get(satBlock['SVID'], ('Unknown', 0))
            satBlock['SVID'] = svNumber

            ret.setdefault(constellation, {})[svNumber] = satBlock
            signals.setdefault(constellation, {})[svNumber] = {}
            
            # Parse chanBlocks
            for subBlock in range(satBlock['N2']):
                chanBlock = self.parseBlock(message, chanFields, chanFormats, start = start)
                start += ret['SB2Length']

                pvtBitList = self.fmts['bitlist'](chanBlock['PVTStatus'], 16)
                trackingBitList = self.fmts['bitlist'](chanBlock['TrackingStatus'], 16)
                for index, signal in enumerate(signalOrder.get(constellation, [])):
                    if not signal:
                        continue
                    startBit = index*2
                    pvtStatus = ps.get(int(''.join((str(_) for _ in pvtBitList[startBit:startBit+2])), 2), 'not in channelStatus pvt dictionary')
                    trackingStatus = ts.get(int(''.join((str(_) for _ in trackingBitList[startBit:startBit+2])), 2), 'not in channelStatus tracking dictionary')
                    ret[constellation][svNumber][signal] = (pvtStatus, trackingStatus)
                    signals[constellation][svNumber][signal] = (pvtStatus, trackingStatus)
                    frequency = self.signalFrequency.get(f'{constellation} {signal}', f'{signal} not in self.signalFrequency ------------------------------------------')
                    used.setdefault(frequency, {})[f'{constellation} {svNumber}'] = bool(pvtStatus == 'used') 

        # Add signals to ret and return
        ret['Signals'] = signals
        ret['Used'] = used
        return ret
    

    ##################### Integration Functions #####################################################################
    def test(self, usb, ntries = 1):
        usb.baudrate = 115200
        bytesBin = b''
        for _ in range(ntries):
            for virtualPort in ['USB1', 'USB2']:
                for _ in range(3):
                    usb.write(f'esoc, {virtualPort}, ReceiverSetup \n'.encode('ASCII'))           # Write twice so ubx commands clear
                    time.sleep(0.125)
                bytesBin += usb.read(usb.inWaiting())
                bytesBin, messages = self.parseAll(bytesBin)
                for parsed in messages:
                    if message:= parsed.get('ReceiverSetup', False):
                        serialNumber = message.get('RxSerialNumber', False)
                        rxType = message.get('ProductName', 'X5')
                        return serialNumber, rxType, virtualPort 
        return False


    def configure(self, filePath):
        # Dont accept logically false filepaths
        if filePath == False:
            return
        
        # Ensure filepath exists
        if not os.path.exists(filePath):
            print(f'Filepath:{filePath} invalid. Please try again')
            return [b'']
        
        # Create return list and send compiled byte commands to return list
        with open(filePath, 'r') as file:                                    # Open configuration file
            ret = [line.encode('ASCII') for line in file.readlines()]        # Iterate lines (each line is an individual ubx command)
        return ret                                                           # Return the return list

    
if __name__ == '__main__':
    sbf = Sbf()

    # Test messages
    import serial
    conn = serial.Serial('COM6', baudrate=115200)
    serialNumber, rxType, virtualPort = sbf.test(conn, ntries = 1)
    print(f'Serial Number: {serialNumber} \nReceiver Type: {rxType} \nVirtual Port: {virtualPort}')
    conn.write(f'sso, Stream1, {virtualPort}, ReceiverStatus, OnChange \n'.encode('ASCII'))
    for _ in range(3):
        while not conn.inWaiting():
            time.sleep(0.1)
        bytesBin = conn.read(conn.inWaiting())
        bytesBin, messages = sbf.parseAll(bytesBin)
        for parsed in messages:
            print(parsed)
    conn.write('erst, hard, Config \n'.encode('ASCII'))
    conn.close()
    print('Done')