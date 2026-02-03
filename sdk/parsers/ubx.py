# -*- coding: utf-8 -*-
"""
UBX (Ublox Propriotary Receiver Format) parser, as defined by the 
Ublox F9 Interface Description
https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.32_InterfaceDescription_UBX-22008968.pdf

Created by and property of Uncompromising Sensor Support LLC
"""

# Import
import struct, os, re, time
import numpy as np
from collections import namedtuple, defaultdict, OrderedDict

# Import configuration information
# from . import ubxCfgInfo

class Ubx:
    """Ublox UBX protocol parsing class"""
        
    def __init__(self):
        """Creates dictionaries mapping the necessary labels, values...etc for parsing
        and creating messages in class."""
        
        # Define message header
        self.header = b'\xB5\x62'
        # self.cfgBytes = ubxCfgInfo.self.cfgBytes
        
        # Map types to bytes, used in creation of messages
        self.toBytes ={
            'U1': lambda x: x.to_bytes(1,'big', signed = False),                                 # Unsigned, 8 bit integer                                                    
            'I1': lambda x: (-1*x).to_bytes(1, 'big', signed = True),                            # Signed 8 bit integer, twos complement
            'X1' : lambda entered_bits: int(entered_bits,2).to_bytes(1,'big', signed = False),   # 8 bit bitlabel
            'U2': lambda x: x.to_bytes(2,'little', signed = False),                              # Unsigned little endian 16 bit integer
            'I2': lambda x: (-1*x).to_bytes(2, 'little', signed = True),                         # Signed little endian 16 bit integer, twos complement 
            'X2' : lambda x: int(''.join(map(str, x)).zfill(16),2).to_bytes(2, byteorder = 'little', signed = False),  # 16 bit little endian bitlabel 
            'U4' : lambda entered_bits: int(entered_bits,2).to_bytes(4,'little', signed = False),# Unsigned little endian 32 bit integer 
            'I4': lambda x: (-1*x).to_bytes(24, 'little', signed = True),                        # Signed little endian 32 bit integer, twos complement
            'X4' : lambda x: int(x,2).to_bytes(4,'little', signed = False),                      # 32 bit little endian bitlabel
            }
        
        # Map bytes to type, used in parsing of messages
        self.fromBytes = {
            'U1': lambda x: sum(struct.unpack('@B',x)),         # Unsigned, 8 bit integer  
            'U2' : lambda x: struct.unpack('<H',x)[0],          # Unsigned little endian 16 bit integer  
            'I1' : lambda x: -1*struct.unpack('<b',x)[0]*-1,    # Signed little endian 8 bit integer, twos complement 
            'I2' : lambda x: -1*struct.unpack('<h',x)[0]*-1,    # Signed little endian 16 bit integer, twos complement 
            'U4' : lambda x: struct.unpack('<I',x)[0],          # Unsigned little endian 32 bit integer
            'I4' : lambda x: -1*struct.unpack('<i',x)[0]*-1,    # Signed little endian 32 bit integer, twos complement             
            'X1' : lambda x: [int(_) for _ in format(int.from_bytes(x, byteorder = 'little'), '08b')],                              # 8 Bit bitlabel, little endian
            'X2' : lambda x: [int(_) for _ in format(x[0],'08b')[::-1] + format(x[1],'08b')[::-1]],                                 # 16 Bit bitlabel, little endian   
            'X4' : lambda x: [int(_) for _ in format(x[0],'08b')[::-1] + format(x[1],'08b')[::-1] + format(x[2],'08b')[::-1] + format(x[3],'08b')[::-1]],      # 32 Bit bitlabel, little endian +  format(x[3],'08b')[::-1]
            'R4' : lambda x: struct.unpack('<f',x)[0],
            'CH' : lambda x: x.decode('ASCII'),
            'R8' : lambda x: struct.unpack('<d',x)[0],
            'intFromBits' : lambda x: int(''.join(map(str, list(x)))[::-1], base = 2) }                                            # Iterates given list of 0s and 1s as little endian, converts to int self.fromBytes['X#'] retruns list of bytes like [0,0,1,1,....etc]
        
        # Map class and message ID bytes:[message name, parsing function] (.name and .parse)
        m = namedtuple('message', ['name', 'parse'])                       # NamedTuple message(m) allows attributes .name and .parse to be accessed below
        self.implemented = {b'\x05\x01' : m('ack-ack', self.ack_ack),      # ACK-ACK
                         b'\x05\x00' : m('ack-nack', self.ack_ack),        # ACK-NACK (cleverly uses same function as ack_ack)
                         b'\x01\x02' : m('nav_posllh', self.nav_posllh),   # NAV-POSLLH
                         b'\x06\x3E' : m('cfg_gnss', self.cfg_gnss),       # CFG-GNSS 
                         b'\x0a\x31' : m('mon_span', self.mon_span),       # MON-SPAN
                         b'\x01\x22' : m('nav_clock', self.nav_clock),     # NAV-CLOCK
                         b'\x01\x04' : m('nav_dop', self.nav_dop),         # NAV-DOP
                         b'\x01\x01' : m('nav_posecef', self.nav_posecef), # NAV-POSECEF
                         b'\x01\x07' : m('nav_pvt', self.nav_pvt),         # NAV-PVT
                         b'\x01\x35' : m('nav_sat', self.nav_sat),         # NAV-SAT
                         b'\x01\x43' : m('nav_sig', self.nav_sig),         # NAV-SIG
                         b'\x01\x24' : m('nav_timebds', self.nav_timebds), # NAV-TIMEBDS
                         b'\x01\x25' : m('nav_timegal', self.nav_timegal), # NAV-TIMEGAL
                         b'\x01\x23' : m('nav_timeglo', self.nav_timeglo), # NAV-TIMEGLO
                         b'\x01\x20' : m('nav_timegps', self.nav_timegps), # NAV-TIMEGPS
                         b'\x01\x27' : m('nav_timeqzss', self.nav_timeqzss), # NAV-TIMEQZSS
                         b'\x01\x21' : m('nav_timeutc', self.nav_timeutc), # NAV-TIMEUTC
                         b'\x0a\x28' : m('mon_gnss', self.mon_gnss),       # MON-GNSS
                         b'\x0a\x09' : m('mon_hw', self.mon_hw),           # MON-HW
                         b'\x0a\x0b' : m('mon_hw2', self.mon_hw2),         # MON-HW2
                         b'\x0a\x38' : m('mon_rf', self.mon_rf),           # MON-RF
                         b'\x0a\x04' : m('mon_ver', self.mon_ver),         # MON-VER
                         b'\x0a\x39' : m('mon_sys', self.mon_sys),         # MON-SYS
                         b'\x02\x15' : m('rxm_rawx', self.rxm_rawx),       # RXM-RAWX
                         b'\x06\x24' : m('cfg_nav5', self.cfg_nav5),       # CFG-NAV5
                         b'\x06>'   : m('cfg_nav5', self.cfg_nav5),        # CFG-NAV5 (experimentally found key)
                         b'\x01\x36' : m('nav_cov', self.nav_cov),         # NAV-COV
                         b'\x06\x08' : m('cfg_rate', self.cfg_rate),       # CFG-RATE
                         b'\x01\x13' : m('nav_hpposecef', self.nav_hpposecef), # NAV-HPPOSECEF
                         b'\x01\x14' : m('nav_hpposllh', self.nav_hpposllh),   # NAV-HPPOSLLH
                         b'\x01\x11' : m('nav_velecef', self.nav_velecef), # NAV-VELECEF
                         b'\x01\x12' : m('nav_velned', self.nav_velned),   # NAV-VELNED
                         b'\x06\x1B' : m('cfg_usb', self.cfg_usb),         # CFG-USB
                         b'\x06#': m('cfg_usb', self.cfg_usb),             # CFG-USB (experimentally found key)
                         b'\x02\x13' : m('rxm_sfrbx' , self.rxm_sfrbx),    # RXM-SFRBX        
                         b'\x27\x03' : m('ubx_sec' , self.ubx_sec),        # UBX-SEC
            }
        
        self.implemented = defaultdict(lambda: 'Not implemented in parser', self.implemented) # Recase self.implemented as default dict, returning 'Not implemented in parser' by default
        # The following messages were put out by an F9 when all ubx are enabled:
        # {"b'\\x01B'", "b'\\n\\x02'", "b'\\x014'", "b'\\x01<'", "b'\\r\\x01'", "b'\\n7'", "b'\\x01='", "b'\\n6'", "b'\\x019'", "b'\\x01;'", "b'\\x01&'", "b'\\x01\\x03'", "b'\\x01a'", "b'\\x012'", "b'\\x02\\x14'", "b'\\x01\\t'", "b'\\n\\x08'", "b'\\n\\x06'", "b'\\n\\x07'"}

        # GNSS Dictionaries, for use in further message functions
        self.gnssId = {
            0 : 'GPS',
            1 : 'SBAS',
            2 : 'GAL', 
            3 : 'BDS', 
            4 : 'IMES', 
            5 : 'QZSS', 
            6 : 'GLONASS', 
            7 : 'NAVIC'
            }
        self.gnssId = defaultdict(lambda: 'Key not in GNSS_id dictionary', self.gnssId)
        
        self.signalId = {
            'GPS' : {0:'L1CA', 3:'L2 CL', 4:'L2 CM', 6:'L5 I', 7: 'L5 Q'},
            'SBAS': {0:'L1CA'},
            'GAL' : {0:'E1C', 1:'E1B', 3:'E5 aI', 4:'E5 aQ', 5:'E5 bI', 6:'E5 bQ'},
            'BDS' : {0:'B1I D1', 1:'B1I D2', 2:'B2I D1', 3:'B2I D2', 5:'B1C', 7:'B2A'},
            'QZSS' : {0:'L1CA', 1:'L1s', 4:'L2 CM', 5:'L2 CL', 8:'L5 I', 9:'L5 Q'},
            'GLONASS' : {0:'L1 OF', 2:'L2 OF'},
            'NAVIC' : {0:'L5 A'}}
        self.signalId = {key:defaultdict(lambda : 'Key not in signal_id sub dictionary', value) for key, value in self.signalId.items()}
        self.signalId = defaultdict(lambda : 'Key not in signal_id dictionary', self.signalId)
        self.signalFreq = {'L1CA' : 'L1', 'E1C' : 'L1', 'E1B' : 'L1', 'L1s' : 'L1', 'L1 OF' : 'L1', 'B1I D1' : 'L1', 'B1I D2' : 'L1','B1C' : 'L1', 
                            'L2 CL' : 'L2', 'L2 CM' : 'L2', 'L2 OF' : 'L2',   'B2I D1' : 'L2', 'B2I D2' : 'L2', 'B2A' : 'L2',
                            'L5 I' : 'L5', 'L5 Q' : 'L5', 'E5 aI' : 'L5', 'E5 aQ' : 'L5', 'E5 bI' : 'L5', 'E5 bQ' : 'L5', 'L5 A' : 'L5'}
    
        self.gnssId = defaultdict(lambda: 'Key not in GNSS_id dictionary', self.gnssId)
    
        self.orbitSource = {0: 'No Information Availible',
                            1 : 'Ephemeris', 
                            2 : 'Almanac',
                            3 : 'AssistNow Offline', 
                            4 : 'Almanac and Ephemeris', 
                            5 : 'Other Orbit Information',
                            6 : 'Other Orbit Information',
                            7 : 'Other Orbit Information',}
        self.orbitSource = defaultdict(lambda: 'Key not in orbit_source dictionary', self.orbitSource)
        
        self.signalHealth = {0: 'Unknown Health',
                         1 : 'Healthy',
                         2 : 'Unhealthy'}
        self.signalHealth = defaultdict(lambda: 'Key not in signal_health dictionary', self.signalHealth)
        
        self.qualityIndicator = {0 : 'No Signal',
                             1 : 'Searching for Signal', 
                             2 : 'Signal Acquired', 
                             3 : 'Signal Detected but Unusable', 
                             4 : 'Code Locked and Time Synchronized', 
                             5 : 'Code and Carrier Locked, Time Synchronized',
                             6 : 'Code and Carrier Locked, Time Synchronized',
                             7 : 'Code and Carrier Locked, Time Synchronized'}
        self.qualityIndicator = defaultdict(lambda: 'Key not in signal_health dictionary', self.qualityIndicator)

        self.ionosphericModel = { 0 : 'No Model',
                                1 : 'Klobuchar model transmitted by GPS',
                                2 : 'SBAS model',
                                3 : 'Klobuchar model transmitted by Beidou',
                                8 : 'Ionospheric delay derived from dual frequency observations'
                                }
        self.ionosphericModel= defaultdict(lambda : 'Key not in ionospheric_model dictionary', self.ionosphericModel)
        
        self.correctionSource = {0 : 'No Corrections',
                                1 : 'SBAS Corrections',
                                2 : 'Beidou Corrections',
                                3 : 'RTCM2 Corrections',
                                4 : 'RTCM3 OSR Corrections', 
                                5 : 'RTCM3 SSR Corrections', 
                                6 : 'QZSS SLAS Corrections', 
                                7 : 'SPARTN Corrections', 
                                8 : 'CLAS Corrections'}
        self.correctionSource = defaultdict(lambda :'Key not in correcion_source dictionary', self.correctionSource)

        self.sigCfgMask = {'GPS L1CA': ('GPS', b'\x01'),
                    'GPS L2C': ('GPS', b'\x10'),
                    'GPS L5': ('GPS', b' '),
                    'SBAS L1CA': ('SBAS', b'\x01'),
                    'Galileo E1': ('GAL', b'\x01'),
                    'Galileo E5a': ('GAL', b'\x10'),
                    'Galileo E5b': ('GAL', b' '),
                    'Beidou B1I': ('BDS', b'\x01'),
                    'Beidou B2I': ('BDS', b'\x10'),
                    'Beidou B2A': ('BDS', b'\x80'),
                    'IMES L1': ('IMES', b'\x01'),
                    'QZSS L1CA': ('QZSS', b'\x01'),
                    'QZSS L1S': ('QZSS', b'\x04'),
                    'QZSS L2C': ('QZSS', b'\x10'),
                    'QZSS L5': ('QZSS', b' '),
                    'GLONASS L1': ('GLONASS', b'\x01'),
                    'GLONASS L2': ('GLONASS', b'\x10')}

    def parse(self,message):
        """Takes a message (bytes) and return a dictionary mapping {messageName : {parsedMessageData}}"""
        
        # Split message given into header, messageType (classId and message_ID), length and payload bytes
        header, messageType, length, payload = tuple(message[start:stop] for start, stop in zip([0,2,4,6],[2,4,6,-2]))
        
        # Setup parsed message return (Ordered Dict), and initialize basic values
        data = {'messageClassAndId': str(messageType), 'length': self.fromBytes['U2'](length)}

        # Check checksum, returning failed message parse as appropriate
        if self.checksum(message[2:-2]) != message[-2:]:         
            data['raw'] =  str(message)
            return {"failedChecksum" : data}
               
        # Handle messages not in parser
        if messageType not in self.implemented.keys():   # Test if messageType (classId and message_ID bytes) is in self.implemented.keys()
            data['raw'] =  str(message)
            return {"unknown" : data}
        
        # Call individual parsing function and apply all returned dicitonary key:value pairs to ret
        parser = self.implemented[messageType].parse                          # Get the appropriate message parsing function
        return {self.implemented[messageType].name : data | parser(payload)}  # Return parsed message
  

    def parseAll(self, bytesBin):
        """Takes bytes, returns a tuple of bytes that were not used in parsed messages
        and a list of parsed messages (dict)"""

        messages = []
        while ubxMessage:= re.search(b'\xB5\x62',bytesBin):
            start = ubxMessage.start()
            if len(bytesBin[start:]) > 8: 
                length = struct.unpack('<H', bytesBin[start+4:start+6])[0] + 8 # Get the message length. 2 sync, 2 class/message id, 2 length and 2 checksum bytes + payload bytes
                end = start + length                                           # Calculate the index of the last byte of the message, based on message length (above)
                if len(bytesBin) >= end:                              # Ensure bytesBin has entire message
                    messages.append(self.parse(bytesBin[start:end]))
                    bytesBin = bytesBin[:start] + bytesBin[end:]  
                else:
                    break
            else:
                break

        return bytesBin, messages
        
    
    def checksum(self,checksum_range):
        """Returns ubx checksum calculated over checksumRange bytes."""
        ca,cb = 0,0                                                       # Initialize checksum a and b values
        for _ in checksum_range:                                          # Iterate bytes of checksumRange (portion of ubx message used to calculate the checksum)
            ca += _                                                       # Add each byte to ca as it is iterated 
            cb += ca                                                      # Add ca to cb for each byte (both ca and cb formulas given in documentation) 
        return self.toBytes['U1'](ca&255) + self.toBytes['U1'](cb&255)    # Format ca and cb as unsigned, 8 bit integers masked by 255 (all 1s in bytecode) 
    

    def awknowledge(self,message):
        """Receive message and return and awknowledged message"""
        header, messageType, length, payload = tuple(message[start:stop] for start, stop in zip([0,2,4,6],[2,4,6,-2]))  # Split message given into header, messageType (classId and message_ID), length and payload bytes
        ackedMessage = b'\x05\x01'
        length =  self.toBytes['U2'](2)
        checksum = self.checksum(ackedMessage + length + messageType)
        return self.header + ackedMessage + length + messageType + checksum
        

    #################################### Individual Message Parsers ####################################
    def ack_ack(self,payload):
        """Used for both ack and nack messages, returns labels and messageName"""
        return {'clsId' : str(payload[0:1]), 'msgId' : str(payload[1:]), 'messageName' : self.implemented.get(payload, 'notInParser')}


    def nav_posllh(self,payload):
        """Latitude, Longitude and Altitude ubx message parser"""
        label_names = ['iTOW (ms)', 'lon (deg)', 'lat (deg)', 'height (m)', 'hMSL (m)', 'hAcc (m)', 'vAcc (m)']
        starts = list(range(0,25,4))
        stops = list(range(4,29,4))                                                                                                       
        fmts = [self.fromBytes['U4']] + [self.fromBytes['I4']]*4 + [self.fromBytes['U4']]*2                  
        scales = [1,1e-7,1e-7] + [1e-3]*4                                               
        return {name:fmt(payload[start:stop])*scale  for start,stop,name,fmt,scale in zip(starts,stops,label_names,fmts,scales)}                    
       
    
    def mon_span(self,payload):
        """Provides span information, used on the F9 and above to create span plots. 
        Adds rfBlock# to the dictionary, mapping to a subdictionary of spectrum information."""
        data = {label: self.fromBytes['U1'](message) for label, message in zip(('version', 'numRfBlocks'), (payload[:1], payload[1:2]))}

        # Iterate the RF blocks portion of the message (spans reported by message)
        for rfBlock in range(data.get('numRfBlocks', 0)):                                       
            start, stop = (4+rfBlock*272,260+rfBlock*272)                            
            starts, stops = [start, stop, stop+4, stop+8, stop+12, stop+13], [stop, stop+4, stop+8, stop+12, stop+13, stop+16]
            labels = ['spectrum', 'span', 'res', 'center', 'pga']
            fmts = [lambda x: list(x)] + [self.fromBytes['U4']]*3 + [self.fromBytes['U1']]*4  
            span = {label:fmt(payload[start:stop]) for label, fmt, start, stop in zip(labels, fmts, starts, stops)}
            data[f'rfBlock{rfBlock}'] = span                         
        return data
        
     
    def cfg_gnss(self,payload):
        """Parses a configuration response/broadcast message"""

        # Handle static portion of the message
        messageNames = ['msgVer', 'numTrkChHw', 'numTrkChUse', 'numConfigBlocks']   
        data = {messageName: payload[index] for index, messageName in enumerate(messageNames)}

        # Handle dynamic portion
        numConfigBlocks = data['numConfigBlocks']                      
        gnssIds = [payload[4+configBlock*8] for configBlock in range(numConfigBlocks)] 
        gnssLabels = ['resTrkCh', 'maxTrkCh']
        for repeat,gnssId in enumerate(gnssIds):                    
            gnss = self.gnssId.get(gnssId, 'gnssIdNotFound')   
            constData = {}                                 
            for index, message in zip(range(5,7), gnssLabels): 
                start = repeat*8+index
                stop = start+1                                                                                     
                constData[message] =  self.fromBytes['U1'](payload[start:stop])            
    
            # Handle bitlabel mask
            bitlabelmask = payload[repeat*8+8:repeat*8+12]                     # Grab the bitlabel mask encoding signal enable values
            bits = self.fromBytes['X4'](bitlabelmask)                          # Format bitlabel mask appropriately
     
            # Pull signal configuration mask and apply 
            sigCfgBits = payload[(repeat+1)*8:(repeat+1)*8+1]  
            signals = [key for key, value in self.sigCfgMask.items() if re.search(gnss, value[0]) and re.search(sigCfgBits, value[1])]  
            if  len(signals) > 0:                           
                constData[signals[0]] = bool(bits[0])  
            else:
                constData[f'{gnss}UnidentifiedSignal'] = f'{sigCfgBits}'

            # Add constData to main data returned
            data[gnss] = constData

        return data
    

    def nav_clock(self, payload):
        """Returns navigation clock solution, bias, drift...etc"""
        starts = list(range(0,17,4))
        stops = list(range(0,22,4))[1:]
        fmts = [self.fromBytes['U4']]+ [self.fromBytes['I4']]*2+ [self.fromBytes['U4']]*2
        labels = ['iTOW (ms)', 'clkB (ns)', 'clkD (ns/s)', 'tAcc (ns)', 'fAcc (ps/s)']
        return {label:fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
    
        
    def nav_dop(self,payload):
        """Return DOP values for navigation solution"""
        starts = [0,4] + list(range(6,17,2))
        stops = starts[1:] + [18]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['U2']]*7
        labels = ['iTOW (ms)', 'gDOP', 'pDOP', 'tDOP', 'vDOP','hDOP', 'nDOP', 'eDOP']
        scales = [1] + [0.01]*7
        return {label:fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}
   
    
    def nav_posecef(self,payload):
        """Return position ECEF for navigation solution."""
        starts = list(range(0,17,4))
        stops = starts[1:] + [20]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['I4']]*3 + [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'ecefX (m)','ecefY (m)','ecefZ (m)','pAcc (m)']
        scales = [1] + [0.01]*4
        return {label:fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}
    

    def nav_pvt(self,payload):
        """Return PVT solution (and metrics) for solution."""

        starts = [0,4] + list(range(6,13)) + [16,20,21,22,23] + list(range(24,73,4))  + [76,78,80,84,88,90]
        stops = starts[1:] + [92]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['U2']] + [self.fromBytes['U1']]*5 + [self.fromBytes['X1']]  + [self.fromBytes['U4']]
        fmts +=  [self.fromBytes['I4']] +  [self.fromBytes['U1']] + [self.fromBytes['X1']]*2 + [self.fromBytes['U1']] + [self.fromBytes['I4']]*4
        fmts += [self.fromBytes['U4']]*2 + [self.fromBytes['I4']]*5 + [self.fromBytes['U4']]*2 + [self.fromBytes['U2']] + [self.fromBytes['X2']]
        fmts += [self.fromBytes['U4']] + [self.fromBytes['I4']] + [self.fromBytes['I2']] + [self.fromBytes['U2']]
        labels = ['iTOW (ms)', 'year', 'month', 'day', 'hour', 'min', 'sec', 'valid',
                  'tAcc (ns)', 'nano (ns)', 'fixType', 'flags', 'flags2', 'numSv', 'lon (deg)', 'lat (deg)', 'height (m)', 
                  'hMSL (m)', 'hAcc (m)', 'vAcc (m)', 'velN(m/s)','velE (m/s)','velD (m/s)', 'gSpeed (m/s)',
                  'headMot (deg)', 'sAcc (m/s)', 'headAcc (deg)', 'pDOP', 'flags3', 'reserved', 'headVeh(deg)','magDec(deg)', 
                  'magAcc (deg)']
        scales = [1]*14 + [1e-7,1e-7] + [1e-3]*8 + [1e-5, 1e-3, 1e-5, 1e-2] + [1,1,1e-5,1e-2,1e-2]                                    # 8 or nine idk
        data = {label:fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}

        # Handle validity flags
        labels = ['validDate', 'validTime', 'fullyResolved', 'validMag']
        data['validFlags'] = {label:bool(bit) for label, bit in zip(labels,reversed(data['valid']))}
        
        # Handle fix type
        fixType = { 0: 'No Fix',
                    1 : 'Dead Reckoning Only', 
                    2 : '2D Fix',
                    3 : '3D Fix', 
                    4 : 'GNSS + Dead Reckoning Combined',
                    5 : 'Time Fix Only'
            }
        data['fixType'] = fixType.get(data['fixType'], f'unknownFixType{data["fixType"]}')
        
        # Handle Fix Status Flags
        labels = ['gnssFixOk', 'diffSoln', 'psmState']
        psm = { 0 : 'Power Saving Mode Not Active',
               1 : 'Power Saving Mode Enabled (Intermediate State Before Acquisition State)',
               2 : 'Acquisition',
               3 : 'Tracking',
               4 : 'Power Optimized Tracking'
            }
        fmts = [lambda x: bool(x)]*2 + [lambda x: psm.get(x, 'unknownPSM')] 
        data['fixFlags'] = {label:fmt(bit) for label, bit, fmt in zip(labels,reversed(data['flags']), fmts)}

        # Handle Confirmation Flags
        labels = ['UTC Date and Time Validity Confirmation Information Availible', 
                  'UTC Date Validity Confirmed', 'UTC Time Validity Confirmed']
        data['utcFlags'] = {label:bool(bit) for label, bit in zip(labels, reversed(data['flags2'][5:8]))}

        # Handle Position Flags
        lastCorrectionAge = {0 : 'Unavailible',
                            1 : '(0-1) Seconds', 
                            2 : '[1-2) Seconds',
                            3 : '[2-5) Seconds',
                            4 : '[5-10) Seconds',
                            5 : '[10-15) Seconds',
                            6 : '[15-20) Seconds',
                            7 : '[20-30) Seconds',
                            8 : '[30-45) Seconds',
                            9 : '[45-60) Seconds',
                            10 : '[60-90) Seconds',
                            11 : '[90-120) Seconds',
                            }
        data['invalidLlh'] =  False if len(data['flags3']) == 0 else bool(data['flags3'][0])
        correctionAge = int(''.join(map(str, data['flags3'][1:])), base = 2)
        if correctionAge >= 12: 
            data['lastCorrectionAge'] = '>= 120 Seconds'
        else:
            data['lastCorrectionAge'] = lastCorrectionAge[correctionAge]
        return data
        
    
    def nav_sat(self,payload):
        """Return SV information, psuedorange residuals"""
        
        # Setup convinient additional mappings 
        cnos, prrs, quality = {}, {}, {}                                                                  # Map {constellation: {svid : cno}} and {constellation : {svid : prr}} and one for quality (usage)

        # Handle static portion of the message
        starts = [0,4,5]
        stops = starts[1:] + [6]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['U1']]*2 
        labels = ['iTOW (ms)', 'version', 'numSvs']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
       
        # Handle dynamic portion of the message
        for numSv in range(data.get('numSvs',0)):
            
            # Setup const, svid and get constData subdictionary
            const = self.gnssId.get(self.fromBytes['U1'](payload[8 + numSv*12:9 + numSv*12]), 'Unknown Constellation')
            svid = self.fromBytes['U1'](payload[9+numSv*12:10+numSv*12])
            constData = data.setdefault(const, {})
            cnoData = cnos.setdefault(const, {})
            prrData = prrs.setdefault(const, {})
            qualityData = quality.setdefault(const, {})

            # Handle SV info
            starts = [index + numSv*12 for index in (10,11,12,14,16)]
            stops = starts[1:] + [20 + numSv*12]
            fmts = [self.fromBytes['U1']] + [self.fromBytes['I1']] + [self.fromBytes['I2']]*2 + [self.fromBytes['X4']]
            labels = ['cno (dBHz)', 'elev (deg)', 'azim (deg)', 'prRes (m)', 'flags']
            svData = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
            svData['prRes (m)'] = 0.1*svData['prRes (m)'] 

            # Handle bitmask flags
            starts = [0,3,4,6,7,8] + list(range(11,15)) + list(range(16,24))
            stops = [3,4,6,7,8,11] + list(range(12,16)) + list(range(17,25))
            _int, _bool = self.fromBytes['intFromBits'], lambda x: bool(x[0])                                                   # Quick conversions for bool/int flags, as needed
            fmts = [_int,_bool,_int,_bool,_bool,_int] + [_bool]*12
            labels = ['qualityInd', 'svUsed', 'health', 'diffCorr','smoothed', 'orbitSource', 'ephAvail', 'almAvail',
                      'anoAvil', 'aopAvail', 'sbasCorrUsed', 'rtcmCorrUsed', 'slasCorrUsed', 'prCorrUsed',
                      'crCorrUsed', 'doCorrUsed', 'clasCorrUsed']
            svData['flagsValues'] = {label : fmt(svData['flags'][start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
            
            # Overwrite coded values
            svData['health'] = self.signalHealth.get(svData['flagsValues']['health'], 'Unknown Health Values')
            svData['orbitSource'] = self.orbitSource.get(svData['flagsValues']['orbitSource'], 'Unknown Orbit Source')
            svData['qualityInd'] = self.qualityIndicator.get(svData['flagsValues']['qualityInd'], 'Unknown Quality Indicator')

              # Update respective dictionaries
            cnoData[svid] = svData['cno (dBHz)']
            prrData[svid] = svData['prRes (m)']
            quality[svid] = svData['qualityInd']
            constData[svid] = svData
        
        # Add convenient mapping and return data
        data['cnos'] = cnos
        data['prrs'] = prrs
        data['quality'] = quality
        return data
    

    def nav_sig(self,payload):
        """Returns singal metric information - only availible on F9 and newer"""
        
        # Setup convinient additional mappings 
        cnos, prrs, quality = {},{},{}                                                                  # Map {constellation: {svid : {sigid: cno}}} and {constellation: {svid : {sigid: prr}}} and one for quality (usage)

        # Handle static portion of the message
        starts = [0,4,5]
        stops = starts[1:] + [6]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['U1']]*2
        labels = ['iTOW (ms)', 'version', 'numSigs']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
        # Handle dynamic portion of the message
        for numSig in range(data.get('numSigs',0)):
            
            # Get gnss, svid and sigid
            const = self.gnssId.get(self.fromBytes['U1'](payload[8 + numSig*16:9 + numSig*16]), 'Unknown Constellation')
            svid = self.fromBytes['U1'](payload[9+numSig*16:10+numSig*16])
            sigid = self.signalId.get(const, {}).get(self.fromBytes['U1'](payload[10+numSig*16:11+numSig*16]), 'Unknown Signal ID')
            
            # Get constData, svData 
            constData = data.setdefault(const, {})
            svData = constData.setdefault(svid, {})

            # Create signalData
            starts = [index + numSig*16 for index in [11, 12, 14, 15, 16, 17, 18]]
            stops = starts[1:] + [21 + numSig*16]
            fmts = [self.fromBytes['U1']] + [self.fromBytes['I2']] + [self.fromBytes['U1']]*4 + [self.fromBytes['X2']] # [self.fromBytes['X2']] 
            labels = ['freqId', 'prRes (m)', 'cno (dBHz)', 'qualityInd', 'corrSource','ionoModel', 'sigFlags']
            signalData = svData.setdefault(sigid, {label: fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)})
            signalData['prRes (m)'] = signalData['prRes (m)'] * 0.1

            # Add signalData to cnos
            cnos.setdefault(const, {}).setdefault(svid, {}).setdefault(sigid, signalData['cno (dBHz)'])
            prrs.setdefault(const, {}).setdefault(svid, {}).setdefault(sigid, signalData['prRes (m)'])
            
            # Handle SV flags
            starts = [0] + list(range(2,9))
            stops = [2] + list(range(3,10))
            _int, _bool = self.fromBytes['intFromBits'], lambda x: bool(x[0])
            fmts = [_int] +  [_bool]*7
            labels = ['health', 'prSmoothed', 'prUsed', 'crUsed','doUsed', 'crCorrUsed', 'doCorrUsed']
            signalDataFlags = {label : fmt(signalData['sigFlags'][start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

            # Overwrite coded values
            signalDataFlags['health'] = self.signalHealth.get(signalDataFlags['health'], 'Unknown Health Values')
            signalData['qualityInd'] = self.qualityIndicator.get(signalData['qualityInd'], 'Unknown Quality Indicator')
            quality.setdefault(const, {}).setdefault(svid, {}).setdefault(sigid, signalData['qualityInd'])                               
            signalData['ionoModel'] = self.ionosphericModel.get(signalData['ionoModel'], 'Unknown Ionospheric Model')
            signalData['corrSource'] = self.correctionSource.get(signalData['corrSource'], 'Unknown Correction Source')
            signalData['sigFlagsValues'] = signalDataFlags

        # Add convenient mapping to data and return
        data['cnos'] = cnos
        data['prrs'] = prrs
        data['quality'] = quality
        return data
    
    
    def nav_timebds(self,payload):
        """Periodic and polled, returns Beidou time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,12,14,15,16]
        stops = starts[1:] + [21]
        fmts = [self.fromBytes['U4']]*2 + [self.fromBytes['I4']] + [self.fromBytes['I2']]+ [self.fromBytes['I1']] + [self.fromBytes['X1']] +  [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'SOW (s)', 'fSOW (ns)', 'week', 'leapS (s)', 'valid', 'tAcc (ns)']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
       
        # Handle Flags
        starts = [0,1,2]
        stops = [1,2,3]
        labels = ['sowValid', 'weekValid', 'leapSValid']
        fmts = [bool]*3
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        return data
    

    def nav_timegal(self,payload):
        """Periodic and polled, returns Galileo time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,12,14,15,16]
        stops = starts[1:] + [21]
        fmts = [self.fromBytes['U4']]*2 + [self.fromBytes['I4']] + [self.fromBytes['I2']]+ [self.fromBytes['I1']] + [self.fromBytes['X1']] +  [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'galTow (s)', 'fGalTow (ns)', 'galWno', 'leapS (s)', 'valid', 'tAcc (ns)']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
        # Handle Flags
        starts = [0,1,2]
        stops = [1,2,3]
        labels = ['galTowValid', 'galWnoValid', 'leapSValid']
        fmts = [bool]*3
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        return data
    

    def nav_timeglo(self,payload):
        """Periodic and polled, returns GLONASS time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,12,14,15,16]
        stops = starts[1:] + [21]
        fmts = [self.fromBytes['U4']]*2 + [self.fromBytes['I4']] + [self.fromBytes['I2']]+ [self.fromBytes['I1']] + [self.fromBytes['X1']] +  [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'TOD (s)', 'fTOD (ns)','Nt (days)','N4', 'valid', 'tAcc (ns)']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
    
        # Handle Flags
        starts = [0,1]
        stops = [1,2]
        labels = ['todValid', 'dateValid']
        fmts = [bool]*2
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        
        # Overwrite four year rollover
        data['N4'] = 1996 + 4*(data['N4']-1) -1 # By definition, overwrite with actual four year interval number
        return data
    

    def nav_timegps(self,payload):
        """Periodic and polled, returns GPS time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,10,11,12]
        stops = starts[1:] + [17]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['I4']] + [self.fromBytes['I2']] + [self.fromBytes['I1']] + [self.fromBytes['X1']] +  [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'fTOW (ns)', 'week', 'leapS (s)', 'valid', 'tAcc (ns)']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
  
        # Handle Flags
        starts = [0,1,2]
        stops = [1,2,3]
        labels = ['towValid', 'weekValid', 'leapSValid']
        fmts = [bool]*3
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        return data
    
    def nav_timeqzss(self,payload):
        """Periodic and polled, returns QZSS time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,12,14,15,16]
        stops = starts[1:] + [21]
        fmts = [self.fromBytes['U4']]*2 + [self.fromBytes['I4']] + [self.fromBytes['I2']]+ [self.fromBytes['I1']] + [self.fromBytes['X1']] +  [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'qzssTow (s)', 'fQzssTow (ns)', 'qzssWno', 'leapS (s)', 'valid', 'tAcc (ns)']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
        # Handle Flags
        starts = [0,1,2]
        stops = [1,2,3]
        labels = ['qzssTowBValid', 'qzssWnoValid', 'leapSValid']
        fmts = [bool]*3
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        return data
    
    def nav_timeutc(self,payload):
        """Periodic and polled, returns QZSS time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = [0,4,8,12,14,15,16,17,18,19]
        stops = starts[1:] + [21]
        fmts = [self.fromBytes['U4']]*2 + [self.fromBytes['I4']] + [self.fromBytes['U2']]+ [self.fromBytes['U1']]*5 + [self.fromBytes['X1']]
        labels = ['iTOW (ms)', 'tAcc (ns)', 'nano (ns)', 'year', 'month', 'day', 'hour', 'min', 'sec', 'valid']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

        # Handle Flags
        starts = [0,1,2,4]
        stops = [1,2,3,7]
        labels = ['validTow', 'validWKN', 'validUTC', 'utcStandard']
        fmts = [bool]*3 + [self.fromBytes['intFromBits']]
        for start, stop, fmt,label in zip(starts,stops,fmts,labels):
            data[label] = fmt(data['valid'][start:stop])
        return data
    

    def mon_gnss(self,payload):
        """Periodic and polled, returns GPS time of the most recent navigation solution, including validity flags and an accuracy estimate."""
        starts = list(range(5))
        stops = starts[1:] + [5]
        fmts = [self.fromBytes['U1']] + [self.fromBytes['X1']]*3 
        labels = ['version', 'supported','defaultGnss' ,'enabled', 'simultaneous']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
 
        # Handle Flags
        starts = [0,1,2,3]
        stops = [1,2,3,4,5]
        labelsList = [['GPSSup', 'GlonassSup', 'BeidouSup', 'GalileoSup'],
                        ['GPSDef', 'GlonassDef', 'BeiDouDef', 'GalileoDef'],
                        ['GPSEna,' 'GlonassEna', 'BeiDouEna', 'GalileoEna']]
        flags = ['supportedValues', 'defaultGnssValues', 'enabledValues']
        fmts = [bool]*4
        for subLabels, flag, mainLabel in zip(labelsList, flags, labels[1:4]):
            bitmask = data[mainLabel]
            data[flag] = {label : fmt(bitmask[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,subLabels)} 
        return data
     

    def mon_hw(self,payload):
        """Periodic and polled, returns hardware status indicators"""
        starts = [0,4,8,12,16,18,20,21,22,23,24,28,45,46,48,52,56]
        stops = starts[1:] + [61]
        fmts = [self.fromBytes['X4']]*4 + [self.fromBytes['U2']]*2 + [self.fromBytes['U1']]*2 + [self.fromBytes['X1']] + [self.fromBytes['U1']]
        fmts += [self.fromBytes['X4']] + [lambda x:[_ for _ in x]] + [self.fromBytes['U1'], self.fromBytes['U2']] + [self.fromBytes['X4']]*3
        labels = ['pinSel', 'pinBank', 'pinDir', 'pinVal', 'noisePerMs', 'agcCnt', 'aStatus', 'aPower', 'flags', 'reserved0', 'usedMask', 'VP'
                  'cwSuppersion', 'reserved1', 'pinIrq', 'pullJ', 'pullL']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
     
        antennaState = { 0 : 'Initializing',
                        1 : 'Unknown',
                        2 : 'Functioning',
                        3 : 'Short Circuit',
                        4 : 'Open Circuit'}
        data['aStatus'] = antennaState.get(data['aStatus'], f'antennaState: {data["aStatus"]} not in parser')
        antennaPower = {0 :'OFF',
                        1 : 'ON',
                        2 : 'Unknown'}
        data['aPower'] = antennaPower.get(data['aPower'], f'antennaPower: {data["aPower"]} not in parser')
        
        # Overwrite flags
        starts = [0,1,2,4]
        stops = starts[1:] + [5]
        fmts = [bool]*2 + [self.fromBytes['intFromBits']] + [bool]
        labels = ['rtcCalib', 'safeBoot', 'jammingState', 'xtalAbsent']
        data['flagsValues'] = {label : fmt(data['flags'][start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
       
        jammingState = {0 : 'Unknown or Feature Disabled',
                         1 : 'No Significant Jamming',
                         2 : 'Jamming Detected, Fix Maintained',
                         3 : 'Jamming Detected. Fix Lost'
            }
        data['flagsValues']['jammingState'] = jammingState.get(data['flagsValues']['jammingState'], f'jammingState: {data["flagsValues"]["jammingState"]} not in parser')

        return data
    

    def mon_hw2(self,payload):
        """Periodic and polled, returns additional hardware status indicators"""
        starts = list(range(5)) 
        stops = starts[1:] + [5]
        fmts = [self.fromBytes['I1'],self.fromBytes['U1'],self.fromBytes['I1']] + [self.fromBytes['U1']]*5 + [self.fromBytes['U4']] + [self.fromBytes['U1']]*8 + [self.fromBytes['U4']] + [self.fromBytes['U1']]*4
        labels = ['ofsI', 'magI', 'ofsQ', 'magQ', 'cfgSource']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        data['lowLevCfg'] = self.fromBytes['U4'](payload[8:12])
        data['postStatus'] = self.fromBytes['U4'](payload[20:24])
        return data

    
    def mon_rf(self,payload):
        """Periodic and polled, returns information about RF blocks (as many as supported by the receiver"""
        
         # Handle Static Portion
        data = {'version' : self.fromBytes['U1'](payload[0:1]),
                'nBlocks' : self.fromBytes['U1'](payload[1:2])}
      
        # Handle dynamic portion
        blockId = {0 : 'L1', 1: 'L2 or L5'}
        for block in range(data['nBlocks']):
            starts = list(range(4,9)) + [12,16,18] + list(range(20,24))
            starts = [_ + block*24 for _ in starts]
            stops = starts[1:] + [24 + (block*24)]
            fmts = [self.fromBytes['U1'],self.fromBytes['X1']] + [self.fromBytes['U1']]*2 + [self.fromBytes['U4']]*2 + [self.fromBytes['U2']]*2 + [self.fromBytes['U1'], self.fromBytes['I1'],self.fromBytes['U1'], self.fromBytes['I1'],self.fromBytes['U1']]
            labels = ['blockId', 'flags', 'antStatus', 'antPower','postStatus', 'reserved1', 'noisePerMS', 'agcCnt', 'cwSuppression', 'ofsI', 'magI', 'ofsQ', 'magQ']
            blockData = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

            # Overwrite Values
            antennaState = { 0 : 'Initializing',
                            1 : 'Unknown',
                            2 : 'Functioning',
                            3 : 'Short Circuit',
                            4 : 'Open Circuit'}
            blockData['antStatus'] = antennaState.get(blockData['antStatus'], f'antennaState: {blockData["antStatus"]} not in parser')

            antennaPower = {0 :'OFF',
                            1 : 'ON',
                            2 : 'Unknown'}
            blockData['antPower'] = antennaPower.get(blockData['antPower'], f'antennaPower: {blockData["antPower"]} not in parser')
            
            # Overwrite Flags
            jammingState = {0 : 'Unknown or Feature Disabled',
                             1 : 'No Significant Jamming',
                             2 : 'Jamming Detected, Fix Maintained',
                             3 : 'Jamming Detected. Fix Lost'}
            blockData['jammingState'] =  jammingState.get(blockData['flags'][0], f'jammingState: {blockData["flags"][0]} not in parser')
            
            data[blockId.get(blockData['blockId'], f'blockId: {blockData["blockId"]} not in parser')] = blockData                          # Add block to data

        return data
    

    def mon_sys(self,payload):
        """Polled message, returns information about receiver and software versions."""
        starts = list(range(0,9)) + list(range(12,19,2))
        stops = starts[1:] + [19]
        fmts = [self.fromBytes['U1']]*8 + [self.fromBytes['U4']] + [self.fromBytes['U2']]*3 + [self.fromBytes['I1']] + [self.fromBytes['U1']]*5
        labels = ['msgVer', 'bootType', 'cpuLoad', 'cpuLoadMax', 'memUsage', 'memUsageMax', 'ioUsage', 'ioUsageMax',
                  'runTime', 'noticeCount', 'warnCount', 'errorCount' 'tempValue']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
        # Overwrite Values
        bootType = { 0 : 'Unknown',
                        1 : 'Cold Start',
                        2 : 'Watchdog',
                        3 : 'Hardware reset',
                        4 : 'Hardware backup',
                        5 : 'Software backup',
                        6 : 'Software reset',
                        7 : 'VIO fail',
                        8 : 'VDD_X fail',
                        9 : 'VDD_RF fail',
                        10 : 'V_CORE_HIGH fail'}
        data['bootType'] = bootType.get(data['bootType'], f'bootType: {data["bootType"]} not in parser')
        return data
    
    
    def mon_ver(self,payload):
        """Polled message, returns information about receiver and software versions."""
        nRepeats = int((len(payload) - 40)/30)
        data = {'swVersion' : payload[0:30].decode('iso-8859-1'),
               'hwVersion' : payload[30:40].decode('iso-8859-1')}
        for repeat in range(nRepeats):
            data[f'extension{repeat}'] = payload[40+repeat*30:71+repeat*30].decode('ASCII')
        return data
    

    def rxm_rawx(self, payload):
        """ Periodic and Polled. Returns multi GNSS raw measurements.
        This message contains the information needed to be able to generate a RINEX 3 multi-GNSS observation file."""
  
        # Parse static portion of message
        starts = [0,8,10,11,12, 13]
        stops = starts[1:] + [14]
        labels = ['rcvTow (s)','week', 'leapS (s)', 'numMeas', 'recStat', 'version']
        fmts = [self.fromBytes['R8'],self.fromBytes['U2'],self.fromBytes['I1'],self.fromBytes['U1'],self.fromBytes['X1'],self.fromBytes['U1']]
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
        
        # Overwrite Receiver Tracking Status Flags
        data['leapSec'] = bool(data['recStat'][-1])
        data['clckReset'] = bool(data['recStat'][-2])
       
        # Parse dynamic portion of the message
        for measurement in range(data['numMeas']):
            starts = [16,24,32,36,37,38,39,40,42,43,44,45,46]
            starts = [start + measurement*32 for start in starts]
            stops = starts[1:] + [47 + measurement*32]
            fmts = [self.fromBytes['R8'],self.fromBytes['R8'],self.fromBytes['R4']] + [self.fromBytes['U1']]*4 + [self.fromBytes['U2'], 
                    self.fromBytes['U1']] + [self.fromBytes['X1']]*4
            labels = ['prMes (m)', 'cpMes (cycles)', 'doMes (Hz)', 'gnssId', 'svId', 'sigId', 'freqId', 'locktime',
                      'cno (dBHz)', 'prStdev (m)', 'cpStdev (cycles)', 'doStdev (Hz)', 'trkStat']
            meas = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
            
            # Overwrite measurement bitlabels
            prStd= list(reversed(meas['prStdev (m)'][0:3]))
            prStd= self.fromBytes['intFromBits'](prStd)*0.01*2**sum(meas['prStdev (m)'])
            meas['prStd'] = prStd
            
            cpStd = list(reversed(meas['cpStdev (cycles)'][0:3]))
            cpStd = self.fromBytes['intFromBits'](cpStd)*0.004
            meas['cpStd'] = cpStd
            
            doStd = list(reversed(meas['doStdev (Hz)'][0:3]))
            doStd = self.fromBytes['intFromBits'](doStd)*0.002*2**sum(meas['doStdev (Hz)'])
            meas['doStd'] = doStd
            
            starts = list(range(4))
            stops = starts[1:] + [4]
            labels = ['prValid', 'cpValid', 'halfCyc', 'subHaldCyc']
            fmts = [bool] * 4
            meas['trkStatValues'] = {label: fmt(meas['trkStat'][start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

            # Determine name of measurement, and add meas to ret
            gnss = self.gnssId.get(meas['gnssId'], f'gnssId: {meas["gnssId"]} not in parser')
            svid = meas['svId']
            sigId = self.signalId.get(gnss, {}).get(meas['sigId'], f'sigId: {meas["sigId"]} not in parser')
            gnssData = data.setdefault(gnss, {})
            svData = gnssData.setdefault(svid, {})
            svData[sigId] = meas

        return data
    

    def cfg_nav5(self,payload):
        """Get/Set Navigation Engine Values...can be done using valset, valget, valdel too.."""                                                               # Return dictionary                                            
        starts = [0,2,3,4,8,12,13,14,16,18,20,22,23,24,25,26,28,30]                        
        stops = starts[1:] + [31]                                                 
        fmts = [self.fromBytes['X2'],self.fromBytes['U1'],self.fromBytes['U1'],self.fromBytes['I4'],self.fromBytes['U4'],
                self.fromBytes['I1'],self.fromBytes['U1']] + [self.fromBytes['U2']]*4 +  [self.fromBytes['U1']]*4 
        fmts += [self.fromBytes['U2']]*2 + [self.fromBytes['U1']]
        labels = ['mask', 'dynModel', 'fixMode', 'fixAlt (m)', 'fixAltVar (m^2)', 'minElev (deg)', 'drLimit (s)', 'pDop', 'tDop', 
                  'pAcc', 'tAcc', 'staticHoldThresh', 'dgnssTimeout', 'cnoThreshNumSVs', 'cnoThresh', 'reserved0', 'staticHoldMaxDist', 
                  'utcStandard']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

        # Overwrite bitmask
        starts = list(range(9)) + [10]
        stops = starts[1:] + [11]
        labels = ['dyn', 'minEl', 'posFixMode', 'drLimit', 'posMask', 'timeMask', 'staticHoldMask', 'dgpsMask', 'cnoThreshold', 'utc',
                  'dynModel']
        data['maskValues'] = {label : bool(data['mask'][start:stop]) for start, stop, label in zip(starts,stops,labels)}
        
        # Apply scales, overwrites
        scales = [0.01, 0.0001, 0.1, 0.1]
        labels = ['fixAlt (m)', 'fixAltVar (m^2)','pDop', 'tDop']
        for scale, label in zip(scales, labels):
            data[label] = data[label]*scale
        
        # Overwrite dictionary values
        dynamicModel = { 0 : 'Portable',
                         2 : 'Stationary',
                         3 : 'Pedestrian',
                         4 : 'Automotive',
                         5 : 'Sea',
                         6 : 'Airborne, < 1g acceleration',
                         7 : 'Airborne, < 2g acceleration',
                         8 : 'Airborne, < 4g acceleration',
                         9 : 'Wristwatch',
                         10 : 'Motorbike',
                         11 : 'Robotic Lawnmower',
                         12 : 'Electric Kick Scooter'           
            }
        fixMode = {1: '2D', 2:'3D', 3:'Auto 2D/3D'}
        utcStandard = {0: 'Automatic, based on GNSS configuratiuon', 
                        3 : 'UTC USNO via GPS derivation', 
                        5 : 'UTC European Laboratoris via Galileo derivation',
                        6 : 'UTC USSR via GLONASS derivation',
                        7 : 'UTC NTSC China via BDS derivation',
                        8 : 'UTC NPLI (National Physics Lab India) via NAVIC derivation'
            }
        data['dynModel'] = dynamicModel.get(data['dynModel'], f'dynamicModel {data["dynModel"]} not in parser')
        data['maskValues']['posFixMode'] = fixMode.get(data['maskValues']['posFixMode'], 'posFixMode not in parser')
        data['utcStandard'] = utcStandard.get(data['utcStandard'], f'utcStandard {data["utcStandard"]} not in parser')
        return data
    

    def nav_cov(self,payload):
        """Periodic/Polled message giving the covariance matrice for position and velocity solutions in
        topcentric coordinate system, (local NED). Matrices are symmetric, henco only upper triangle is output."""                                                                                                      
        starts = [0,4,5,6,7] + list(range(16,61,4))
        stops = starts[1:] + [64]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['U1']]*3 + [sum] + [self.fromBytes['R4']]*12
        labels = ['iTOW (ms)', 'version' ,'posCovValid', 'velCovValid', 'reserved0', 'posCovNN', 'posCovNE', 'posCovND',
                  'posCovEE', 'posCovED', 'posCovDD', 'velCovNN', 'velCovNE', 'velCovND', 'velCovEE', 'velCovED', 'velCovDD']
        return {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
      
    
    def cfg_rate(self,payload):
        """Get/Set message that determines the rate at which navigation solutions (and the measurements they
        depend on) are generated by the receiver. Calculation of the navigation solution will be alligned to 
        the top of a second zero of the configured reference time system. Power saving mode can alter
        expected effects from this command."""                                                                                                     
        starts = list(range(0,5,2))
        stops = starts[1:] + [6]
        fmts = [self.fromBytes['U2']]*3
        labels = ['measRate (ms)', 'navRate (cycles)', 'timeRef']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}

        # Overwrite Time Reference
        timeRef = {0: 'UTC',
                    1: 'GPS',
                    2: 'GLONASS',
                    3: 'BeiDou',
                    4: 'Galileo',
                    5: 'NavIC'}
        data['timeRef'] = timeRef.get(data['timeRef'], f'timeRef {data["timeRef"]} not in parser')
        return data
        

    def nav_hpposecef(self,payload):
        """Periodic/Polled message providing high precision position in ECEF."""                                    
        starts = [0,1,4,8,12,16,20,21,22,23,24]
        stops = starts[1:] + [28]
        fmts = [self.fromBytes['U1']]  + [sum] + [self.fromBytes['U4']] + [self.fromBytes['I4']]*3 + [self.fromBytes['I1']]*3 + [self.fromBytes['X1'],self.fromBytes['U4']]
        labels = ['version', 'reserved0','iTOW (ms)', 'ecefX (m)', 'ecefY (m)', 'ecefZ (m)','ecefXHp (m)', 'ecefYHp (m)', 'ecefZHp (m)', 'flags', 'pAcc (m)']
        scales = [1]*3 + [1e-2]*3 + [1e-4]*3 + [1, 1e-4]
        data = {label: fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels, scales)}

        # Overwrite Values
        data['invalidEcef'] = bool(data['flags'][0])
        labels = ['calculatedEcefX (m)', 'calculatedEcefY (m)', 'calculatedEcefZ (m)']
        ecefs = ['ecefX (m)', 'ecefY (m)', 'ecefZ (m)']
        hppEcefs = ['ecefXHp (m)', 'ecefYHp (m)', 'ecefZHp (m)']
        for label, ecef, hppEcef in zip(labels, ecefs,hppEcefs):
            data[label] = data[ecef] +data[hppEcef]
        return data


    def nav_hpposllh(self,payload):
        """Periodic/Polled message providing high precision position in LLH.
        Defaults to WGS84 coordinate system (can be altered)."""
        starts = [0,1,3,4] + list(range(8,25,4)) + list(range(25,29)) + [32]
        stops = starts[1:] + [36]
        fmts = [self.fromBytes['U1'], self.fromBytes['U2']] + [self.fromBytes['X1'],self.fromBytes['U4']] + [self.fromBytes['I4']]*4 
        fmts += [self.fromBytes['I1']]*4 + [self.fromBytes['U4']]*2
        labels = ['version', 'reserved0', 'flags', 'iTOW (ms)', 'lon (deg)', 'lat (deg)', 'height (m)', 'hMSL (m)', 
                'lonHp (deg)', 'latHp (deg)', 'heightHp (m)', 'hMSLHp (m)', 'hAcc (m)', 'vAcc (m)']
        scales = [1]*4 + [1e-7]*2 + [1e-3]*2 + [1e-9]*2 + [1e-4]*4
        data = {label: fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}

        # Overwrite Values
        data['invalidLLH'] = bool(data['flags'][0])
        
        labels = ['calculatedLon (deg)', 'calculatedLat (deg)', 'calculatedHeight (m)', 'calculatedHMSL (m)']
        llhs = ['lon (deg)', 'lat (deg)', 'height (m)', 'hMSL (m)']
        hpLlhs = ['lonHp (deg)', 'latHp (deg)', 'heightHp (m)', 'hMSLHp (m)']
        for label, llh, hpLlh in zip(labels, llhs,hpLlhs):
            data[label] = data[llh] + data[hpLlh]
        return data


    def nav_velecef(self,payload):
        """Periodic/Polled message providing velocity vectors in ECEF frame."""                                     
        starts = list(range(0,17,4))
        stops = starts[1:] + [20]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['I4']]*3 + [self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'ecefVX (m/s)', 'ecefVY (m/s)', 'ecefVZ (m/s)', 'sAcc (m/s)']
        scales = [1] + [1e-2]*4
        return {label : fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}
       

    def nav_velned(self,payload):
        """Periodic/Polled message providing velocity vectors in ECEF frame."""                               
        starts = list(range(0,33,4))
        stops = starts[1:] + [36]
        fmts = [self.fromBytes['U4']] + [self.fromBytes['I4']]*3 + [self.fromBytes['U4']]*2 + [self.fromBytes['I4'],self.fromBytes['U4'],self.fromBytes['U4']]
        labels = ['iTOW (ms)', 'velN (m/s)', 'velE (m/s)', 'velD (m/s)', 'speed (m/s)', 'gSpeed (m/s)', 'heading (deg)', 'sAcc (m/s)', 'cAcc (deg)']
        scales = [1] + [1e-2]*6 + [1e-2, 1e-5]
        return {label : fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}
       

    def cfg_usb(self,payload):
        """Get/Set Navigation Engine Values...can be done using valset, valget, valdel too.."""                                                                                                     
        starts = [0,2,4,6,8,10,12,44,76]
        stops = starts[1:] + [108]                                                       # List of indices where message labels end. 29] ensures indexing ends at 24
        fmts = [self.fromBytes['U2']]*5 + [self.fromBytes['X2']] + [self.fromBytes['CH']]*3
        labels = ['vendorId', 'productID', 'reserved0', 'reserved1','powerConsumption',
                 'flags', 'vendorString', 'productString', 'serialNumber']
        data = {label : fmt(payload[start:stop]) for start, stop, fmt, label in zip(starts,stops,fmts,labels)}
    
        # Overwrite bitmask
        data['reEnum'] = bool(data['flags'][0])
        if bool(data['flags'][1]):
            data['powerMode'] = 'self-powered'
        else:
            data['powerMode'] = 'bus-powered'
        
        # Overwrite on unit
        data['powerConsumption (mA)'] = data['powerConsumption']
        return data

    
    def rxm_sfrbx(self,message):    
        """Output message containing the complete subframe of broacast navigation data decoded from a single signal
        the number of data words reported in each message depends on the nature of the signal."""
        
        # Setup iterables
        numberWords = self.fromBytes['U1'](message[4:5])
        starts = list(range(8)) 
        stops = starts[1:] + [8]
        fmts = [self.fromBytes['U1']]*7
        labels = ['gnssId', 'svId', 'sigId', 'freqId', 'numWords', 'chn', 'version']
        data = {label:fmt(message[start:stop]) for start,stop,label,fmt in zip(starts,stops,labels,fmts)} 
        for word in range(1, numberWords):
            data[f'dword{word}'] = [self.fromBytes['U4'](message[8 + word*4 : 12 + word*4])]
        gnss = self.gnssId.get(data['gnssId'], f'gnssId {data["gnssId"]} not in parser')
        signal = self.signalId.get(gnss, {}).get(data['sigId'], f'signalId {data["sigId"]} not in parser')
        return {gnss: {signal : data}}
    
    def cfg_valget(self, payload):
        length = len(payload)                             
        starts = [0,1,2] + list(range(4,length,4))
        stops = starts[1:] + [length]
        fmts = [self.fromBytes['U1'],self.fromBytes['U1'],self.fromBytes['U2']] + [self.fromBytes['U1']]*length
        labels = ['version', 'layer', 'position'] + ['']*(length-3)
        scales = [1] + [1e-2]*5 + [1e-5, 1e-2, 1e-5]
        return {label : fmt(payload[start:stop])*scale for start, stop, fmt, label, scale in zip(starts,stops,fmts,labels,scales)}
    
    def ubx_sec(self, payload):
        return {'version': self.fromBytes['U1'](payload[0:1]), 'uniqueId': ''.join([str(_) for _ in payload[4:10]])}

    ########################################## Writing messages ########################################
    def set_cfg_gnss(self, settings = {'GPS' : {'GPS L1CA': True,
                                                'GPS L2C': True,
                                                'GPS L5': True},
                                        'SBAS' : {'SBAS L1CA': True},
                                        'Galileo' : {'Galileo E1': True,
                                                    'Galileo E5a': True,
                                                    'Galileo E5b': True},
                                        'Beidou' : {'Beidou B1I': True,
                                                    'Beidou B2I': True,
                                                    'Beidou B2A': True},
                                        'IMES' : {'IMES L1': True},
                                        'QZSS' : {'QZSS L1CA': True,
                                                'QZSS L1S': True,
                                                'QZSS L2C': True,
                                                'QZSS L5': True},
                                        'GLONASS' : {'GLONASS L1': True,
                                                    'GLONASS L2': True}}): 

        """Create and return a GNSS configuration message with the following inputs:
            {signal: enabled}
            Number of tracking channels are reserved.
            Turning off a signal = do not include in settings!"""       

        # Create necessary bits
        header = self.header
        classId = b'\x06\x3e'
        length = self.toBytes['U2'](4 + len(settings)*8)
        nCfgs = self.toBytes['U1'](len(settings))
        
        # Iteratively create repeated portion of the message as cfgBlock
        cfgBlock = b''
        reversedGnssId = {value:key for key, value in self.gnssId.items()}
        for const, signalSettings in settings.items():
            gnssId = self.toBytes['U1'](reversedGnssId[const])
            enabledSignals = [self.sigCfgMask.get(signal, b'\x00') for signal, enabled in signalSettings.items() if enabled]
            enableConst = len(enabledSignals) > 0
            sigCfgMaskBits = hex(int(enableConst)).encode('ASCII') + bytes(15)
            sigCfgMaskBits += b''.join(enabledSignals) + bytes(32-len(enabledSignals))
            cfgBlock += gnssId + b'\x00'*3 + sigCfgMaskBits
        
        payload = b'\x00'*3 + nCfgs + cfgBlock
        checksumPortion = classId + length + payload
        checksum = self.checksum(checksumPortion)
        return self.header + checksumPortion + checksum

            
    def set_cfg_reset(self, params = {'eph' : 1, 'alm' : 1, 'health' : 1, 'klob' : 1, 
                                    'pos' : 1, 'clkd' : 1, 'osc' : 1,
                                    'utc' : 1, 'rtc' : 1, 'aop' : 1}, 
                  resetType = 'GNSS Only', special = 'Cold Start'):
        """Returns bytes needed to perform cold, hot, warm...etc. starts. Reset types include
        Hardware', 'Controlled Software', 'GNSS Only', 'Soft Hardware', 'Controlled GNSS Stop', 
        'Controlled GNSS Start'"""
        
        # Set class, id, length
        classId = b'\x06\x04'
        length = self.toBytes['U2'](4)
        
        # Set parameters and labels
        params = defaultdict(lambda: 1, params)                                # Set default dictionary to 1 (reset value)
        labels = ['eph', 'alm', 'health', 'klob', 'pos', 'clkd', 'osc', 'utc', 'rtc', 'sfdr', 'vmon', 'tct', 'aop']
       
        # Build bitlabel of parameters to restart as list of ones and zeros
        navBbrMask = self.toBytes['X2']([msg.append(params.get(label,0)) for bit, label in enumerate(labels)])
 
        # Set reset type
        reset = {'Hardware'              : b'\x00',
                 'Controlled Software'   : b'\x01',
                 'GNSS Only'             : b'\x02',
                 'Soft Hardware'         : b'\x04',
                 'Controlled GNSS Stop'  : b'\x08',
                 'Controlled GNSS Start' : b'\x09',
            }
        
        # Overwrite with special sets
        specialDict = {'Hot Start' : b'\x00\x00',                             # Note: switched bytes for little endian fmt
                   'Cold Start' : b'\xff\xff',
                   'Warm Start' : b'\x01\x00'}
        if special in specialDict:
            msg = specialDict[special]
        
        # Create actual message bytes
        payload = msg + reset[resetType] + b'\x00'                            # Add reserved label as '\x00'
        checksumRange = classId + length + payload
        checksum = self.checksum(checksumRange)
        return self.header + checksumRange + checksum

    def set_cfg_valdel(self, layer = 'Flash', transaction = 'Override', cfgKeyIds = []):
        """This is a set message deletees saved configuration values, effectively setting them to default.
        -------Changes upload to flash and BBR layer, but are NOT effective until layers are loaded into RAM--------
        valid layers: 'BBR', 'Flash'"""
        
        # Setup messge constants
        classId =  b'\x06\x8C'
        version =  b'\x00' # This is version 0 (this message for v1) used in testing ucenter
        reserved = b'\x00' #'\x00' V0 has 2 reserved, V1 only has one
        
        # Setup layer bitmask
        if re.search('BBR', layer, re.IGNORECASE):
            layers = [0,1,0,0,0,0,0,0]
        else:
            layers = [0,0,1,0,0,0,0,0]
        layers = self.fromBytes['intFromBits'](layers)
        layers = int(layers).to_bytes(1,'big')
        
        # Setup Transaction
        transactionDict = {'Override': [0,0],  # 0 Transactionless, config applied and cancels prior (unflashed?) transactions
                        'Restart' : [0,1],       # 1 Re-start, config restarts transactions, overwriting prior transactions
                        'Deletion': [1,0],       # 2 Deletes ongoing transaction
                        'Apply and End' : [1,1]  # 3 Applies a transaction and ends transaction
                        }
        transactionDict = defaultdict(lambda :0, transactionDict)
        transactionBits = transactionDict[transaction] + [0]*6
        transaction = self.fromBytes['intFromBits'](transactionBits)
        transaction = int(transaction).to_bytes(1,'big')
        
        # Setup configuration IDs to delete
        rptGroup = b''
        for configurationKey in cfgKeyIds:
            try: 
                cfgId = self.cfgBytes[configurationKey]
                cfgId = bytes(byte for byte in reversed(cfgId))
                rptGroup += cfgId
            except:
                try: 
                    cfgId = configurationKey
                    cfgId = bytes(byte for byte in reversed(cfgId))
                    rptGroup += cfgId
                except:
                    print(f'Failed to apply {cfgId} to val del')
        
        # Calculate length
        payload  = version + layers + transaction + reserved + rptGroup
        length = self.toBytes['U2'](len(payload))
        
        # Calculate checksum
        checksumRange = classId + length + payload
        checksum = self.checksum(checksumRange)
        
        # Create and retrun message
        return self.header + checksumRange + checksum
    

    def set_cfg_valget(self, payload, rx_response = True, layer = 'Flash', cfgKeyIds = ['CFG_SFIMU_GYRO_TC_UPDATE_PERIOD']):
        """This is a pollled message to return configuration settings. Set rx_response = False and
        add kwargs to return bytes for a poll"""

        # Setup messge constants
        classId =  b'\x06\x8b'
        version =  b'\x00'                 # This is version 0 (this message for v1) used in testing ucenter
        position = b'\x00\x00'
        
        # Setup layer bitmask
        if re.search('RAM', layer, re.IGNORECASE):
            layer = 0
        elif re.search('BBR', layer, re.IGNORECASE):
            layer = 1
        elif re.search('Flash', layer, re.IGNORECASE):
              layer = 2
        else:
            layer = 7
        layer = self.toBytes['U1'](layer)
      
        # Setup configuration IDs to delete
        rptGroup = b''
        for configurationKey in cfgKeyIds:
            try:
                cfgId = self.cfgBytes[configurationKey]
                cfgId = bytes(byte for byte in reversed(cfgId))       
                rptGroup += cfgId
            except:
                try: 
                    cfgId = configurationKey
                    cfgId = bytes(byte for byte in reversed(cfgId)) 
                    rptGroup += cfgId
                except:
                    print(f'Failed create val_del message with configuration id {cfgId}')
                
        # Calculate length
        payload  = version + layer + position + rptGroup
        length = self.toBytes['U2'](len(payload))
        
        # Calculate checksum
        checksumRange = classId + length + payload
        checksum = self.checksum(checksumRange)
        
        # Create and retrun message
        return self.header + checksumRange + checksum
    
    
    def set_cfg_valset(self, layer = 'Flash', transaction = 'Override', cfgKeyIds = []):
        """This is much like valdel, but used to actually set configuration values into the receiver.
        cfgKeyIds MUST have (cfg_key_id, value) pair."""
        
        # Setup messge constants
        classId =  b'\x06\x8a'
        version =  b'\x00' 
        
        # Setup layer bitmask
        if re.search('Ram', layer, re.IGNORECASE):
            layers = [1,0,0,0,0,0,0,0]
        elif re.search('BBR', layer, re.IGNORECASE):
            layers = [0,1,0,0,0,0,0,0]
        elif re.search('Flash', layer, re.IGNORECASE):
            layers = [0,0,1,0,0,0,0,0]
        else:
            layers = [0,0,1,0,0,0,0,0]
        layers = self.fromBytes['intFromBits'](layers)
        layers = int(layers).to_bytes(1,'big')
        
        # Setup Transaction
        transactionDict = {'Override': [0,0],    # 0 Transactionless, config applied and cancels prior (unflashed?) transactions
                        'Restart' : [0,1],       # 1 Re-start, config restarts transactions, overwriting prior transactions
                        'Deletion': [1,0],       # 2 Deletes ongoing transaction
                        'Apply and End' : [1,1]  # 3 Applies a transaction and ends transaction
                        }
        transactionDict = defaultdict(lambda :0, transactionDict)
        transactionBits = transactionDict[transaction] + [0]*6
        transaction = self.fromBytes['intFromBits'](transactionBits)
        transaction = int(transaction).to_bytes(1,'big')
        
        # Setup reserved
        reserved = b'\x00'
        
        # Setup configuration IDs to change
        rptGroup = b''
        for configurationKey,value in cfgKeyIds:
            try: 
                cfgId = self.cfgBytes[configurationKey]
                cfgId = bytes(byte for byte in reversed(cfgId))
                rptGroup += cfgId
               
            except:
                try: 
                    cfgId = configurationKey
                    cfgId = bytes(byte for byte in reversed(cfgId))
                    rptGroup += cfgId
                except:
                    print(f'Failed to apply {cfgId} to val del')
            rptGroup += self.toBytes['U2'](value)                              # Add the value being set
                        
        # Calculate length
        payload  = version + layers + transaction + reserved + rptGroup
        length = self.toBytes['U2'](len(payload))
        
        # Calculate checksum
        checksumRange = classId + length + payload
        checksum = self.checksum(checksumRange)
        
        # Create and retrun message
        return self.header + checksumRange + checksum     
    
    


    #### NOTE: page 233 gives sample keys to enable/disable by signal 
    
    # def l1ca_nav(self, parsed):
    #     """Parse the raw hex values provided from parsed rxm_sfrbx L1CA parseds into
    #     useful information.
        
    #     Timing:
    #     (25 pages with 5 subframes each and 300 bits/subframe) / 50 bits/second = 750 seconds, 
    #     or 12.5 minutes per NAV parsed."""
        
    #     words = [parsed[key] for key in parsed.keys() if re.search('Word [0-9]', key)]
    #     bits = ''.join([format(int(word, base = 16), '030b') for word in words])                                   # Convert back to integer, then to bits
    #     # bits = ''.join([bit for bit in reversed(bits)])
        
    #     # CONFIRMED: WORDS ARE IN PROPER ORDER, AS GIVEN
    #     TLM = ''.join([bit for bit in format(int(words[0], base = 16), '030b')])
    #     HOW = ''.join([bit for bit in format(int(words[1], base = 16), '030b')])
        
        
    #     subframes = {'000' : 'Invalid',
    #                  '001' : 1,
    #                  '010' : 2,
    #                  '011' : 3,
    #                  '100' : 4,
    #                  '101' : 5,
    #                  '110' : 'Invalid',
    #                  '111' : 'Invalid'
    #         }
    #     integrity = TLM[23]                             # Telemetry integrity flag
    #     as_flag = HOW[18]                               # Anti spoof bit
    #     subframe = subframes[HOW[19:22]]             # Subframe ID
    #     # print(subframe_id)
    #     # subframe = subframes[subframe_id]               # Subframe
    #     alert = HOW[17]                                 # Alert bit, if 1 then user range accuracy is worse than indicated in subframe 1 and use the SV at your own risk
    #     C = TLM[22:24]                                  # Reserved bits
    #     t = HOW[17:19]                                  # Solved for bits to preserve parity check with zeros in bits 29 and 30 (?)
        
    #     if subframe == 1:
            
    #         # Map label name: (start, stop), format function
    #         parse_fmt = {
    #         # TLM 0:22                                       22 bits
    #         # C (TLM integrity and reserved) 22:24           2 bits
    #         # Parity 24:30                                   6 bits
    #         # HOW 30:52                                      22 bits
    #         # t 52:54                                        2 bits
    #         # Parity 54:60                                   6 bits
    #         'Week Number' : (60,70),                       # 10 bits     
    #         'L2 Codes' : (70, 72),                         # 2 bits
    #         'URA Index' : (72, 76),                        # 4 bits
    #         'SV Health' : (76, 82),                        # 6 bits
    #         'IODC 2MSB' : (82, 84),                        # 2 bits 
    #         # Parity 84:90                                 # 6 bits             
    #         'L2 P Data Flag' : (90,91),                    # 1 bit
    #         # Reserved 91:114                              # 23 bits          
    #         # Parity 114:120                               # 6 bits
    #         # Reserved 120:144                             # 24 bits
    #         # Parity 144:150                               # 6 bits                 
    #         # Reserved 150:174                             # 24 bits
    #         # Parity 174:180                               # 6 bits
    #         # Reserved 180:196                             # 16 bits
    #         'T_GD' : (196,204),                            # 8 bits
    #         # Parity 204:210                               # 6 bits
    #         'IODC 8LSB' : (210,218),                       # 8 bits
    #         'TOC' : (218, 234),                            # 16 bits
    #         # Parity 234:240                               # 6 bits
    #         'a_f2' : (240,248),                            # 8 bits
    #         'a_f1' : (248,264),                            # 16 bits   
    #         # Parity 264:270                               # 6 bits
    #         'a_f0' : (270,292),                            # 22 bits
    #         # t      292:294                               # 2 bits
    #         # Parity 294:300                               # 6 bits   
    #             }
            
    #         # Parse bits
    #         ret = {}
    #         for label, indexes in parse_fmt.items():
    #             start, stop = indexes
    #             ret[label] = bits[start:stop]
            
    #         # Overwrite values
    #         # ret['Week Number'] = int(ret['Week Number'], base = 2)%1024 # Convert week number to integer, take 1024 modulus to get modulo 1024 binary representation
    #         ret['Week Number'] = 1024*2 + int(ret['Week Number'], base = 2)  # NEED TO CONVERT FOR WEEK ROLLOVERS
    #         print(f'WEEK NUMBER: {ret["Week Number"]}')
            
    #         codes = {'00' : 'Invalid', 
    #                  '01' : 'P Code ON',
    #                  '10' : 'CA Code ON', 
    #                  '11' : 'Invalid'
    #             }
    #         ret['L2 Codes'] = codes[ret['L2 Codes']]
            
    #         ura_meters = {0 : (0,2.4),
    #                       1 : (2.4, 3.4),
    #                       2 : (3.4, 4.85),
    #                       3 : (4.85, 6.85),
    #                       4 : (6.85, 9.65),
    #                       5 : (9.65, 13.65),
    #                       6 : (13.65, 24.0),
    #                       7 : (24.0, 48.0),
    #                       8 : (48.0, 96.0),
    #                       9 : (96.0, 192.0),
    #                       10 : (192.0, 384.0),
    #                       11 : (384.0, 768.0),
    #                       12 : (786.0, 1536.0),
    #                       13 : (1536.0, 3072.0),
    #                       14 : (3072.0, 6144.0), 
    #                       15 : (6144.0, 'No accuracy availible')}
    #         low, high = ura_meters[int(ret['URA Index'],base = 2)]
    #         ret['URA Meters'] = f'User Range Accuracy between {low} and {high} meters'
            
    #         health = ret['SV Health']
    #         if health[0] == 0:
    #             ret['SV Health'] = 'All LNAV data OK'
    #         else:       
    #             health_codes = {
    #                 '00000' : 'All Signals OK',
    #                 '00001' : 'All signals are weak (3-6 dB below specified power)',
    #                 '00010' : 'All signals are dead',
    #                 '00011' : 'All signals have no data modulation',
                    
    #                 # L1P
    #                 '00100' : 'L1 P signal weak',
    #                 '00101' : 'L1 P signal dead',
    #                 '00110' : 'L1 P signal has no data modulation',
                    
    #                 # L2 P
    #                 '00111' : 'L2 P signal weak',
    #                 '01000' : 'L2 P signal dead',
    #                 '01001' : 'L2 P signal has no data modulation',
                    
    #                 # L1C
    #                 '01010' : 'L1C signal weak',
    #                 '01011' : 'L1C signal dead',
    #                 '01100' : 'L1C signal has no data modulation',
    #                 # L2C
    #                 '01101' : 'L2C signal weak',
    #                 '01110' : 'L2C signal dead',
    #                 '01111' : 'L2C signal has no data modulation',
                    
    #                 # L1 and L2 P
    #                 '10000' : 'L1 and L2 P signal weak',
    #                 '10001' : 'L1 and L2 P signal dead',
    #                 '10010' : 'L1 and L2 P signal has no data modulation',
                    
    #                 # L1 and L2C
    #                 '10011' : 'L1 and L2 C signal weak',
    #                 '10100' : 'L1 and L2 C signal dead',
    #                 '10101' : 'L1 and L2 C signal has no data modulation',
                    
    #                 # L1
    #                 '10110' : 'L1 signal weak',
    #                 '10111' : 'L1 signal dead',
    #                 '11000' : 'L1 signal has no data modulation',
                    
    #                 # L2
    #                 '11001' : 'L2 signal weak',
    #                 '11010' : 'L2 signal dead',
    #                 '11011' : 'L2 signal has no data modulation',
                    
    #                 # Assorted
    #                 '11100' : 'SV is temporarily out', 
    #                 '11101' : 'SV will be temporarily out', 
    #                 '11110' : 'One or more signals are deformed, but URA parameters are valid', 
    #                 '11111' : 'More than one combination of health issues'
    #                 }
                
    #             ret['SV Health'] = health_codes[health[1:]]
                
    #         iodc_bits = ret['IODC 2MSB'] + ret['IODC 8LSB']
    #         iodc = int(iodc_bits, base = 2)
    #         ret['IODC'] = iodc
    #         ret['L2 P Data Flag'] = not bool(ret['L2 P Data Flag'])
            
    #         # Group delay, clock correction...etc.  unaccounted for (requires additional computations on our part) ALSO: NEED TO APPLY SCALE FACTORS, ALGORITHMS, if used
    #         print(f'Parsed subframe 1 with a data set of: {ret}')
            
        
    #     print(f'SVID {parsed["SVID"]} Subframe {subframe} with integrity {bool(integrity)} and as bit {as_flag}')
        
############## Integration Functions ####################################################################


    def get_ubx_message(self, bytes_bin):
        """Takes bytes arrangement and returns (same bytes bin - 'found message', 'found message')
        'found message = False if there are no ubx messages in the bytes bin. Must be iteratively assigned
        to return all ubx messages in the bytes bin."""
        _ubx = re.search(b'\xB5\x62',bytes_bin)                                 # Get the ubx message
        if _ubx:                                                                # If ublox message found, continue parsing 
            start = _ubx.span()[0]                                              # Find index of ublox message start
            if len(bytes_bin[start:]) > 8:                                      # Ensure the message length is included in the received bytes
                length = struct.unpack('<H', bytes_bin[start+4:start+6])[0] + 8 # Get the message length. 2 sync, 2 class/message id, 2 length and 2 checksum bytes + payload bytes
                stop = start + length                                           # Calculate the index of the last byte of the message, based on message length (above)
                if len(bytes_bin) - start >= stop:                              # Ensure bytes_bin has entire message
                    return bytes_bin[:start] + bytes_bin[stop:], bytes_bin[start:stop]   # Return the bytes_bin-message, message
        return bytes_bin, False                                                   # If the full message is not found, return False and bytes bin (for consistency) if no complete ubx messages are found
    
    
    def splitAll(self, bytesBin):
        """Takes bytes, returns a tuple of bytes that were not used in parsed messages
        and a list of parsed messages (dict)"""

        messages = []
        while ubxMessage:= re.search(b'\xB5\x62',bytesBin):
            start = ubxMessage.start()
            if len(bytesBin[start:]) > 8: 
                length = struct.unpack('<H', bytesBin[start+4:start+6])[0] + 8 # Get the message length. 2 sync, 2 class/message id, 2 length and 2 checksum bytes + payload bytes
                end = start + length                                           # Calculate the index of the last byte of the message, based on message length (above)
                if len(bytesBin) >= end:                              # Ensure bytesBin has entire message
                    messages.append(bytesBin[start:end])
                    bytesBin = bytesBin[:start] + bytesBin[end:]  
                else:
                    break
            else:
                break

        return bytesBin, messages
    

        
    def filtered(self, bytesBin, sendMessages):
        messages = []
        while ubxMessage:= re.search(b'\xB5\x62',bytesBin):
            start = ubxMessage.start()
            if len(bytesBin[start:]) > 8: 
                length = struct.unpack('<H', bytesBin[start+4:start+6])[0] + 8 # Get the message length. 2 sync, 2 class/message id, 2 length and 2 checksum bytes + payload bytes
                end = start + length                                           # Calculate the index of the last byte of the message, based on message length (above)
                if len(bytesBin) >= end:                                       # Ensure bytesBin has entire message
                    messageBytes = bytesBin[start:end]
                    if next(iter(self.parse(messageBytes))) in sendMessages:
                        messages.append(messageBytes)
                    bytesBin = bytesBin[:start] + bytesBin[end:]  
                else:
                    break
            else:
                break
        return messages
    
    
    def configure(self, filepath):
        """Opens filepath to ublox config file and returns bytes necessary to send
        the results to a receiver. Config file format: 'msg-name - B5 62 ... checksum'
        where the hex string already includes header and checksum."""
        
        # Dont accept logically false filepaths
        if filepath == False:
            return []
        
        # Ensure filepath exists
        if not os.path.exists(filepath):
            print(f'Filepath:{filepath} invalid. Please try again')
            return []
        
        # Create return list and send compiled byte commands to return list
        ret = []                                  # Initialize return list
        with open(filepath, 'r') as file:         # Open configuration file
            for line in file.readlines():         # Iterate lines (each line is an individual ubx command)
                if re.match(r'^\s*#', line) or re.match(r'^\s*$', line): # Ignore comments and blank lines
                    continue
                line = line.rstrip()              # Remove trailing whitespace
                
                # Extract hex string after '-' delimiter (already includes header and checksum)
                if '-' not in line:
                    continue
                hexString = line[line.rfind('-') + 1:].strip()
                
                # Convert space-separated hex to bytes (e.g., 'B5 62 06 01...' -> b'\xb5\x62\x06\x01...')
                try:
                    command = bytes.fromhex(hexString.replace(' ', ''))
                    ret.append(command)
                except ValueError as e:
                    print(f'Failed to parse hex in line: {line[:50]}... Error: {e}')
                    continue
                    
        return ret

    def test(self, usb, nTries = 3):
        for baudRate in (9600, 115200):
            usb.baudrate = baudRate
            ret = {}
            messageBytes, messageNames, fields = [b"\xb5b'\x03\x00\x00*\xa5", b'\xb5b\n\x04\x00\x00\x0e4'],['ubx_sec', 'mon_ver'],['uniqueId', 'extension3']
            for messageByte, messageName, field in zip(messageBytes, messageNames, fields):
                for _ in range(nTries):
                    usb.read(usb.inWaiting())
                    for _ in range(3):
                        usb.write(messageByte)
                        time.sleep(0.125)
                    bytesBin, messages = self.parseAll(usb.read(usb.inWaiting()))
                    for parsed in messages:
                        if messageName in parsed:
                            ret[messageName] = parsed[messageName].get(field, 'unknown')
            # Handle return here to break out early if baud rate is found
            if ret:
                id, model = ret.get('ubx_sec', 'unknown'), ret.get('mon_ver', 'unknown')
                if match := re.search('[0-9]{1,2}', model):
                    model = model[match.start() - 1: match.end()+1]
                return id, model
            else:
                return False
        
        
if __name__ == '__main__':
    ubx = Ubx()

    # raw = {'nav_sig' : b'B5 62 01 43 78 02 68 56 9C 1D 00 27 00 00 00 01 00 00 A6 FF 1E 07 01 02 69 01 00 00 00 00 00 01 03 00 40 00 15 04 00 02 21 00 00 00 00 00 00 02 00 00 2D 00 1E 07 01 02 69 01 00 00 00 00 00 02 04 00 00 00 00 01 00 00 01 00 00 00 00 00 00 07 00 00 01 00 1C 07 01 02 69 01 00 00 00 00 00 07 03 00 1A 00 0F 04 00 02 21 00 00 00 00 00 00 08 00 00 00 00 00 01 00 00 01 00 00 00 00 00 00 08 04 00 00 00 00 01 00 00 01 00 00 00 00 00 00 0D 00 00 16 00 21 07 01 00 69 01 00 00 00 00 00 0D 04 00 00 00 00 01 00 00 01 00 00 00 00 00 00 0E 00 00 F5 FF 25 07 01 00 69 01 00 00 00 00 00 0E 03 00 E1 FF 1D 07 00 00 21 00 00 00 00 00 00 11 00 00 FE FF 2E 07 01 00 69 01 00 00 00 00 00 11 03 00 17 00 1A 07 00 00 21 00 00 00 00 00 00 13 00 00 F3 FF 1E 07 01 00 69 01 00 00 00 00 00 16 00 00 BE FF 21 07 01 00 69 01 00 00 00 00 00 16 04 00 00 00 00 01 00 00 01 00 00 00 00 00 00 1E 00 00 1B 00 25 07 01 00 69 01 00 00 00 00 00 1E 03 00 D0 FF 1E 07 00 00 21 00 00 00 00 00 01 83 00 00 03 00 2A 07 01 00 69 01 00 00 00 00 01 85 00 00 04 00 25 07 01 00 69 01 00 00 00 00 01 8A 00 00 00 00 00 01 00 00 00 00 00 00 00 00 02 13 00 00 00 00 25 07 00 00 00 00 00 00 00 00 06 04 00 0D 00 00 00 01 00 00 01 00 00 00 00 00 06 04 02 0D A1 FF 16 04 00 00 29 00 00 00 00 00 06 05 00 08 DC FF 1E 04 00 00 29 00 00 00 00 00 06 05 02 08 E7 FF 1D 07 00 00 29 00 00 00 00 00 06 06 00 03 00 00 13 03 00 00 01 00 00 00 00 00 06 06 02 03 00 00 00 01 00 00 01 00 00 00 00 00 06 0E 00 00 15 00 1E 07 00 02 29 00 00 00 00 00 06 0E 02 00 68 00 18 04 00 02 29 00 00 00 00 00 06 0F 00 07 2B 00 21 07 00 00 29 00 00 00 00 00 06 0F 02 07 07 00 1C 07 00 00 29 00 00 00 00 00 06 11 00 0B 06 00 1F 07 00 02 29 00 00 00 00 00 06 11 02 0B F4 FF 15 04 00 02 29 00 00 00 00 00 06 17 00 0A 02 00 18 04 00 02 29 00 00 00 00 00 06 17 02 0A 00 00 00 01 00 00 01 00 00 00 00 00 06 18 00 09 24 00 21 07 00 02 29 00 00 00 00 00 06 18 02 09 49 00 17 04 00 02 29 00 00 00 00 00 A3 C2',
    #         'cfg_gnss' : b'B5 62 06 3E 34 00 00 00 3C 06 00 08 10 00 01 00 11 01 01 03 03 00 01 00 01 01 02 0A 12 00 01 00 21 01 03 02 05 00 01 00 11 01 05 00 04 00 01 00 11 01 06 08 0C 00 01 00 11 01 96 DF',
    #         'cfg_nav5' : b'B5 62 06 23 28 00 02 00 4C 66 C0 00 00 00 00 00 03 20 06 00 00 00 00 00 32 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 28 65',
    #         'cfg_usb' : b'B5 62 06 1B 6C 00 46 15 A9 01 00 00 00 00 00 00 02 00 75 2D 62 6C 6F 78 20 41 47 20 2D 20 77 77 77 2E 75 2D 62 6C 6F 78 2E 63 6F 6D 00 00 00 00 00 00 75 2D 62 6C 6F 78 20 47 4E 53 53 20 72 65 63 65 69 76 65 72 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
    #         'mon_ver' : b'B5 62 0A 04 DC 00 45 58 54 20 43 4F 52 45 20 31 2E 30 30 20 28 66 31 30 63 33 36 29 00 00 00 00 00 00 00 00 30 30 31 39 30 30 30 30 00 00 52 4F 4D 20 42 41 53 45 20 30 78 31 31 38 42 32 30 36 30 00 00 00 00 00 00 00 00 00 00 00 46 57 56 45 52 3D 48 50 47 20 31 2E 31 33 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 50 52 4F 54 56 45 52 3D 32 37 2E 31 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 4D 4F 44 3D 5A 45 44 2D 46 39 50 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 47 50 53 3B 47 4C 4F 3B 47 41 4C 3B 42 44 53 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 53 42 41 53 3B 51 5A 53 53 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 F3 B0',
    #         'mon_gnss' : b'B5 62 0A 28 08 00 00 0F 0F 0F 04 00 00 00 6B 9E',
    #         'nav_hpposecef' : b'B5 62 01 13 1C 00 00 00 00 00 18 D2 29 0F 98 0C 43 F5 36 84 2D E5 7C 6E A3 18 31 DB F3 00 E6 4B 00 00 CF F6',
    #         'nav_hpposllh' : b'B5 62 01 14 24 00 00 00 00 00 F8 00 2A 0F 11 DF 59 BD C0 3D 3A 18 F0 97 14 00 46 E1 14 00 2D E1 FF FE CD 2C 00 00 C0 3A 00 00 93 73',
    #         'ack_ack' : b'B5 62 05 01 02 00 06 24 32 5B',
    #         'ack_nack' : b'B5 62 05 00 02 00 06 01 0E 33'
    # }
    # for test in raw.values(): 
    #     test = b''.join([int(_,16).to_bytes(1,'big') for _ in test.split(b' ')])
    #     print(ubx.parse(test))

    # Setup connection
    # import serial
    # conn = serial.Serial('COM5')
    # originalSettings = {'GPS' : {'GPS L1CA': True,
    #                             'GPS L2C': True,
    #                             'GPS L5': True},
    #                     'SBAS' : {'SBAS L1CA': True},
    #                     'Galileo' : {'Galileo E1': True,
    #                                 'Galileo E5a': True,
    #                                 'Galileo E5b': True},
    #                     'Beidou' : {'Beidou B1I': True,
    #                                 'Beidou B2I': True,
    #                                 'Beidou B2A': True},
    #                     'IMES' : {'IMES L1': True},
    #                     'QZSS' : {'QZSS L1CA': True,
    #                             'QZSS L1S': True,
    #                             'QZSS L2C': True,
    #                             'QZSS L5': True},
    #                     'GLONASS' : {'GLONASS L1': True,
    #                                 'GLONASS L2': True}}
    
    # # Iterate signals
    # for const, constDict in settings.items():
    #     for signals, enable in constDict.items():
    #         settingsCopy = {key:value for key, value in originalSettings.items()}
    #         for const2, constDict2 in settingsCopy.items():
    #             for signals2, enable2 in constDict2.items():

    #                 # Try each new signal as false
    #                 settingsCopy2 = {key:value for key, value in originalSettings.items()}
    #                 settingsCopy2[const2][signals2] = False

    #                 # Add each of the others as false too
    #                 for _const, _constDict in settingsCopy2.items():
    #                     for _signal, _enable in _constDict.items():
    #                         settingsCopy2[_const][_signal] = False
    #                         print(settingsCopy2)


    # conn.close()

# import socket
# conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# conn.bind(('localhost', 8080))
# conn.listen(1)
# print("Server is listening on port 8080...")
# client, addr = conn.accept()
# print(f"Connection from {addr} has been established!")
# while True:
#     print(f'Waiting for data from {addr}...')
#     data = client.recv(1024)
#     if not data:
#         break
#     print(f'Received data: {data}')
#     data = [hex(_)[2:].upper().zfill(2) for _ in list(data)]
#     print(f'>>> Converted to list: {" ".join(data)}')
